"""Temporal activities：所有 IO 与重计算收口在此。

注册名唯一真源：ishome-contracts `activities/registry.md`（#11-13），**只增不改**。
命名规则（规范 §2.4）：注册名 kebab-case 显式声明；函数名同词 snake_case 动词前置。

成文线纪律（图 v0.2 §0/§3，逐条兑现）：
- 输入=报告数据包（自包含，不回查任何库；匿名由 models extra=forbid 结构性保证）；
- **dom- 为参数不拆 activity**（规则 5.0c 一台引擎 N 域资产包）；
- 降档门禁（规则 4.10）先于写作执行：数据包自身违约直接 failed，不烧 LLM 调用；
- 出口过检不合格 → 违规清单作反馈重写 ≤max_rewrites 轮 → 仍不过 verdict=failed 上抛，
  绝不静默假成功；
- 出口过检两道：规则层（gate，确定性）→ 判官层（judge，语义，观察态只记录不拦截）。
  **判官不注册新 activity**——它在单元子图内（图 v0.2 §3），是这一步的一部分不是一次派发。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from temporalio import activity

from reportgen_worker.deriver import (
    DeriveRequest,
    DeriverOutputError,
    LlmNarrativeDeriver,
    NarrativeDeriver,
)
from reportgen_worker.gate import (
    anchor_id_of,
    annotation_required_anchors,
    backed_predicates,
    collect_banned_terms,
    provenance_note,
    required_provenance_notes,
    run_package_gate,
    run_unit_gate,
    unbacked_predicates,
)
from reportgen_worker.judge import (
    Judge,
    JudgeRequest,
    LlmJudge,
    blocking_check_ids,
    judge_checks,
    observe,
)
from reportgen_worker.models import (
    AnchorBrief,
    BookCheckRequest,
    BookCheckResult,
    Card,
    JudgeObservation,
    JudgeRun,
    NarrativeClaim,
    Page,
    PageAssembleRequest,
    PageAssembleResult,
    ReleaseRef,
    UnitComposeRequest,
    UnitComposeResult,
    Violation,
)
from reportgen_worker.writer import CardWriter, LlmCardWriter, WriterOutputError, WriterRequest

ActivityResult = dict[str, Any]

logger = logging.getLogger(__name__)

JUDGE_LEDGER_ENV = "REPORTGEN_JUDGE_LEDGER"
"""判官观察台账的落地路径（环境变量，未设＝不落地）。

**为什么是一个文件而不是一张表**：观察数据的用途只有一个——规则 4.17 门禁二"跑 N 份看触发率"，
它是**追加、只读、聚合时全量扫**的时序记录，没有查询模式、没有并发写者、没有关联查询。
按红线"配置只放数据、不建通用平台"，此处不建库、不起服务、不发明 schema：一行一次送审的 JSON。

**长期载体不是它**：两条线接通后，`judge_run` 随 activity 结果回到编排侧，由持有状态的那一侧落库
（成文线不回查任何库，也不该持有跨份状态）。本台账是**冷启动期**唯一能查到触发率的地方——
在它存在之前，判官跑过什么只活在日志里，而裁决⑨ 要的是数据。

