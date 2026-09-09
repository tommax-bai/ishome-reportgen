"""叙事推导（图 v0.2 §3 第一步）：只定"讲哪几件事"，看不见落点的值，产不出数字。"""

from __future__ import annotations

import copy
import json

import pytest

from reportgen_worker.deriver import (
    COST_CLAIM_TEXT,
    DeriveRequest,
    DeriverOutputError,
    build_derive_messages,
    parse_claims,
)
from reportgen_worker.gate import backed_predicates, collect_banned_terms, unbacked_predicates
from reportgen_worker.models import (
    AnchorBrief,
    GapRecord,
    NarrativeClaim,
    TriggeredRule,
    TriggerEvidence,
)
from tests.fixtures import TIERS, Tier
from tests.fixtures import load_package as load_tier
from tests.support import load_package

PACKAGE = load_package()
DOMAIN = "ergonomics"


def request_for(domain: str = DOMAIN) -> DeriveRequest:
    return DeriveRequest(
        domain=domain,
        identity=PACKAGE.personas_by_domain[domain][0].identity,
        anchors=[AnchorBrief.of(a) for a in PACKAGE.domain_anchors(domain)],
        gaps=PACKAGE.gaps,
        profile=PACKAGE.anonymous_profile,
        banned_terms=collect_banned_terms(domain, PACKAGE),
        backed_predicates=backed_predicates(domain, PACKAGE),
        unbacked_predicates=unbacked_predicates(domain, PACKAGE),
    )


def test_derive_prompt_shows_names_without_values() -> None:
    """推导步**结构性**看不见数字：给的是落点题名，值在下一步。

    不是"叮嘱它别写数字"——不给值，它产不出数字（图 v0.2 §3"不产生任何数字"）。
    """
    user = build_derive_messages(request_for())[1]["content"]

    assert "lkp-counter-height（橱柜台面高，mm）" in user
    assert "900" not in user  # 落点的值一个都不能出现
    assert "950" not in user
    assert "2136" not in user


def test_derive_prompt_carries_stance_and_budget_but_not_samples() -> None:
    """给身份与断言预算题目，不给判断句示范——示范的可抄性已实测（writer.judgment_pairs）。"""
    system = build_derive_messages(request_for())[0]["content"]

    assert PACKAGE.personas_by_domain[DOMAIN][0].identity in system
    assert "通道净宽" in system  # 断言预算题目：许说的与不许说的都要让它知道
    assert "台面按主厨的身体定，不取平均值。" not in system  # persona 的 ✓ 示范句
    assert "不许出现任何数字" in system


def test_derive_prompt_carries_banned_terms() -> None:
    """禁词要往上游多走一层（规则 4.15 双消费的第三个消费点，真跑立案）。

    主张逐字进写作 prompt：主张里带一个禁词，写作器跟着写进卡片被打回，而下一稿拿到的主张还是那句——
    真跑实测整单元连吃三稿死在同一个词（`净宽`）上。
    """
    system = build_derive_messages(request_for())[0]["content"]
    assert "人体工学" in system
    assert "综合考量" in system  # 公共禁词与域内禁词都要给


def test_anchor_lines_flag_banned_terms_inline() -> None:
    """名字撞禁词的落点逐行点名（数据驱动）：全局提醒实测压不过逐行复现（「净宽」连吃三稿）。"""
    tainted = copy.deepcopy(request_for())
    tainted.banned_terms.append("净宽")
    tainted.anchors.append(AnchorBrief(lkp_id="lkp-passage-main", name="主通道净宽", unit="mm"))
    user = build_derive_messages(tainted)[1]["content"]
    assert "lkp-passage-main（主通道净宽，mm）（名字里的 「净宽」 是内部词，勿写进主张）" in user
    # 没撞词的行不加尾巴
    assert "lkp-counter-height（橱柜台面高，mm）\n" in user + "\n"


def test_derive_prompt_bans_invented_coupling() -> None:
    """归组依据＝同属一件事，落点间因果/耦合不得编造（用户裁决 2026-08-29 晚，规范 v2.5 §14.10）。

    真跑立案：5/5 主张全带"得一起定/配着调/互相让"式耦合胶水——部分是旧指令
    "每条主张要有取舍或因果"自己逼出来的。关系与数字同族，都不由 LLM 决定。
    """
    system = build_derive_messages(request_for())[0]["content"]
    assert "不许把同组落点写成相互约束的关系" in system
    assert "就该进同一条主张" in system  # 禁耦合不禁归组——上一版措辞把归组也劝退了（22 主张那轮）
    # 词面不进 prompt：「得一起定」作为反例写进指令的那轮，4/5 主张逐字照抄了禁句本身
    assert "得一起定" not in system
    assert "取舍或因果" not in system  # 逼出耦合的旧指令退场


