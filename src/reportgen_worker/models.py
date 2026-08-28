"""reportgen_worker 出入参模型（pydantic）。

报告数据包镜像 = contracts `rulebook/report_data_package.schema.json`（生产方 project-svc 规则引擎，
Jackson camelCase 序列化——本侧 snake_case 字段 + to_camel 别名对齐）。
三条硬性约定（图 v0.2 §0/§2）：

- **自包含**：persona 全文、cr- 判据、禁词表随包，成文线不回查任何库；
- **匿名**：`extra="forbid"`——任何用户/项目标识字段直接解析失败（结构性守卫）；
- **数字纪律**：卡片正文禁裸数字，数字只经 ``{lkp-*}`` 占位引用落点对象
  （出口过检逐字段比对零漂移）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _PackageModel(BaseModel):
    """包侧基类：camelCase 别名对齐 Java 序列化；extra=forbid 即匿名性结构守卫。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ReleaseRef(_PackageModel):
    domain: str
    release_tag: str


class ReportAnchor(_PackageModel):
    """落点对象：成文线数字字段唯一合法来源。degraded=未过可核性门（规则 4.10/4.18 消费侧降档）。"""

    lkp_id: str
    name: str
    number_class: str | None = None
    unit: str | None = None
    value: dict[str, Any]
    basis_tag: str
    source: str | None = None
    calibration: str
    degraded: bool


class GapRecord(_PackageModel):
    lkp_id: str
    reason: str
    detail: str | None = None


class PersonaAsset(_PackageModel):
    """persona 载荷（规则 4.13 四件）：prompt 只从此拼装，运行时不读任何人写的文本（规则 4.19）。"""

    asset_id: str
    identity: str
    judgment_samples: list[Any] = []
    assertion_budget: list[Any] = []
    banned_terms: dict[str, Any] = {}
    version: int


class CheckAsset(_PackageModel):
    """cr- 判据（规则 4.10b 纪律形态）：release 数据物化执行，不硬编码。"""

    asset_id: str
    check_type: str
    scope: list[str] = []
    pattern: str | None = None
    requirement: str | None = None
    message: str
    decided_by: str
    threshold_refs: list[str] = []
    version: int


class EvaluationProfile(_PackageModel):
    """匿名画像：字段全集即上限——extra=forbid 拒绝任何用户标识。"""

    chief_height_mm: int | None = None
    tallest_height_mm: int | None = None
    eye_height_mm: int | None = None
    tv_screen_height_mm: int | None = None
    layout_features: dict[str, str] = {}


class ReportDataPackage(_PackageModel):
    domains: list[str]
    releases: list[ReleaseRef]
    anchors: list[ReportAnchor]
    gaps: list[GapRecord]
    personas_by_domain: dict[str, list[PersonaAsset]]
    checks_by_domain: dict[str, list[CheckAsset]]
    banned_terms_by_domain: dict[str, list[str]]
    anonymous_profile: EvaluationProfile

    def domain_anchors(self, domain: str) -> list[ReportAnchor]:
        """本域落点切片（单元输入只见本域，图 v0.2 §2 依赖只存在于求值线）。"""
        return [a for a in self.anchors if a.basis_tag.split("@")[0] == domain]


# ---------------------------------------------------------------------------
# 成文线 activity 出入参
# ---------------------------------------------------------------------------


class Card(BaseModel):
    """卡片（客户语域）：body/thesis 禁裸数字，数字经 {lkp-*} 占位；number_refs=占位符全集声明。"""

    model_config = ConfigDict(extra="forbid")

    thesis: str
    body: str
    number_refs: list[str] = []


class Violation(BaseModel):
    """过检违规：check=引擎纪律码（gate-*）或 release 判据 asset_id（cr-*）。"""

    model_config = ConfigDict(extra="forbid")

    check: str
    detail: str


class UnitComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    package: ReportDataPackage
    max_rewrites: int = 2


class UnitComposeResult(BaseModel):
    """单元成文结果：重写用尽仍不过 → verdict=failed 上抛，绝不静默假成功（图 v0.2 §3）。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ok", "failed"]
    domain: str
    cards: list[Card] = []
    violations: list[Violation] = []
    rewrites_used: int = 0
    releases: list[ReleaseRef] = []


class Page(BaseModel):
    """页（唯一知道"页"的层）：page_type 待 pt- 页型库编译，首版按域成页。"""

    model_config = ConfigDict(extra="forbid")

    page_id: str
    domain: str
    page_type: str | None = None
    cards: list[Card]


class PageAssembleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[UnitComposeResult]


class PageAssembleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ok", "failed"]
    pages: list[Page] = []
    violations: list[Violation] = []


class BookCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[Page]
    package: ReportDataPackage


class BookCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ok", "failed"]
    violations: list[Violation] = []
