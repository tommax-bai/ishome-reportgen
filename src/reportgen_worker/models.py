"""reportgen_worker 出入参模型（pydantic）。

报告数据包镜像 = contracts `rulebook/report_data_package.schema.json`（生产方 project-svc 规则引擎，
Jackson camelCase 序列化——本侧 snake_case 字段 + to_camel 别名对齐）。
三条硬性约定（图 v0.2 §0/§2）：

- **自包含**：persona 全文、cr- 判据、禁词表随包，成文线不回查任何库；
- **匿名**：`extra="forbid"`——任何用户/项目标识字段直接解析失败（结构性守卫）；
- **数字纪律**：卡片正文禁裸数字，数字只经 ``{lkp-*}`` 占位引用落点对象
  （出口过检逐字段比对零漂移）。**两层模型**（规则 1.9，v2.8）：一条落点＝若干项，
  一项的值＝一个数或一个区间，正文可引用其中一项（``{lkp-x.项名}``）。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

ValueKind = Literal["single", "range", "scenario", "tier", "dimension", "component", "comparison"]
"""落点值的构成类别（规则 1.9 一，v2.8）：七值闭集，**随包下发不靠推断**。

一条字段同时判定三件事：``value`` 的形态、可否单项引用、项名受哪个词表约束。
枚举之外的值**整包解析失败**（同 ``presentation`` 的收窄纪律）：认不出构成类别，
prompt 写不对、引用也判不对，静默当成某一类比拒收危险。
"""

ANONYMOUS_ITEM_KINDS: frozenset[str] = frozenset({"single", "range"})
"""只有**一个匿名项**的两类（规则 1.9 一）：``single`` 的值是标量、``range`` 的值是 ``{min,max}``。

匿名 = 没有项名可写，正文只能整条引用 ``{lkp-x}``。**``min``/``max`` 不是项**，是项的值形态——
故 ``{lkp-x.min}`` 在语法上不存在，"引一端丢掉另一端"由**结构**堵死而不靠纪律禁止
（物理隔离优先于规则隔离，落在引用语法上的形态）。
"""

ITEM_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
"""项名形态：ASCII 小写 kebab-case，与落点标识同一套（规则 1.9 三）。

项名与落点标识**同处一个记号**（``{lkp-x.项名}``），混语言等于在一个标识符里放两种文字；
项名不进业主视野（读者看到的是正文里的人话与渲染出的数值），故"中文语义更准"在此不成立。
取值落在哪个词表（``tier`` 闭集、``scenario`` 受控词表…）由**资产回路的核验拒灌**，
消费侧只守形态——词表是开集且不随包下发，在这里照抄一份等于把词表分裂成两处真源。
"""

AnchorValue = int | float | dict[str, Any]
"""两层模型的值形态（规则 1.9 一）：``single`` 是标量；其余是字典——``range`` 为 ``{min,max}``
（单边界只给一侧），其余五类为 项名 → 标量 或 项名 → ``{min,max}``。

**元信息不进这里**：单位见 ``unit``、参考平面见 ``reference_plane``（规则 1.9 二）。
只要元信息与项同层，``{lkp-x.unit}``（引用出一个单位字符串）就是语法上合法的写法，约定管不住。
"""


class _PackageModel(BaseModel):
    """包侧基类：camelCase 别名对齐 Java 序列化；extra=forbid 即匿名性结构守卫。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ReleaseRef(_PackageModel):
    domain: str
    release_tag: str


class AnchorProvenance(_PackageModel):
    """落点依据（规则 4.10c「标注必挂」，v2.4 裁决 2026-08-29）：这个数从哪来、什么时候取的。

    ``annotation_required`` 是**强制**：判定在求值线做完（未过可核性门，或 ``effective_to`` 早于
    本次求值基准日），成文线只执行不重判——与 ``presentation`` 同机制。

    ``source`` 为 ``None`` **是事实不是缺失**：规则 4.10 的"经验条目"就是"无外部依据、靠行业判断"，
    标注照挂，由渲染层说明这是经验判断——**禁编造一个来源**（规范 §12）。

    时效两字段保持字符串（ISO 日期）不转 ``date``：越界与否已在生产侧判完，消费侧不做日期运算，
    只把它原样交给渲染层；转类型只会给 activity 出参的 JSON 序列化添一处无谓的坑。
    """

    source: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    calibration: str
    annotation_required: bool


