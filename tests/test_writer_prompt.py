"""prompt 拼装：降档纪律进 system prompt（规则 4.10/5.8）。

prompt 是第一道不是门禁（判据下沉次序 schema > 规则 > prompt > 判官），但它必须与 gate 同口径——
两处口径不一致，写作器就会被反复打回却不知道该怎么改。
"""

from __future__ import annotations

import copy

from reportgen_worker.gate import (
    backed_predicates,
    collect_banned_terms,
    run_unit_gate,
    unbacked_predicates,
)
from reportgen_worker.models import Card, PersonaAsset
from reportgen_worker.writer import WriterRequest, build_messages, judgment_pairs
from tests.support import PACKAGE_JSON, load_package

PACKAGE = load_package()
DOMAIN = "ergonomics"


def request_for(domain: str = DOMAIN) -> WriterRequest:
    return WriterRequest(
        domain=domain,
        persona=PACKAGE.personas_by_domain[domain][0],
        anchors=PACKAGE.domain_anchors(domain),
        gaps=PACKAGE.gaps,
        profile=PACKAGE.anonymous_profile,
        banned_terms=collect_banned_terms(domain, PACKAGE),
        backed_predicates=backed_predicates(domain, PACKAGE),
        unbacked_predicates=unbacked_predicates(domain, PACKAGE),
    )


def test_anchor_lines_carry_presentation_tier() -> None:
    """落点逐条标档：未过门的标"建议口吻"。

    v2.4 起不再写"禁进主旨句"——那条门禁已随隐藏档一并作废，prompt 与 gate 仍需同口径。
    """
    user = build_messages(request_for())[1]["content"]
    assert "lkp-counter-height" in user
    assert "【未过门·建议口吻】" in user
    assert "lkp-passage-main（主通道净宽，mm）" in user
    assert "【可作支点】" in user


def test_prompt_warns_that_anchor_names_carry_banned_words() -> None:
    """落点名里就带着禁词（真跑立案：ergonomics 连吃三稿死在「净宽」上——
    而那正是 lkp-passage-main 的名字）。

    不改数据改说法：名字是内部标签，`照度`/`净宽` 这类工程词在落点名里是准确的，
    要求它们改成业主词面反而会把「照度」写成「亮度」——两个物理量。语域分界在**正文**，不在标签。
    """
    system = build_messages(request_for())[0]["content"]
    assert "落点的名字里可能就带着禁词" in system


def test_city_tier_stays_out_of_prompt() -> None:
    """城市档不下发进写作 prompt：档在求值线已经用掉（按它选单价区间，规则 5.15）。

    下发它没有任何写作用途，只会诱出"一线城市…"这类关于这家人的陈述——那正是
    ``cr-fabricated-fact`` 管的形态（可知家庭事实=画像，但画像字段不等于该被复述的话）。
    """
    messages = build_messages(request_for())
    assert PACKAGE.anonymous_profile.city_tier == "一线"
    assert "一线" not in messages[0]["content"] + messages[1]["content"]
    assert "cityTier" not in messages[1]["content"]


def test_unbacked_anchor_enters_prompt() -> None:
    """未过门的落点照常进 prompt（v2.4 取消隐藏档）——藏起来的建议对业主等于没有。

    它在 v2.3 是"连名字都不下发"的那一类；现在写作器看得见、可以用，只是语域限建议口吻，
    依据标注由系统挂在页上。
    """
    user = build_messages(request_for())[1]["content"]
    assert "lkp-wardrobe-rod" in user
    assert "【未过门·建议口吻】" in user


def test_assertion_budget_split_in_prompt() -> None:
    """判断句题目切成"这轮许说/不许说"两张清单，并要求声明 assertions。"""
    system, user = build_messages(request_for())
    assert "通道净宽是否够" in system["content"]
    assert "assertions" in system["content"]
    assert "台面高度" in user["content"]
    assert "挂杆高度" in user["content"]


def test_bare_identifier_ban_in_prompt() -> None:
    """裸 lkp- 标识名禁令进 prompt（真实缺陷：内部编号泄入客户正文）。"""
    system = build_messages(request_for())[0]["content"]
    assert "内部编号" in system


def test_judgment_samples_enter_prompt_as_contrast_pairs() -> None:
    """persona 四件之②接上（规则 4.13）：好/坏对照句对进 system prompt，成对下发。"""
    system = build_messages(request_for())[0]["content"]
    assert "✗ 根据人体工程学原理，台面高度宜设定为标准值。" in system
    assert "✓ 台面做到这个高度，你切菜时手腕是平的，不用弓腰。" in system


