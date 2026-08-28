"""单元成文 activity：重写循环、failed verdict 上抛、装配与册级校验、降档门禁前置。"""

from __future__ import annotations

import copy

import pytest

from reportgen_worker import activities
from reportgen_worker.models import (
    BookCheckRequest,
    BookCheckResult,
    Card,
    Page,
    PageAssembleRequest,
    PageAssembleResult,
    ReportDataPackage,
    UnitComposeRequest,
    UnitComposeResult,
)
from reportgen_worker.writer import WriterRequest
from tests.support import PACKAGE_JSON, load_package

PACKAGE = load_package()

GOOD_CARD = Card(
    thesis="操作台的高度要跟着主厨的身体走。",
    body="台面高按 {lkp-counter-height} 做。",
    number_refs=["lkp-counter-height"],
)
BAD_CARD = Card(thesis="台面高做九百。", body="就是 900mm，综合考量后定的。", number_refs=[])


class ScriptedWriter:
    """按脚本逐稿产出的假写作器：记录每稿收到的反馈（验证违规确实回流成重写输入）。"""

    def __init__(self, scripts: list[list[Card]]) -> None:
        self._scripts = scripts
        self.seen_feedback: list[list[str]] = []
        self.seen_requests: list[WriterRequest] = []

    async def write(self, request: WriterRequest) -> list[Card]:
        self.seen_feedback.append([v.check for v in request.feedback])
        self.seen_requests.append(request)
        return self._scripts[min(request.attempt, len(self._scripts) - 1)]


@pytest.fixture(autouse=True)
def _restore_writer_factory() -> object:
    original = activities.writer_factory
    yield
    activities.writer_factory = original


async def compose(
    writer: ScriptedWriter,
    domain: str = "ergonomics",
    package: ReportDataPackage = PACKAGE,
) -> UnitComposeResult:
    activities.writer_factory = lambda: writer
    raw = await activities.compose_report_unit(UnitComposeRequest(domain=domain, package=package))
    return UnitComposeResult.model_validate(raw)


async def test_ok_on_first_attempt() -> None:
    result = await compose(ScriptedWriter([[GOOD_CARD]]))
    assert result.verdict == "ok"
    assert result.rewrites_used == 0
    assert result.cards == [GOOD_CARD]
    assert [r.release_tag for r in result.releases] == ["ergonomics@v1", "lighting@v2"]


async def test_violations_feed_rewrite_then_ok() -> None:
    writer = ScriptedWriter([[BAD_CARD], [GOOD_CARD]])
    result = await compose(writer)
    assert result.verdict == "ok"
    assert result.rewrites_used == 1
    assert writer.seen_feedback[0] == []
    assert "gate-digit-outside-ref" in writer.seen_feedback[1]


async def test_failed_verdict_after_max_rewrites() -> None:
    """重写 ≤2 轮仍不过 → failed 上抛，绝不静默假成功（图 v0.2 §3）。"""
    result = await compose(ScriptedWriter([[BAD_CARD]]))
    assert result.verdict == "failed"
    assert result.rewrites_used == 2
    assert result.violations
    assert not result.cards


async def test_domain_without_persona_fails() -> None:
    result = await compose(ScriptedWriter([[GOOD_CARD]]), domain="budget")
    assert result.verdict == "failed"
    assert result.violations[0].check == "gate-domain-not-in-package"


async def test_assemble_and_book_check() -> None:
    ok_unit = await compose(ScriptedWriter([[GOOD_CARD]]))
    lighting_unit = UnitComposeResult(
        verdict="ok",
        domain="lighting",
        cards=[
            Card(
                thesis="灯光按人的活动分层。",
                body="起居室照度锚在 {lkp-illuminance-living}。",
                number_refs=["lkp-illuminance-living"],
            )
        ],
        releases=ok_unit.releases,
    )
    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(PageAssembleRequest(units=[lighting_unit, ok_unit]))
    )
    assert assembled.verdict == "ok"
    assert [p.page_id for p in assembled.pages] == ["page-ergonomics", "page-lighting"]

    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=assembled.pages, package=PACKAGE))
    )
    assert book.verdict == "ok"


async def test_package_gate_fails_before_calling_writer() -> None:
    """数据包自身违约（隐藏落点带值下发）→ 写作器一次都不调（规则 4.10 门禁前置）。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["presentation"] = "WITHHELD"
    writer = ScriptedWriter([[GOOD_CARD]])
    result = await compose(writer, package=ReportDataPackage.model_validate(tainted))

    assert result.verdict == "failed"
    assert result.violations[0].check == "gate-withheld-anchor-delivered"
    assert writer.seen_feedback == []


async def test_writer_sees_assertion_budget_split() -> None:
    """写作器拿到的是"这轮许说/不许说"两张清单，不是自己判 degraded（prompt 侧同步）。"""
    writer = ScriptedWriter([[GOOD_CARD]])
    await compose(writer)

    assert writer.seen_requests[0].backed_predicates == ["通道净宽是否够"]
    assert writer.seen_requests[0].unbacked_predicates == ["台面高度", "挂杆高度"]


async def test_book_check_rejects_withheld_reference() -> None:
    """册级最后一道：渲染前再拦一次被隐藏落点的引用（规则 4.10）。"""
    page = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[
            Card(
                thesis="挂杆按你的身高定。",
                body="定在 {lkp-wardrobe-rod}。",
                number_refs=["lkp-wardrobe-rod"],
            )
        ],
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[page], package=PACKAGE))
    )
    assert book.verdict == "failed"
    assert "gate-withheld-anchor-referenced" in {v.check for v in book.violations}


async def test_assemble_rejects_failed_unit() -> None:
    failed_unit = UnitComposeResult(verdict="failed", domain="lighting")
    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(PageAssembleRequest(units=[failed_unit]))
    )
    assert assembled.verdict == "failed"
    assert assembled.violations[0].check == "gate-unit-failed"


async def test_empty_card_set_fails_at_unit_level() -> None:
    """真跑回归：全域降档后模型交空数组——零卡片曾以 verdict=ok 溜过（逐卡片过检无卡片即无违规）。

    失败必须在源头报出，不靠下游装配/册检兜（图 v0.2 §3）。
    """
    result = await compose(ScriptedWriter([[]]))
    assert result.verdict == "failed"
    assert "gate-empty-composition" in {v.check for v in result.violations}
    assert not result.cards