class ReportAnchor(_PackageModel):
    """落点对象：成文线数字字段唯一合法来源。

    三字段分工：``degraded`` 是标记（未过可核性门）；``presentation`` 是**语域强制**——
    ``THESIS_SUPPORT`` 可作判断句支点、``REFERENCE_ONLY`` 语域限建议口吻（规则 4.10a/5.8）；
    ``provenance`` 是**标注强制**——未过门或已过期的落点进正文，同页必须挂依据标注（规则 4.10c）。

    v2.4 裁决 2026-08-29 取消了"隐藏"这一档，故 ``presentation`` 不再有 ``WITHHELD``：
    枚举值收窄是**故意的严**——真收到一个 WITHHELD，说明生产方还停在 v2.3 在隐藏落点，
    那时整包解析失败比放它进来更安全（隐藏本身已是违约，而不再是纪律）。

    **两层模型**（规则 1.9，v2.8）：一条落点 = 若干「项」，一项的值 = 一个数或一个区间。
    ``value_kind`` 是必填而非可推断项——收到没有它的包**整包解析失败**，同 ``presentation``
    的理由：``{"min": 900}`` 到底是"下限 900 的区间"还是"名叫 min 的项"，靠猜就等于把
    "不靠推断"这条裁决在消费侧还回去。
    """

    lkp_id: str
    name: str
    number_class: str | None = None
    unit: str | None = None
    value_kind: ValueKind
    value: AnchorValue
    basis_tag: str
    source: str | None = None
    calibration: str
    degraded: bool
    provenance: AnchorProvenance | None = None
    presentation: Literal["THESIS_SUPPORT", "REFERENCE_ONLY"]
    reference_plane: str | None = None
    """参考平面及其高度（国标术语，如"0.75m 水平面"）：v2.8 前它挤在 ``value`` 里当一个键。

    **成文线只收不发**：它一个字都不进写作/推导 prompt——文字里带着数（"0.75m"），进 prompt
    就是给模型一段可照抄的裸数字（正文禁裸数字，抄了必被打回）；而"这个数说的是哪个平面"
    是渲染层出标注时的事，渲染层直读数据包，不经成文线转手。
    """

    @property
    def has_items(self) -> bool:
        """这条落点分不分项（规则 1.9 一）：``single``/``range`` 只有一个匿名项，其余五类分项。"""
        return self.value_kind not in ANONYMOUS_ITEM_KINDS

    @property
    def item_names(self) -> list[str]:
        """本落点的项名清单（不分项或值形态不合时为空）。

        **按生产方给的顺序**不排序：档位落点的 low/medium/high、维度落点的 depth/width/height
        本身有序，排字典序会把它打乱；JSON 对象的顺序随包稳定，确定性不因此丢失。
        """
        if not self.has_items or not isinstance(self.value, dict):
            return []
        return list(self.value)

    @property
    def requires_annotation(self) -> bool:
        """本落点进正文是否必须随页挂依据标注（规则 4.10c）。

        ``provenance`` 缺席 = 生产方未升级到 v2.4：回退按 ``calibration`` 判，**方向偏严**
        （宁可多标一条，不可漏标一条）——漏标是纪律失效，多标只是页脚多一行。
        """
        if self.provenance is not None:
            return self.provenance.annotation_required
        return self.calibration != "calibrated"


