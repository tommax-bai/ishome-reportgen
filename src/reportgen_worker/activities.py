"""Temporal activities：所有 IO 与重计算收口在此。

注册名唯一真源：ishome-contracts `activities/registry.md`（#11-13），**只增不改**。
命名规则（规范 §2.4）：注册名 kebab-case 显式声明；函数名同词 snake_case 动词前置。

成文线纪律（图 v0.2 §0/§3，逐条兑现）：
- 输入=报告数据包（自包含，不回查任何库；匿名由 models extra=forbid 结构性保证）；
- **dom- 为参数不拆 activity**（规则 5.0c 一台引擎 N 域资产包）；
- 降档门禁（规则 4.10）先于写作执行：数据包自身违约直接 failed，不烧 LLM 调用；
- 出口过检不合格 → 违规清单作反馈重写 ≤max_rewrites 轮 → 仍不过 verdict=failed 上抛，
  绝不静默假成功。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from temporalio import activity

from reportgen_worker.gate import (
    backed_predicates,
    collect_banned_terms,
    run_package_gate,
    run_unit_gate,
    unbacked_predicates,
)
from reportgen_worker.models import (
    BookCheckRequest,
    BookCheckResult,
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


# 测试注入点：monkeypatch 本工厂即可替换写作器（activity 入参保持纯数据）
writer_factory: Callable[[], CardWriter] = default_writer_factory


@activity.defn(name="report-unit-compose")
async def compose_report_unit(request: UnitComposeRequest) -> ActivityResult:
    """单元成文：叙事推导+卡片写作（LLM）→ 出口过检·规则层 → 重写循环 → ok/failed verdict。"""
    domain, package = request.domain, request.package

    def failed(violations: list[Violation], rewrites: int = 0) -> ActivityResult:
        return UnitComposeResult(
            verdict="failed",
            domain=domain,
            violations=violations,
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
            return UnitComposeResult(
                verdict="ok",
                domain=domain,
                cards=cards,
                rewrites_used=attempt,
                releases=package.releases,
            ).model_dump()
    return failed(feedback, rewrites=request.max_rewrites)


@activity.defn(name="report-page-assemble")
async def assemble_report_pages(request: PageAssembleRequest) -> ActivityResult:
    """页面装配（确定性，唯一知道"页"的节点）：首版按域成页；pt- 页型库编译后接管 page_type。"""
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
        Page(page_id=f"page-{unit.domain}", domain=unit.domain, cards=unit.cards)
        for unit in sorted(request.units, key=lambda u: u.domain)
    ]
    return PageAssembleResult(verdict="ok", pages=pages).model_dump()


@activity.defn(name="report-book-check")
async def check_report_book(request: BookCheckRequest) -> ActivityResult:
    """册级校验（渲染前）：首版为结构完整性——域齐/页非空/全册数字引用可解析；
    册级 cr-（branch-cap/set-closure/promise-fulfilled…）随 release 判据编译后物化执行。"""
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
    for page in request.pages:
        if not page.cards:
            violations.append(Violation(check="gate-empty-page", detail=f"{page.page_id} 空页"))
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
