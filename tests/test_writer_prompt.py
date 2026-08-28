"""prompt 拼装：降档纪律进 system prompt（规则 4.10/5.8）。

prompt 是第一道不是门禁（判据下沉次序 schema > 规则 > prompt > 判官），但它必须与 gate 同口径——
两处口径不一致，写作器就会被反复打回却不知道该怎么改。
"""

from __future__ import annotations

from reportgen_worker.gate import backed_predicates, collect_banned_terms, unbacked_predicates
from reportgen_worker.writer import WriterRequest, build_messages
from tests.support import load_package

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
    """落点逐条标档：降档的明确写"禁进主旨句"，与 gate-thesis-degraded-anchor 同口径。"""
    user = build_messages(request_for())[1]["content"]
    assert "lkp-counter-height" in user
    assert "【降档·只可参考口吻，禁进主旨句】" in user
    assert "lkp-passage-main（主通道净宽，mm）" in user
    assert "【可作支点】" in user


def test_withheld_anchor_never_enters_prompt() -> None:
    """隐藏落点连名字都不下发——写作器无从提起（规则 4.10）。"""
    messages = build_messages(request_for())
    assert all("lkp-wardrobe-rod" not in m["content"] for m in messages)


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
