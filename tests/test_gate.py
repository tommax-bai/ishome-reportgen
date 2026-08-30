"""出口过检·规则层：数字纪律、占位解析、禁词、cr- 判据物化执行、降档门禁（规则 4.10/5.8）。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

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


def package_with_anchor(
    value_kind: str, value: Any, lkp_id: str = "lkp-probe"
) -> ReportDataPackage:
    """在 ergonomics 域塞一条指定 valueKind 的落点——七类值构成各自的引用合法性都要能单测到。

    值构成是**契约字段不是可推断项**，故夹具里逐类写死（同生产方的下发形态），不由代码猜。
    """
    raw = copy.deepcopy(PACKAGE_JSON)
    raw["anchors"].append(
        {
            "lkpId": lkp_id,
            "name": "测试落点",
            "numberClass": "analysis",
            "unit": "mm",
            "valueKind": value_kind,
            "value": value,
            "basisTag": "ergonomics@v1",
            "source": "行业通行",
            "calibration": "calibrated",
            "degraded": False,
            "provenance": {
                "source": "行业通行",
                "effectiveFrom": None,
                "effectiveTo": None,
                "calibration": "calibrated",
                "annotationRequired": False,
            },
            "presentation": "THESIS_SUPPORT",
        }
    )
    return ReportDataPackage.model_validate(raw)


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


def test_declared_but_unused_ref_rejected() -> None:
    """refs 声明了没用＝违约的另一半（真跑立案：假坦白卡声明五个有值落点、正文零占位符）。"""
    card = Card(
        thesis="这几项目前给不出可靠数值。",
        body="这些都还没确定，只能留待现场确认。",
        number_refs=["lkp-counter-height"],
    )
    assert "gate-number-ref-unused" in checks_of(run_unit_gate([card], "ergonomics", PACKAGE))


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

    v2.8 起打回理由不再说"一个占位符＝整条落点"——那条实现层契约的理由只对区间成立，
    已由两层模型承接：区间落点**只有一个匿名项**，故整条引用即可；min/max 是值形态不是项。
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
    assert "只有一个值" in violations[0].detail
    assert "丢掉另一端" in violations[0].detail
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


# ---------------------------------------------------------------------------
# 两层模型（规则 1.9，v2.8）：一条落点＝若干项，一项的值＝一个数或一个区间
# ---------------------------------------------------------------------------

MULTI_ITEM_ANCHORS = [
    ("scenario", {"general": 100, "reading": 300}, "reading"),
    ("tier", {"low": 8, "medium": 12, "high": 18}, "medium"),
    ("dimension", {"depth": 600, "width": 900, "height": 2000}, "depth"),
    (
        "component",
        {
            "main-material": {"min": 0.2, "max": 0.35},
            "demolition": {"min": 0.05, "max": 0.1},
        },
        "main-material",
    ),
    ("comparison", {"high-vs-medium": {"min": 1.4, "max": 1.8}}, "high-vs-medium"),
]


@pytest.mark.parametrize(("kind", "value", "item"), MULTI_ITEM_ANCHORS)
def test_single_item_reference_passes_for_every_multi_item_kind(
    kind: str, value: Any, item: str
) -> None:
    """五类分项落点都能引用**其中一项**——本轮裁决要解决的那件事。

    立案证据：灯光域同包同码同参六轮 0/6 过检，六轮全部 27 种越界占位符 27/27 逐字等于
    「真实落点 id」＋「该落点 value 里一个真实的键」——模型不是不守规矩，是想说的那句话
    （"沙发旁读书那块要单独加亮"）没有合法写法。
    """
    package = package_with_anchor(kind, value)
    card = Card(
        thesis="这一块单独说。",
        body=f"这块按 {{lkp-probe.{item}}} 来，别跟着大面积一起走。",
        number_refs=[f"lkp-probe.{item}"],
    )

    assert run_unit_gate([card], "ergonomics", package) == []


def test_whole_anchor_reference_still_passes_for_multi_item_anchor() -> None:
    """整条引用**没有被取消**：裁决加的是"可以引其中一项"，不是"必须逐项引"。

    这一步不替裁决收窄——分项落点整条渲染成什么样是渲染层的题目，不是引用合法性的题目。
    """
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="起居室的亮度分层来看。",
        body="这一片整体按 {lkp-probe} 走。",
        number_refs=["lkp-probe"],
    )

    assert run_unit_gate([card], "ergonomics", package) == []


def test_unknown_item_rejected_with_the_real_items_listed_verbatim() -> None:
    """项不存在即违规，且打回提示**逐字列出这条落点有哪几项**。

    这是本轮最要紧的一条：旧提示连吃三稿的原因不是模型不听话，是它没被告知有哪些合法选择。
    """
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="书桌那块要单独加亮。",
        body="书桌上按 {lkp-probe.task} 做。",
        number_refs=["lkp-probe.task"],
    )

    violations = [
        v
        for v in run_unit_gate([card], "ergonomics", package)
        if v.check == "gate-number-ref-unresolved"
    ]

    assert len(violations) == 1
    assert "没有「task」这一项" in violations[0].detail
    assert "{lkp-probe.general}" in violations[0].detail  # 合法写法逐字摆出来
    assert "{lkp-probe.reading}" in violations[0].detail


@pytest.mark.parametrize(
    "ref",
    ["lkp-counter-height.min", "lkp-counter-height.max", "lkp-wardrobe-rod.v"],
)
def test_anonymous_item_kinds_reject_any_item_name(ref: str) -> None:
    """single/range 只有一个匿名项，带项名即违规——``{lkp-x.min}`` 在语义上不成立。

    min/max 是项的**值形态**不是项：引一端丢掉另一端由**结构**堵死（写得出但一定不合法），
    不再靠打回提示劝住。``.v`` 同理——单值的 ``{"v": …}`` 外壳 v2.8 已经退场。
    """
    card = Card(thesis="这个高度这么定。", body=f"按 {{{ref}}} 做。", number_refs=[ref])

    violations = [
        v
        for v in run_unit_gate([card], "ergonomics", PACKAGE)
        if v.check == "gate-number-ref-unresolved"
    ]

    assert len(violations) == 1
    assert "只有一个值，没有项可指" in violations[0].detail
    if ref.endswith((".min", ".max")):
        # 点破这一支，否则打回会被读成"项名写错了"，下一稿换个项名再来一遍
        assert "min/max 是这条落点值的两端，不是项" in violations[0].detail


def test_number_refs_are_declared_at_token_granularity() -> None:
    """refs 的粒度＝记号本身逐字（"落点.项"），不是落点——两个集合仍逐字相等。"""
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="这一片分开两种用法看。",
        body="平时按 {lkp-probe.general}，沙发旁读书那块按 {lkp-probe.reading}。",
        number_refs=["lkp-probe.general", "lkp-probe.reading"],
    )

    assert run_unit_gate([card], "ergonomics", package) == []


def test_declaring_the_anchor_while_writing_an_item_is_a_granularity_violation() -> None:
    """粒度对不上要说粒度的话——不能配上"有值的落点不许说给不出"那句（那句在这一支是错的）。"""
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="沙发旁那块单独加亮。",
        body="读书那块按 {lkp-probe.reading} 来。",
        number_refs=["lkp-probe"],
    )

    violations = run_unit_gate([card], "ergonomics", package)
    unused = [v for v in violations if v.check == "gate-number-ref-unused"]

    assert "gate-number-ref-undeclared" in checks_of(violations)
    assert len(unused) == 1
    assert "粒度对不上" in unused[0].detail
    assert "禁止的隐藏" not in unused[0].detail


def test_declaring_one_item_and_writing_another_is_still_fake_confession() -> None:
    """假坦白封堵在项这一层同样成立：声明了阅读那一项却只写了一般照度，那一项被藏起来了。"""
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="这一片按平时的用法定。",
        body="整片按 {lkp-probe.general} 走，读书那档这轮给不出。",
        number_refs=["lkp-probe.general", "lkp-probe.reading"],
    )

    unused = [
        v
        for v in run_unit_gate([card], "ergonomics", package)
        if v.check == "gate-number-ref-unused"
    ]

    assert len(unused) == 1
    assert "lkp-probe.reading" in unused[0].detail
    assert "禁止的隐藏" in unused[0].detail


def test_item_written_with_a_hyphen_gets_the_dot_form_in_the_hint() -> None:
    """把项名用连字符拼进落点 id（{lkp-x-reading}）＝ 区间拆两端那条真跑形态的同族。

    打回提示从**本域真实落点**算出它想写的是哪一个，不是套模板。
    """
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="读书那块单独加亮。",
        body="按 {lkp-probe-reading} 做。",
        number_refs=["lkp-probe-reading"],
    )

    violations = [
        v
        for v in run_unit_gate([card], "ergonomics", package)
        if v.check == "gate-number-ref-unresolved"
    ]

    assert "{lkp-probe.reading}" in violations[0].detail


def test_item_name_leaking_into_the_body_is_rejected() -> None:
    """项名不进业主视野（规则 1.9 三明文）：记号里可以有，正文里不行——业主不认识 general。"""
    package = package_with_anchor("scenario", {"general": 100, "reading": 300})
    card = Card(
        thesis="这一片分两种用法。",
        body="general 那一档按 {lkp-probe.general} 走。",
        number_refs=["lkp-probe.general"],
    )

    violations = [
        v for v in run_unit_gate([card], "ergonomics", package) if v.check == "gate-item-name-leak"
    ]

    assert len(violations) == 1
    assert "「general」" in violations[0].detail


def test_item_name_leak_check_does_not_over_block() -> None:
    """过拦与漏拦同样是失效：记号内部的项名不算泄漏，`low-E` 这类正当写法也不算。"""
    package = package_with_anchor("tier", {"low": 8, "medium": 12, "high": 18})
    card = Card(
        thesis="玻璃与档位这一段。",
        body="窗上用 low-E 玻璃，柜子按 {lkp-probe.medium} 这一档配。",
        number_refs=["lkp-probe.medium"],
    )

    assert "gate-item-name-leak" not in checks_of(run_unit_gate([card], "ergonomics", package))


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("single", {"v": 2136}),  # 单值套壳 v2.8 退场：标量就是标量
        ("range", {"min": 900, "max": 950, "typical": 920}),  # 区间只有 min/max 两个边界
        ("range", 900),
        ("scenario", 100),  # 分项落点的值必须是 项名→值
        ("scenario", {}),
    ],
)
def test_package_gate_rejects_value_shape_that_contradicts_value_kind(
    kind: str, value: Any
) -> None:
    """valueKind 是**判定**不是描述：数据里两者打架，模型会照着一份错清单写再被打回。

    与既有 provenance 那两条同路——生产侧违约在最前面拦一次，不烧一次 LLM 调用。
    """
    package = package_with_anchor(kind, value)

    assert "gate-anchor-value-shape" in checks_of(run_package_gate("ergonomics", package))


def test_package_gate_rejects_min_max_as_an_item() -> None:
    """把 min/max 当成项＝把 ``{lkp-x.min}`` 变回合法写法——本裁决"由结构堵死"的那条缝在这儿。"""
    package = package_with_anchor("scenario", {"min": 100, "reading": 300})
    violations = [
        v for v in run_package_gate("ergonomics", package) if v.check == "gate-anchor-value-shape"
    ]

    assert len(violations) == 1
    assert "值形态" in violations[0].detail


def test_package_gate_rejects_item_names_outside_the_namespace() -> None:
    """项名与落点标识同一套（ASCII 小写 kebab-case，规则 1.9 三）：形态不合的项引用不到。

    只守形态不守词表：取值落在 tier 闭集还是 scenario 词表由资产回路的核验拒灌——
    词表是开集且不随包下发，在消费侧照抄一份等于把真源劈成两处。
    """
    package = package_with_anchor("component", {"主材": {"min": 0.2, "max": 0.35}})
    violations = [
        v
        for v in run_package_gate("ergonomics", package)
        if v.check == "gate-anchor-item-name-invalid"
    ]

    assert len(violations) == 1
    assert "主材" in violations[0].detail


@pytest.mark.parametrize(("kind", "value", "item"), MULTI_ITEM_ANCHORS)
def test_package_gate_accepts_every_well_formed_value_kind(
    kind: str, value: Any, item: str
) -> None:
    """七类里的五类分项形态照收（另两类＝夹具里的 range 与 single）——门禁只拦形态不符的。"""
    assert run_package_gate("ergonomics", package_with_anchor(kind, value)) == []