class WithheldAnchor(_PackageModel):
    """**已作废、恒空**（v2.4 裁决 2026-08-29，规范 §14.9 取消隐藏档）。

    字段按契约"只增不删"保留（生产方恒发空数组），消费侧**不得据此拦截**：未校准与已过期的
    落点现在照常进正文，纪律改由 :class:`AnchorProvenance` 的"标注必挂"承接。原语义：按规则 4.10
    隐藏掉的落点审计（只有 id/来源/原因，无值）。
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


class TriggerEvidence(_PackageModel):
    """规则为什么触发（规则 4.3 可追溯性的户型侧对应物）。

    ``type`` 首版只有 ``always`` 与 ``layout_feature``（answer/light/switch 随需求采集与清单族
    落地时由求值线扩展，契约只增不改）；``feature`` 是户型特征标记名（闭集见 contracts
    ``rulebook/layout_features.json``）；``evidence`` 是**这条标记成立的依据**
    （人话，来自解析产出），报告里"因为你家阳台带家政位"的数据就来自这里。
    ``always`` 触发时后两者为 None。
    """

    type: str
    feature: str | None = None
    evidence: str | None = None


class TriggeredRule(_PackageModel):
    """本户**已经触发**的规则条目（求值线判定完随包下发，成文线不重判触发）。

    **用途限定＝"这一章该讲到什么"的输入，喂叙事推导步定主张**；``content``/``rationale`` 是内部
    陈述句，**禁止逐字进写作 prompt 当句子抄**——示范句可抄性同病（坑单 prompt 铁律一：
    禁止词面与现成句子进 prompt 就会被照抄）。

    背书纪律不变：``calibration != "calibrated"`` 的规则**不构成判断句依据**（断言预算只认
    calibrated，同落点）。
    """

    asset_id: str
    layer: str
    content: str
    rationale: str | None = None
    severity: str | None = None
    calibration: str
    triggered_by: TriggerEvidence


class EvaluationProfile(_PackageModel):
    """匿名画像：字段全集即上限——extra=forbid 拒绝任何用户标识。

    ``city_tier`` = 城市档（裁决 2026-08-29）：**市场参数不是身份**——一个城市几十万住户，
    不具标识性；缺它则造价章要么失真要么哑火。它在**求值线**用掉（按档选单价区间，规则 5.15），
    成文线只是收下来：写作 prompt 里**不下发**——档已经选完了，写作侧知道它只会多一句
    "一线城市…"这类关于这家人的陈述，而卡片该说的是这个数是多少、为什么。
    """

    chief_height_mm: int | None = None
    tallest_height_mm: int | None = None
    eye_height_mm: int | None = None
    tv_screen_height_mm: int | None = None
    layout_features: dict[str, str] = {}
    city_tier: str | None = None


class ReportDataPackage(_PackageModel):
    """``entitlement`` = 本包服务的产物权益档（FREE/PAID，规范 §3.1 权益列）：降档判定的口径，
    随包下发只为让成文线能复核门禁，成文线**不重判**——判定结果已在 anchors[].presentation。
    它是产物属性不是用户属性，不破匿名纪律。
    """

    entitlement: Literal["FREE", "PAID"]
    evaluated_on: str | None = None
    """本次求值的基准日（as-of）：时效越界的判定基准，判定已在求值线做完、随包下发故可重放。
    成文线**只搬运不重算**——按运行时当天重判等于让同一份包在两天里过检结果不同。"""
    domains: list[str]
    releases: list[ReleaseRef]
    anchors: list[ReportAnchor]
    withheld_anchors: list[WithheldAnchor] = []
    gaps: list[GapRecord]
    personas_by_domain: dict[str, list[PersonaAsset]]
    checks_by_domain: dict[str, list[CheckAsset]]
    triggered_rules_by_domain: dict[str, list[TriggeredRule]] = {}
    """本户触发的规则条目，按域分组（键＝dom- 去前缀形态，同 ``personas_by_domain``）。

    **消费侧先建、生产侧后发**（同 provenance 那批的顺序纪律）：缺省空 = 生产方未升级的旧包，
    **不是"未触发"**——成文线据此不产生误判，也**不得据此拦截**。语义是"求值线判定后已经成立的
    规则"，成文线**不重判触发**（同"成文线不重判求值线"）。
    """
    banned_terms_by_domain: dict[str, list[str]]
    locked_texts_by_domain: dict[str, list[str]] = {}
    """本产物必挂的锁定文案 ID 集（图 v0.2 §2 报告数据包的"锁定清单"，枚举见 contracts
    `registries/locked_texts.md`）。

    **只有 ID，没有正文**：gen-locked 的定义是"引用锁定文案 ID 或结构化数据直接渲染，零生成"
    （规则 2.4）——正文在渲染层按 ID 取，成文线连看都不该看到，看不到就拼接不出来。

    键是 dom- 去前缀形态而非 art-：**成文线不认识 art-**（包内无产物字段，单元轴=dom-，
    图 v0.2 §2），art-→dom- 的换算由调用方在求值时做完，与 ``entitlement`` 同机制（调用方按它在
    生成哪个 art- 传入，规则引擎不持有产物清单）。缺省空 = 本产物无必挂文案，不是"未知"——生产方
    未升级的旧包据此不产生误判（宽进），真正的"该挂没挂"由册级校验报出（严查）。
    """
    anonymous_profile: EvaluationProfile

    def domain_anchors(self, domain: str) -> list[ReportAnchor]:
        """本域落点切片（单元输入只见本域，图 v0.2 §2 依赖只存在于求值线）。"""
        return [a for a in self.anchors if a.basis_tag.split("@")[0] == domain]

    def domain_triggered_rules(self, domain: str) -> list[TriggeredRule]:
        """本域触发规则切片（同 :meth:`domain_anchors`，单元输入只见本域）。"""
        return self.triggered_rules_by_domain.get(domain, [])


# ---------------------------------------------------------------------------
# 成文线 activity 出入参
# ---------------------------------------------------------------------------


class Card(BaseModel):
    """卡片（客户语域）：body/thesis 禁裸数字，数字经 {lkp-*} 占位；number_refs=占位符全集声明。

    ``number_refs`` 的粒度＝**记号本身逐字**（规则 1.9 两层模型，v2.8）：正文写
    ``{lkp-x.reading}`` 就声明 ``lkp-x.reading``，写 ``{lkp-x}`` 就声明 ``lkp-x``。
    定在"落点.项"而不是落点，理由是这个字段的定义就是"占位符全集"——而一个占位符现在
    可以是一项：按落点声明会让"声明了两项、正文只写了一项"这种半隐藏与合规稿在集合上长得一样，
    而封堵假坦白（``gate-number-ref-unused``）靠的正是两个集合逐字相等。
    需要落点粒度的下游（依据标注、册级解析）自己取记号的落点段，那是投影不是口径。

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


