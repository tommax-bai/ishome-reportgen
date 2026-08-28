"""出口过检·规则层：数字纪律、占位解析、禁词、cr- 判据物化执行。"""

from __future__ import annotations

from reportgen_worker.gate import collect_banned_terms, run_unit_gate
from reportgen_worker.models import Card
from tests.support import load_package

PACKAGE = load_package()


def checks_of(violations: list) -> set[str]:  # type: ignore[type-arg]
    return {v.check for v in violations}


def test_clean_card_passes() -> None:
    card = Card(
        thesis="操作台的高度要跟着主厨的身体走。",
        body="台面高按 {lkp-counter-height} 做，弯腰和架肩都不会发生。",
        number_refs=["lkp-counter-height"],
    )
    assert run_unit_gate([card], "ergonomics", PACKAGE) == []


def test_bare_digit_rejected() -> None:
    card = Card(thesis="台面高做九百。", body="就是 900mm。", number_refs=[])
    assert "gate-digit-outside-ref" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


def test_unresolved_and_undeclared_ref_rejected() -> None:
    card = Card(
        thesis="照度按标准来。",
        body="起居室做到 {lkp-not-exist}。",
        number_refs=[],
    )
    violations = checks_of(run_unit_gate([card], "ergonomics", PACKAGE))
    assert "gate-number-ref-unresolved" in violations
    assert "gate-number-ref-undeclared" in violations


def test_cross_domain_ref_rejected() -> None:
    """单元只见本域落点（图 v0.2 §2）：ergonomics 单元引用 lighting 落点即违规。"""
    card = Card(
        thesis="顺便说照度。",
        body="起居室 {lkp-illuminance-living} 即可。",
        number_refs=["lkp-illuminance-living"],
    )
    assert "gate-number-ref-unresolved" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


def test_banned_terms_merged_from_vocab_and_persona() -> None:
    assert collect_banned_terms("ergonomics", PACKAGE) == ["人体工学", "依据", "综合考量"]
    card = Card(thesis="从人体工学出发。", body="综合考量后给出结论。", number_refs=[])
    violations = run_unit_gate([card], "ergonomics", PACKAGE)
    assert [v for v in violations if v.check == "gate-banned-term"]


def test_release_check_pattern_executed() -> None:
    """cr- 判据是 release 数据：包内 cr-weak-words 的 pattern 被物化执行。"""
    card = Card(thesis="这里可能可以再看看。", body="建议考虑一下。", number_refs=[])
    assert "cr-weak-words" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))
