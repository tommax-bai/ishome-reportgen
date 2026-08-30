"""单元成文 activity：重写循环、failed verdict 上抛、装配与册级校验、降档门禁前置。"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from reportgen_worker import activities
from reportgen_worker.deriver import DeriveRequest, DeriverOutputError, NarrativeDeriver
from reportgen_worker.judge import JudgeRequest
from reportgen_worker.models import (
    BookCheckRequest,
    BookCheckResult,
    Card,
    JudgeObservation,
    NarrativeClaim,
    Page,
    PageAssembleRequest,
    PageAssembleResult,
    ProvenanceNote,
    ReportDataPackage,
    UnitComposeRequest,
    UnitComposeResult,
)
from reportgen_worker.writer import WriterRequest
from tests.support import PACKAGE_JSON, load_package

PACKAGE = load_package()

GOOD_CARD = Card(
    thesis="操作台的高度要跟着主厨的身体走。",
    body="台面高按 {lkp-counter-height} mm 做。",
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


CLAIMS = [
    NarrativeClaim(claim="台面高度该按主厨的身体定，不按平均身高", anchors=["lkp-counter-height"]),
    NarrativeClaim(claim="通道宽度决定两个人能不能同时在厨房里转身", anchors=["lkp-passage-main"]),
]


class ScriptedDeriver:
    """假推导器：固定返回给定主张，并记录被问了几次（推导每单元只该跑一次）。"""

    def __init__(self, claims: list[NarrativeClaim] | None = None) -> None:
        self._claims = CLAIMS if claims is None else claims
        self.seen_requests: list[DeriveRequest] = []

    async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]:
        self.seen_requests.append(request)
        return list(self._claims)


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
    originals = (activities.writer_factory, activities.judge_factory, activities.deriver_factory)
    yield
    activities.writer_factory, activities.judge_factory, activities.deriver_factory = originals


async def compose(
    writer: ScriptedWriter,
    domain: str = "ergonomics",
    package: ReportDataPackage = PACKAGE,
    judge: ScriptedJudge | None = None,
    deriver: NarrativeDeriver | None = None,
) -> UnitComposeResult:
    activities.writer_factory = lambda: writer
    activities.judge_factory = lambda: judge or ScriptedJudge()
    activities.deriver_factory = lambda: deriver or ScriptedDeriver()
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
    """数据包自身违约（未过门却标称无需标注）→ 写作器一次都不调（门禁前置，不烧 LLM 调用）。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["provenance"]["annotationRequired"] = False
    writer = ScriptedWriter([[GOOD_CARD]])
    result = await compose(writer, package=ReportDataPackage.model_validate(tainted))

    assert result.verdict == "failed"
    assert result.violations[0].check == "gate-provenance-inconsistent"
    assert writer.seen_feedback == []


async def test_writer_sees_assertion_budget_split() -> None:
    """写作器拿到的是"这轮许说/不许说"两张清单，不是自己判 degraded（prompt 侧同步）。"""
    writer = ScriptedWriter([[GOOD_CARD]])
    await compose(writer)

    assert writer.seen_requests[0].backed_predicates == ["通道净宽是否够"]
    assert writer.seen_requests[0].unbacked_predicates == ["台面高度", "挂杆高度"]


async def test_book_check_accepts_unbacked_reference_with_annotation() -> None:
    """v2.4：曾被隐藏的落点照常引用——册级要的不再是"别提它"，而是"这页标了它的依据没有"。"""
    page = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[
            Card(
                thesis="挂杆按你的身高定。",
                body="定在 {lkp-wardrobe-rod} mm。",
                number_refs=["lkp-wardrobe-rod"],
            )
        ],
        provenance_notes=[
            ProvenanceNote(lkp_id="lkp-wardrobe-rod", source="行业通行", calibration="draft")
        ],
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[page], package=PACKAGE))
    )
    checks = {v.check for v in book.violations}
    assert "gate-number-ref-unresolved" not in checks
    assert "gate-provenance-annotation-missing" not in checks


async def test_assemble_rejects_failed_unit() -> None:
    failed_unit = UnitComposeResult(verdict="failed", domain="lighting")
    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(PageAssembleRequest(units=[failed_unit]))
    )
    assert assembled.verdict == "failed"
    assert assembled.violations[0].check == "gate-unit-failed"


async def test_derivation_runs_once_and_shapes_the_writer_prompt() -> None:
    """推导每单元只跑一次：重写打回的是"卡片怎么写"，不是"这一章该讲什么"。"""
    writer = ScriptedWriter([[BAD_CARD], [GOOD_CARD]])
    deriver = ScriptedDeriver()
    result = await compose(writer, deriver=deriver)

    assert result.verdict == "ok"
    assert result.rewrites_used == 1
    assert len(deriver.seen_requests) == 1  # 两稿共用一次推导
    assert [c.claim for c in writer.seen_requests[0].claims] == [c.claim for c in CLAIMS]
    assert result.claims == CLAIMS  # 主张随结果上抛：卡片垮了要分得清是哪一步垮的