def test_parse_rejects_coupling_phrases_deterministically() -> None:
    """耦合词面走确定性校验不走 prompt（照抄病）：命中即打回，理由进反馈循环。"""
    raw = json.dumps(
        [{"claim": "床面高度和床侧净距得一起定，这样上下床都顺", "anchors": []}],
        ensure_ascii=False,
    )
    with pytest.raises(DeriverOutputError, match="相互约束措辞"):
        parse_claims(raw, set())
    # 并列形态照收
    ok = json.dumps(
        [{"claim": "床太高起身费力，床边太窄下床碰腿——都按你的身体来定", "anchors": []}],
        ensure_ascii=False,
    )
    assert len(parse_claims(ok, set())) == 1


def test_unbacked_topic_must_be_confessed_in_derivation() -> None:
    """坦白主张（裁决 B）在推导步同款：不许描述性分析、不许发明因果去填。"""
    system = build_derive_messages(request_for())[0]["content"]
    assert "坦白主张" in system
    assert "不许发明因果去填" in system


def test_gaps_can_become_a_claim() -> None:
    """ "这件事现在还算不出来"本身可以是一条主张（规则 4.18：宁可说没有，不许硬写）。"""
    user = build_derive_messages(request_for())[1]["content"]
    assert "lkp-tv-distance" in user


def test_parse_drops_invented_anchor_ids_but_keeps_the_claim() -> None:
    """推导步自造的落点 id 剔掉、主张留下：anchors 是给写作器的建议，引用校验在写作那一步。"""
    raw = json.dumps(
        [{"claim": "台面高该按主厨的身体定", "anchors": ["lkp-counter-height", "lkp-made-up"]}],
        ensure_ascii=False,
    )
    claims = parse_claims(raw, {"lkp-counter-height"})

    assert len(claims) == 1
    assert claims[0].anchors == ["lkp-counter-height"]


def test_parse_rejects_empty_derivation() -> None:
    """一域有落点却推导不出一件事 = 这一步没工作，响亮失败——静默放行会退回"一数一卡"的老形态。"""
    with pytest.raises(DeriverOutputError):
        parse_claims("[]", {"lkp-counter-height"})
    with pytest.raises(DeriverOutputError):
        parse_claims('[{"claim": "  ", "anchors": []}]', {"lkp-counter-height"})


def test_parse_rejects_garbage() -> None:
    with pytest.raises(DeriverOutputError):
        parse_claims("模型讲了一段废话", set())
    with pytest.raises(DeriverOutputError):
        parse_claims('[{"claim": 3}]', set())


# ---------------------------------------------------------------------------
# 户型触发条目（2026-08-30：户型特征进报告，那批 layout_feature 规则第一次有执行器）
# ---------------------------------------------------------------------------


def _triggered_rule(
    *, content: str, rationale: str | None, feature: str | None, evidence: str | None
) -> TriggeredRule:
    return TriggeredRule(
        # 字段名用 snake_case：包侧模型 populate_by_name=True 两种都收，但 camelCase 过不了
        # mypy（别名生成器对静态检查不可见）——上一提交把 mypy 门带红了，这里一并订正
        asset_id="rule-practice-storage-balcony-cleaning",
        layer="tier-practice",
        content=content,
        rationale=rationale,
        severity="recommended",
        calibration="draft",
        triggered_by=TriggerEvidence(
            type="layout_feature" if feature else "always", feature=feature, evidence=evidence
        ),
    )


BALCONY_RULE = _triggered_rule(
    content="阳台留清洁工具位（含插座）",
    rationale="吸尘器和拖把要有固定的家，还要能充电",
    feature="balcony_service",
    evidence="阳台内有洗衣机设备位",
)


def test_triggered_rules_reach_derivation_with_their_evidence() -> None:
    """触发条目落在**推导步**不落写作步：它回答"这一章该讲什么"，而那正是推导步的题目。

    依据（"因为这户：…"）必须同行下发——规则 4.3 可追溯性的户型侧对应物，
    报告里"因为你家阳台带家政位"这句话的数据就是它。
    """
    request = request_for().model_copy(update={"triggered_rules": [BALCONY_RULE]})

    system, user = (m["content"] for m in build_derive_messages(request))

    assert "阳台留清洁工具位（含插座）" in user
    assert "因为这户：阳台内有洗衣机设备位" in user
    assert "必须讲到" in system  # 条目是要讲到的点，不是可选素材


