"""出口过检·规则层（图 v0.2 §3）：确定性机检，卡片不过检产生违规清单
（重写反馈/failed verdict 的依据）。

三类判据分离：

- **引擎纪律（gate-\\*）**：图 v0.2 §0 的硬性约束，代码即形态——数字只经 {lkp-*} 占位、
  占位必须可解析、必填非空、禁词零命中、客户语域禁裸 lkp- 标识名、语域示范不得被逐字抄进正文。
  不属 cr- 命名空间（cr- 是 release 数据，规则 4.10b）。
- **语域与标注门禁（gate-thesis-\\* / gate-assertion-\\* / gate-provenance-\\*）**：规则
  4.10a/4.10c/5.8 的消费侧强制。判定由求值线做完（anchors[].presentation 与
  anchors[].provenance.annotationRequired），本层只做**可确定性判定**的事：①主旨句引用的落点必须
  全部是 THESIS_SUPPORT；②卡片声明的断言预算谓词必须在本域 persona 的 assertion_budget 内，
  且其 requires 的 lkp- 全部已求值且非降档；③未过门/已过期落点被引用时，同页必须挂它的依据标注
  （规则 4.10c 标注必挂，v2.4 起替代隐藏档；页级比对在册级 :mod:`reportgen_worker.activities`）。
  **明确不做的**：判断句的语义识别——"这句话算不算判断句"没有确定性判据，机检不假实现。
  未声明 assertions 却写成判断句、参考口吻被写成断言口吻，全部归**判官层**（分域反例库，
  图 v0.2 §3 出口过检·判官层）。本层只保证"声明了就必须有背书"，不保证"没声明就不是断言"。
- **cr- 判据（release 数据物化执行）**：随报告数据包下发的 checks 中带 pattern 的文本级判据
  逐条跑；无 pattern 的类型（count_max/cross_field 等）本层暂不执行。
"""

from __future__ import annotations

import re

from reportgen_worker.models import Card, ProvenanceNote, ReportAnchor, ReportDataPackage, Violation

PLACEHOLDER_RE = re.compile(r"\{(lkp-[a-z0-9-]+)\}")
DIGIT_RE = re.compile(r"[0-9０-９]")
BARE_LKP_RE = re.compile(r"lkp-", re.IGNORECASE)

# 中文数字：真跑（2026-08-28）抓到的绕过——模型被禁裸数字后改写"亮三到五倍""不能低于九十"，
# 阿拉伯数字门禁一字未命中，但读者读到的仍是没有落点背书的数字。
#
# 判据形态经一次自我修正：初版只判"数字+量词"，结果**漏掉了它自己的立案样本**——"不能低于九十"
# 没有量词（自迭代回路首采 §五-1 实测，该句在四次跑里逐字出现且全部通过）。光判数字字符又会把
# "一般活动""一起""十分"打成违规。故收成三条互补形态，每条都要求数词处在**数量语境**里：
#   ① 数词 + 量词/单位（三到五倍、九十厘米）
#   ② 比较词 + 数词（不低于九十、达到三成）——补的正是立案样本这一类
#   ③ 数词 + 概数词 + 单位（七十多厘米、二十几万）——真跑漏过一次定位数字（0.75m 参考平面）
# 量词表按真实误报补，不做通用中文数字解析（不发明表达式语法）。
CHINESE_NUMERAL = "零一二三四五六七八九十百千两半"
# 量词只收**测量/选型**类：规则 2.3 数字三分法的射程是定位/选型/分析数字，**列举计数不在其内**。
# 故 个|条|项|次|遍|盏|种|层|档|点 一律不收——真跑实测"四个区域""这三项""这两点"会被打成违规，
# 那是把"数东西"当成"报数值"，拦下去等于让引擎写不成句（过拦与漏拦同样是失效）。
# 计数若确属选型数字（如"色温不超过三种"），由形态②的比较词兜住。
CHINESE_QUANTIFIER = (
    "倍|度|米|厘米|毫米|公分|平米|平方米|㎡|元|万|级|成|折|"
    "分之|秒|分钟|小时|天|周|月|年|K|lx|mm|cm|m"
)
CHINESE_COMPARATOR = (
    "低于|高于|不到|不足|达到|超过|至少|最多|多于|少于|大于|小于|将近|接近|大约|近|约"
)
CHINESE_APPROX = "多|几|来|余"
_NUM = f"[{CHINESE_NUMERAL}]+(?:到|至|~|-)?[{CHINESE_NUMERAL}]*"
CHINESE_NUMBER_RE = re.compile(
    f"(?:{_NUM}(?:{CHINESE_QUANTIFIER})"  # ① 数词+量词
    f"|(?:{CHINESE_COMPARATOR}){_NUM}"  # ② 比较词+数词（立案样本"不能低于九十"）
    f"|{_NUM}(?:{CHINESE_APPROX})(?:{CHINESE_QUANTIFIER}))"  # ③ 数词+概数+单位
)

