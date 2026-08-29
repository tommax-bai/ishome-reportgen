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

from collections.abc import Callable, Coroutine
from typing import Any

from temporalio import activity

from reportgen_worker.gate import (
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
    BookCheckRequest,
    BookCheckResult,
    JudgeObservation,
    Page,
    PageAssembleRequest,
    PageAssembleResult,
    UnitComposeRequest,
    UnitComposeResult,
    Violation,
)
from reportgen_worker.writer import CardWriter, LlmCardWriter, WriterOutputError, WriterRequest

ActivityResult = dict[str, Any]


def default_writer_factory() -> CardWriter:
    return LlmCardWriter()


def default_judge_factory() -> Judge:
    return LlmJudge()


# 测试注入点：monkeypatch 本工厂即可替换写作器/判官（activity 入参保持纯数据）
writer_factory: Callable[[], CardWriter] = default_writer_factory
judge_factory: Callable[[], Judge] = default_judge_factory


@activity.defn(name="report-unit-compose")
async def compose_report_unit(request: UnitComposeRequest) -> ActivityResult:
    """单元成文：卡片写作（LLM）→ 出口过检·规则层 → **判官层** → 重写循环 → ok/failed verdict。

    判官在单元子图内、规则层之后（图 v0.2 §3），**不注册新 activity**——它是这一步的一部分，
    不是一次派发。只在规则层放行后才跑：规则层没过的稿子还要重写，先烧一次判官调用没有意义。
    """
    domain, package = request.domain, request.package
    observations: list[JudgeObservation] = []

    def failed(violations: list[Violation], rewrites: int = 0) -> ActivityResult:
        return UnitComposeResult(
            verdict="failed",
            domain=domain,
            violations=violations,
            observations=observations,
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
    checks = judge_checks(domain, package)
    blocking = blocking_check_ids(domain, package)
    feedback: list[Violation] = []
    for attempt in range(request.max_rewrites + 1):
        writer_request = WriterRequest(
            domain=domain,
            persona=personas[0],
            anchors=anchors,
            gaps=package.gaps,
            profile=package.anonymous_profile,
            banned_terms=collect_banned_terms(domain, package),
            backed_predicates=backed_predicates(domain, package),
            unbacked_predicates=unbacked_predicates(domain, package),
            feedback=feedback,
            attempt=attempt,
        )
        try:
            cards = await writer.write(writer_request)
        except WriterOutputError as e:
            feedback = [Violation(check="gate-writer-output-invalid", detail=str(e))]
            continue
        # 空卡片组不算过检：run_unit_gate 逐卡片跑，没有卡片自然没有违规——"什么都不写"会成为
        # 绕过全部门禁最省事的路径（真跑 2026-08-28 即出现：全域降档后模型交了空数组）。
        # 失败必须在源头响亮报出，不靠下游装配/册检兜住（图 v0.2 §3 绝不静默假成功）。
        feedback = run_unit_gate(cards, domain, package)
        if not feedback and not cards:
            feedback = [
                Violation(check="gate-empty-composition", detail=f"{domain} 未产出任何卡片")
            ]
        if not feedback:
            # 出口过检·判官层（图 v0.2 §3 第二道）：规则层放行后才问判官。
            # 观察态（规则 4.17 门禁二）= blocking 为空 → 判出什么都只记录，verdict 不受影响；
            # 某条判据经观察期转正为 active 后，同一份观察即成为重写反馈——**开关在数据不在这里**。
            observations = await observe(
                judge,
                JudgeRequest(
                    domain=domain,
                    cards=cards,
                    checks=checks,
                    profile=package.anonymous_profile,
                    anchors=anchors,
                ),
            )
            blocked = [o for o in observations if o.check in blocking]
            if not blocked:
                return UnitComposeResult(
                    verdict="ok",
                    domain=domain,
                    cards=cards,
                    observations=observations,
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
    withheld_ids = {w.lkp_id for w in request.package.withheld_anchors}
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
        referenced = sorted({ref for card in page.cards for ref in card.number_refs})
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
                # 隐藏落点的最后一道：单元级已拦，册级再拦一次（渲染前是最后能停下来的地方）
                if ref in withheld_ids:
                    violations.append(
                        Violation(
                            check="gate-withheld-anchor-referenced",
                            detail=f"{page.page_id} 引用 {ref}：该落点已按纪律隐藏（规则 4.10）",
                        )
                    )
                elif ref not in anchor_ids:
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