def test_always_rule_line_carries_no_empty_evidence() -> None:
    """``always`` 条目不写依据括号：写"因为：无"会诱出"根据通用规范"这类无依据背书。"""
    always_rule = _triggered_rule(
        content="玄关设快递拆包位（台面或翻板）",
        rationale="拆包在门口完成，纸箱不进屋",
        feature=None,
        evidence=None,
    )

    user = build_derive_messages(
        request_for().model_copy(update={"triggered_rules": [always_rule]})
    )[1]["content"]

    assert "玄关设快递拆包位（台面或翻板）" in user
    assert "因为这户" not in user


def test_layout_features_go_in_as_evidence_not_as_marker_names() -> None:
    """户型特征**只下发依据文字**：标记名是内部标识符，主张逐字进写作 prompt，混进去就上卡片。"""
    profile = PACKAGE.anonymous_profile.model_copy(
        update={"layout_features": {"balcony_service": "阳台内有洗衣机设备位"}}
    )

    user = build_derive_messages(request_for().model_copy(update={"profile": profile}))[1][
        "content"
    ]

    assert "阳台内有洗衣机设备位" in user
    assert "balcony_service" not in user  # 内部标记名一个字都不下发


def test_parse_rejects_verbatim_copy_of_triggered_rule() -> None:
    """条目逐字照抄判在推导步：主张逐字进写作 prompt，抄进去就会被写成卡片（示范句同病）。

    prompt 里叮嘱无效已实测三次（禁词、耦合词面、示范句），故这一道是**确定性校验**。
    """
    raw = json.dumps(
        [{"claim": "阳台留清洁工具位（含插座）", "anchors": []}],
        ensure_ascii=False,
    )

    with pytest.raises(DeriverOutputError, match="逐字照抄"):
        parse_claims(raw, set(), (), (BALCONY_RULE,))


def test_paraphrased_claim_passes() -> None:
    """换成人话就放行——判的是照抄不是"讲这件事"。"""
    raw = json.dumps(
        [{"claim": "你家阳台带着家政位，扫地机和拖把该在那儿有个能充电的固定角落", "anchors": []}],
        ensure_ascii=False,
    )

    assert parse_claims(raw, set(), (), (BALCONY_RULE,))[0].claim.startswith("你家阳台")


# ---------------------------------------------------------------------------
# 两层模型（规则 1.9，v2.8）：项名进推导入参，值仍然不进
# ---------------------------------------------------------------------------


def test_derive_prompt_shows_item_names_but_still_no_values() -> None:
    """推导要知道一条落点**分了几项**，因为"分场景讲"正是这一步该决定的事。

    用户裁决原话："我觉得是需要的，因为卧室的灯光和客厅的灯光肯定会不一样"。
    项名与名字/量纲同类（标签不是数），故"看不见值"这条不破——拿着 general/reading
    依然产不出任何一个数字。
    """
    user = build_derive_messages(request_for("lighting"))[1]["content"]

    assert "（分 2 项：general、reading——项名是内部记号，主张里说人话）" in user
    assert "100" not in user  # 值一个都不能出现
    assert "300" not in user


def test_derive_prompt_leaves_the_split_decision_to_the_derivation() -> None:
    """拆不拆由推导定：对这家人真是两回事就分两条主张，是一回事就一条带着。"""
    system = build_derive_messages(request_for("lighting"))[0]["content"]

    assert "这几项是分开讲还是合起来讲**由你定**" in system


def test_single_valued_anchor_line_has_no_item_note() -> None:
    """只有一个匿名项的落点不加分项尾巴——没有项可拆，多一句只会诱它去拆。"""
    user = build_derive_messages(request_for())[1]["content"]

    assert "lkp-counter-height（橱柜台面高，mm）\n" in user + "\n"


def test_parse_rejects_item_names_copied_into_claims() -> None:
    """项名逐字进主张＝内部记号会跟着进写作 prompt、再进卡片（同禁词/条目照抄那条路径）。

    prompt 里叮嘱压不住已实测三次，故这一道也是**确定性校验**。
    """
    raw = json.dumps(
        [{"claim": "起居室的 general 照明和 reading 那档要分开定", "anchors": []}],
        ensure_ascii=False,
    )

    with pytest.raises(DeriverOutputError, match="分项记号"):
        parse_claims(raw, set(), (), (), ("general", "reading"))