class ProvenanceNote(BaseModel):
    """页上挂的**依据标注**（规则 4.10c 标注必挂，v2.4）：来源 + 取数时间 + 可核性状态。

    **结构化数据，不是文案**：正文由渲染层按这几个字段出（规则 2.4 gen-locked"引用 ID 或结构化
    数据直接渲染，零生成"），成文线不拼接、不改写、不翻译。载体在**页**不在卡片，与锁定文案同理——
    "这一页上必须有这条标注"是页级装配契约；放到 :class:`Card` 上就等于交给写作层产出，而标注里
    全是数字（取数时间），写作器结构性地写不出来（卡片正文禁裸数字）。

    ``source=None`` 是经验条目的**事实**（规则 4.10 无外部依据、靠行业判断），不是待填的洞：
    渲染层据此说明"这是我们的经验判断"，禁编造来源。
    """

    model_config = ConfigDict(extra="forbid")

    lkp_id: str
    source: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    calibration: str


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


class AnchorBrief(BaseModel):
    """落点题名：id + 名字 + 量纲 + **项名清单**，**没有值**——叙事推导唯一看得见的落点信息。

    为什么砍掉值而不是"叮嘱它别写数字"：图 v0.2 §3 要求推导步"只组织怎么讲，不产生任何数字"。
    把值递过去再要求它别用，是拿 prompt 当门禁；不给值，它**结构性地产不出数字**——同"写作层
    拿不到锁定文案正文"的分层理由（规则 2.4）。数字是下一步（卡片写作）的事，那一步看得见值。

    ``items`` 是**项名不是值**（规则 1.9，v2.8 加）：一条落点分几项、分的是哪几项，
    是"这一章讲几件事"要用的信息——"卧室的灯光和客厅的灯光肯定会不一样"（用户裁决原话）
    正是推导该决定的事，而推导若不知道照度落点分着一般活动与阅读两项，它只能把两项挤成一条主张，
    到写作那一步再临场决定拆不拆——那正好是"讲什么"没有环节负责的老形态。
    加项名**不破**"看不见值"：项名与 ``name``/``unit`` 同类，是标签不是数，
    推导拿着 general/reading 依然产不出任何一个数字。
    """

    model_config = ConfigDict(extra="forbid")

    lkp_id: str
    name: str
    unit: str | None = None
    items: list[str] = []

    @classmethod
    def of(cls, anchor: ReportAnchor) -> AnchorBrief:
        return cls(
            lkp_id=anchor.lkp_id, name=anchor.name, unit=anchor.unit, items=anchor.item_names
        )


class NarrativeClaim(BaseModel):
    """一条主张 + 它要用的落点（叙事推导的产物，图 v0.2 §3 单元子图第一步）。

    主张是**内部语域**：它说"这一章讲哪几件事、每件事的取舍在哪"，不面向业主、不进产物。
    卡片按主张组织（一条主张一张卡），这是它存在的全部理由——真跑立案：推导步缺席时，
    user message 里最结构化的东西就是落点清单，模型顺着它一一对应是最省力解，于是 23 条落点
    变成 23 张"念数字"的卡（2026-08-29 ergonomics 22/22 正文与主旨句逐字相同）。

    ``anchors`` 是**建议不是强制**：写作那一步仍按本域全部落点校验引用（``gate-number-ref-*``）——
    推导步看不见值，让它决定"哪条数字能用"是越权；它决定的是"这件事要讲"。
    """

    model_config = ConfigDict(extra="forbid")

    claim: str
    anchors: list[str] = []