一行 = 一次送审。activity 重试会追加新行，那**不是重复记账**：重试是新的一次 LLM 送审，
它本来就该被记成新的一份。
"""


def append_judge_ledger(domain: str, run: JudgeRun | None, releases: list[ReleaseRef]) -> None:
    """把一次送审的台账追加进观察记录（未配置路径即跳过）。

    落 activity 层是分层要求：IO 全部收口在这里（判官层保持纯函数 + 一次网关调用）。
    **写失败只记一条日志**：台账写不进去是观察数据的损失，不是这份内容的事故——同"判官不阻塞"
    的同一条理由，把可用性问题变成质量问题是反的。
    """
    path = os.environ.get(JUDGE_LEDGER_ENV)
    if not path or run is None:
        return
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "domain": domain,
        "releases": [r.release_tag for r in releases],
        **run.model_dump(),
    }
    try:
        with open(path, "a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("判官台账写入失败（不影响 verdict）：%s", path, exc_info=True)


def default_writer_factory() -> CardWriter:
    return LlmCardWriter()


def default_judge_factory() -> Judge:
    return LlmJudge()


def default_deriver_factory() -> NarrativeDeriver:
    return LlmNarrativeDeriver()


# 测试注入点：monkeypatch 本工厂即可替换写作器/判官/推导器（activity 入参保持纯数据）
writer_factory: Callable[[], CardWriter] = default_writer_factory
judge_factory: Callable[[], Judge] = default_judge_factory
deriver_factory: Callable[[], NarrativeDeriver] = default_deriver_factory


@activity.defn(name="report-unit-compose")
async def compose_report_unit(request: UnitComposeRequest) -> ActivityResult:
    """单元成文：**叙事推导** → 卡片写作（LLM）→ 出口过检·规则层 → **判官层** → 重写循环 → verdict。

    推导与写作是两次调用两个语域（图 v0.2 §3）：先定"这一域讲哪几件事"（内部语域、看不见落点的值），
    再按主张写卡（客户语域、数字只经占位引用）。塌成一步的代价实测过——落点清单成了唯一结构化输入，
    模型顺着它一一对应，23 条落点写成 23 张"念数字"的卡（2026-08-29 真跑）。

    判官在单元子图内、规则层之后（图 v0.2 §3），**不注册新 activity**——它是这一步的一部分，
    不是一次派发。只在规则层放行后才跑：规则层没过的稿子还要重写，先烧一次判官调用没有意义。
    """
    domain, package = request.domain, request.package
    observations: list[JudgeObservation] = []
    judge_run: JudgeRun | None = None
    claims: list[NarrativeClaim] = []
    derive_feedback: list[str] = []

    def failed(violations: list[Violation], rewrites: int = 0) -> ActivityResult:
        return UnitComposeResult(
            verdict="failed",
            domain=domain,
            violations=violations,
            # 主张随失败结果一并上抛：卡片垮了要分得清是"讲什么"没定好还是"怎么写"没写好
            claims=claims,
            observations=observations,
            # 判官跑过就记，哪怕这一稿后来被打回：观察态的"份"是**送审一次**，不是"发出去一份"
            judge_run=judge_run,
            rewrites_used=rewrites,
            releases=package.releases,
        ).model_dump()

    if domain not in package.domains:
        return failed(
            [Violation(check="gate-domain-not-in-package", detail=f"{domain} 不在数据包内")]
        )
    personas = package.personas_by_domain.get(domain, [])
    if not personas:
        return failed(
            [
                Violation(
                    check="gate-persona-missing", detail=f"{domain} 无 persona 载荷，无语域可依"
                )
            ]
        )
    anchors = package.domain_anchors(domain)
    if not anchors:
        return failed(
            [
                Violation(
                    check="gate-no-anchors",
                    detail=f"{domain} 无落点对象——四件不齐不发布（规则 5.0b）",
                )
            ]
        )
    # 生产方契约先过一遍：数据包自身违约（如隐藏落点却带值下发）不必烧一次 LLM 调用才发现
    package_violations = run_package_gate(domain, package)
    if package_violations:
        return failed(package_violations)

    writer = writer_factory()
    judge = judge_factory()
    deriver = deriver_factory()
    checks = judge_checks(domain, package)
    blocking = blocking_check_ids(domain, package)
    feedback: list[Violation] = []
    previous_cards: list[Card] = []
    for attempt in range(request.max_rewrites + 1):
        # 叙事推导（图 v0.2 §3 第一步）：只跑一次，重写循环重跑的是写作不是推导——
        # 打回的理由是卡片怎么写，不是这一章该讲什么。推导本身失败才重来（网关抖动/输出不可解析）。
        if not claims:
            try:
                claims = await deriver.derive(
                    DeriveRequest(
                        domain=domain,
                        identity=personas[0].identity,
                        anchors=[AnchorBrief.of(a) for a in anchors],
                        gaps=package.gaps,
                        profile=package.anonymous_profile,
                        banned_terms=collect_banned_terms(domain, package),
                        triggered_rules=package.domain_triggered_rules(domain),
                        backed_predicates=backed_predicates(domain, package),
                        unbacked_predicates=unbacked_predicates(domain, package),
                        feedback=derive_feedback,
                    )
                )
            except DeriverOutputError as e:
                # 不退回"没有主张照样写"：那正是这一步要修的老形态，静默退回＝静默假成功。
                # 打回理由回流进下一次推导——不告诉它哪儿错了，它只会把同一句再写一遍。
                derive_feedback = [str(e)]
                feedback = [Violation(check="gate-narrative-derive-failed", detail=str(e))]
                continue
        writer_request = WriterRequest(
            domain=domain,
            persona=personas[0],
            claims=claims,
            anchors=anchors,
            gaps=package.gaps,
            profile=package.anonymous_profile,
            banned_terms=collect_banned_terms(domain, package),
            backed_predicates=backed_predicates(domain, package),
            unbacked_predicates=unbacked_predicates(domain, package),
            feedback=feedback,
            # 上一稿原样带回（用户裁决 2026-08-30）：不带回等于让它"逐条修正"一份看不见的稿子
            previous_cards=previous_cards,
            attempt=attempt,
        )
        try:
            cards = await writer.write(writer_request)
        except WriterOutputError as e:
            feedback = [Violation(check="gate-writer-output-invalid", detail=str(e))]
            previous_cards = []  # 这一稿没解析出来，没有原稿可带回
            continue
        previous_cards = cards
        # 空卡片组不算过检：run_unit_gate 逐卡片跑，没有卡片自然没有违规——"什么都不写"会成为
        # 绕过全部门禁最省事的路径（真跑 2026-08-28 即出现：全域降档后模型交了空数组）。
        # 失败必须在源头响亮报出，不靠下游装配/册检兜住（图 v0.2 §3 绝不静默假成功）。
        feedback = run_unit_gate(cards, domain, package, claims)
        if not feedback and not cards:
            feedback = [
                Violation(check="gate-empty-composition", detail=f"{domain} 未产出任何卡片")
            ]
        if not feedback:
            # 出口过检·判官层（图 v0.2 §3 第二道）：规则层放行后才问判官。
            # 观察态（规则 4.17 门禁二）= blocking 为空 → 判出什么都只记录，verdict 不受影响；
            # 某条判据经观察期转正为 active 后，同一份观察即成为重写反馈——**开关在数据不在这里**。
            observations, judge_run = await observe(
                judge,
                JudgeRequest(
                    domain=domain,
                    cards=cards,
                    checks=checks,
                    profile=package.anonymous_profile,
                    anchors=anchors,
                ),
            )
            append_judge_ledger(domain, judge_run, package.releases)
            blocked = [o for o in observations if o.check in blocking]
            if not blocked:
                return UnitComposeResult(
                    verdict="ok",
                    domain=domain,
                    cards=cards,
                    claims=claims,
                    observations=observations,
                    judge_run=judge_run,
                    rewrites_used=attempt,
                    releases=package.releases,
                    # 锁定文案只透传不生产：本域要挂哪几条由求值线随包给定（规则 2.4 零生成），
                    # 单元把它交给装配层去挂——写作器全程不知道有这回事。
                    required_locked_texts=sorted(package.locked_texts_by_domain.get(domain, [])),
                    # 依据标注同理（规则 4.10c 标注必挂，v2.4）：内容全部来自落点的 provenance，
                    # 单元只按"这一稿实际引用了哪几个落点"做投影——引用面只有写完卡片才知道，
                    # 故要求集必须在这里算，不能像锁定文案那样随包给定。
                    required_provenance=required_provenance_notes(cards, domain, package),
                ).model_dump()
            feedback = [
                Violation(check=o.check, detail=f"判官命中「{o.quote}」：{o.why}") for o in blocked
            ]
    return failed(feedback, rewrites=request.max_rewrites)


@activity.defn(name="report-page-assemble")
async def assemble_report_pages(request: PageAssembleRequest) -> ActivityResult:
    """页面装配（确定性，唯一知道"页"的节点）：首版按域成页；pt- 页型库编译后接管 page_type。

    **锁定文案在此挂载**（规则 2.4 gen-locked：引用 ID 直接渲染、零生成）：装配层按产物要求把
    ID 挂上页，不拼接正文、不选择挂哪条——要挂哪几条是求值线的裁决，随包下发经单元透传到这里。
    挂载与校验分层（挂在装配、验在册级）不是重复：册级读的是数据包这个独立来源，两边不一致
    才报得出"该挂没挂"，装配自验自己等于什么都没验。

    **依据标注在此一并挂上**（规则 4.10c 标注必挂，v2.4）：同一条挂载路径、同一条分层理由——
    单元投影出要求，装配挂上页，册级拿数据包重算一遍比对。标注是 v2.4 取消隐藏档后纪律的落点，
    拦截点从"说出来"移到"不标就说"。
    """
    failed_units = [u for u in request.units if u.verdict != "ok"]
    if failed_units:
        return PageAssembleResult(
            verdict="failed",
            violations=[
                Violation(check="gate-unit-failed", detail=f"{u.domain} 单元 failed，不得进入装配")
                for u in failed_units
            ],
        ).model_dump()
    pages = [
        Page(
            page_id=f"page-{unit.domain}",
            domain=unit.domain,
            cards=unit.cards,
            locked_text_ids=list(unit.required_locked_texts),
            provenance_notes=list(unit.required_provenance),
        )
        for unit in sorted(request.units, key=lambda u: u.domain)
    ]
    return PageAssembleResult(verdict="ok", pages=pages).model_dump()


@activity.defn(name="report-book-check")
async def check_report_book(request: BookCheckRequest) -> ActivityResult:
    """册级校验（渲染前）：首版为结构完整性——域齐/页非空/全册数字引用可解析/**锁定文案齐**/
    **依据标注齐**；
    册级 cr-（branch-cap/set-closure/promise-fulfilled…）随 release 判据编译后物化执行。

    锁定文案齐不齐是**确定性**校验，故落规则层不落判官层：要求集在数据包（``locked_texts_by_domain``），
    挂载集在页上，两个集合一比即可判——规则 5.15"DISCLAIM_PRICE 必挂"这类条文此前在成文线是空文，
    因为既没有 ID 枚举也没有载体。注：包内 ``checks`` 里的 ``presence_require`` 判据（如
    ``cr-budget-disclaimer``）**不在此层执行**：它的 ``requirement`` 是给人读的自然语言，
    从中抠 ID 等于发明一套表达式语法（禁止项）；要挂哪条以数据包的锁定清单为唯一口径。

    **依据标注比对（规则 4.10c 标注必挂，v2.4）**：页上引用了未过门或已过期的落点，同页就必须有它的
    标注——这是 v2.3 隐藏禁令的等价物，拦截点从"说出来"移到"不标就说"，且是**确定性判据**（要求集
    从数据包的 provenance 重算，挂载集在页上，两集合一比即可判），故落规则层不落判官层。
    比对不止"有没有"还比"对不对"：标注内容与包内 provenance 逐字段核，中间层改写过的标注等于没标——
    业主据以判断这个数有多硬的，必须是求值线给的那份事实。
    """
    violations: list[Violation] = []
    page_domains = [p.domain for p in request.pages]
    if len(set(page_domains)) != len(page_domains):
        violations.append(
            Violation(check="gate-duplicate-domain-page", detail=f"域重复：{page_domains}")
        )
    for wanted in request.package.domains:
        if wanted not in page_domains:
            violations.append(Violation(check="gate-domain-page-missing", detail=f"{wanted} 无页"))
    anchor_ids = {a.lkp_id for a in request.package.anchors}
    annotation_required = annotation_required_anchors(request.package)
    for page in request.pages:
        if not page.cards:
            violations.append(Violation(check="gate-empty-page", detail=f"{page.page_id} 空页"))
        mounted = set(page.locked_text_ids)
        required = sorted(request.package.locked_texts_by_domain.get(page.domain, []))
        missing = [t for t in required if t not in mounted]
        if missing:
            violations.append(
                Violation(
                    check="gate-locked-text-missing",
                    detail=(
                        f"{page.page_id} 缺锁定文案 {missing}——本产物要求必挂"
                        "（规范 §7 锁定文案全集；造价章即规则 5.15 的 DISCLAIM_PRICE 必挂）"
                    ),
                )
            )
        mounted_notes = {note.lkp_id: note for note in page.provenance_notes}
        # 记号先取落点段（规则 1.9 两层模型，v2.8）：标注是整条落点的属性（这个数从哪来、
        # 什么时候取的），引其中一项不改变它的来源——按项比对只会把同一份标注要求算成几份。
        referenced = sorted({anchor_id_of(ref) for card in page.cards for ref in card.number_refs})
        for ref in referenced:
            if ref not in annotation_required:
                continue
            note = mounted_notes.get(ref)
            if note is None:
                violations.append(
                    Violation(
                        check="gate-provenance-annotation-missing",
                        detail=(
                            f"{page.page_id} 引用 {ref} 却没有它的依据标注——未过门或已过期的落点"
                            "进正文必须同页标出来源与取数时间（规则 4.10c 标注必挂，v2.4）"
                        ),
                    )
                )
            elif note != provenance_note(annotation_required[ref]):
                violations.append(
                    Violation(
                        check="gate-provenance-note-mismatch",
                        detail=(
                            f"{page.page_id} 的 {ref} 标注与数据包对不上——标注是求值线给的事实，"
                            "装配与渲染只搬运不改写（规则 2.4 零生成）"
                        ),
                    )
                )
        for card in page.cards:
            for ref in card.number_refs:
                # 册级只判**落点段**解析得到与否：项名合不合法在单元层判过（那里有本域落点的
                # 全部项，话也说得具体），册级重判一遍只会把同一件事用两套话说两遍。
                if anchor_id_of(ref) not in anchor_ids:
                    violations.append(
                        Violation(
                            check="gate-number-ref-unresolved",
                            detail=f"{page.page_id} 引用 {ref} 无落点对象",
                        )
                    )
    return BookCheckResult(
        verdict="failed" if violations else "ok", violations=violations
    ).model_dump()


ACTIVITY_REGISTRY: dict[str, Callable[..., Coroutine[Any, Any, ActivityResult]]] = {
    "report-unit-compose": compose_report_unit,
    "report-page-assemble": assemble_report_pages,
    "report-book-check": check_report_book,
}
"""注册名 → 实现。键与 contracts 注册表逐字一致（tests/test_activity_registry.py 断言）。"""
