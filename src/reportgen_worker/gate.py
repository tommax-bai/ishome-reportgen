"""出口过检·规则层（图 v0.2 §3）：确定性机检，卡片不过检产生违规清单
（重写反馈/failed verdict 的依据）。

两类判据分离：
- **引擎纪律（gate-\\*）**：图 v0.2 §0 的硬性约束，代码即形态——数字只经 {lkp-*} 占位、
  占位必须可解析、
  必填非空、禁词零命中。不属 cr- 命名空间（cr- 是 release 数据，规则 4.10b）。
- **cr- 判据（release 数据物化执行）**：随报告数据包下发的 checks 中带 pattern 的文本级判据逐条跑；
  无 pattern 的类型（count_max/cross_field 等）本层暂不执行——断言预算核验（cr-assertion-backed）
  需判断句语义识别，随判官层落地，不在规则层假实现。
"""

from __future__ import annotations

import re

from reportgen_worker.models import Card, ReportDataPackage, Violation

PLACEHOLDER_RE = re.compile(r"\{(lkp-[a-z0-9-]+)\}")
DIGIT_RE = re.compile(r"[0-9０-９]")


def collect_banned_terms(domain: str, package: ReportDataPackage) -> list[str]:
    """公共禁词（包内已物化）+ persona 域内禁词（banned_terms 内字符串列表，如 domain_extra）。"""
    terms = set(package.banned_terms_by_domain.get(domain, []))
    for persona in package.personas_by_domain.get(domain, []):
        for value in persona.banned_terms.values():
            if isinstance(value, list):
                terms.update(t for t in value if isinstance(t, str))
    return sorted(terms)


def run_unit_gate(cards: list[Card], domain: str, package: ReportDataPackage) -> list[Violation]:
    violations: list[Violation] = []
    anchor_ids = {a.lkp_id for a in package.domain_anchors(domain)}
    banned = collect_banned_terms(domain, package)
    pattern_checks = [c for c in package.checks_by_domain.get(domain, []) if c.pattern]

    for index, card in enumerate(cards):
        label = f"card[{index}]"
        text = f"{card.thesis}\n{card.body}"
        if not card.thesis.strip() or not card.body.strip():
            violations.append(
                Violation(check="gate-required-field", detail=f"{label} thesis/body 空")
            )

        placeholders = set(PLACEHOLDER_RE.findall(text))
        for ref in placeholders | set(card.number_refs):
            if ref not in anchor_ids:
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

        stripped = PLACEHOLDER_RE.sub("", text)
        if DIGIT_RE.search(stripped):
            violations.append(
                Violation(
                    check="gate-digit-outside-ref",
                    detail=f"{label} 正文出现裸数字（数字只能经 {{lkp-*}} 占位引用落点对象）",
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
