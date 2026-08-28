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
    """落点对象：成文线数字字段唯一合法来源。

    ``degraded`` 是标记（未过可核性门），``presentation`` 是强制——求值线按产物权益档判定的
    呈现档位（规则 4.10）：``THESIS_SUPPORT`` 可作判断句支点、``REFERENCE_ONLY`` 只可参考口吻。
    ``WITHHELD`` 本不该出现在 anchors 里（求值线判隐藏的落点根本不下发），此处仍收下它是为了
    **宽进严查**：由 :func:`reportgen_worker.gate.run_package_gate` 报一条读得懂的生产方违约，
    而不是让整包解析崩在 pydantic 里。
    """

    lkp_id: str
    name: str
    number_class: str | None = None
    unit: str | None = None
    value: dict[str, Any]
    basis_tag: str
    source: str | None = None
    calibration: str
    degraded: bool
    presentation: Literal["THESIS_SUPPORT", "REFERENCE_ONLY", "WITHHELD"]


class WithheldAnchor(_PackageModel):
    """按规则 4.10 隐藏掉的落点：只有 id/来源/原因，无值——隐藏即内容不下发。

    与 :class:`GapRecord` 分列：gap- 是"求不出"（补公式或补输入），withheld 是"求出了但纪律
    不许发"（补外部依据把条目转正），两条回流信号不混。成文线用它把"引用了被隐藏的落点"打回
    成一条说得清的违规，而不是笼统的"引用不存在"。
    """

    lkp_id: str
    basis_tag: str
    reason: str


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


class CheckExample(_PackageModel):
    """判官反例样例（规则 4.17）三件：``bad`` 真跑里模型写出的原句、``why`` 为什么错、
    ``fixed`` 合规写法。

    **只收真跑观察到的样本**，禁想象填充（规范 v2.3 §12）——种子集真实度即系统能发现问题的上限。
    ``fixed`` 不进判官 prompt：判官只报编号不改写，把改好的答案递给它等于请它越权
    （见 :mod:`reportgen_worker.judge`）。
    """

    bad: str
    why: str
    fixed: str


class CheckAsset(_PackageModel):
    """cr- 判据（规则 4.10b 纪律形态）：release 数据物化执行，不硬编码。

    一条判据落哪一层由数据说了算（图 v0.2 §3 判据下沉次序 schema > 规则 > prompt > 判官）：
    带 ``pattern`` 的走规则层逐字机检；``examples`` 非空的走判官层（规则层判不出的语义违规）。
    ``status`` 是规则 4.17 入册门禁第二道的开关——``observing`` 只记录不拦截、``active`` 命中即违规、
    ``retired`` 停用。**代码里没有"要不要拦"的分支**，拦截权只能由 release 数据授予。
    V4 之前的快照无此二字段，缺省 = 空样例 + observing（无拦截权，安全方向）。
    """

    asset_id: str
    check_type: str
    scope: list[str] = []
    pattern: str | None = None
    requirement: str | None = None
    message: str
    decided_by: str
    threshold_refs: list[str] = []
    examples: list[CheckExample] = []
    status: str = "observing"
    version: int


class EvaluationProfile(_PackageModel):
    """匿名画像：字段全集即上限——extra=forbid 拒绝任何用户标识。"""

    chief_height_mm: int | None = None
    tallest_height_mm: int | None = None
    eye_height_mm: int | None = None
    tv_screen_height_mm: int | None = None
    layout_features: dict[str, str] = {}


class ReportDataPackage(_PackageModel):
    """``entitlement`` = 本包服务的产物权益档（FREE/PAID，规范 §3.1 权益列）：降档判定的口径，
    随包下发只为让成文线能复核门禁，成文线**不重判**——判定结果已在 anchors[].presentation。
    它是产物属性不是用户属性，不破匿名纪律。
    """

    entitlement: Literal["FREE", "PAID"]
    domains: list[str]
    releases: list[ReleaseRef]
    anchors: list[ReportAnchor]
    withheld_anchors: list[WithheldAnchor] = []
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
    """卡片（客户语域）：body/thesis 禁裸数字，数字经 {lkp-*} 占位；number_refs=占位符全集声明。

    ``assertions`` = 本卡声明使用的**断言预算谓词**（persona 的 assertion_budget 谓词名，规则
    5.8/4.13）。"这句是不是判断句"是语义判断、机检判不确定，所以纪律改成可确定性判定的形态：
    要说判断句就得先声明用的是哪条预算，出口过检再核该谓词的 requires 是否全部已求值且非降档
    （规则 4.10a：经验条目不得作判断句背书）。未声明而实际写了判断句，属语义违规，归判官层。
    """

    model_config = ConfigDict(extra="forbid")

    thesis: str
    body: str
    number_refs: list[str] = []
    assertions: list[str] = []


class Violation(BaseModel):
    """过检违规：check=引擎纪律码（gate-*）或 release 判据 asset_id（cr-*）。"""

    model_config = ConfigDict(extra="forbid")

    check: str
    detail: str


class JudgeObservation(BaseModel):
    """判官观察：命中的 cr- 编号 + 原句片段 + 为什么。

    **三个字段就是判官的全部输出面**（extra=forbid）：没有放改写建议的位置——判官越权改写＝判官与
    写手同源漂移，那是写作器的活（图 v0.2 §3"只报 cr- 编号不改写"）。观察态下它不影响 verdict，
    只随结果回流，供规则 4.17 的入册门禁统计触发率。
    """

    model_config = ConfigDict(extra="forbid")

    check: str
    quote: str
    why: str


class UnitComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    package: ReportDataPackage
    max_rewrites: int = 2


class UnitComposeResult(BaseModel):
    """单元成文结果：重写用尽仍不过 → verdict=failed 上抛，绝不静默假成功（图 v0.2 §3）。

    ``observations`` = 判官层产出（规则层之后的第二道）。**观察态只记录不拦截**：它出现在 ok 结果里
    是常态，不是矛盾——规则 4.17 入册门禁第二道要求新判据先只记录跑 N 份，触发率合格才谈拦截。
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["ok", "failed"]
    domain: str
    cards: list[Card] = []
    violations: list[Violation] = []
    observations: list[JudgeObservation] = []
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
