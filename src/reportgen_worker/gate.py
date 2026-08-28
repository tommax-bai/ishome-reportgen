"""出口过检·规则层（图 v0.2 §3）：确定性机检，卡片不过检产生违规清单
（重写反馈/failed verdict 的依据）。

三类判据分离：

- **引擎纪律（gate-\\*）**：图 v0.2 §0 的硬性约束，代码即形态——数字只经 {lkp-*} 占位、
  占位必须可解析、必填非空、禁词零命中、客户语域禁裸 lkp- 标识名。
  不属 cr- 命名空间（cr- 是 release 数据，规则 4.10b）。
- **降档门禁（gate-thesis-\\* / gate-assertion-\\* / gate-withheld-\\*）**：规则 4.10/4.10a/5.8
  的消费侧强制。档位判定由求值线做完（anchors[].presentation），本层只做**可确定性判定**的
  三件事：①主旨句引用的落点必须全部是 THESIS_SUPPORT；②卡片声明的断言预算谓词必须在本域
  persona 的 assertion_budget 内，且其 requires 的 lkp- 全部已求值且非降档；③被隐藏的落点
  一律不得被引用，也不该出现在 anchors 里。
  **明确不做的**：判断句的语义识别——"这句话算不算判断句"没有确定性判据，机检不假实现。
  未声明 assertions 却写成判断句、参考口吻被写成断言口吻，全部归**判官层**（分域反例库，
  图 v0.2 §3 出口过检·判官层）。本层只保证"声明了就必须有背书"，不保证"没声明就不是断言"。
- **cr- 判据（release 数据物化执行）**：随报告数据包下发的 checks 中带 pattern 的文本级判据
  逐条跑；无 pattern 的类型（count_max/cross_field 等）本层暂不执行。
"""

from __future__ import annotations

import re

from reportgen_worker.models import Card, ReportDataPackage, Violation

PLACEHOLDER_RE = re.compile(r"\{(lkp-[a-z0-9-]+)\}")
DIGIT_RE = re.compile(r"[0-9０-９]")
BARE_LKP_RE = re.compile(r"lkp-", re.IGNORECASE)

# 中文数字 + 量词/单位：真跑（2026-08-28 三域 PAID）抓到的绕过——模型被禁裸数字后改写
# "亮三到五倍""不能低于九十"，阿拉伯数字门禁一字未命中，但读者读到的仍是没有落点背书的数字。
# 只在**跟着量词/单位**时判违规：光判中文数字字符会把"一般活动""一起""十分"全打成违规
# （量词表按真实误报补，不做通用中文数字解析——不发明表达式语法）。
CHINESE_NUMERAL = "零一二三四五六七八九十百千两"
CHINESE_QUANTIFIER = (
    "倍|度|米|厘米|毫米|公分|平米|平方米|㎡|元|万|盏|个|条|级|档|种|层|成|折|"
    "分之|点|秒|分钟|小时|天|周|月|年|K|lx|mm|cm|m"
)
CHINESE_NUMBER_RE = re.compile(
    f"[{CHINESE_NUMERAL}]+(?:到|至|~|-)?[{CHINESE_NUMERAL}]*({CHINESE_QUANTIFIER})"
)

THESIS_SUPPORT = "THESIS_SUPPORT"
WITHHELD = "WITHHELD"


def collect_banned_terms(domain: str, package: ReportDataPackage) -> list[str]:
    """公共禁词（包内已物化）+ persona 域内禁词（banned_terms 内字符串列表，如 domain_extra）。"""
    terms = set(package.banned_terms_by_domain.get(domain, []))
    for persona in package.personas_by_domain.get(domain, []):
        for value in persona.banned_terms.values():
            if isinstance(value, list):
                terms.update(t for t in value if isinstance(t, str))
    return sorted(terms)


def assertion_budget(domain: str, package: ReportDataPackage) -> dict[str, list[str]]:
    """本域断言预算：谓词 → 它需要的 lkp- 清单（persona 四件之三，规则 4.13/5.8）。

    多 persona 合并；形态不合的条目静默跳过——预算是 release 数据，损坏条目由资产回路的核验
    跑批负责，运行时不替它兜底。
    """
    budget: dict[str, list[str]] = {}
    for persona in package.personas_by_domain.get(domain, []):
        for entry in persona.assertion_budget:
            if not isinstance(entry, dict):
                continue
            predicate = entry.get("predicate")
            requires = entry.get("requires", [])
            if not isinstance(predicate, str) or not isinstance(requires, list):
                continue
            budget[predicate] = sorted({r for r in requires if isinstance(r, str)})
    return budget


def thesis_support_ids(domain: str, package: ReportDataPackage) -> set[str]:
    """本域可作判断句支点的落点（规则 4.10a：只有过可核性门的条目能背书断言）。"""
    return {a.lkp_id for a in package.domain_anchors(domain) if a.presentation == THESIS_SUPPORT}


def backed_predicates(domain: str, package: ReportDataPackage) -> list[str]:
    """本次背书得起的判断句谓词——requires 全部已求值且非降档，可被卡片声明使用。"""
    supported = thesis_support_ids(domain, package)
    budget = assertion_budget(domain, package)
    return sorted(
        p for p, requires in budget.items() if requires and supported.issuperset(requires)
    )


def unbacked_predicates(domain: str, package: ReportDataPackage) -> list[str]:
    """本次背书不起的谓词：写进 prompt 让写作器知道这几句这轮不许说（规则 4.18 宁薄勿撑）。"""
    backed = set(backed_predicates(domain, package))
    return sorted(p for p in assertion_budget(domain, package) if p not in backed)


