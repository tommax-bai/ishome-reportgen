"""三档拟真上游数据包（**测试替身**：桩上游，不是生产数据、不是生产补丁）。

## 这东西为什么存在

报告线的质量以前没有基线：每次测都拿真求值线（project-svc）当场算出来的包当卷子，
release 一变、种子一改，卷子就换了——"整册四跑一成"这种数**跨天不可比**，
"这次改动到底有没有用"答不出来。三档 fixture 把卷子**定死**（`evaluatedOn` 也定死在
2026-08-31，时效判定不看运行时时钟），跑法见 :mod:`tests.quality_baseline`。

## 它证明什么、不证明什么

证明的是"报告线拿一份**合法上游数据**能产出什么质量"。
**不**证明"我们替上游补数据"——用户裁决 2026-08-31 原话：

    「每一个模块保证自己模块内部的质量，上游模块产生的数据对下游来说的话是可以信任的，
    上游如果做的不对的话，我们应该是去找上游的服务去修改，而不是我们自己在这边去补。
    系统应该是一个松藕合的状态。」

## 两条硬约束（**不是**只写在提交信息里的那种）

1. **fixture 禁止成为生产回退路径**。它只准出现在测试与本仓工具里；生产链路上
   上游没给就是没给，响亮失败，然后去找上游。这条不靠自觉：本包在 ``tests/`` 下，
   而打包只收 ``src/reportgen_worker``（``pyproject.toml`` ``[tool.hatch.build.targets.wheel]``）
   ——**它根本不在发出去的轮子里**，生产侧 import 它会直接 ImportError
   （物理隔离优先于规则隔离）。
2. **禁止 import_seeds、禁止进 release**。库里仍然是"没有就是没有"：这里的 mock 落点
   一条都不许回灌资产库，也不许拿它去铺种子——种子禁预置 calibrated、反例只收真跑样本。

## 三档分别是什么

同一户人家、同一批 58 条落点，**只差上游算出来了几条**——所以三档之间可比：

- ``full`` **齐全档**：58 条全给值，0 缺口。上游把该给的量都给全了。
- ``partial-gaps`` **部分缺档**：55 条有值 + 3 条缺口。就是 2026-08-31 那次六章整册真跑
  的形状（缺口的 ``reason``/``detail`` 逐字来自那次真跑，只补了当时还没有的 ``basisTag``）。
- ``mostly-gaps`` **大量缺档**：12 条有值（每域 2 条）+ 46 条缺口。测"上游给得很少时
  我们怎么应对"——按规则 4.18 宁薄勿撑，缺口只作为"别编它"的禁令下发，不作为可写的题材。

## 为什么只存一个 JSON、另两档由这里派生

三份 JSON 之间 90% 是同一批 persona / cr- 判据 / 禁词表，存三份的代价是"改一处要改三遍"，
真源就劈成了三处。故：**齐全档是存下来的那份**（``upstream-package-full.json``，
取自一次真跑的 activity 入参 + 三条 mock 落点），另两档由本模块按两张**声明式**表派生
——差异是"哪几条被抹掉"，这句话比 90% 的重复文本更容易看懂也更容易改。派生是纯函数，
同一个提交跑出来的三档逐字相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from reportgen_worker.models import ReportDataPackage

Tier = Literal["full", "partial-gaps", "mostly-gaps"]

TIERS: tuple[Tier, ...] = ("full", "partial-gaps", "mostly-gaps")
"""三档的档名（对外口径）。名字说的是"上游给成什么样"，不带序号——排序不写进名字。"""

TIER_LABELS: dict[Tier, str] = {
    "full": "齐全档",
    "partial-gaps": "部分缺档",
    "mostly-gaps": "大量缺档",
}

MOCK_MARK = "【MOCK fixture，禁止入库】"
"""mock 落点的显式标记：``source`` 与 ``provenance.source`` **都**以它开头。

两处都要标是因为两处都会被人看见：``source`` 是审这份 fixture 的人看的，
``provenance.source`` 是**渲染层印在成册页脚的依据标注**——mock 数据一旦混进哪份产物，
册子上自己写着它是 mock。两处同源不许有两个答案，同 ``calibration`` 那条
（``gate-provenance-inconsistent``）。
"""

_FULL_PACKAGE_PATH = Path(__file__).parent / "upstream-package-full.json"

MOCK_ANCHOR_IDS: frozenset[str] = frozenset(
    {"lkp-rug-size-rule", "lkp-storage-total-meters", "lkp-budget-driver"}
)
"""齐全档里**我们造出来的**那三条落点（其余 55 条取自真跑，值是求值线自己算的）。

