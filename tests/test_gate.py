"""出口过检·规则层：数字纪律、占位解析、禁词、cr- 判据物化执行、降档门禁（规则 4.10/5.8）。"""

from __future__ import annotations

import copy

from reportgen_worker.gate import (
    assertion_budget,
    backed_predicates,
    collect_banned_terms,
    run_package_gate,
    run_unit_gate,
    unbacked_predicates,
)
from reportgen_worker.models import Card, ReportDataPackage
from tests.support import PACKAGE_JSON, load_package

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


# ---------------------------------------------------------------------------
# 降档门禁（规则 4.10/4.10a/5.8）：主旨句支点、断言预算、隐藏落点、裸标识名泄漏
# ---------------------------------------------------------------------------


def test_thesis_may_not_lean_on_degraded_anchor() -> None:
    """判断句支点必须过可核性门：降档落点进正文可以，进主旨句不行。"""
    in_thesis = Card(
        thesis="台面就该做到 {lkp-counter-height}。",
        body="这样你切菜时手腕是平的。",
        number_refs=["lkp-counter-height"],
    )
    assert "gate-thesis-degraded-anchor" in checks_of(
        run_unit_gate([in_thesis], "ergonomics", PACKAGE)
    )

    in_body = Card(
        thesis="操作台的高度要跟着主厨的身体走。",
        body="参考范围是 {lkp-counter-height}，最终以现场复核为准。",
        number_refs=["lkp-counter-height"],
    )
    assert run_unit_gate([in_body], "ergonomics", PACKAGE) == []


def test_thesis_on_calibrated_anchor_passes() -> None:
    """过门落点照常作支点——门禁只拦未背书的，不误伤有背书的。"""
    card = Card(
        thesis="这条主通道必须留够 {lkp-passage-main}。",
        body="端着汤走过去不用侧身。",
        number_refs=["lkp-passage-main"],
        assertions=["通道净宽是否够"],
    )
    assert run_unit_gate([card], "ergonomics", PACKAGE) == []


def test_assertion_budget_index_and_backing() -> None:
    """断言预算核验的可确定性判定形态：谓词 → requires 是否全部已求值且非降档。"""
    assert assertion_budget("ergonomics", PACKAGE) == {
        "通道净宽是否够": ["lkp-passage-main"],
        "台面高度": ["lkp-counter-height"],
        "挂杆高度": ["lkp-wardrobe-rod"],
    }
    assert backed_predicates("ergonomics", PACKAGE) == ["通道净宽是否够"]
    assert unbacked_predicates("ergonomics", PACKAGE) == ["台面高度", "挂杆高度"]


def test_assertion_declared_but_unbacked_rejected() -> None:
    """requires 降档 → 该谓词不得被声明使用（规则 4.10a 经验条目不进断言预算）。"""
    degraded_require = Card(
        thesis="操作台高度这样定就对了。",
        body="参考范围见 {lkp-counter-height}。",
        number_refs=["lkp-counter-height"],
        assertions=["台面高度"],
    )
    assert "gate-assertion-unbacked" in checks_of(
        run_unit_gate([degraded_require], "ergonomics", PACKAGE)
    )

    missing_require = Card(
        thesis="挂杆高度这样定就对了。",
        body="抬手就够得着。",
        number_refs=[],
        assertions=["挂杆高度"],
    )
    assert "gate-assertion-unbacked" in checks_of(
        run_unit_gate([missing_require], "ergonomics", PACKAGE)
    )


def test_assertion_outside_budget_rejected() -> None:
    """预算外的判断句题目一律拒——断言预算是 persona release 数据，不是写作器自由裁量。"""
    card = Card(
        thesis="这套房子的收纳一定不够。",
        body="东西没地方放。",
        number_refs=[],
        assertions=["收纳够不够"],
    )
    assert "gate-assertion-not-budgeted" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


def test_withheld_anchor_reference_rejected() -> None:
    """隐藏落点不得被引用，且打回理由说得清（不是笼统的"引用不存在"）。"""
    card = Card(
        thesis="挂杆按你的身高定。",
        body="定在 {lkp-wardrobe-rod} 这个高度。",
        number_refs=["lkp-wardrobe-rod"],
    )
    violations = run_unit_gate([card], "ergonomics", PACKAGE)
    assert "gate-withheld-anchor-referenced" in checks_of(violations)
    assert "gate-number-ref-unresolved" not in checks_of(violations)


def test_package_gate_rejects_delivered_withheld_anchor() -> None:
    """生产方契约：判为隐藏却带值下发 = 求值线违约，写作前就拦下（不烧 LLM 调用）。"""
    assert run_package_gate("ergonomics", PACKAGE) == []

    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["presentation"] = "WITHHELD"
    violations = run_package_gate("ergonomics", ReportDataPackage.model_validate(tainted))
    assert checks_of(violations) == {"gate-withheld-anchor-delivered"}