THESIS_SUPPORT = "THESIS_SUPPORT"
WITHHELD = "WITHHELD"
CALIBRATED = "calibrated"

# persona 判断句样例（规则 4.13 之②）进 prompt 后的真跑副作用（2026-08-28）：模型把 ✓ 示范句
# **逐字抄进卡片**当成这家人的结论——示范是"怎么讲"的样本，不是"讲什么"的素材，抄过去就成了
# 一句没有落点背书、与这家人无关的断言。整句重合是确定性判据，故归本层；半句化用属语义，归判官层。
MIN_SAMPLE_LENGTH = 12


def collect_banned_terms(domain: str, package: ReportDataPackage) -> list[str]:
    """公共禁词（包内已物化）+ persona 域内禁词（banned_terms 内字符串列表，如 domain_extra）。"""
    terms = set(package.banned_terms_by_domain.get(domain, []))
    for persona in package.personas_by_domain.get(domain, []):
        for value in persona.banned_terms.values():
            if isinstance(value, list):
                terms.update(t for t in value if isinstance(t, str))
    return sorted(terms)


def judgment_good_texts(domain: str, package: ReportDataPackage) -> list[str]:
    """本域判断句样例的**正例原文**（persona 四件之②的 ✓ 侧，规则 4.13）。

    形态不合的条目静默跳过（同 :func:`assertion_budget`）。太短的正例不收：示范句是整句，
    短语级重合是正常用词而非照抄，拿它判违规就成了过拦（过拦与漏拦同样是失效）。
    """
    texts: list[str] = []
    for persona in package.personas_by_domain.get(domain, []):
        for entry in persona.judgment_samples:
            if not isinstance(entry, dict):
                continue
            good = entry.get("good")
            if isinstance(good, str) and len(good.strip()) >= MIN_SAMPLE_LENGTH:
                texts.append(good.strip())
    return sorted(set(texts))


def annotation_required_anchors(package: ReportDataPackage) -> dict[str, ReportAnchor]:
    """全册范围内"进正文就必须随页标注"的落点（规则 4.10c）：lkp_id → 落点。

    **不切域**：册级校验按页比对，而页是按域成的——切域会让"某页引用了别域落点"这种情况漏检。
    要求集的口径是落点自己的 ``provenance``，不是页、不是卡片：谁被引用了谁就得有标注。
    """
    return {a.lkp_id: a for a in package.anchors if a.requires_annotation}


def provenance_note(anchor: ReportAnchor) -> ProvenanceNote:
    """落点 → 页上的依据标注（纯投影，零生成：字段原样搬，不拼接、不改写、不补空）。"""
    provenance = anchor.provenance
    return ProvenanceNote(
        lkp_id=anchor.lkp_id,
        source=provenance.source if provenance is not None else anchor.source,
        effective_from=provenance.effective_from if provenance is not None else None,
        effective_to=provenance.effective_to if provenance is not None else None,
        calibration=provenance.calibration if provenance is not None else anchor.calibration,
    )