async def test_derive_failure_retries_then_fails_loudly() -> None:
    """推导失败不退回"没有主张照样写"——那正是这一步要修的老形态，静默退回＝静默假成功。"""

    class BrokenDeriver:
        def __init__(self) -> None:
            self.calls = 0

        async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]:
            self.calls += 1
            raise DeriverOutputError("推导没有产出任何主张")

    deriver = BrokenDeriver()
    writer = ScriptedWriter([[GOOD_CARD]])
    result = await compose(writer, deriver=deriver)

    assert result.verdict == "failed"
    assert [v.check for v in result.violations] == ["gate-narrative-derive-failed"]
    assert deriver.calls == 3  # 每一轮都重试推导（max_rewrites=2 → 三次机会）
    assert writer.seen_requests == []  # 没有主张就不写：不烧那次写作调用


async def test_cards_exceeding_claims_are_rejected() -> None:
    """卡片多于主张＝又在按落点一条一张排版（图 v0.2 §3 卡片按主张组织）。

    阈值来自推导步自己的产出，不是拍出来的数——"每单元卡片上限 6~8"那种无据阈值已被撤回。
    """
    one_claim = [CLAIMS[0]]
    writer = ScriptedWriter([[GOOD_CARD, LIGHTING_CARD.model_copy(update={"number_refs": []})]])
    result = await compose(writer, deriver=ScriptedDeriver(one_claim))

    assert result.verdict == "failed"
    assert "gate-cards-exceed-claims" in {v.check for v in result.violations}


async def test_fewer_cards_than_claims_passes() -> None:
    """少于主张数不拦：讲不动的那件事宁可不讲（规则 4.18 宁薄勿撑）。"""
    result = await compose(ScriptedWriter([[GOOD_CARD]]), deriver=ScriptedDeriver(CLAIMS))

    assert result.verdict == "ok"


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
    activities.deriver_factory = ScriptedDeriver
    raw = await activities.compose_report_unit(
        UnitComposeRequest(domain="ergonomics", package=PACKAGE)
    )
    result = UnitComposeResult.model_validate(raw)

    assert result.verdict == "ok"
    assert result.cards == [GOOD_CARD]
    assert result.observations == []


async def test_judge_run_ledger_records_counts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """计数载体（裁决 2026-08-29）：一次送审记一行——判据 × 份 × 批大小 × 触发。

    台账是规则 4.17 门禁二唯一的数据来源；没有它，"0 命中"与"根本没问成"在统计里长得一样。
    """
    ledger = tmp_path / "judge-ledger.jsonl"
    monkeypatch.setenv(activities.JUDGE_LEDGER_ENV, str(ledger))

    result = await compose(ScriptedWriter([[GOOD_CARD]]), judge=ScriptedJudge([FABRICATION]))

    assert result.judge_run is not None
    assert result.judge_run.cards_reviewed == 1
    assert result.judge_run.batches == 1
    assert [(c.check, c.hits) for c in result.judge_run.checks] == [("cr-fabricated-fact", 1)]

    line = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert line["domain"] == "ergonomics"
    assert line["releases"] == ["ergonomics@v1", "lighting@v2"]  # 触发率要能归到判据的哪一版
    assert line["checks"] == [
        {"check": "cr-fabricated-fact", "version": 1, "status": "observing", "hits": 1}
    ]


async def test_judge_run_absent_when_judge_never_ran() -> None:
    """规则层没放行 → 判官没跑 → 台账为 None：没送审就不该在分母里占一份。"""
    result = await compose(ScriptedWriter([[BAD_CARD]]))

    assert result.verdict == "failed"
    assert result.judge_run is None


async def test_ledger_write_failure_does_not_block_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """台账写不进去只是观察数据的损失，不是这份内容的事故（同"判官不阻塞"的同一条理由）。"""
    monkeypatch.setenv(activities.JUDGE_LEDGER_ENV, "/nonexistent-dir/judge.jsonl")

    result = await compose(ScriptedWriter([[GOOD_CARD]]), judge=ScriptedJudge([FABRICATION]))

    assert result.verdict == "ok"
    assert result.judge_run is not None


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


# ---------------------------------------------------------------------------
# 标注链路（规则 4.10c 标注必挂，v2.4）：单元投影 → 装配挂载 → 册级比对
# ---------------------------------------------------------------------------

COUNTER_NOTE = ProvenanceNote(lkp_id="lkp-counter-height", source="行业通行", calibration="draft")