它们与"部分缺档里那三条缺口"是同一批**不是巧合**：真跑里求值线算不出来的，正好就是要造
齐全档时不得不 mock 的那三条。两档因此是同一件事的两面——上游算出来了 / 上游没算出来。
"""

_REAL_GAPS: dict[str, dict[str, str]] = {
    # 2026-08-31 那次六章整册真跑里，求值线真的没算出来的三条：reason/detail 逐字照抄，
    # 一个字都不改（它是那次真跑的证据）。
    # **basisTag 不写在这儿**：当时求值线还没发这个字段，而它现在是必填（缺了整包解析失败，
    # 见 models.GapRecord）——缺口切不回自己那个域，整册缺口就会群发给每一章。补的办法是
    # 从这条落点在齐全档里的 basisTag 直接取（见 :func:`_withhold`）：同一条落点的域只有
    # 一个答案，在这里再抄一份就是把它劈成两处真源。
    "lkp-rug-size-rule": {
        "reason": "formula_not_implemented",
        "detail": "沙发前沿外扩 [200,300]mm，短边不小于沙发长度",
    },
    "lkp-storage-total-meters": {
        "reason": "formula_not_implemented",
        "detail": "Σ 各柜体投影沿墙长度（从定稿平面 gen-evaluated 求得）",
    },
    "lkp-budget-driver": {
        "reason": "formula_not_implemented",
        "detail": "占比最高且量可变的分项（通常为定制延米或主材档位）",
    },
}

_KEPT_IN_MOSTLY_GAPS: dict[str, tuple[str, str]] = {
    # 大量缺档里每域留下来的两条。选法有判据，不是随手挑的：**一条整条引用（single/range，
    # 一个匿名项）+ 一条分项（scenario/component/dimension/comparison）**——这样"上游给得少"
    # 测的是量少，而不是把某一种值形态整个从卷子上拿掉（那会连带把一半的引用语法一起停测）。
    # 有 calibrated 落点的域优先留 calibrated 的：断言预算得有东西背书，否则整章只剩坦白，
    # 测出来的是"没得写"而不是"给得少时怎么写"。
    "budget": ("lkp-price-custom-cabinet", "lkp-budget-share"),
    "ergonomics": ("lkp-counter-height", "lkp-shower-clear"),
    "lighting": ("lkp-cct-living", "lkp-illuminance-living"),
    "material": ("lkp-material-variety-max", "lkp-material-tier-gap"),
    "softdeco": ("lkp-color-main-max", "lkp-color-ratio"),
    "storage": ("lkp-storage-density-baseline", "lkp-wardrobe-hang-fold-ratio"),
}

_WITHHELD_REASON = "missing_input"
"""抹掉的落点在缺口里写的 reason。

求值线的 reason 是闭集（``missing_input`` / ``formula_not_implemented`` /
``empty_definition``，见 :mod:`reportgen_worker.writer` 缺口块的注释），而 reason 是
**逐字进 prompt** 的——在这儿发明一个新词面，等于让写作器读到一份真跑里不存在的口径。
"上游这轮没拿到算它要的输入" = ``missing_input``。
"""


def load_package_json(tier: Tier) -> dict[str, Any]:
    """取一档的报告数据包（camelCase 原样，与 project-svc Jackson 序列化同形）。

    每次调用都重读文件、返回新对象：调用方改它不会污染下一次调用。
    """
    package: dict[str, Any] = json.loads(_FULL_PACKAGE_PATH.read_text(encoding="utf-8"))
    if tier == "full":
        return package
    if tier == "partial-gaps":
        return _withhold(package, set(_REAL_GAPS), _REAL_GAPS)
    if tier == "mostly-gaps":
        kept = {lkp for ids in _KEPT_IN_MOSTLY_GAPS.values() for lkp in ids}
        dropped = {a["lkpId"] for a in package["anchors"]} - kept
        return _withhold(package, dropped, _REAL_GAPS)
    raise ValueError(f"没有这一档：{tier}（只有 {'、'.join(TIERS)}）")


def load_package(tier: Tier) -> ReportDataPackage:
    """取一档并解析成模型——**解析失败就是 fixture 不合法**，不是测试写错了。

    三档都过得了 ``model_validate`` 是它们"是合法上游数据"的判据（见 test_upstream_fixtures）。
    """
    return ReportDataPackage.model_validate(load_package_json(tier))


def _withhold(
    package: dict[str, Any], lkp_ids: set[str], known_gaps: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """把指定落点从"有值"挪到"缺口"：上游这一轮没算出来的形态。

    ``known_gaps`` 里有的按真跑原话写（reason/detail 逐字），没有的按 ``missing_input`` 写、
    ``detail`` 带 MOCK 标记说明它是 fixture 抹掉的——**detail 不进 prompt**（只有 reason 进），
    所以在这里做标记不会把 "fixture" 这个词喂给写作器。
    """
    kept: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for anchor in package["anchors"]:
        lkp_id = anchor["lkpId"]
        if lkp_id not in lkp_ids:
            kept.append(anchor)
            continue
        real = known_gaps.get(lkp_id)
        gaps.append(
            {
                "lkpId": lkp_id,
                "basisTag": anchor["basisTag"],
                "reason": real["reason"] if real else _WITHHELD_REASON,
                "detail": (
                    real["detail"] if real else f"{MOCK_MARK}上游这轮没给值：{anchor['name']}"
                ),
            }
        )
    missing = lkp_ids - {g["lkpId"] for g in gaps}
    if missing:
        raise ValueError(f"要抹掉的落点在齐全档里根本没有：{sorted(missing)}")
    package["anchors"] = kept
    package["gaps"] = gaps
    return package
