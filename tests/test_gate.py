"""出口过检·规则层：数字纪律、占位解析、禁词、cr- 判据物化执行、降档门禁（规则 4.10/5.8）。"""

from __future__ import annotations

import copy

from reportgen_worker.gate import (
    annotation_required_anchors,
    assertion_budget,
    backed_predicates,
    collect_banned_terms,
    required_provenance_notes,
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


def test_thesis_body_duplicate_rejected() -> None:
    """正文逐字等于主旨句 = 这张卡没承载任何推导（真跑立案：ergonomics 22/22 逐字相同）。

    临时护栏：模型补一句填充话就能绕过，治本在叙事推导那一步；留它的理由是"逐字相同"零误判。
    """
    line = "床面高度在 {lkp-counter-height} 之间，这样你上下床时身体弯折幅度会更小。"
    card = Card(thesis=line, body=line, number_refs=["lkp-counter-height"])
    assert "gate-thesis-body-duplicate" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


def test_thesis_body_duplicate_only_when_verbatim() -> None:
    """只判逐字相同：不做相似度、不设阈值（阈值无数据依据，同"卡片数上限 6~8"被撤回的理由）。"""
    card = Card(
        thesis="台面高按主厨的身体定。",
        body="台面高按主厨的身体定：{lkp-counter-height} 这个区间，切菜时手腕不会架起来。",
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


def test_release_pattern_runs_on_raw_text_with_placeholders() -> None:
    """release 判据跑原文（2026-08-29 晚改）：占位符引用类 pattern 剥了占位就永远打不中。

    立案判据＝cr-bound-word-before-placeholder（边界词+{lkp-，渲染层单边界措辞叠字那条裁决）。
    """
    check = {
        "assetId": "cr-bound-word-before-placeholder",
        "checkType": "regex_deny",
        "scope": ["正文"],
        "pattern": "(不少于|不低于|至少|不超过)\\s*[（(]?\\{lkp-",
        "message": "占位符前禁边界词",
        "decidedBy": "用户裁决 2026-08-29 晚",
        "version": 1,
    }
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["checksByDomain"]["ergonomics"].append(check)
    package = ReportDataPackage.model_validate(tainted)
    card = Card(
        thesis="主通道要走得开。",
        body="主通道留出不少于 {lkp-passage-main} 的空间。",
        number_refs=["lkp-passage-main"],
    )
    assert "cr-bound-word-before-placeholder" in checks_of(
        run_unit_gate([card], "ergonomics", package)
    )


def test_release_check_pattern_executed() -> None:
    """cr- 判据是 release 数据：包内 cr-weak-words 的 pattern 被物化执行。"""
    card = Card(thesis="这里可能可以再看看。", body="建议考虑一下。", number_refs=[])
    assert "cr-weak-words" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


# ---------------------------------------------------------------------------
# 降档门禁（规则 4.10/4.10a/5.8）：主旨句支点、断言预算、隐藏落点、裸标识名泄漏
# ---------------------------------------------------------------------------


def test_unbacked_anchor_may_enter_thesis() -> None:
    """未过门的落点**可以进主旨句**（v2.4：规则 4.10c 明文废止"支点必须是可作支点档"）。

    机检不再管"用哪个落点下判断"，改管两件确定性的事：断言预算有没有背书（下面几条），
    以及这一页有没有标出依据（册级页面比对）。原门禁在这里拦了整整三个版本，拦的代价是
    未过门的建议根本进不了主旨句——也就进不了业主眼里，转正需要的行为信号无从产生。
    """
    in_thesis = Card(
        thesis="台面就该做到 {lkp-counter-height}。",
        body="这样你切菜时手腕是平的。",
        number_refs=["lkp-counter-height"],
    )
    assert run_unit_gate([in_thesis], "ergonomics", PACKAGE) == []


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


def test_unbacked_anchor_is_referenceable() -> None:
    """曾被隐藏的点值落点现在照常可引用（v2.4）：它只是语域受限，不是不存在。"""
    card = Card(
        thesis="挂杆按你的身高定。",
        body="定在 {lkp-wardrobe-rod} 这个高度。",
        number_refs=["lkp-wardrobe-rod"],
    )
    assert run_unit_gate([card], "ergonomics", PACKAGE) == []


def test_package_gate_passes_clean_package() -> None:
    """v2.4 起生产方契约守卫只剩标注一致性一条：隐藏档没了，"判为隐藏却带值下发"无从发生。"""
    assert run_package_gate("ergonomics", PACKAGE) == []


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


def test_verbatim_sample_copy_rejected() -> None:
    """真跑回归（2026-08-28）：语域示范进 prompt 后，模型把 ✓ 示范句逐字抄进卡片当结论。

    示范给的是"怎么讲"，不是"讲什么"——抄过去就成了一句与这家人无关、也没有落点背书的断言。
    真跑原句（budget 域）：`主材是唯一你能事后调的一项——柜子和水电定了就改不动了。`
    """
    card = Card(
        thesis="台面按主厨的身体定。",
        body="台面做到这个高度，你切菜时手腕是平的，不用弓腰。",
    )
    violations = [
        v
        for v in run_unit_gate([card], "ergonomics", PACKAGE)
        if v.check == "gate-sample-verbatim-copy"
    ]
    assert violations
    assert "示范给的是怎么讲" in violations[0].detail


def test_short_sample_does_not_over_block() -> None:
    """过拦与漏拦同样是失效：短语级重合是正常用词，只判整句照抄。"""
    persona = copy.deepcopy(PACKAGE_JSON)
    persona["personasByDomain"]["ergonomics"][0]["judgmentSamples"] = [
        {"bad": "台面偏高。", "good": "手腕是平的。", "reason": "cr-x"}
    ]
    package = ReportDataPackage.model_validate(persona)
    card = Card(thesis="台面按主厨的身体定。", body="做完你会发现手腕是平的。")
    assert "gate-sample-verbatim-copy" not in checks_of(
        run_unit_gate([card], "ergonomics", package)
    )


# ---------------------------------------------------------------------------
# 标注纪律（规则 4.10c，v2.4）：隐藏档取消后，"说了就必须标"的消费侧一半
# ---------------------------------------------------------------------------


def test_annotation_requirement_reads_provenance() -> None:
    """要求集口径是落点自己的 provenance：未过门的要标、过门的不要标。"""
    assert set(annotation_required_anchors(PACKAGE)) == {"lkp-counter-height", "lkp-wardrobe-rod"}


def test_annotation_requirement_falls_back_without_provenance() -> None:
    """老包（生产方未升级）无 provenance → 按 calibration 回退，**方向偏严**：宁可多标不可漏标。"""
    legacy = copy.deepcopy(PACKAGE_JSON)
    legacy["anchors"][0].pop("provenance")
    package = ReportDataPackage.model_validate(legacy)

    assert set(annotation_required_anchors(package)) == {"lkp-counter-height", "lkp-wardrobe-rod"}


def test_provenance_notes_cover_only_referenced_anchors() -> None:
    """标注跟着**引用**走，不是把包里所有未过门落点都堆到页脚——没提到的数不需要标。"""
    referenced = Card(
        thesis="操作台高度跟着主厨的身体走。",
        body="台面高按 {lkp-counter-height} 做。",
        number_refs=["lkp-counter-height"],
    )
    untouched = Card(
        thesis="主通道要留得开。",
        body="净宽按 {lkp-passage-main} 走。",
        number_refs=["lkp-passage-main"],
    )

    assert [n.lkp_id for n in required_provenance_notes([referenced], "ergonomics", PACKAGE)] == [
        "lkp-counter-height"
    ]
    assert required_provenance_notes([untouched], "ergonomics", PACKAGE) == []


def test_provenance_note_carries_source_and_calibration_verbatim() -> None:
    """标注是**投影**不是生成：来源与状态逐字取自包内，成文线不拼接、不补空、不改写。"""
    card = Card(
        thesis="操作台高度跟着主厨的身体走。",
        body="台面高按 {lkp-counter-height} 做。",
        number_refs=["lkp-counter-height"],
    )
    note = required_provenance_notes([card], "ergonomics", PACKAGE)[0]

    assert note.source == "行业通行"
    assert note.calibration == "draft"
    assert note.effective_from is None


def test_package_gate_rejects_unbacked_anchor_claiming_no_annotation() -> None:
    """生产方声称"不用标"但根本没过可核性门 → 整条标注链路会被一个字段悄悄关掉，故前置拦一次。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["provenance"]["annotationRequired"] = False
    violations = run_package_gate("ergonomics", ReportDataPackage.model_validate(tainted))

    assert checks_of(violations) == {"gate-provenance-inconsistent"}


def test_package_gate_rejects_calibration_drift_between_two_carriers() -> None:
    """平铺 calibration 与 provenance.calibration 同源，出现两个答案即生产方违约。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["provenance"]["calibration"] = "calibrated"
    violations = run_package_gate("ergonomics", ReportDataPackage.model_validate(tainted))

    assert checks_of(violations) == {"gate-provenance-inconsistent"}


def test_package_gate_accepts_expired_but_flagged_anchor() -> None:
    """过期的过门条目照常下发（规则 5.15，v2.4 推翻"只出占比"）——标了就合规，不再有隐藏档。"""
    expired = copy.deepcopy(PACKAGE_JSON)
    expired["anchors"][1]["provenance"]["effectiveTo"] = "2026-06-30"
    expired["anchors"][1]["provenance"]["annotationRequired"] = True
    package = ReportDataPackage.model_validate(expired)

    assert run_package_gate("ergonomics", package) == []
    assert set(annotation_required_anchors(package)) == {
        "lkp-counter-height",
        "lkp-passage-main",
        "lkp-wardrobe-rod",
    }
