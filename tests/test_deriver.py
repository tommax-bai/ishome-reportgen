"""叙事推导（图 v0.2 §3 第一步）：只定"讲哪几件事"，看不见落点的值，产不出数字。"""

from __future__ import annotations

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
