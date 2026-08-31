"""质量基线跑法：拿一档拟真上游数据包（:mod:`tests.fixtures`）连跑 N 次，出可比的数。

    uv run python -m tests.quality_baseline --tier partial-gaps --runs 5

## 它答的是哪个问题

"这次改动到底有没有用"。以前答不出：每次测都拿真求值线当场算的包当卷子，release 一变、
种子一改卷子就换了，"整册四跑一成"这种数**跨天不可比**。这里把卷子定死（三档 fixture +
定死的 ``evaluatedOn``），跑法定死，于是**同一档跨天的成册率可以直接比**。
每次跑都打印卷子指纹（``--dry-run`` 也打），指纹一样才算同一张卷子。

## 它做什么、不做什么

做：选档 → 过一遍生产方契约守卫（``run_package_gate``）→ 派发给编排侧 → 等收口 →
打印判读三件套（``verdict`` / ``book_key`` / ``failed_domains``）与两个旋钮的实际用量
（``rewrite_rounds_by_domain`` 章内重写 / ``unit_retries_by_domain`` 整章重开）→ 汇总。

**不做**：不写库、不发 release、不改任何生产配置、不碰资产回路。它只是个测量工具，
fixture 是测试替身不是生产补丁——上游没给就是没给，生产链路上该响亮失败然后去找上游
（见 :mod:`tests.fixtures` 的两条硬约束）。

## 为什么派发用 HTTP、收口用 Temporal

派发走编排侧既有入口 ``POST /api/v1/genpipe/reports``——**同一条缝、同一套校验**，
这条路上伪造的数据会被机检逐条挑出来（真跑验证过）。绕开它自建一条派发路，测的就不是
生产那条线了。收口没有 HTTP 查询面（编排侧只有 batch 有状态查询，报告的状态真相在
``svc_project``，规则 8.1 禁第二台状态机），所以结果直接从 Temporal 取。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import time
from collections import Counter
from typing import Any, cast

import httpx
from temporalio.client import Client, WorkflowFailureError

from reportgen_worker.gate import run_package_gate
from reportgen_worker.models import ReportDataPackage
from tests.fixtures import TIER_LABELS, TIERS, Tier, load_package_json

DEFAULT_GENPIPE_URL = "http://127.0.0.1:8104"
DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
GENPIPE_NAMESPACE = "genpipe"

DID_NOT_RUN = "did-not-run"
"""这一跑压根没跑起来（派发失败 / 等超时 / workflow 自己炸了）。

