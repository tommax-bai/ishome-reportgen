"""质量基线跑法的自检：参数解析与 ``--dry-run`` 这条路走得通，**不真派发**。

真派发要烧 LLM、要占共用的本地栈，那一步由人手动跑（``--runs N``）。这里只保证
"命令敲下去不会因为脚本自己的毛病失败"：档选得出、包过得了守卫、id 形态对、汇总算得对。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from tests.fixtures import TIERS, Tier
from tests.quality_baseline import (
    _counter_line,
    build_spec,
    fingerprint,
    guard_package,
    main,
    make_report_id,
    parse_args,
    print_summary,
)


@pytest.mark.parametrize("tier", TIERS)
def test_dry_run_walks_the_whole_path_without_dispatching(tier: Tier, capsys: Any) -> None:
    """``--dry-run`` 返回 0 且不碰网络：选档 → 解析 → 守卫 → 打印抬头。"""
    assert main(["--tier", tier, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "卷子指纹" in out
    assert "派发前守卫：过" in out
    assert "--dry-run：不派发" in out


def test_default_tier_is_the_real_run_shape() -> None:
    """不写 ``--tier`` 就是部分缺档——今天真跑那份形状，基线从它起步。"""
    args = parse_args([])
    assert args.tier == "partial-gaps"
    assert args.runs == 1
    # 两个旋钮的缺省与编排侧 ReportComposeSpec 同值：基线的旋钮不许悄悄动
    assert (args.max_rewrites, args.max_unit_retries) == (2, 2)


def test_spec_carries_the_package_and_its_own_domains() -> None:
    args = parse_args(["--tier", "full"])
    spec, package = build_spec("full", args)
    assert spec["domains"] == package.domains
    assert spec["package"]["gaps"] == []
    assert spec["max_rewrites"] == 2
    assert guard_package(package) == []


def test_report_id_says_it_came_from_a_fixture() -> None:
    """26 位 ULID 形态，头 10 位人眼可读——事后翻账一眼分得出这册不是真报告。"""
    report_id = make_report_id("mostly-gaps")
    assert len(report_id) == 26
    assert report_id.startswith("01MOCKGAPS")
    assert make_report_id("full") != make_report_id("full")  # 每跑一份新 id，否则 409


def test_fingerprint_changes_with_the_exam() -> None:
    """指纹一样才算同一张卷子；档不同必然不同指纹。"""
    from tests.fixtures import load_package_json

    assert fingerprint(load_package_json("full")) == fingerprint(load_package_json("full"))
    assert fingerprint(load_package_json("full")) != fingerprint(load_package_json("mostly-gaps"))


def test_summary_counts_booked_runs_and_per_domain_failures(capsys: Any) -> None:
    """汇总是这个工具的产出：成册率 + 各章失败次数。"""
    results: list[dict[str, Any]] = [
        {"verdict": "ok", "book_key": "reports/x/book.html", "rewrite_rounds_by_domain": {"a": 1}},
        {
            "verdict": "failed",
            "failed_domains": ["budget"],
            "unit_retries_by_domain": {"budget": 2},
        },
        {"verdict": "failed", "failed_domains": ["budget", "lighting"]},
        {"verdict": "ok", "book_key": "reports/y/book.html"},
    ]
    print_summary("partial-gaps", results)
    out = capsys.readouterr().out
    assert "成册率：2/4（50%）" in out
    assert "budget 2、lighting 1" in out


def test_counter_line_reads_as_none_when_empty() -> None:
    assert _counter_line(Counter()) == "无"
    assert _counter_line(Counter({"b": 2, "a": 1})) == "a 1、b 2"