class JudgeCheckCount(BaseModel):
    """一条判据在一次送审里的触发次数（规则 4.17 门禁二的最小计数单元）。

    带 ``version`` 与 ``status``：触发率必须归到**判据的哪一版**上——判据改了 requirement 就是
    另一条判据了，把两版的触发混在一个数里，观察期数据读出来的是平均值，而门禁二问的是
    "这一版工不工作"。
    """

    model_config = ConfigDict(extra="forbid")

    check: str
    version: int
    status: str
    hits: int


class JudgeRun(BaseModel):
    """判官一次送审的台账（裁决 2026-08-29「先建计数载体，N 与阈值有数据再定」）。

    维度取够四个就停（存储最小化，不建通用平台）：**判据 × 份 × 批大小 × 触发**。
    ``batch_size``/``batches`` 是必需维度不是调试信息——真跑实测同一份文稿 22 张一次送审 0 命中、
    前 6 张送审 6 命中，**不记批量就无法回溯解释触发率的变化**，历史数据会变成一堆不可比的数。

    ``batches_failed`` 记的是"这一份里有几批没问成"：判官不阻塞（批失败只丢该批观察），
    但**丢了多少必须记**——不然一份只成功问了一批的稿子，和一份干干净净的稿子，在统计里长得一样。
    """

    model_config = ConfigDict(extra="forbid")

    cards_reviewed: int
    batch_size: int
    batches: int
    batches_failed: int = 0
    checks: list[JudgeCheckCount] = []


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
    claims: list[NarrativeClaim] = []
    """本单元推导出的主张集（图 v0.2 §3 第一步产物）：**内部语域，不进产物**。

    随结果上抛只为两件事：出问题时看得见"它当时打算讲哪几件事"（卡片退化是推导垮了还是写作垮了，
    没有这份东西分不开）；以及自迭代回路采样。渲染层不消费它——业主看到的是卡片。"""
    observations: list[JudgeObservation] = []
    judge_run: JudgeRun | None = None
    """本次送审的计数台账（``None`` = 判官没跑：本域无判官判据，或规则层就没放行）。

    与 ``observations`` 的分工：那个是**判出了什么**（内容，给重写循环与样本采集用），
    这个是**问了几次、问了多少张、每条判据中了几次**（计数，给规则 4.17 门禁二用）。
    观察数为 0 有两种截然不同的成因——"很干净"与"根本没问成"，只有台账区分得开。"""
    rewrites_used: int = 0
    releases: list[ReleaseRef] = []
    required_provenance: list[ProvenanceNote] = []
    """本单元**实际引用**的落点里需要标注的那些（规则 4.10c）：按 lkp_id 升序。

    与 ``required_locked_texts`` 同一条路：单元只**投影**不产出（内容全部来自落点的 provenance），
    挂载在装配层、校验在册级。与锁定文案的区别在于要求集从哪来——锁定文案的要求集随包给定，
    标注的要求集取决于**这一稿实际引用了哪几个落点**，只有写完卡片才知道，故必须在单元算。
    """
    required_locked_texts: list[str] = []
    """本域必挂的锁定文案 ID（自包内 ``locked_texts_by_domain`` 切片**原样透传**）。

    单元层只搬运不产出：选择在求值线（哪个 art- 要哪几条）、挂载在装配层、校验在册级——
    :class:`Card` 上没有放锁定文案的位置（``extra="forbid"``），写作器结构性地产不出它
    （规则 2.4 gen-locked 零生成）。走单元结果透传而不新开编排入参，是因为编排把单元结果原样
    喂给装配（载荷不透明），这条路不需要改编排侧一行。"""


class Page(BaseModel):
    """页（唯一知道"页"的层）：page_type 待 pt- 页型库编译，首版按域成页。

    ``locked_text_ids`` = 本页挂载的锁定文案 ID（规范 §7 全集，contracts
    `registries/locked_texts.md`）。**载体放在页而不是卡片**：gen-locked 文案是页/册级装配契约
    （"这一页上必须有这句话"），不是卡片级写作约束——放到 :class:`Card` 上等于把它交给写作层产出，
    与规则 2.4"零生成"直接冲突。挂载动作在装配层（确定性，按产物要求挂），成文单元只透传要求。
    """

    model_config = ConfigDict(extra="forbid")

    page_id: str
    domain: str
    page_type: str | None = None
    cards: list[Card]
    locked_text_ids: list[str] = []
    provenance_notes: list[ProvenanceNote] = []
    """本页挂的依据标注（规则 4.10c 标注必挂，v2.4）：页上引用了未过门/已过期落点，就得有它。

    它是隐藏禁令的等价物——拦截点从"说出来"移到"不标就说"。挂载在装配层（确定性投影），
    该挂没挂由册级校验按数据包这个独立来源报出（``gate-provenance-annotation-missing``）。"""


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
