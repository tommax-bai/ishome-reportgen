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
from reportgen_worker.models import AnchorBrief
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
