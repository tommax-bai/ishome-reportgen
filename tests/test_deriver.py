"""叙事推导（图 v0.2 §3 第一步）：只定"讲哪几件事"，看不见落点的值，产不出数字。"""

from __future__ import annotations

import copy
import json

import pytest

from reportgen_worker.deriver import (
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
    request.gaps = [GapRecord(
            lkp_id="lkp-rug-size-rule", basis_tag="softdeco@v9", reason="formula_not_implemented"
        )]
    user = build_derive_messages(request)[1]["content"]
    assert "没有值" in user and "不许为它单独写一张卡" in user, "缺口不该变成可写的题材"
    assert "等你确认" in user and "我们下一步补给你" in user and "不许写成" in user, (
        "两个方向都要堵死：推给业主、和推给我们自己的「以后」"
    )
    assert "留待现场确认" not in user, "这半句正是把活推给业主的出处"