def test_claim_saying_the_scene_in_plain_words_passes() -> None:
    """判的是照抄记号不是"讲这一项"：换成人话照放。"""
    raw = json.dumps(
        [{"claim": "客厅平时待着和沙发旁读书是两回事，亮度得分开定", "anchors": []}],
        ensure_ascii=False,
    )

    assert parse_claims(raw, set(), (), (), ("general", "reading"))


def test_always_rules_are_downweighted_against_this_household() -> None:
    """两档权重不同：户型条目必讲，通行做法可讲。

    真库实测 always 类 7 条且分布不均（照明 3／用材 2／造价 1／收纳 1）——一律"必须讲到"
    等于给收敛最差的那一章再压三个通用话题，而"通用专业建议"正是获客线要摆脱的东西。
    """
    always_rule = _triggered_rule(
        content="全屋色温种类不超过三种",
        rationale="色温杂了整屋就不像一个作品",
        feature=None,
        evidence=None,
    )
    request = request_for().model_copy(update={"triggered_rules": [BALCONY_RULE, always_rule]})

    system, user = (m["content"] for m in build_derive_messages(request))

    assert "这套户型触发的条目（**必须讲到**" in user
    assert "通行做法条目（**可以讲到**" in user
    assert "别为它挤掉这一户的事" in system
    # 分组正确：户型条目带依据、通行条目不带
    household_block = user.split("通行做法条目")[0]
    assert "阳台留清洁工具位（含插座）" in household_block
    assert "全屋色温种类不超过三种" not in household_block


def test_derive_prompt_does_not_itself_write_the_words_it_bans() -> None:
    """**我们自己写的那部分 prompt，一个禁词都不许出现**（2026-08-30 立案，与写作步同守卫）。

    推导的产物逐字进写作 prompt（坑单三），所以这一步的自相矛盾会一路传到正文：
    原文四处写「依据」、一处写「可能」，而两者都在公共禁词表里。

    禁词表本身**必然**含词面（要告诉它禁哪些），从检查范围里剔除，且只剔这一处。
    """
    banned = ["照度", "显指", "可能", "也许", "依据", "推导", "保证", "宜", "责任", "本方案"]
    request = copy.deepcopy(request_for())
    request.banned_terms = banned
    system = build_derive_messages(request)[0]["content"]
    system = system.replace("、".join(banned), "")
    leaked = [t for t in banned if t in system]
    assert not leaked, f"推导 prompt 自己写了禁词：{leaked}"


def test_parse_claims_rejects_numbers_in_claims() -> None:
    """第五道确定性校验：主张里不许有数（推导步纪律第 2 条，此前只在 prompt 里叮嘱）。

    真跑立案（2026-08-30 晚）：主张写"暖冷调子加起来不能超过三种"，逐字进写作 prompt，
    写作步照抄后被 gate-chinese-numeral 打回，而重写两轮拿到的主张还是那句——
    "连吃三稿"的老形态换了条判据重演。判在这一步，写作步才有一份不带数的骨架。
    """
    with pytest.raises(DeriverOutputError, match="主张里写了数"):
        parse_claims('[{"claim": "你家所有灯的调子加起来不能超过三种。", "anchors": []}]', set())
    with pytest.raises(DeriverOutputError, match="主张里写了数"):
        parse_claims('[{"claim": "台面高度按 900 定。", "anchors": []}]', set())
    # 列举计数不在射程（与写作步同一份口径）：数东西不是报数值
    claims = parse_claims('[{"claim": "你家这四个区域的光要分开想。", "anchors": []}]', set())
    assert len(claims) == 1


def test_derive_rewrite_carries_previous_claims_and_earlier_reasons() -> None:
    """推导步同写作步：带回上一稿 + 更早各轮只带理由 + 重复犯的标出来（射程＝所有裁判场）。

    真跑立案（2026-08-30 晚 w2）：「一起定」连吃三轮，整单元死在推导步——此前这一步只递一句
    错误文字、稿子本身不回传，与写作步早先修掉的是同一个毛病，只是漏在了这一步。
    """
    request = copy.deepcopy(request_for())
    request.previous_claims = [NarrativeClaim(claim="床面高和床侧净距得一起定。", anchors=[])]
    request.earlier_feedback = [["主张里写了落点间的相互约束措辞 ['一起定']"]]
    request.feedback = ["主张里写了落点间的相互约束措辞 ['一起定']"]
    user = build_derive_messages(request)[1]["content"]
    assert "更早几稿也被打回过" in user
    assert "床面高和床侧净距得一起定。" in user
    assert "前面几稿也栽在这条" in user


