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
    """落点 ∪ 缺口 恒等于齐全档那 64 条（60 有值 + 4 求值线自记的缺口）：同一户人家、同一批落点。

    这条不成立三档就不可比了——"给得少时质量掉多少"会混进"换了一户人家"的影响。
    """
    full = load_package("full")
    full_ids = {a.lkp_id for a in full.anchors} | {g.lkp_id for g in full.gaps}
    package = load_package(tier)
    assert {a.lkp_id for a in package.anchors} | {g.lkp_id for g in package.gaps} == full_ids


@pytest.mark.parametrize("tier", TIERS)
def test_every_gap_slices_back_to_its_own_domain(tier: Tier) -> None:
    """缺口的 ``basisTag`` 必填且与该落点在齐全档里的域一致。

    立案在 2026-08-31：缺口原先没有域，整册缺口被原样发给每一章，于是各章为**别的章的缺口**
    写坦白卡，storage 的禁词还被带进了 softdeco 的正文。
    """
    full = load_package("full")
    domain_of = {a.lkp_id: a.basis_tag for a in full.anchors}
    domain_of |= {g.lkp_id: g.basis_tag for g in full.gaps}
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
    # 真跑来的 53 条、照种子取值的硬装单价、按求值线算法派生的四条（两条金额、占比、三档）
    # 都不带标记：
    # 标记是"这条是我们造的"的意思，给真数据挂上就是假标记
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


SHARE_GAP_IDS: frozenset[str] = frozenset(
    {
        "lkp-share-custom-cabinet",
        "lkp-share-demolition",
        "lkp-share-electrical-point",
        "lkp-share-wall-paint",
    }
)
"""齐全档自带的四条缺口：声明了 ``share_of`` 但量还没有的分项，求值线记 gap 不填
（backend 533aa06）。"""


def test_tier_shapes() -> None:
    """三档各自的落点/缺口条数——档与档的差别就是这几行。

    齐全档自 2026-09-09 起不再是"0 缺口"：那 4 条是求值线的真实输出（占比由算得不由搜得，
    量缺的分项占比为空），不是考卷造的。
    """
    full = load_package("full")
    assert (len(full.anchors), len(full.gaps)) == (60, 4)
    assert {g.lkp_id for g in full.gaps} == SHARE_GAP_IDS
    for gap in full.gaps:
        assert gap.reason == "missing_input"
        assert gap.detail is not None and gap.detail.startswith("等平面出来按量算")
    partial = load_package("partial-gaps")
    assert (len(partial.anchors), len(partial.gaps)) == (58, 6)
    # 齐全档那 4 条 + 真跑那次没算出来、至今仍在下发的 2 条
    assert {g.lkp_id for g in partial.gaps} == SHARE_GAP_IDS | MOCK_ANCHOR_IDS
    sparse = load_package("mostly-gaps")
    assert (len(sparse.anchors), len(sparse.gaps)) == (12, 52)
    for domain in sparse.domains:
        assert len(sparse.domain_anchors(domain)) == 2  # 每域只留两条


def test_each_load_returns_a_fresh_object() -> None:
    """调用方改了它不该影响下一次调用（连跑 N 次共用一个进程）。"""
    first = load_package_json("full")
    first["anchors"].clear()
    assert len(load_package_json("full")["anchors"]) == 60


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