async def test_provenance_note_flows_unit_to_page_to_book() -> None:
    """整条链路一次跑通：单元按引用投影出要求 → 装配挂上页 → 册级比对通过。"""
    unit = await compose(ScriptedWriter([[GOOD_CARD]]))
    assert unit.required_provenance == [COUNTER_NOTE]
    # 过门落点无需标注：lighting 那页页脚是空的（标注不是每个数都加一行）
    lighting_unit = await compose(ScriptedWriter([[LIGHTING_CARD]]), domain="lighting")
    assert lighting_unit.required_provenance == []

    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(PageAssembleRequest(units=[unit, lighting_unit]))
    )
    mounted = {p.domain: p.provenance_notes for p in assembled.pages}
    assert mounted == {"ergonomics": [COUNTER_NOTE], "lighting": []}

    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=assembled.pages, package=PACKAGE))
    )
    assert book.verdict == "ok"


async def test_book_check_reports_unannotated_anchor() -> None:
    """隐藏禁令的等价物：未过门的数进了正文却没标依据 → 渲染前拦住（拦截点=不标就说）。"""
    page = Page(page_id="page-ergonomics", domain="ergonomics", cards=[GOOD_CARD])
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[page], package=PACKAGE))
    )

    assert book.verdict == "failed"
    missing = [v for v in book.violations if v.check == "gate-provenance-annotation-missing"]
    assert len(missing) == 1
    assert "lkp-counter-height" in missing[0].detail


async def test_book_check_reports_tampered_annotation() -> None:
    """标注被中间层改写等于没标：业主据以判断这个数有多硬的，必须是求值线给的那份事实。"""
    page = Page(
        page_id="page-ergonomics",
        domain="ergonomics",
        cards=[GOOD_CARD],
        provenance_notes=[
            ProvenanceNote(lkp_id="lkp-counter-height", source="国标", calibration="calibrated")
        ],
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[page], package=PACKAGE))
    )

    assert book.verdict == "failed"
    assert "gate-provenance-note-mismatch" in {v.check for v in book.violations}


async def test_calibrated_anchor_needs_no_annotation() -> None:
    """过门的数不必标：标注是"这条有多硬"的说明，不是每个数都加一行页脚。"""
    page = Page(
        page_id="page-lighting",
        domain="lighting",
        cards=[LIGHTING_CARD],
        locked_text_ids=["DISCLAIM_P1"],
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=[page], package=PACKAGE))
    )

    assert "gate-provenance-annotation-missing" not in {v.check for v in book.violations}


# ---------------------------------------------------------------------------
# 两层模型（规则 1.9，v2.8）：引用某一项走完单元 → 装配 → 册级
# ---------------------------------------------------------------------------

ITEM_CARD = Card(
    thesis="沙发旁读书那块得比平时亮。",
    body=(
        "客厅平时待着按 {lkp-illuminance-living.general} lx 就够，"
        "沙发旁读书那块单独提到 {lkp-illuminance-living.reading} lx。"
    ),
    number_refs=["lkp-illuminance-living.general", "lkp-illuminance-living.reading"],
)


async def test_item_reference_flows_from_unit_to_book() -> None:
    """立案样本本尊跑通整条链路：六轮真跑 0/6 想说而说不出的那句话，现在有合法写法了。

    册级按**落点段**解析（引其中一项不改变它是哪条落点），故标注要求集与老形态一致。
    """
    lighting_unit = await compose(ScriptedWriter([[ITEM_CARD]]), domain="lighting")
    assert lighting_unit.verdict == "ok"
    assert lighting_unit.required_provenance == []  # 过门落点无需标注，与整条引用同结果

    ergonomics_unit = await compose(ScriptedWriter([[GOOD_CARD]]))
    assembled = PageAssembleResult.model_validate(
        await activities.assemble_report_pages(
            PageAssembleRequest(units=[lighting_unit, ergonomics_unit])
        )
    )
    book = BookCheckResult.model_validate(
        await activities.check_report_book(BookCheckRequest(pages=assembled.pages, package=PACKAGE))
    )

    assert book.verdict == "ok"


async def test_derivation_sees_item_names_of_the_domain_anchors() -> None:
    """项名随落点题名下发到推导步（值仍然不下发）——"分场景讲"是这一步的决定。"""
    deriver = ScriptedDeriver([NarrativeClaim(claim="客厅平时待着和读书是两回事", anchors=[])])
    await compose(ScriptedWriter([[LIGHTING_CARD]]), domain="lighting", deriver=deriver)

    brief = deriver.seen_requests[0].anchors[0]
    assert brief.items == ["general", "reading"]
    assert "value" not in brief.model_dump()