def test_parse_claims_error_carries_the_rejected_draft() -> None:
    """打回带原文：这一步的"原文"就是它刚写出来的那组主张，挂在异常上传回重试。"""
    with pytest.raises(DeriverOutputError) as excinfo:
        parse_claims('[{"claim": "床面高和床侧净距得一起定。", "anchors": []}]', set())
    assert [c.claim for c in excinfo.value.claims] == ["床面高和床侧净距得一起定。"]


def test_banned_pushback_in_derivation_routes_by_group_like_the_writing_step() -> None:
    """推导步的禁词打回也按"为什么禁"分路——两步同形（射程＝所有裁判场）。

    真跑立案（2026-08-31，第一次六章整册）：budget 推导三次全失败、全栽在「报价」，三次拿到的
    都是同一句**定死的**「换人话重写」——写作步同名判据一直查 ``banned_route_of``，这一步没查。
    没有"为什么不行"，模型只能换个说法再撞一次；那一章因此把整册拖成 failed。
    """
    groups = {"deal": ["报价"], "weak": ["宜"]}
    with pytest.raises(DeriverOutputError) as excinfo:
        parse_claims(
            '[{"claim": "这一章不替谁报价。", "anchors": []}]',
            set(),
            ["报价"],
            banned_groups=groups,
        )
    detail = str(excinfo.value)
    assert "成交那一步不归你说" in detail, "打回没带上这个词为什么不行"
    assert "换人话重写" not in detail, "退回了定死的那一句"
    # 查不到组仍给得出一句话——加组不破消费，同写作步的兜底
    with pytest.raises(DeriverOutputError, match="换人话说"):
        parse_claims(
            '[{"claim": "这一章不替谁报价。", "anchors": []}]', set(), ["报价"], banned_groups={}
        )


def test_gap_block_forbids_writing_about_it_at_all() -> None:
    """缺口是**我们这边还没算出来的**，不是业主该告诉我们的——两步 prompt 都要说清。

    真跑立案（2026-08-31 成册的那一本）：四章都把缺口写成了"等你确认沙发落位""等你提供物品清单"，
    **把我们自己没做完的活说成业主没交作业**。往上追两层：写作 prompt 原文就写着"可坦白留待现场
    确认"；而求值线的缺口 reason 只有 missing_input / formula_not_implemented / empty_definition
    三种，**没有一种的意思是"等客户告诉我们"**。地毯那条的规则还是我们自己写的（沙发前沿外扩
    [200,300]mm），缺的沙发落位来自定稿平面——那也是我们下一步自己产的。

    用户裁决 2026-08-31（两句，第二句否掉了第一版改法）：
    ①「地毯尺寸和收纳走长应该是我们给出的建议，对吗？而不应该是客户给出来的。客户怎么知道地毯
      应该用多长的呢？这应该是我们告诉他的，而不是他告诉我们的。」
    ②「不要在报告里写我们后面算给他，这报告都给客户了，我们后面怎么算给他？」「我们不应该有后面
      算给他这个概念。」

    所以现口径不是"把活认回来"，是**没有值就不写**：报告是一次性交付物，交付那一刻要么有这个数，
    要么这件事根本不进报告（规则 4.18 宁薄勿撑）。缺口只作为"别编它"的禁令下发。
    """
    request = copy.deepcopy(request_for())
    request.gaps = [
        GapRecord(
            lkp_id="lkp-rug-size-rule", basis_tag="softdeco@v9", reason="formula_not_implemented"
        )
    ]
    user = build_derive_messages(request)[1]["content"]
    assert "没有值" in user and "不许为它单独写一张卡" in user, "缺口不该变成可写的题材"
    assert "等你确认" in user and "我们下一步补给你" in user and "不许写成" in user, (
        "两个方向都要堵死：推给业主、和推给我们自己的「以后」"
    )
    assert "留待现场确认" not in user, "这半句正是把活推给业主的出处"


# ---------------------------------------------------------------------------
# 写手看到的家庭事实全是真的（2026-09-08，立案＝9-07 真跑册三章写了输入包里没有的"阳台家政位"）
# + 按场景归组、标题一句一个数（规则 5.16，用户裁决 2026-09-08）
# ---------------------------------------------------------------------------