@pytest.mark.parametrize("tier", ("full", "partial-gaps"))
@pytest.mark.parametrize(
    ("price_id", "cost_id", "round_to"),
    [
        # 水电人工（backend ac7cbd3，2026-09-08）：单价资产同批加了 cost_round_to: 100，
        # 真跑给 6600–7500
        ("lkp-price-hydro-labor-sqm", "lkp-cost-hydro-labor-sqm", 100),
        # 全屋硬装（backend e48d8ed，2026-09-08）：种子声明 cost_round_to: 100，金额两端各自到百元
        ("lkp-price-hardfit-total-sqm", "lkp-cost-hardfit-total-sqm", 100),
    ],
)
def test_cost_anchor_is_the_price_times_this_household_area(
    tier: Tier, price_id: str, cost_id: str, round_to: int | None
) -> None:
    """金额条目（``lkp-cost-*``）进考卷，且**逐字段照求值线的形态**（2026-09-08 立案：9-07 册造价章
    一个「元」都没有——考卷只有单价条目，没有求值线派生的金额条目）。

    求值线 ``RulebookEvaluator.projectWorkItemCost``：``min/max = round(单价两端 × 建筑面积)``，
    两端各自乘不交叉、再按单价资产声明的 ``cost_round_to`` 取整（没声明就不取整）；
    ``unit`` 硬编 ``元``；``name`` = 单价资产名 + ``合计``；推导原文进顶层 ``source``、
    ``provenance.source`` 仍是单价的外部出处
    （两处**不同值**，照它）。
    这里把关系再算一遍，考卷上的金额与单价、面积对不上就红——它不是 mock，是派生。
    """
    package = load_package(tier)
    by_id = {a.lkp_id: a for a in package.anchors}
    price, cost = by_id[price_id], by_id[cost_id]
    area = package.anonymous_profile.building_area_sqm
    assert area is not None and isinstance(price.value, dict) and isinstance(cost.value, dict)

    def to_yuan(unit_price: float) -> int:
        exact = round(unit_price * area)
        return exact if round_to is None else round(exact / round_to) * round_to

    assert cost.value == {"min": to_yuan(price.value["min"]), "max": to_yuan(price.value["max"])}
    assert cost.unit == "元" and cost.value_kind == "range"
    assert cost.name == price.name + "合计"
    assert cost.basis_tag == price.basis_tag and cost.calibration == price.calibration
    assert cost.provenance is not None and price.provenance is not None
    assert cost.provenance.source == price.provenance.source  # 依据标注印的是单价的出处
    assert cost.source is not None and cost.source.startswith("求值线按「单价 × 量」算出")


def test_share_anchor_is_the_part_over_the_total_crosswise() -> None:
    """占比条目（``lkp-share-*``）＝分项金额 ÷ 合计金额，**两端交叉、按 1 个百分点取整**。

    用户裁决 2026-09-09：占比由算得不由搜得。求值线 ``RulebookEvaluator.projectWorkItemShare``
    （backend 533aa06）：``min = 分项 min ÷ 合计 max``、``max = 分项 max ÷ 合计 min``，除的是
    取整后的金额落点，再按 ``share_round_to``（水电人工声明为 1）取整到整数百分点；
    ``unit`` 硬编 ``%``；
    ``provenance.source`` 是分子与分母两条单价资产的外部出处拼起来（分母前缀「合计来源：」）；
    可核性取两条资产的交集、时效窗取两窗的交。考卷上占比与两笔金额对不上就红。
    """
    package = load_package("full")
    by_id = {a.lkp_id: a for a in package.anchors}
    part = by_id["lkp-cost-hydro-labor-sqm"]
    total = by_id["lkp-cost-hardfit-total-sqm"]
    share = by_id["lkp-share-hydro-labor-sqm"]
    assert isinstance(part.value, dict) and isinstance(total.value, dict)
    assert share.value == {
        "min": round(part.value["min"] / total.value["max"] * 100),
        "max": round(part.value["max"] / total.value["min"] * 100),
    }
    assert share.unit == "%" and share.value_kind == "range"
    assert share.basis_tag == part.basis_tag == total.basis_tag
    assert share.calibration == "calibrated" == part.calibration == total.calibration
    assert share.provenance is not None and part.provenance is not None
    assert total.provenance is not None
    assert share.provenance.source == (
        f"{part.provenance.source}；合计来源：{total.provenance.source}"
    )
    assert part.provenance.effective_from and total.provenance.effective_from
    assert part.provenance.effective_to and total.provenance.effective_to
    assert share.provenance.effective_from == max(
        part.provenance.effective_from, total.provenance.effective_from
    )
    assert share.provenance.effective_to == min(
        part.provenance.effective_to, total.provenance.effective_to
    )
    assert share.source is not None
    assert share.source.startswith("求值线按「分项金额 ÷ 合计金额」算出")


