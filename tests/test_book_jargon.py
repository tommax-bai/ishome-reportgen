"""册级行话跨域扫（``book-jargon-across-domains``，规则 4.13 增补，用户裁决 2026-09-07）。"""

from __future__ import annotations

from reportgen_worker import activities
from reportgen_worker.gate import book_jargon_violations, jargon_terms_across_domains
from reportgen_worker.models import BookCheckRequest, BookCheckResult, Card, Page, ProvenanceNote
from tests.fixtures import load_package

# 整册卷子（六域全）：行话并集要跨六个域才有意义。
PACKAGE = load_package("full")

CLEAN_ERGONOMICS = Card(
    thesis="操作台的高度要跟着主厨的身体走。",
    body="台面高按 {lkp-counter-height} mm 做。",
    number_refs=["lkp-counter-height"],
)


def test_jargon_union_takes_domain_extra_and_jargon_buckets_only() -> None:
    """夹具形态查实：五个域的桶叫 domain_extra、灯光域叫 jargon；造价域的 deal/unbacked_verdict
    不是行话（那是"替谁把买卖谈成""替业主下判断"），公共四组按域各扫各的，都不进并集。"""
    union = jargon_terms_across_domains(PACKAGE)
    assert union["净宽"] == ["ergonomics"]
    assert union["照度"] == ["lighting"]
    assert union["延米"] == ["storage"]
    assert "总价" not in union
    assert "性价比高" not in union
    assert "可能" not in union


def test_footer_anchor_name_with_jargon_fails_the_book() -> None:
    """原文回放（2026-09-07 人体工学页脚）：「主通道净宽，状态：未校准」——「净宽」是本域行话，
    单元层扫不到页脚，册级扫题名逮住。"""
    page = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[
            Card(
                thesis="主通道要留够端汤穿行的宽度。",
                body="主通道不低于 {lkp-passage-main} mm，你端着热汤从厨房走到餐厅时不用侧身。",
                number_refs=["lkp-passage-main"],
            )
        ],
        provenance_notes=[
            ProvenanceNote(lkp_id="lkp-passage-main", source="行业通行", calibration="draft")
        ],
    )
    # 考卷题名已随业务侧改成「主通道宽度」（2026-09-08），这里把旧题名放回去复现立案样本。
    package = PACKAGE.model_copy(deep=True)
    for anchor in package.anchors:
        if anchor.lkp_id == "lkp-passage-main":
            anchor.name = "主通道净宽"
    hits = [v for v in book_jargon_violations([page], package) if "页脚" in v.detail]
    assert len(hits) == 1
    assert "主通道净宽" in hits[0].detail and "「净宽」" in hits[0].detail
    assert "求值线改题名" in hits[0].detail


def test_other_domains_jargon_in_a_card_is_caught_at_book_level() -> None:
    """灯光域的「照度」写进人体工学章：单元层拿本域禁词扫不到，册级并集扫到。"""
    page = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[
            Card(
                thesis="操作台的高度要跟着主厨的身体走。",
                body="台面高按 {lkp-counter-height} mm 做，台面照度另说。",
                number_refs=["lkp-counter-height"],
            )
        ],
    )
    hits = book_jargon_violations([page], PACKAGE)
    assert len(hits) == 1
    assert hits[0].check == "book-jargon-across-domains"
    assert "card[0]" in hits[0].detail and "lighting 域行话" in hits[0].detail


async def test_book_check_fails_on_jargon_and_passes_without() -> None:
    """接进册检：命中即 failed；干净的整册不受影响。"""
    clean = Page(page_id="page-ergonomics", domain="ergonomics", cards=[CLEAN_ERGONOMICS])
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[clean], package=PACKAGE))
    )
    assert "book-jargon-across-domains" not in {v.check for v in book.violations}

    tainted = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[
            Card(
                thesis="收纳系统要跟着主厨走。",
                body="台面高按 {lkp-counter-height} mm 做。",
                number_refs=["lkp-counter-height"],
            )
        ],
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[tainted], package=PACKAGE))
    )
    assert book.verdict == "failed"
    assert any(
        v.check == "book-jargon-across-domains" and "「收纳系统」" in v.detail
        for v in book.violations
    )