def test_bare_lkp_identifier_leak_rejected() -> None:
    """真实缺陷回归：LLM 把内部标识名写进客户正文（"未背书的 lkp-budget-share 显示…"）。

    该句不含数字，裸数字门禁拦不住；本条纯正则可判，补进 gate。
    """
    leaked = Card(
        thesis="钱主要花在定制柜上。",
        body="未背书的 lkp-budget-share 显示占比偏高。",
        number_refs=[],
    )
    assert "gate-lkp-identifier-leak" in checks_of(run_unit_gate([leaked], "ergonomics", PACKAGE))

    placeholder_form = Card(
        thesis="操作台的高度要跟着主厨的身体走。",
        body="参考范围 {lkp-counter-height}。",
        number_refs=["lkp-counter-height"],
    )
    assert "gate-lkp-identifier-leak" not in checks_of(
        run_unit_gate([placeholder_form], "ergonomics", PACKAGE)
    )


def test_chinese_numeral_evasion_rejected() -> None:
    """真跑回归（2026-08-28 三域 PAID）：模型被禁裸数字后改用中文数字写数。

    "亮三到五倍""不能低于九十"——阿拉伯数字门禁一字未命中，读者读到的仍是无背书的数字。
    """
    evaded = Card(
        thesis="重点区域要比周围亮三到五倍。",
        body="显色指数不能低于九十。",
        number_refs=[],
    )
    assert "gate-chinese-numeral" in checks_of(run_unit_gate([evaded], "ergonomics", PACKAGE))


def test_chinese_words_containing_numerals_pass() -> None:
    """量词判据不误伤日常词：一般/一起/十分/百般 里的数字字都不跟量词。"""
    ordinary = Card(
        thesis="操作台的高度要跟着主厨的身体走。",
        body="一般活动时你会一起进出，十分顺手，台面按 {lkp-counter-height} 做。",
        number_refs=["lkp-counter-height"],
    )
    assert "gate-chinese-numeral" not in checks_of(run_unit_gate([ordinary], "ergonomics", PACKAGE))


def test_chinese_numeral_founding_sample_now_caught() -> None:
    """自迭代回路首采 §五-1：初版判据漏掉了它自己的立案样本（"不能低于九十"无量词）。

    该句在四次真跑里逐字出现且全部通过——比较词形态补上后必须被拦。
    """
    for bad in (
        "显色指数不能低于九十。",
        "亮度要达到三成以上。",
        "地面以上七十多厘米高的位置要有足够亮度。",
        "主材与定制往往占去近半支出。",
    ):
        card = Card(thesis=bad, body="正文。", number_refs=[])
        assert "gate-chinese-numeral" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE)), (
            bad
        )


def test_chinese_numeral_still_not_false_positive() -> None:
    """收宽形态后仍不许误伤日常词。"""
    ordinary = Card(
        thesis="操作台的高度要跟着主厨的身体走。",
        body="一般活动时你会一起进出，十分顺手，台面按 {lkp-counter-height} 做。",
        number_refs=["lkp-counter-height"],
    )
    assert "gate-chinese-numeral" not in checks_of(run_unit_gate([ordinary], "ergonomics", PACKAGE))


def test_chinese_numeral_case_matrix() -> None:
    """判据边界钉死：**测量/选型**的数要拦，**列举计数**的数不拦（规则 2.3 数字三分法的射程）。

    全部取自 2026-08-28 真跑：拦的六条逐字出现过，放的八条曾被过拦或属日常词。
    过拦与漏拦同样是失效——"四个区域"被打成违规会让引擎写不成句。
    """
    from reportgen_worker.gate import CHINESE_NUMBER_RE

    for text in (
        "显色指数不能低于九十。",
        "重点区域比周围亮三到五倍。",
        "地面以上七十多厘米高的位置。",
        "主材与定制往往占去近半支出。",
        "全屋色温不超过三种。",
        "预算大概是二十几万。",
    ):
        assert CHINESE_NUMBER_RE.search(text), f"应拦未拦：{text}"
    for text in (
        "厨房、卫生间、书房、卧室四个区域",
        "拆改、水电、泥木这三项",
        "这两点你要盯住",
        "三层照明规划",
        "一般活动时",
        "十分顺手",
        "一起进出",
        "更接近自然光下的样子",
    ):
        assert not CHINESE_NUMBER_RE.search(text), f"误伤：{text}"


def test_range_split_reference_gets_contract_hint() -> None:
    """真跑形态：模型想分引用区间两端，自造 {lkp-x-min}/{lkp-x-max}。

    打回理由必须讲清渲染契约（一个占位符=整条落点），否则它只会换个名字再造一次；
    拆 min/max 会丢掉另一端，而上下限往往各管一条纪律。
    """
    card = Card(
        thesis="台面高度定在这个范围。",
        body="从 {lkp-counter-height-min} 到 {lkp-counter-height-max}。",
        number_refs=["lkp-counter-height-min", "lkp-counter-height-max"],
    )
    violations = [
        v
        for v in run_unit_gate([card], "ergonomics", PACKAGE)
        if v.check == "gate-number-ref-unresolved"
    ]
    assert violations
    assert "占位符代表整条落点" in violations[0].detail
    assert "{lkp-counter-height}" in violations[0].detail