不是 ``ReportStage`` 里的任何一档——那四档说的是"报告线走到哪儿失败的"，
而这个说的是"报告线没被问到"。占着分母，但在汇总里单独列，不算进"哪一章不行"。
"""

_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIER_CODE: dict[Tier, str] = {"full": "FULL", "partial-gaps": "PART", "mostly-gaps": "GAPS"}


def make_report_id(tier: Tier) -> str:
    """26 位 ULID 形态的定址 id，**头 10 位人眼可读地写着"这是 fixture 跑的"**。

    形态 ``01MOCK<档码><时间戳><随机>``。为什么不发一个纯随机 ULID：这个 id 会变成对象存储
    里的键（``reports/{report_id}/book.html``）和 workflow id，事后翻账时它是唯一的线索——
    一眼分得出"这册是拿测试替身跑的"，就不会有人把它当成一份真报告。
    """
    stamp = _base32(int(time.time() * 1000), width=10)
    tail = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"01MOCK{_TIER_CODE[tier]}{stamp}{tail}"


def _base32(value: int, width: int) -> str:
    out = ""
    for _ in range(width):
        value, rem = divmod(value, len(_ID_ALPHABET))
        out = _ID_ALPHABET[rem] + out
    return out


def fingerprint(package: dict[str, Any]) -> str:
    """卷子指纹：包内容一个字节变了它就变。跨天比数之前先比这个。"""
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def guard_package(package: ReportDataPackage) -> list[str]:
    """派发前先过一遍**生产方契约守卫**：fixture 自己违约的，不该烧一次 LLM 调用才发现。

    跑的就是 activity 写作前跑的那一道（``run_package_gate``），不另写一套判据——
    这里若和生产侧判得不一样，测出来的质量就不是生产那条线的质量。
    """
    return [
        f"[{v.check}] {v.detail}"
        for domain in package.domains
        for v in run_package_gate(domain, package)
    ]


async def dispatch_and_wait(
    spec: dict[str, Any],
    genpipe_url: str,
    temporal_address: str,
    timeout_s: float,
) -> dict[str, Any]:
    """派发一次并等它收口，返回编排侧的 ``ReportComposeResult``（原样 dict）。"""
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(f"{genpipe_url}/api/v1/genpipe/reports", json=spec)
        resp.raise_for_status()
        receipt = cast(dict[str, Any], resp.json())
    # workflow id 由 report_id 确定性推得（编排侧 ``report-compose-{report_id}``）：回执只用来
    # 确认派发受理了。回执字段名两种形态都认（别名口径不归本仓管），认不出就用推得的那个。
    derived = f"report-compose-{spec['report_id']}"
    workflow_id = str(receipt.get("workflow_id") or receipt.get("workflowId") or derived)
    client = await Client.connect(temporal_address, namespace=GENPIPE_NAMESPACE)
    handle = client.get_workflow_handle(workflow_id)
    return cast(dict[str, Any], await asyncio.wait_for(handle.result(), timeout=timeout_s))


def print_verdict(index: int, runs: int, result: dict[str, Any], elapsed_s: float) -> None:
    """一跑的判读三件套 + 两个旋钮的实际用量。"""
    verdict = result.get("verdict")
    failed = result.get("failed_domains") or []
    print(f"  第 {index}/{runs} 跑（{elapsed_s:.0f}s）：verdict={verdict}", end="")
    print(f" | 册={result.get('book_key') or '（没出）'}", end="")
    print(f" | 失败章={'、'.join(failed) if failed else '无'}")
    if result.get("failed_stage"):
        print(f"    停在：{result['failed_stage']}")
    print(f"    章内重写：{result.get('rewrite_rounds_by_domain') or '{}'}")
    print(f"    整章重开：{result.get('unit_retries_by_domain') or '{}'}")
    for unit in result.get("failed_units") or []:
        for violation in unit.get("violations") or []:
            detail = str(violation.get("detail", ""))[:120]
            print(f"    * {unit.get('domain')} [{violation.get('check')}] {detail}")


def print_summary(tier: Tier, results: list[dict[str, Any]]) -> None:
    """连跑汇总——**这才是"可比基线"要的东西**：成册率 + 各章失败次数。

    单跑的数没有意义：同一份输入连跑五次，dom-budget 在第 1/3/4 跑失败、第 2/5 跑通过
    （2026-08-31 实测）——章失败是随机的不是必然的，一跑一个数只是在读噪声。
    """
    runs = len(results)
    booked = [r for r in results if r.get("verdict") == "ok"]
    failed_by_domain: Counter[str] = Counter()
    rewrites_by_domain: Counter[str] = Counter()
    retries_by_domain: Counter[str] = Counter()
    for result in results:
        failed_by_domain.update(result.get("failed_domains") or [])
        rewrites_by_domain.update(result.get("rewrite_rounds_by_domain") or {})
        retries_by_domain.update(result.get("unit_retries_by_domain") or {})
    stalled = [r for r in results if r.get("failed_stage") == DID_NOT_RUN]
    rate = f"{len(booked) / runs:.0%}" if runs else "—"
    print(f"\n=== {TIER_LABELS[tier]}（{tier}）× {runs} 跑 ===")
    print(f"成册率：{len(booked)}/{runs}（{rate}）")
    if stalled:
        print(f"其中 {len(stalled)} 跑压根没跑起来（环境问题，不是报告线判的失败）")
    print(f"各章失败次数：{_counter_line(failed_by_domain)}")
    print(f"各章章内重写轮数合计：{_counter_line(rewrites_by_domain)}")
    print(f"各章整章重开次数合计：{_counter_line(retries_by_domain)}")


def _counter_line(counter: Counter[str]) -> str:
    if not counter:
        return "无"
    return "、".join(f"{name} {count}" for name, count in sorted(counter.items()))


def build_spec(tier: Tier, args: argparse.Namespace) -> tuple[dict[str, Any], ReportDataPackage]:
    package_json = load_package_json(tier)
    package = ReportDataPackage.model_validate(package_json)
    spec: dict[str, Any] = {
        "report_id": make_report_id(tier),
        # 派发集**不从包里读**（编排侧把包当不透明载荷）：这里显式按包内 domains 传，
        # 与求值线的调用方式一致；两边不符时由 activity 出 gate-domain-not-in-package。
        "domains": list(package.domains),
        "package": package_json,
        "max_rewrites": args.max_rewrites,
        "max_unit_retries": args.max_unit_retries,
    }
    return spec, package


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.quality_baseline",
        description="拿一档拟真上游数据包连跑 N 次，出可比的成册率基线（测量工具，不碰生产数据）",
    )
    parser.add_argument("--tier", choices=list(TIERS), default="partial-gaps", help="选哪一档")
    parser.add_argument("--runs", type=int, default=1, help="连跑几次（成册率的分母）")
    parser.add_argument("--dry-run", action="store_true", help="只做参数解析与派发前守卫，不真派发")
    parser.add_argument("--genpipe-url", default=DEFAULT_GENPIPE_URL, help="编排侧入站面")
    parser.add_argument("--temporal-address", default=DEFAULT_TEMPORAL_ADDRESS, help="收口从这儿取")
    parser.add_argument("--timeout", type=float, default=1800.0, help="单跑等收口上限（秒）")
    # 两个旋钮**默认不动**（与编排侧 ReportComposeSpec 默认同值）：基线的旋钮一动，
    # 前后两天的数就不可比了。允许覆写是为了做对照实验，故实际取值一律打进抬头随结果留痕。
    parser.add_argument("--max-rewrites", type=int, default=2, help="章内重写轮数上限")
    parser.add_argument("--max-unit-retries", type=int, default=2, help="整章重开次数上限")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    tier = cast(Tier, args.tier)
    spec, package = build_spec(tier, args)
    package_json = cast(dict[str, Any], spec["package"])
    print(f"档：{TIER_LABELS[tier]}（{tier}）")
    print(f"卷子指纹：{fingerprint(package_json)}（跨天比数前先比这个）")
    print(
        f"落点 {len(package.anchors)} 条 | 缺口 {len(package.gaps)} 条 | 章 {len(package.domains)}"
    )
    print(f"求值基准日：{package.evaluated_on} | 权益档：{package.entitlement}")
    print(f"旋钮：章内重写 ≤{args.max_rewrites} | 整章重开 ≤{args.max_unit_retries}")

    breaches = guard_package(package)
    if breaches:
        # 响亮失败：fixture 自己违约就别派发了，烧 LLM 也测不出报告线的质量
        print("\n派发前守卫不过——这份 fixture 自己违约，先修 fixture：")
        for breach in breaches:
            print(f"  * {breach}")
        return 1
    print("派发前守卫：过（run_package_gate 六章零违规）")

    if args.dry_run:
        print(f"\n--dry-run：不派发。真跑会 POST 到 {args.genpipe_url}/api/v1/genpipe/reports")
        print(f"  report_id（本次示例）：{spec['report_id']}")
        print(f"  domains：{'、'.join(package.domains)}")
        print(
            f"  body 大小：{len(json.dumps(spec, ensure_ascii=False).encode('utf-8')) // 1024} KB"
        )
        return 0

    results: list[dict[str, Any]] = []
    for index in range(1, args.runs + 1):
        if index > 1:  # 每跑一份新的 id：workflow id 由它推得，重复派发会 409
            spec, package = build_spec(tier, args)
        print(f"\n派发 {spec['report_id']} …")
        started = time.monotonic()
        try:
            result = await dispatch_and_wait(
                spec, args.genpipe_url, args.temporal_address, args.timeout
            )
        except (TimeoutError, httpx.HTTPError, WorkflowFailureError) as err:
            # 跑没跑成也要记：**算进分母**而不是悄悄跳过，否则成册率会被"没跑成"抬高。
            # 但它与"报告线判 failed"是两回事，故单独标一个 stage，汇总里分开报——
            # 把环境问题混进质量数，读出来的就是一个不知道在说什么的比例。
            print(f"  第 {index}/{args.runs} 跑：没跑成——{type(err).__name__}: {err}")
            results.append({"verdict": "failed", "failed_stage": DID_NOT_RUN})
            continue
        results.append(result)
        print_verdict(index, args.runs, result, time.monotonic() - started)

    print_summary(tier, results)
    return 0 if all(r.get("verdict") == "ok" for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