INVENTED_HOUSEHOLD_FACTS = ("家政", "老房", "侧边")
"""9-07 册里出现过、而输入包里一个字都没有的三样"家庭事实"。

「家政」出自推导 prompt 自己的示范句「因为你家阳台带家政位」；「侧边」是写手把
``{"entrance_shape": "side"}`` 直译出来的；「老房」是从"下沉卫生间"引申出来的。三条来路不同，
共同点是**都不在输入面里**——所以判据是 prompt 文本里一个都不许有。
"""


def _tier_request(tier: Tier, domain: str) -> DeriveRequest:
    package = load_tier(tier)
    return DeriveRequest(
        domain=domain,
        identity=package.personas_by_domain[domain][0].identity,
        anchors=[AnchorBrief.of(a) for a in package.domain_anchors(domain)],
        gaps=package.domain_gaps(domain),
        profile=package.anonymous_profile,
        banned_terms=collect_banned_terms(domain, package),
        triggered_rules=package.domain_triggered_rules(domain),
        backed_predicates=backed_predicates(domain, package),
        unbacked_predicates=unbacked_predicates(domain, package),
        banned_term_groups=package.banned_term_groups_by_domain.get(domain, {}),
    )


@pytest.mark.parametrize("tier", TIERS)
def test_derive_prompt_carries_no_household_fact_the_package_did_not_give(tier: Tier) -> None:
    """三档考卷 × 六章：推导 prompt 里一个编出来的家庭事实都没有。

    示范句是 prompt 自己写的，模型照抄示范句已实测多次（judgment_pairs 的 A/B），
    所以示范句里的具体事实等于喂给它一条"这家人的事"——改成占位形态之后这里守着不许回来。
    """
    package = load_tier(tier)
    for domain in package.domains:
        text = "\n".join(m["content"] for m in build_derive_messages(_tier_request(tier, domain)))
        leaked = [w for w in INVENTED_HOUSEHOLD_FACTS if w in text]
        assert not leaked, f"{tier}/{domain} 的推导 prompt 带了输入包里没有的家庭事实：{leaked}"


def test_derive_prompt_restricts_household_facts_to_the_input_surface() -> None:
    """家庭事实只有两处来源（画像那一行 + 户型条目括号里的理由），其余不许出现也不许引申。"""
    system = build_derive_messages(request_for())[0]["content"]
    assert "关于这家人的事实只有两处来源" in system
    assert "也不许从条目内容里引申出来" in system
    # 示范句改成占位形态：括号里那条理由是什么就说什么，示范本身不带任何一户的事实
    assert "因为你家〈括号里那条理由〉" in system


def test_derive_prompt_groups_by_scene_with_one_judgment_per_group() -> None:
    """规则 5.16：同一场景的参数归一条主张，主张是这组的判断句不是清单（用户裁决 2026-09-08）。

    立案＝9-07 册人体工学第五张卡：马桶/玄关柜/衣柜/沙发/走廊六条塞一个标题，写手只能把六个数
    排成标题、正文再逐条复读。成因就在 prompt 这一句「宁可一条主张多带几个落点」——它退场。
    """
    system = build_derive_messages(request_for())[0]["content"]
    assert "宁可一条主张多带几个落点" not in system
    assert "先把落点按场景归组" in system
    assert "主张是这一组的**一句判断**，不是这组参数的清单" in system
    assert "你这一步照旧一个数都不写" in system
    assert "最多只放得下一个数" not in system
    # 好例（厨房四条一卡）与坏例（六样互不相干的东西一卡）都在，且坏例只描述形态不给可抄的句子
    assert "好例＝「厨房」一条，挂两排间距、水槽深、吊柜底沿、冰箱散热四条落点" in system
    assert "坏例＝把马桶、玄关柜、衣柜、沙发、走廊这几样互不相干的东西塞进同一条" in system
    # 归组不等于编关系：v2.5 §14.10 那条口径一字不动
    assert "不许把同组落点写成相互约束的关系" in system


def test_cost_anchor_not_claimed_gets_a_claim_appended_by_the_system() -> None:
    """2026-09-08/09 真跑：金额条目在包里、推导两轮重开仍不挂——不求模型，系统追加一条主张挂上。"""
    raw = '[{"claim": "定制柜是造价里最吃钱的一项", "anchors": ["lkp-budget-share"]}]'
    known = {"lkp-budget-share", "lkp-cost-hydro-labor-sqm"}
    claims = parse_claims(raw, known, must_claim_ids=["lkp-cost-hydro-labor-sqm"])
    assert len(claims) == 2
    assert claims[1].claim == COST_CLAIM_TEXT
    assert claims[1].anchors == ["lkp-cost-hydro-labor-sqm"]
    raw_ok = (
        '[{"claim": "定制柜是造价里最吃钱的一项", "anchors": ["lkp-budget-share"]},'
        ' {"claim": "水电这笔钱按这家的面积已经能算出来", "anchors": ["lkp-cost-hydro-labor-sqm"]}]'
    )
    assert len(parse_claims(raw_ok, known, must_claim_ids=["lkp-cost-hydro-labor-sqm"])) == 2