def required_provenance_notes(
    cards: list[Card], domain: str, package: ReportDataPackage
) -> list[ProvenanceNote]:
    """本稿卡片实际引用的落点里，需要标注的那些（按 lkp_id 升序，确定性）。

    引用面取 ``number_refs`` 与正文占位符的**并集**：两者不一致本身是违规
    （``gate-number-ref-undeclared``），但标注要求不能等违规先被修好——过检没过的稿子不会成页，
    过了检的两者必然一致，取并集只是让本函数与门禁的执行次序无关。
    """
    required = annotation_required_anchors(package)
    referenced: set[str] = set()
    for card in cards:
        referenced |= set(card.number_refs)
        referenced |= set(PLACEHOLDER_RE.findall(f"{card.thesis}\n{card.body}"))
    return [provenance_note(required[ref]) for ref in sorted(referenced & set(required))]


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
    """写作前的生产方契约守卫：数据包本身违约的，不必烧一次 LLM 调用才发现。

    v2.4 起守的是**标注纪律的上游**：求值线声称某落点不必标注，但它根本没过可核性门——
    这一条若放过去，页级比对门禁会跟着一起放过（要求集是从 provenance 读的），
    整条标注链路就被生产侧一个字段悄悄关掉了。故在最前面拦一次，方向偏严。
    """
    violations: list[Violation] = []
    for anchor in package.anchors:
        # 隐藏档的旧守卫（规则 4.10 v2.3）：v2.4 已取消隐藏，**本条随拆分支一并删**——
        # 规范写死的实现纪律是"先建标注链路，再拆隐藏分支"，故它在本轮仍在岗。
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
        provenance = anchor.provenance
        if provenance is None:
            continue
        if provenance.calibration != anchor.calibration:
            violations.append(
                Violation(
                    check="gate-provenance-inconsistent",
                    detail=(
                        f"{anchor.lkp_id} provenance.calibration={provenance.calibration} "
                        f"与落点 calibration={anchor.calibration} 不一致——两处同源，不该有两个答案"
                    ),
                )
            )
        elif not provenance.annotation_required and provenance.calibration != CALIBRATED:
            violations.append(
                Violation(
                    check="gate-provenance-inconsistent",
                    detail=(
                        f"{anchor.lkp_id} 未过可核性门（{provenance.calibration}）却标称无需标注"
                        "——标注必挂是硬约束（规则 4.10c），生产侧判定违约"
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
    good_samples = judgment_good_texts(domain, package)
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
                # 真跑常见形态：模型想分别引用区间两端，自造 {lkp-x-min}/{lkp-x-max}。
                # 打回理由要说清渲染契约（一个占位符=整条落点），否则它只会换个名字再造一次。
                base = re.sub(r"-(min|max)$", "", ref)
                hint = (
                    f"——占位符代表整条落点，区间写 {{{base}}} 即可，拆 min/max 会丢掉另一端"
                    if base != ref and base in anchor_ids
                    else "（数字只能引用求值线产出）"
                )
                violations.append(
                    Violation(
                        check="gate-number-ref-unresolved",
                        detail=f"{label} 引用 {ref} 不在本域落点对象内{hint}",
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

        for sample in good_samples:
            if sample in text:
                violations.append(
                    Violation(
                        check="gate-sample-verbatim-copy",
                        detail=(
                            f"{label} 逐字抄了语域示范「{sample}」——示范给的是怎么讲，不是讲什么；"
                            "照抄等于把一句与这家人无关、也没有落点背书的话当成结论"
                        ),
                    )
                )

        for check in pattern_checks:
            assert check.pattern is not None
            hit = re.search(check.pattern, stripped) is not None
            # check_type 决定 pattern 的语义，此前被整个忽略（凡带 pattern 一律"命中即违规"），
            # 于是 regex_require_annotation（"出现工程量纲**则要求**配翻译"）被反着执行。
            # 当前因裸数字已禁而不会命中，是休眠 bug——自迭代回路首采 §五-2 抓到。
            # require 类的"是否配了翻译"本身判不确定（语义），故只在**命中且本卡没有任何落点引用**时
            # 判违规：引用了落点即视为已挂翻译，其余归判官层，机检不假实现。
            if check.check_type == "regex_require_annotation":
                if hit and not card.number_refs:
                    violations.append(
                        Violation(
                            check=check.asset_id,
                            detail=f"{label} {check.message}（命中量纲但本卡未引用任何落点）",
                        )
                    )
            elif hit:
                violations.append(
                    Violation(check=check.asset_id, detail=f"{label} {check.message}")
                )

    return violations
