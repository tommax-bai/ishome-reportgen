"""单元成文 activity：重写循环、failed verdict 上抛、装配与册级校验、降档门禁前置。"""

from __future__ import annotations

import copy

import pytest

from reportgen_worker import activities
from reportgen_worker.judge import JudgeRequest
from reportgen_worker.models import (
    BookCheckRequest,
    BookCheckResult,
    Card,
    JudgeObservation,
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
LIGHTING_CARD = Card(
    thesis="起居室的亮度按人在这儿做什么来分层。",
    body="沙发那片区域锚在 {lkp-illuminance-living}。",
    number_refs=["lkp-illuminance-living"],
)


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


class ScriptedJudge:
    """假判官：固定返回给定观察，并记录被问了几次（默认不判出任何问题）。"""

    def __init__(self, observations: list[JudgeObservation] | None = None) -> None:
        self._observations = observations or []
        self.seen_requests: list[JudgeRequest] = []

    async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
        self.seen_requests.append(request)
        return list(self._observations)


FABRICATION = JudgeObservation(
    check="cr-fabricated-fact", quote="台面高", why="画像里没有家庭构成信息"
)


@pytest.fixture(autouse=True)
def _restore_factories() -> object:
    original_writer, original_judge = activities.writer_factory, activities.judge_factory
    yield
    activities.writer_factory = original_writer
    activities.judge_factory = original_judge


async def compose(
    writer: ScriptedWriter,
    domain: str = "ergonomics",
    package: ReportDataPackage = PACKAGE,
    judge: ScriptedJudge | None = None,
) -> UnitComposeResult:
    activities.writer_factory = lambda: writer
    activities.judge_factory = lambda: judge or ScriptedJudge()
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
        cards=[LIGHTING_CARD],
        releases=ok_unit.releases,
        required_locked_texts=["DISCLAIM_P1"],
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


async def test_locked_texts_mounted_by_assembly_not_by_writer() -> None:
    """gen-locked 挂载链路（规则 2.4/5.15）：要求随包 → 单元透传 → **装配层挂上页** → 册级验齐。

    写作器全程不参与：卡片上没有放它的位置，模型也没被告知有这回事（见 test_writer_prompt）。
    """
    ergonomics_unit = await compose(ScriptedWriter([[GOOD_CARD]]))
    assert ergonomics_unit.required_locked_texts == []  # 本域无必挂文案

    lighting_unit = await compose(ScriptedWriter([[LIGHTING_CARD]]), domain="lighting")
    assert lighting_unit.required_locked_texts == ["DISCLAIM_P1"]

    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(
            PageAssembleRequest(units=[lighting_unit, ergonomics_unit])
        )
    )
    mounted = {p.domain: p.locked_text_ids for p in assembled.pages}
    assert mounted == {"ergonomics": [], "lighting": ["DISCLAIM_P1"]}

    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=assembled.pages, package=PACKAGE))
    )
    assert book.verdict == "ok"


async def test_book_check_reports_missing_locked_text() -> None:
    """册级确定性校验：产物要求的锁定文案没挂上 → 渲染前拦住（规则 5.15 "必挂"此前是空文）。"""
    pages = [
        Page(page_id="page-ergonomics", domain="ergonomics", cards=[GOOD_CARD]),
        Page(page_id="page-lighting", domain="lighting", cards=[LIGHTING_CARD]),  # 漏挂
    ]
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=pages, package=PACKAGE))
    )
    assert book.verdict == "failed"
    missing = [v for v in book.violations if v.check == "gate-locked-text-missing"]
    assert len(missing) == 1
    assert "DISCLAIM_P1" in missing[0].detail