# ---------------------------------------------------------------------------
# 造价章推导限三件事（规则 5.15 第 2 节 v2.13，用户裁决 2026-09-09：占比由算得不由搜得）
# ---------------------------------------------------------------------------

BUDGET_ANCHORS = [
    AnchorBrief(lkp_id="lkp-cost-hardfit-total-sqm", name="硬装全包合计", unit="元"),
    AnchorBrief(lkp_id="lkp-cost-hydro-labor-sqm", name="水电改造人工费合计", unit="元"),
    AnchorBrief(
        lkp_id="lkp-cost-hardfit-by-grade",
        name="硬装三档总价",
        unit="元",
        items=["basic", "medium", "premium"],
    ),
    AnchorBrief(lkp_id="lkp-share-hydro-of-total", name="水电占总价", unit="%"),
    AnchorBrief(lkp_id="lkp-price-hardfit-total-sqm", name="硬装全包行情单价", unit="元/㎡"),
    AnchorBrief(lkp_id="lkp-price-hydro-labor-sqm", name="水电改造人工费", unit="元/㎡"),
    AnchorBrief(lkp_id="lkp-price-demolition", name="墙体拆除", unit="元/㎡"),
    AnchorBrief(lkp_id="lkp-price-electrical-point", name="水电点位", unit="元/点位"),
    AnchorBrief(lkp_id="lkp-price-wall-paint", name="墙面乳胶漆涂刷", unit="元/㎡"),
    AnchorBrief(lkp_id="lkp-price-custom-cabinet", name="定制柜", unit="元/投影㎡"),
]
"""退役三条占比参数之后造价域包里剩的形态：单价、金额、派生占比、三档总价（缺口另给）。"""

WAITING_GAPS = [
    GapRecord(lkp_id="lkp-cost-demolition", basis_tag="budget@v12", reason="missing_input"),
    GapRecord(lkp_id="lkp-cost-electrical-point", basis_tag="budget@v12", reason="missing_input"),
    GapRecord(lkp_id="lkp-cost-wall-paint", basis_tag="budget@v12", reason="missing_input"),
    GapRecord(lkp_id="lkp-cost-custom-cabinet", basis_tag="budget@v12", reason="missing_input"),
]
"""四个量等平面：拆改㎡、水电点位数、涂刷面积、定制柜投影面积——金额缺、单价在。"""


def _budget_request(gaps: list[GapRecord] | None = None) -> DeriveRequest:
    return DeriveRequest(
        domain="budget",
        identity="你是这家人的造价顾问。",
        anchors=BUDGET_ANCHORS,
        gaps=WAITING_GAPS if gaps is None else gaps,
        profile=PACKAGE.anonymous_profile,
        backed_predicates=["各工项单价行情"],
        unbacked_predicates=[],
    )


def test_budget_derivation_lists_three_things_in_order() -> None:
    """造价章的三件事按序进 prompt，每件事下面贴的条目 id 是按包算出来的。

    ① 眼下算得出的钱＝全部金额（含三档总价）+ 派生占比；② 等平面的＝缺口配它的单价条目、说清缺的
    是什么量；③ 其余单价。缺口全部归进第②件时，通用"不许为它单独写一张卡"那段不再出现——
    这一章恰恰要为它写一张卡（坦白"等平面出来按量算"，规则 5.15 v2.13）。
    """
    system, user = (m["content"] for m in build_derive_messages(_budget_request()))
    assert "最多三条，多了会被打回" in system
    assert "通常三到五件" not in system

    first, second, third = (user.index(mark) for mark in ("① ", "② ", "③ "))
    assert first < second < third
    money_part, waiting_part, price_part = user[first:second], user[second:third], user[third:]
    for cost_id in (
        "lkp-cost-hardfit-total-sqm",
        "lkp-cost-hydro-labor-sqm",
        "lkp-cost-hardfit-by-grade",
        "lkp-share-hydro-of-total",
    ):
        assert cost_id in money_part, f"{cost_id} 该挂在第①件事"
    assert "分 3 项" in money_part, "三档总价分三项要标出来"
    assert "lkp-cost-demolition：缺的量＝㎡" in waiting_part
    assert "lkp-cost-electrical-point：缺的量＝点位" in waiting_part
    assert "lkp-cost-custom-cabinet：缺的量＝投影㎡" in waiting_part
    assert "lkp-price-demolition" in waiting_part and "现在就有，挂它" in waiting_part
    assert "等平面出来按量算" in waiting_part and "占比一个字都不写" in waiting_part
    assert "lkp-price-hardfit-total-sqm" in price_part
    assert "lkp-price-hydro-labor-sqm" in price_part
    assert "lkp-price-demolition" not in price_part, "配给第②件的单价不再进第③件"
    assert "不许为它单独写一张卡" not in user