def run_package_gate(domain: str, package: ReportDataPackage) -> list[Violation]:
    """写作前的生产方契约守卫：数据包本身违约的，不必烧一次 LLM 调用才发现。"""
    violations: list[Violation] = []
    for anchor in package.anchors:
        if anchor.presentation == WITHHELD:
            violations.append(
                Violation(
                    check="gate-withheld-anchor-delivered",
                    detail=(
                        f"{anchor.lkp_id} 判为隐藏却随包下发了值——隐藏即不下发"
                        "（规则 4.10；求值线降档纪律的输出违约）"
                    ),
                )
            )
    return violations


def run_unit_gate(cards: list[Card], domain: str, package: ReportDataPackage) -> list[Violation]:
    violations: list[Violation] = []
    anchor_ids = {a.lkp_id for a in package.domain_anchors(domain)}
    supported = thesis_support_ids(domain, package)
    withheld_ids = {w.lkp_id for w in package.withheld_anchors}
    banned = collect_banned_terms(domain, package)
    budget = assertion_budget(domain, package)
    pattern_checks = [c for c in package.checks_by_domain.get(domain, []) if c.pattern]

    for index, card in enumerate(cards):
        label = f"card[{index}]"
        text = f"{card.thesis}\n{card.body}"
        if not card.thesis.strip() or not card.body.strip():
            violations.append(
                Violation(check="gate-required-field", detail=f"{label} thesis/body 空")
            )

        placeholders = set(PLACEHOLDER_RE.findall(text))
        for ref in sorted(placeholders | set(card.number_refs)):
            if ref in withheld_ids:
                violations.append(
                    Violation(
                        check="gate-withheld-anchor-referenced",
                        detail=(
                            f"{label} 引用 {ref}：该落点已按纪律隐藏，本产物内不存在（规则 4.10）"
                        ),
                    )
                )
            elif ref not in anchor_ids:
                violations.append(
                    Violation(
                        check="gate-number-ref-unresolved",
                        detail=f"{label} 引用 {ref} 不在本域落点对象内（数字只能引用求值线产出）",
                    )
                )
        undeclared = placeholders - set(card.number_refs)
        if undeclared:
            violations.append(
                Violation(
                    check="gate-number-ref-undeclared",
                    detail=f"{label} 占位符未在 number_refs 声明：{sorted(undeclared)}",
                )
            )

        # 主旨句是判断句的所在处（规则 5.8 断言预算只花在这里）：支点必须全部过可核性门
        for ref in sorted(set(PLACEHOLDER_RE.findall(card.thesis))):
            if ref in anchor_ids and ref not in supported:
                violations.append(
                    Violation(
                        check="gate-thesis-degraded-anchor",
                        detail=(
                            f"{label} 主旨句以降档落点 {ref} 作支点——未背书条目只能以参考口吻"
                            "进正文，撑不起判断句（规则 4.10/4.10a）"
                        ),
                    )
                )

        for predicate in card.assertions:
            requires = budget.get(predicate)
            if requires is None:
                violations.append(
                    Violation(
                        check="gate-assertion-not-budgeted",
                        detail=(
                            f"{label} 声明谓词「{predicate}」不在本域断言预算内"
                            f"（预算内的：{sorted(budget)}，规则 4.13/5.8）"
                        ),
                    )
                )
                continue
            missing = [r for r in requires if r not in anchor_ids]
            degraded = [r for r in requires if r in anchor_ids and r not in supported]
            if missing or degraded:
                violations.append(
                    Violation(
                        check="gate-assertion-unbacked",
                        detail=(
                            f"{label} 谓词「{predicate}」背书不足：缺失 {missing}、降档 {degraded}"
                            "——断言预算要求 requires 全部已求值且非降档（规则 5.8/4.10a）"
                        ),
                    )
                )

        stripped = PLACEHOLDER_RE.sub("", text)
        if DIGIT_RE.search(stripped):
            violations.append(
                Violation(
                    check="gate-digit-outside-ref",
                    detail=f"{label} 正文出现裸数字（数字只能经 {{lkp-*}} 占位引用落点对象）",
                )
            )
        chinese_number = CHINESE_NUMBER_RE.search(stripped)
        if chinese_number:
            violations.append(
                Violation(
                    check="gate-chinese-numeral",
                    detail=f"{label} 正文以中文数字写数（「{chinese_number.group(0)}」）"
                    "——换 {lkp-*} 占位；数字纪律管的是数不是字形",
                )
            )
        # 客户语域禁内部标识名：{lkp-*} 是渲染契约，裸 lkp- 是把内部命名空间漏给业主看
        if BARE_LKP_RE.search(stripped):
            violations.append(
                Violation(
                    check="gate-lkp-identifier-leak",
                    detail=(
                        f"{label} 正文出现裸 lkp- 标识名——内部落点编号不进客户语域；"
                        "要引用数字写 {lkp-id} 占位，要说这条没背书就用人话说"
                    ),
                )
            )

        for term in banned:
            if term in text:
                violations.append(
                    Violation(check="gate-banned-term", detail=f"{label} 命中禁词「{term}」")
                )

        for check in pattern_checks:
            assert check.pattern is not None
            if re.search(check.pattern, stripped):
                violations.append(
                    Violation(check=check.asset_id, detail=f"{label} {check.message}")
                )

    return violations