async def test_package_without_locked_texts_requires_none() -> None:
    """旧包（生产方未升级）缺省空 = 无要求，不误判——宽进；该挂没挂由上面那条严查。"""
    legacy = copy.deepcopy(PACKAGE_JSON)
    legacy.pop("lockedTextsByDomain")
    package = ReportDataPackage.model_validate(legacy)
    unit = await compose(ScriptedWriter([[LIGHTING_CARD]]), domain="lighting", package=package)

    assert unit.required_locked_texts == []
    book = BookCheckResult.model_validate(
        await activities.check_report_book(
            BookCheckRequest(
                pages=[Page(page_id="page-lighting", domain="lighting", cards=[LIGHTING_CARD])],
                package=package,
            )
        )
    )
    assert "gate-locked-text-missing" not in {v.check for v in book.violations}


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


async def test_judge_observations_do_not_change_verdict() -> None:
    """观察态（规则 4.17 门禁二）：判官判出问题也只记录不拦截——verdict 不变、不触发重写。"""
    writer = ScriptedWriter([[GOOD_CARD]])
    judge = ScriptedJudge([FABRICATION])
    result = await compose(writer, judge=judge)

    assert result.verdict == "ok"
    assert result.rewrites_used == 0
    assert result.cards == [GOOD_CARD]
    assert not result.violations
    assert len(writer.seen_feedback) == 1  # 判官的话没变成第二稿的反馈


async def test_judge_observations_are_carried_back_verbatim() -> None:
    """判官输出原样带回：编号/原句/为什么三件，不加工也不据此改写卡片。"""
    result = await compose(ScriptedWriter([[GOOD_CARD]]), judge=ScriptedJudge([FABRICATION]))

    assert result.observations == [FABRICATION]
    assert result.cards == [GOOD_CARD]


async def test_judge_runs_only_after_rule_layer_passes() -> None:
    """判官在规则层之后（图 v0.2 §3）：机检没过的稿子不问判官，别烧那次调用。"""
    judge = ScriptedJudge()
    result = await compose(ScriptedWriter([[BAD_CARD], [GOOD_CARD]]), judge=judge)

    assert result.verdict == "ok"
    assert len(judge.seen_requests) == 1  # 第一稿机检就没过，只有第二稿被判官看过
    assert judge.seen_requests[0].cards == [GOOD_CARD]
    assert [c.asset_id for c in judge.seen_requests[0].checks] == ["cr-fabricated-fact"]


async def test_judge_failure_does_not_block_composition() -> None:
    """判官挂掉不阻塞成文：第二道不可用只是没有观察数据，不是这份内容不能发。"""

    class BrokenJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            raise RuntimeError("判官网关 502")

    activities.writer_factory = lambda: ScriptedWriter([[GOOD_CARD]])
    activities.judge_factory = BrokenJudge
    raw = await activities.compose_report_unit(
        UnitComposeRequest(domain="ergonomics", package=PACKAGE)
    )
    result = UnitComposeResult.model_validate(raw)

    assert result.verdict == "ok"
    assert result.cards == [GOOD_CARD]
    assert result.observations == []


async def test_promoted_check_intercepts_and_feeds_rewrite() -> None:
    """拦截能力在数据侧：把同一条判据 status 改成 active，观察即成为重写反馈——代码一行没改。"""
    promoted = copy.deepcopy(PACKAGE_JSON)
    promoted["checksByDomain"]["ergonomics"][1]["status"] = "active"
    writer = ScriptedWriter([[GOOD_CARD]])
    result = await compose(
        writer,
        package=ReportDataPackage.model_validate(promoted),
        judge=ScriptedJudge([FABRICATION]),
    )

    assert result.verdict == "failed"
    assert result.rewrites_used == 2
    assert result.violations[0].check == "cr-fabricated-fact"
    assert writer.seen_feedback[1] == ["cr-fabricated-fact"]
    assert result.observations == [FABRICATION]  # 失败也带回观察，回路要得到这份信号


async def test_empty_card_set_fails_at_unit_level() -> None:
    """真跑回归：全域降档后模型交空数组——零卡片曾以 verdict=ok 溜过（逐卡片过检无卡片即无违规）。

    失败必须在源头报出，不靠下游装配/册检兜（图 v0.2 §3）。
    """
    result = await compose(ScriptedWriter([[]]))
    assert result.verdict == "failed"
    assert "gate-empty-composition" in {v.check for v in result.violations}
    assert not result.cards