def test_budget_gap_without_a_unit_price_falls_back_to_the_generic_gap_block() -> None:
    """缺口不是金额、或它的单价也不在包里，不属于第②件事——退回"没有值就不写"那段。"""
    odd = GapRecord(lkp_id="lkp-budget-confidence-width", basis_tag="budget@v12", reason="x")
    user = build_derive_messages(_budget_request(gaps=[*WAITING_GAPS, odd]))[1]["content"]
    assert "② 等平面才算得出的项：这轮没有" not in user
    assert "不许为它单独写一张卡" in user
    assert "- lkp-budget-confidence-width：x" in user
    assert "- lkp-cost-demolition：missing_input" not in user, "等平面的金额不进通用缺口段"


def test_budget_without_waiting_gaps_says_the_second_thing_is_skipped() -> None:
    user = build_derive_messages(_budget_request(gaps=[]))[1]["content"]
    assert "② 等平面才算得出的项：这轮没有，这件事不讲" in user
    assert "lkp-price-demolition" in user[user.index("③ ") :]


def test_non_budget_domain_keeps_its_own_count_rule_and_no_agenda() -> None:
    system, user = (m["content"] for m in build_derive_messages(request_for()))
    assert "通常三到五件" in system
    assert "最多三条" not in system
    assert "这一章的三件事" not in user


def _claims(n: int) -> str:
    return json.dumps(
        [{"claim": f"第{'一二三四五'[i]}件事的取舍在这里", "anchors": []} for i in range(n)],
        ensure_ascii=False,
    )


def test_budget_rejects_a_fourth_claim_and_carries_the_draft() -> None:
    """上限在 parse_claims 判（prompt 里"最多三条"只是叮嘱）；打回带原稿，射程同其他裁判场。"""
    with pytest.raises(DeriverOutputError) as e:
        parse_claims(_claims(4), set(), domain="budget")
    assert "只讲三件事" in str(e.value) and "4 条" in str(e.value)
    assert len(e.value.claims) == 4
    assert len(parse_claims(_claims(3), set(), domain="budget")) == 3


def test_claim_limit_is_budget_only() -> None:
    assert len(parse_claims(_claims(4), set(), domain="ergonomics")) == 4
    assert len(parse_claims(_claims(5), set())) == 5


def test_budget_unclaimed_cost_ids_merge_into_the_first_claim() -> None:
    """金额没挂仍由系统保证挂上，但造价域**并进第①件**而不是另开一条——另开会顶破上限。"""
    known = {a.lkp_id for a in BUDGET_ANCHORS}
    must = ["lkp-cost-hardfit-total-sqm", "lkp-cost-hydro-labor-sqm", "lkp-cost-hardfit-by-grade"]
    raw = (
        '[{"claim": "按面积眼下先能算出来一笔", "anchors": ["lkp-cost-hardfit-total-sqm"]},'
        ' {"claim": "几项要等平面", "anchors": ["lkp-price-demolition"]},'
        ' {"claim": "单价口径是行情价", "anchors": ["lkp-price-hydro-labor-sqm"]}]'
    )
    claims = parse_claims(raw, known, must_claim_ids=must, domain="budget")
    assert len(claims) == 3
    assert claims[0].anchors == must
    assert all(c.claim != COST_CLAIM_TEXT for c in claims)

    raw_ok = raw.replace(
        '"anchors": ["lkp-cost-hardfit-total-sqm"]',
        '"anchors": ["lkp-cost-hardfit-total-sqm", "lkp-cost-hydro-labor-sqm",'
        ' "lkp-cost-hardfit-by-grade"]',
    )
    assert parse_claims(raw_ok, known, must_claim_ids=must, domain="budget")[0].anchors == must
