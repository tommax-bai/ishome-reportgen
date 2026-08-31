"""三档拟真上游数据包的自检：它们**是合法上游数据**，不是随手拼的 JSON。

这一组守的是 fixture 自己的资格。测量工具拿它当卷子，卷子本身要是不合法，
量出来的成册率就不是"报告线的质量"而是"fixture 的手误"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reportgen_worker.gate import run_package_gate
from tests.fixtures import (
    MOCK_ANCHOR_IDS,
    MOCK_MARK,
    TIERS,
    Tier,
    load_package,
    load_package_json,
)

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


@pytest.mark.parametrize("tier", TIERS)
def test_every_tier_parses_as_a_legal_upstream_package(tier: Tier) -> None:
    """三档都过得了 ``model_validate``——这是"合法上游数据"的判据。

    模型侧几个字段是**故意的严**（``extra=forbid`` 拒任何用户标识、``valueKind`` /
    ``presentation`` / 缺口 ``basisTag`` 缺了就整包失败），所以这一条不是形式测试：
    它同时证明了 fixture 没夹带用户标识、没漏必填字段。
    """
    package = load_package(tier)
    assert package.entitlement == "PAID"
    assert package.evaluated_on == "2026-08-31"  # 卷子定死：时效判定不看运行时时钟
    assert len(package.domains) == 6


@pytest.mark.parametrize("tier", TIERS)
def test_three_tiers_are_one_exam_with_different_amounts_computed(tier: Tier) -> None:
    """落点 ∪ 缺口 恒等于齐全档那 58 条：同一户人家、同一批落点。

    这条不成立三档就不可比了——"给得少时质量掉多少"会混进"换了一户人家"的影响。
    """
    full_ids = {a.lkp_id for a in load_package("full").anchors}
    package = load_package(tier)
    assert {a.lkp_id for a in package.anchors} | {g.lkp_id for g in package.gaps} == full_ids


@pytest.mark.parametrize("tier", TIERS)
def test_every_gap_slices_back_to_its_own_domain(tier: Tier) -> None:
    """缺口的 ``basisTag`` 必填且与该落点在齐全档里的域一致。

    立案在 2026-08-31：缺口原先没有域，整册缺口被原样发给每一章，于是各章为**别的章的缺口**
    写坦白卡，storage 的禁词还被带进了 softdeco 的正文。
    """
    domain_of = {a.lkp_id: a.basis_tag for a in load_package("full").anchors}
    package = load_package(tier)
    for gap in package.gaps:
        assert gap.basis_tag == domain_of[gap.lkp_id]
        assert gap.basis_tag.split("@")[0] in package.domains
    # 按域切完不丢不重：每条缺口有且只有一章看得见它
    assert sum(len(package.domain_gaps(d)) for d in package.domains) == len(package.gaps)


def test_mocked_anchors_are_marked_in_both_places() -> None:
    """造出来的落点必须自报家门，且 ``provenance.calibration`` 与落点自身一致。

    两处都标：``source`` 给审 fixture 的人看，``provenance.source`` 会被渲染层印进成册页脚
    的依据标注——mock 数据混进哪份产物，那份产物自己写着它是 mock。
    校准口径不一致会被 ``gate-provenance-inconsistent`` 打回（2026-08-31 真跑踩过）。
    """
    package = load_package("full")
    mocked = [a for a in package.anchors if a.lkp_id in MOCK_ANCHOR_IDS]
    assert len(mocked) == len(MOCK_ANCHOR_IDS)
    for anchor in mocked:
        assert anchor.source is not None and anchor.source.startswith(MOCK_MARK)
        assert anchor.provenance is not None
        assert anchor.provenance.source is not None
        assert anchor.provenance.source.startswith(MOCK_MARK)
        assert anchor.provenance.calibration == anchor.calibration
        assert anchor.provenance.annotation_required is True  # draft 进正文必挂标注
        assert anchor.presentation == "REFERENCE_ONLY"  # 合法值只有两个，draft 落这一个
    # 真跑来的那 55 条不带标记：标记是"这条是我们造的"的意思，给真数据挂上就是假标记
    for anchor in package.anchors:
        if anchor.lkp_id not in MOCK_ANCHOR_IDS:
            assert not (anchor.source or "").startswith(MOCK_MARK)


@pytest.mark.parametrize("tier", TIERS)
def test_every_tier_passes_the_producer_contract_gate(tier: Tier) -> None:
    """跑的是 activity 写作前跑的那一道（``run_package_gate``），不另写判据。

    这条过不了，真跑就会在烧掉一次 LLM 调用之后才发现 fixture 违约。
    """
    package = load_package(tier)
    for domain in package.domains:
        assert run_package_gate(domain, package) == []


@pytest.mark.parametrize("tier", TIERS)
def test_every_domain_still_has_something_to_write_about(tier: Tier) -> None:
    """任何一档下每章至少一条落点——一条都不剩会撞 ``gate-no-anchors`` 整章装死。

    那时测出来的是"这章根本没派上用场"，不是"给得少时怎么应对"。
    """
    package = load_package(tier)
    for domain in package.domains:
        assert package.domain_anchors(domain), f"{domain} 在 {tier} 档下一条落点都不剩"


def test_tier_shapes() -> None:
    """三档各自的落点/缺口条数——档与档的差别就是这几行。"""
    full = load_package("full")
    assert (len(full.anchors), len(full.gaps)) == (58, 0)
    partial = load_package("partial-gaps")
    assert (len(partial.anchors), len(partial.gaps)) == (55, 3)
    assert {g.lkp_id for g in partial.gaps} == MOCK_ANCHOR_IDS  # 就是真跑那次没算出来的三条
    sparse = load_package("mostly-gaps")
    assert (len(sparse.anchors), len(sparse.gaps)) == (12, 46)
    for domain in sparse.domains:
        assert len(sparse.domain_anchors(domain)) == 2  # 每域只留两条


def test_each_load_returns_a_fresh_object() -> None:
    """调用方改了它不该影响下一次调用（连跑 N 次共用一个进程）。"""
    first = load_package_json("full")
    first["anchors"].clear()
    assert len(load_package_json("full")["anchors"]) == 58


def test_unknown_tier_fails_loudly() -> None:
    with pytest.raises(ValueError, match="没有这一档"):
        load_package_json("half-baked")  # type: ignore[arg-type]


def test_production_code_never_references_the_fixture() -> None:
    """**fixture 禁止成为生产回退路径**——这条不靠自觉，靠这一条自动拦。

    生产链路上上游没给就是没给，响亮失败然后去找上游（用户裁决 2026-08-31 松耦合原则）。
    打包只收 ``src/reportgen_worker``，fixture 在 ``tests/`` 下本就进不了轮子；这一条守的是
    更前面一步：连"在 src 里 import 它"这个念头都当场拦住，不必等部署才炸。
    """
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "tests.fixtures" not in text, f"{path} 引了 fixture"
        assert "upstream-package" not in text, f"{path} 引了 fixture 数据文件"