def test_judgment_sample_reason_is_not_shown_to_writer() -> None:
    """reason 是 cr- 判据编号：业主语域没有编号，写作器也不需要认识判据——只有判官层用它。"""
    assert all("cr-" not in m["content"] for m in build_messages(request_for()))


def test_malformed_judgment_samples_are_skipped() -> None:
    """形态不合的条目静默跳过：损坏条目归核验跑批，运行时不兜底也不因此拒绝成文。"""
    persona = PACKAGE.personas_by_domain[DOMAIN][0]
    assert judgment_pairs(persona, collect_banned_terms(DOMAIN, PACKAGE)) == [
        (
            "根据人体工程学原理，台面高度宜设定为标准值。",
            "台面做到这个高度，你切菜时手腕是平的，不用弓腰。",
        )
    ]
    system = build_messages(request_for())[0]["content"]
    assert "主通道净宽建议不小于标准值。" not in system  # 只有反例没有正例，不成对即不下发
    assert "整条不是对象" not in system


def test_sample_that_fails_the_gate_itself_is_not_shown() -> None:
    """两句都要自己过得了机检：带数字的示范＝逐字示范失败模式（真跑三轮实测模型照抄）。"""
    system = build_messages(request_for())[0]["content"]
    assert "挂杆按你的身高定在 1200 这个高度" not in system  # ✓ 带数字
    assert "衣柜挂杆高度按身高折算" not in system  # 整对丢弃，不留半边
    assert "餐厅吊灯下沿距桌面700-800mm。" not in system  # ✗ 带数字，模型照抄反例
    assert "那盏灯吊到坐下来抬头不刺眼" not in system


def test_sample_carrying_a_banned_term_is_not_shown() -> None:
    """禁词是零容忍扫描：反例里演示禁词换不来等价示范价值，把词摆进上下文只会复发。"""
    system = build_messages(request_for())[0]["content"]
    assert "综合考量" not in system.split("这个域里")[-1]
    assert "台面按主厨的身体定，不取平均值。" not in system


def test_sample_filter_uses_the_same_predicates_as_the_gate() -> None:
    """同口径是硬要求：prompt 与门禁两处口径不一致，写作器会被反复打回却不知道该怎么改。"""
    persona = PACKAGE.personas_by_domain[DOMAIN][0]
    banned = collect_banned_terms(DOMAIN, PACKAGE)
    for _, good in judgment_pairs(persona, banned):
        card = Card(thesis=good, body=good)
        # 两条判据除外，它们判的都是**卡片**不是示范句：照抄（示范当然逐字等于它自己）、
        # 正文与主旨句重复（同一句同时当 thesis 与 body 是这里的测试构造，不是被测对象）
        structural = {"gate-sample-verbatim-copy", "gate-thesis-body-duplicate"}
        assert [
            v for v in run_unit_gate([card], DOMAIN, PACKAGE) if v.check not in structural
        ] == []


def test_prompt_without_judgment_samples_does_not_break() -> None:
    """缺样例（多数域的现状）不崩、不留空标题——示范块整块缺席。"""
    system = build_messages(request_for("lighting"))[0]["content"]
    assert "这么写不行" not in system
    assert "禁词" in system


def test_empty_string_pair_is_skipped() -> None:
    """空串与非字符串同属形态不合——空示范比没有示范更坏（教模型写空话）。"""
    raw = copy.deepcopy(PACKAGE_JSON["personasByDomain"][DOMAIN][0])
    raw["judgmentSamples"] = [{"bad": "   ", "good": "台面做到这个高度。", "reason": "cr-x"}]
    assert judgment_pairs(PersonaAsset.model_validate(raw)) == []


def test_locked_texts_never_reach_the_writer() -> None:
    """gen-locked 零生成（规则 2.4）：锁定文案 ID 一个字都不进写作 prompt——挂载是装配层的事。"""
    assert all("DISCLAIM" not in m["content"] for m in build_messages(request_for("lighting")))


def test_prompt_hands_annotation_duty_to_the_system() -> None:
    """标注由系统挂在页上（规则 4.10c，v2.4）——写作器不写来源与日期，写了必被打回。

    这不是礼貌提示：标注里全是日期，正文禁裸数字，写作器**结构性地**产不出合规的标注，
    让它试等于白烧一轮重写。
    """
    system = build_messages(request_for())[0]["content"]
    assert "由系统自动挂在这一页上" in system