# 档位单价：backend ``rulebook-seeds/budget/attributes.yaml`` 里 ``attr-price-hardfit-total-sqm``
# 的 ``grade_breakdown``（533aa06，键为 tier 闭集名，只有档一维、不分城市）
HARDFIT_GRADE_PRICE_PER_SQM: dict[str, tuple[int, int]] = {
    "low": (800, 1200),
    "medium": (1200, 1800),
    "high": (2000, 3000),
}


def test_grade_cost_anchor_is_each_grade_price_times_this_household_area() -> None:
    """三档合计（``lkp-cost-hardfit-total-sqm-by-grade``）＝各档单价 × 建筑面积，按百元取整。

    求值线从单价资产自带的 ``grade_breakdown`` 派生（backend 533aa06）：``valueKind`` 是 ``tier``、
    三档各一个区间，两端各自乘不交叉，再按单价资产的 ``cost_round_to``（100）取整；``unit`` 硬编
    ``元``；``name`` = 单价资产名 + ``分三档合计``；出处、时效、可核性照单价条目。
    "三档差在哪"只由这三个区间说，不引用任何搜来的倍数（退役的 lkp-budget-tier-gap 就是那种）。
    """
    package = load_package("full")
    by_id = {a.lkp_id: a for a in package.anchors}
    price = by_id["lkp-price-hardfit-total-sqm"]
    graded = by_id["lkp-cost-hardfit-total-sqm-by-grade"]
    area = package.anonymous_profile.building_area_sqm
    assert area is not None

    def to_yuan(unit_price: float) -> int:
        return int(round(round(unit_price * area) / 100) * 100)

    assert graded.value == {
        grade: {"min": to_yuan(low), "max": to_yuan(high)}
        for grade, (low, high) in HARDFIT_GRADE_PRICE_PER_SQM.items()
    }
    assert graded.unit == "元" and graded.value_kind == "tier"
    assert graded.name == price.name + "分三档合计"
    assert graded.basis_tag == price.basis_tag and graded.calibration == price.calibration
    assert graded.provenance is not None and price.provenance is not None
    assert graded.provenance.source == price.provenance.source
    assert graded.provenance.effective_from == price.provenance.effective_from
    assert graded.provenance.effective_to == price.provenance.effective_to
    assert graded.source is not None
    assert graded.source.startswith("求值线按「各档单价 × 量」算出")


def test_budget_assertion_budget_hangs_on_derived_anchors_only() -> None:
    """造价域断言预算的 ``requires`` 逐字照业务侧 ``budget/persona.yaml``（backend 533aa06）。

    退役的三条搜来的占比参数（lkp-budget-share / lkp-budget-driver / lkp-budget-tier-gap）
    不许再出现在任何题目的支点里；占比与"哪一项最吃钱"挂全部五条 lkp-share-*，其中四条现在是缺口
    ——这两个题目在考卷上**自然无背书**，平面接通后自然有，不是考卷漏了什么。
    """
    package = load_package("full")
    persona = package.personas_by_domain["budget"][0]
    by_predicate = {a["predicate"]: list(a["requires"]) for a in persona.assertion_budget}
    shares = [
        "lkp-share-demolition",
        "lkp-share-electrical-point",
        "lkp-share-custom-cabinet",
        "lkp-share-hydro-labor-sqm",
        "lkp-share-wall-paint",
    ]
    assert by_predicate["眼下能算出的钱"] == [
        "lkp-cost-hydro-labor-sqm",
        "lkp-cost-hardfit-total-sqm",
    ]
    assert by_predicate["各分项占比"] == shares
    assert by_predicate["哪一项最吃钱"] == shares
    assert by_predicate["三档差在哪"] == ["lkp-cost-hardfit-total-sqm-by-grade"]
    retired = {"lkp-budget-share", "lkp-budget-driver", "lkp-budget-tier-gap"}
    for requires in by_predicate.values():
        assert not retired & set(requires)
    known = {a.lkp_id for a in package.anchors} | {g.lkp_id for g in package.gaps}
    for requires in by_predicate.values():
        assert set(requires) <= known  # 每个支点要么有值要么是缺口，没有凭空的 id
