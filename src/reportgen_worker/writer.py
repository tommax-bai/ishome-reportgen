"""卡片写作器：叙事推导+卡片写作的 LLM 执行件（gen-generated）。

prompt 只从报告数据包内的 persona 载荷与落点对象拼装——运行时不读任何人写的文本（规则 4.19）；
模型经 LiteLLM 网关按逻辑名调用（变化轴 3：`report-unit-compose.default`，
换模型改网关配置不改代码）。
输出为结构化 JSON 卡片：数字只经 {lkp-*} 占位（出口过检的零漂移前提）。

语域纪律在 prompt 侧的形态（规则 4.10a/4.10c/5.8）：落点按 ``presentation`` 分【可作支点】与
【未过门·建议口吻】两档逐条标注；判断句题目按断言预算切成"这轮许说/这轮不许说"两张清单。
引用纪律的形态（规则 1.9 两层模型，v2.8）：每条落点**逐行摆出它的合法记号**——只有一个值的
写整条，分了项的把每一项的记号列全（:func:`_anchor_line`）。反例一律不进 prompt（铁律一），
写不得的形态由 :mod:`reportgen_worker.gate` 拦。
prompt 只是第一道，**不是门禁**——真正拦截在 :mod:`reportgen_worker.gate`
（判据下沉次序 schema > 规则 > prompt > 判官，图 v0.2 §3）。
**v2.4 起没有落点被扣着不下发**（隐藏档取消）：未过门的照常进 prompt、也可以进主旨句，
它的依据标注由系统挂在页上——故 prompt 里明说"别自己写来源和日期"。

persona 四件（规则 4.13）在本仓的消费面已齐：①身份语域=system 头；②**判断句风格样例**=本模块
:func:`judgment_pairs`（好/坏对照句对，唯一以"示范"形态进 prompt 的判据）；③断言预算与
④禁词表在 :mod:`reportgen_worker.gate`（预算切两张清单进 prompt，禁词双消费=prompt 约束
+ 机检扫描，规则 4.15）。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from reportgen_worker.gate import (
    BARE_LKP_RE,
    BOUND_CARRIED,
    BOUND_REQUIRED,
    BOUND_ROOTS_BY_SIDE,
    BOUND_SIDE_NAME,
    BOUND_WORD_RE,
    CHINESE_NUMBER_RE,
    DIGIT_RE,
    PLACEHOLDER_RE,
    THESIS_SUPPORT,
    bound_expectation,
    item_tokens,
)
from reportgen_worker.models import (
    Card,
    EvaluationProfile,
    GapRecord,
    NarrativeClaim,
    PersonaAsset,
    ReportAnchor,
    Violation,
)

COMPOSE_LOGICAL_MODEL = "report-unit-compose.default"
_CARDS_ADAPTER: TypeAdapter[list[Card]] = TypeAdapter(list[Card])
_JSON_BLOCK_RE = re.compile(r"\[.*\]", re.DOTALL)


class WriterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    persona: PersonaAsset
    claims: list[NarrativeClaim] = []
    """叙事推导那一步定下的"讲哪几件事"（图 v0.2 §3 第一步产物）：**一条主张一张卡**。

    缺省空＝没有推导（老形态，只在单测夹具里出现）：prompt 退回按落点清单写，那正是
    "一数一卡"的成因，故生产路径上它恒非空——空的话 activity 已经先失败了。"""
    anchors: list[ReportAnchor]
    gaps: list[GapRecord]
    profile: EvaluationProfile
    banned_terms: list[str]
    backed_predicates: list[str] = []
    unbacked_predicates: list[str] = []
    feedback: list[Violation] = []
    attempt: int = 0


class WriterOutputError(Exception):
    """LLM 输出不可解析为卡片组——作为违规进入重写循环，不静默丢弃。"""


class CardWriter(Protocol):
    async def write(self, request: WriterRequest) -> list[Card]: ...


def _writes_numbers(text: str) -> bool:
    """样例正文是否自己就违反数字纪律（口径逐字同 gate：先剥占位符，再查三种写数形态）。"""
    stripped = PLACEHOLDER_RE.sub("", text)
    return bool(
        DIGIT_RE.search(stripped)
        or CHINESE_NUMBER_RE.search(stripped)
        or BARE_LKP_RE.search(stripped)
    )


def _self_violating(text: str, banned_terms: Sequence[str]) -> bool:
    """这句示范自己过不过得了机检（数字纪律 + 禁词，两处口径都取自 gate 的同一份实现）。"""
    return _writes_numbers(text) or any(term in text for term in banned_terms)


def judgment_pairs(
    persona: PersonaAsset, banned_terms: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """persona 四件之②：判断句风格样例（好/坏对照句对，规则 4.13）。

    **``reason`` 被刻意丢掉**：它是 cr- 判据编号，业主语域里没有编号，写作器也不需要认识判据
    编号——它只需要看会不会写。要编号的是判官层（只报编号不改写，:mod:`reportgen_worker.judge`），
    两层各拿各的那一半，样例不因此变成第二套判据下发。

    形态不合的条目**静默跳过**（同 :func:`reportgen_worker.gate.assertion_budget` 的既有写法）：
    样例是 release 数据，损坏条目归资产回路的核验跑批，运行时不替它兜底、更不因此拒绝成文——
    persona 少一条示范只是写得差一点，不是发不出。

    **一对里两句都必须自己过得了机检**，任一句脏则整对不下发。判据对称不是洁癖，是四轮 A/B
    量出来的（2026-08-28 真跑，budget+lighting 各 3 跑，同一份真实数据包，唯一变量是示范块）：

    ====== =================== ======== ========= =========
    轮次   过滤规则            下发对数 示范开    示范关
    ====== =================== ======== ========= =========
    一      只滤 ✓（✗ 原样）    十九       0/6       3/6
    二      **两侧对称**          四       3/6       4/6
    三      滤 ✓、✗ 脏则只丢 ✗   十三       0/6       6/6
    四      **两侧对称**          四       4/6       2/6
    ====== =================== ======== ========= =========

    半过滤的两轮方向一致且极端：**prompt 里的示范句越多，过检率越低**，机制在
    ``gate-sample-verbatim-copy``——模型把 ✓ 逐字抄进卡片当结论（第三轮 budget 三跑全中）。
    示范句是完整、好看、可独立成立的句子，照抄它比自己写省力，而抄来的话既与这家人无关也没有
    落点背书。第一轮的 ✗ 侧同理：`餐厅吊灯下沿距桌面700-800mm。` 这类反例把裸数字带进正文，
    模型不区分那句挂的是对钩还是叉。

    第三轮是**为保样本量特意试的非对称规则**（✗ 脏只丢 ✗、留下干净的 ✓，十九对里可留十三对、
    六域无一归零），结果比对称规则更差：样本量不是这里的稀缺资源，可抄性才是代价。留四对。

    对称规则本身（二、四轮合计 ON 7/12 : OFF 6/12）**在过检率上与关闭无显著差别**——照实记：
    OFF 单轮在 2/6 到 6/6 间摆动，每格 6 跑量不出这个量级的差异，不要拿它当"示范有效"的证据。
    留着它的理由是语域（真跑里 ON 的卡片明显更像在跟这家人说话），不是过检率；而非对称/无过滤
    的 0/12 是确定的有害，两者不可混为一谈。

    丢弃是运行时的自保，不是修数据：根治在改源重编译（规则 4.19——示范改用 {lkp-*} 占位或体验化
    表达，且**不写成能独立成立的结论句**，否则修好了数字仍会被抄），丢弃条数即自迭代回路该收到
    的信号。判据与 gate 同口径是硬要求：prompt 与门禁两处口径不一致，写作器就会被反复打回却不
    知道该怎么改。
    """
    pairs: list[tuple[str, str]] = []
    for entry in persona.judgment_samples:
        if not isinstance(entry, dict):
            continue
        bad, good = entry.get("bad"), entry.get("good")
        if not isinstance(bad, str) or not isinstance(good, str):
            continue
        bad, good = bad.strip(), good.strip()
        if not bad or not good:
            continue
        if _self_violating(good, banned_terms) or _self_violating(bad, banned_terms):
            continue
        pairs.append((bad, good))
    return pairs


def _wording_note(anchor: ReportAnchor) -> str:
    """**这个空要填的值是什么特征**，写在记号旁边（用户裁决 2026-08-30 的前半句）。

    四种特征各说一句：固定值（不许加限定词）／只给下限、只给上限（必须写那一族的词，可用词
    逐字列出）／两端齐的区间（不许加单侧词）；再加并列多项那一种（记号自带，不用写）。单位永远
    由记号带出，故另说一句。**后半句"检测时检测内容合不合这个特征"由
    :func:`reportgen_worker.gate._adjacent_wording_violations` 执行，两边共用
    :func:`reportgen_worker.gate.bound_expectation` 一份判定**——说的和拦的不会是两回事。

    立案与形态变更史：原先渲染层把"不超过"一起渲出来、写手一律不许写边界词，结果成品叠字
    （`全屋灯光颜色种类不能多于 不超过 3 种 种。`）。改成禁词表不管用——列了「不少于/不低于/
    至少/不超过」，模型转头写了「不能多于」和「上限」，都不在表上；改成抽象说"自带边界说法"
    也不管用，十二跑九跑复发。**根因是那句中文的语法主干正好落在洞里**：写手要说一个上界，
    顺口的说法就是"最多 3 种"，我们却要求它写"…种类▢。"。故边界说法交还给句子，
    而"这个空是什么"必须摆在空上——单位那一半正是这么给的，十二跑零复发。

    **反例仍然不进 prompt**（铁律一）：这里给的是"必须从这几个词里挑一个"的**正面清单**，
    不是"不许写哪些词"。两者失败方向不同：正面清单漏一个词只多烧一轮重写，禁止清单漏一个词
    缺陷就进成品。
    """
    parts = []

    def _choices(side: str) -> str:
        return "、".join(f"「{root}」" for root in BOUND_ROOTS_BY_SIDE[side])

    kind, side = bound_expectation(anchor)
    if not anchor.has_items:
        if kind == BOUND_REQUIRED:
            assert side is not None
            parts.append(
                f"这个空要填的值**只给了{BOUND_SIDE_NAME[side]}**，记号出来是裸的数（带单位）——"
                f"句子里必须写清这是{BOUND_SIDE_NAME[side]}，从这几个词里挑一个放在记号前面："
                f"{_choices(side)}"
            )
        elif isinstance(anchor.value, dict):
            parts.append("这个空要填的是一个**两端都给了的范围**，写「在…到…之间」，别加单侧限定词")
        else:
            parts.append(
                "这个空要填的是一个**确定的数**，前面别加「不低于」「最多」这类限定词——"
                "数据里没有那层意思，加了就是替它下一个它没给的判断"
            )
    else:
        # 分项落点：整条引用与按项引用是**两个不同的空**，特征也不同，必须分开说——
        # 只说整条那一种，模型按项引用时会照着一句对不上的话写，然后被打回。
        if kind == BOUND_CARRIED:
            parts.append("整条引用时，记号会把每一项的边界说法一起带出来，你不用也不要再写")
        else:
            parts.append("整条引用时，各项都是给定的数值，前面别加「不低于」「最多」这类单侧限定词")
        by_side: dict[str, list[str]] = {}
        for name in anchor.item_names:
            item_kind, item_side = bound_expectation(anchor, name)
            if item_kind == BOUND_REQUIRED and item_side is not None:
                by_side.setdefault(item_side, []).append(name)
        for item_side, names in by_side.items():
            tokens = "、".join(f"{{{anchor.lkp_id}.{n}}}" for n in names)
            parts.append(
                f"按项引用 {tokens} 时出的是裸的数，那一项**只给了"
                f"{BOUND_SIDE_NAME[item_side]}**，句子里必须写清：{_choices(item_side)}"
            )

    if anchor.unit:
        parts.append(f"单位「{anchor.unit}」由记号自己带出来，正文里不用也不要再写一遍")
    collision = BOUND_WORD_RE.search(anchor.name)
    if collision is not None:
        parts.append(
            f"这条的题名里带着「{collision.group(0)}」——题名是内部标签，照抄它未必对上这个空的"
            "特征，按上面那条写"
        )
    return "\n  " + "；".join(parts)


def _anchor_line(anchor: ReportAnchor) -> str:
    """落点的下发行：题名 + 值 + 语域档 + **这条落点可以怎么引用**（规则 1.9，v2.8）。

    合法写法**逐行摆出来**（数据驱动：记号从 value 里的项算出来，不是编的）——同"撞禁词的落点
    逐行点名"那条路径。立案证据：灯光域同包同码同参六轮 0/6 过检，六轮全部 27 种越界占位符
    **27/27 逐字等于「真实落点 id」＋「该落点 value 字典里一个真实的键」**——模型想说的那句话
    （"沙发旁读书那块要单独加亮"）当时没有合法写法，而 prompt 里也没有一处告诉它有哪些选择。

    **反例不进这里**（prompt 铁律一，三次真跑打脸）：不写"不要写 {lkp-x.min}"这类禁止形态——
    把禁句写进指令，模型会照抄禁句本身（「得一起定」那轮 4/5 主张逐字带它）。禁止形态由
    :mod:`reportgen_worker.gate` 拦，prompt 只正面给它数据算出来的合法选择。
    """
    tier = "【可作支点】" if anchor.presentation == THESIS_SUPPORT else "【未过门·建议口吻】"
    head = (
        f"- {anchor.lkp_id}（{anchor.name}，{anchor.unit or '无单位'}）"
        f"= {json.dumps(anchor.value, ensure_ascii=False)}{tier}"
    )
    tokens = item_tokens(anchor)
    if tokens:
        ref = f"\n  这条分 {len(tokens)} 项，各项的记号：" + "、".join(tokens)
    else:
        ref = f"\n  这条只有一个值，引用写 {{{anchor.lkp_id}}}"
    return head + ref + _wording_note(anchor)


def build_messages(request: WriterRequest) -> list[dict[str, str]]:
    """prompt 拼装（纯函数，可单测）：素材全部来自 release 数据载荷。"""
    banned = "、".join(request.banned_terms) if request.banned_terms else "（无）"
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    # 对照句对是"这么写不行 → 这么写才对"的示范，模型最吃这个形态；放在纪律之后、输出格式之前，
    # 让它先读完规则再看规则长什么样。下发的每一句（含 ✗）都自己过得了机检（见 judgment_pairs）。
    # 主张是这一稿的骨架（图 v0.2 §3 第一步产物）：一条主张一张卡，正文写"为什么"不复述主旨句。
    # 真跑立案：没有这一段时，最结构化的输入是落点清单，模型顺着它一一对应，
    # 23 条落点写成 23 张"念数字"的卡、22/22 正文与主旨句逐字相同（2026-08-29）。
    claims_discipline = (
        "8. 下面给了这一章**要讲的几件事**，一件事写一张卡、按给的顺序写，"
        "不要多写、不要按落点一条一张。主旨句说这件事的结论，"
        "正文说**为什么是这个数、它管的是哪一刻、放弃了什么**——"
        "正文与主旨句逐字相同会被打回（那等于这张卡什么都没讲）。"
        "同一张卡里几条落点**各给各的理由**，不许在它们之间编「得一起定/互相让/配着调」"
        "这种相互约束——数据里没有的联动就是没有；\n"
        if request.claims
        else ""
    )
    pairs = judgment_pairs(request.persona, request.banned_terms)
    samples = (
        "这个域里，同一件事这么写不行、这么写才对：\n"
        + "\n".join(f"✗ {bad}\n✓ {good}" for bad, good in pairs)
        + "\n（示范只管口吻与句式，别照抄句子——照抄会被打回。）\n"
        if pairs
        else ""
    )
    system = (
        f"{request.persona.identity}\n"
        "写作纪律（违反即被机检打回）：\n"
        "1. 正文与主旨句不得出现任何数字字符；需要数字处写 {lkp-id} 占位符，"
        "渲染层会替换为求值结果。**中文数字同样算数**——"
        "「三到五倍」「不低于九十」「七十多厘米」「近半」都要换成占位符；"
        "纪律管的是数不是字形。（列举计数不算：「四个区域」「这三项」可以写）\n"
        "2. 只能引用下方给出的落点对象，不得自造数字或引用不存在的 lkp-。"
        "**每条落点后面都写着它可以怎么引用，照着那个记号写**："
        "只有一个值的落点写 {lkp-id}，值是区间就整条渲染成区间（如「亮 3-5 倍」），"
        "句式用「在…之间」「…到…」；分了项的落点（分场景、分档位、分维度、分项）"
        "已把每一项的记号逐个列出，要说哪一项就写哪一项的记号，一个记号出的就是那一项的值；"
        "**每条落点下面写着这个空要填的值是什么特征**（是一个确定的数、还是只给了上限/下限、"
        "还是一个范围），照那句话写：只给一侧的必须在记号前面写清是上限还是下限（可用的词逐字"
        "列在那里），是确定的数就别加限定词；**单位一律由记号自己带出来，正文里不要再写一遍**；\n"
        "3. 落点分两档：标【可作支点】的可以拿来下判断；标【未过门·建议口吻】的照常用，"
        "**主旨句里也可以出现**，但语域限「我们建议…」「按行业通行做法…」，"
        "不许写成「国标要求」「必须」这类标准口吻——它没有外部依据背书；\n"
        "4. 不许把 lkp- 开头的内部编号写进正文或主旨句——业主不认识这些编号。"
        "要用它的数字就写 {lkp-id} 占位；要说这条没有依据背书，用人话说（如"
        "「这一项目前只能给参考范围」），不要点名编号。"
        "**记号里点号后面那一段（项名）同样是内部标签**：它只出现在记号里，"
        "那一项是哪个场合、哪个档位、哪个分项，正文里用人话说；\n"
        f"5. 判断句只允许落在这几个题目上：{backed}。"
        "写了判断句就在 assertions 里声明用的是哪一个；其余题目这轮没有背书，不许下结论；\n"
        f"6. 禁词（一个都不能出现）：{banned}。"
        "**落点的名字里可能就带着禁词**（如「主通道净宽」「照度标准值」）——名字是内部标签，"
        "不是要你照抄的说法：引用它的数字写 {lkp-id} 占位，那件事本身用人话说；\n"
        "7. 没有外部依据或已过期的落点，它的**来源与取数时间由系统自动挂在这一页上**，"
        "你不要在正文里写来源、标准号或日期——写了既是裸数字违规，也会和系统挂的那份对不上；\n"
        f"{claims_discipline}"
        f"{samples}"
        "输出：JSON 数组，每个元素 "
        '{"thesis": 主旨句, "body": 正文, "number_refs": [引用的 lkp- 列表], '
        '"assertions": [声明使用的判断句题目]}，'
        "不要输出数组以外的任何内容。"
    )
    anchor_lines = [_anchor_line(a) for a in request.anchors]
    gap_lines = [f"- {g.lkp_id}：{g.reason}" for g in request.gaps]
    user_parts = [
        f"领域：{request.domain}",
        "这家人的情况（匿名）：" + json.dumps(request.profile.layout_features, ensure_ascii=False),
    ]
    if request.claims:
        user_parts.append(
            "这一章要讲的几件事（一件一张卡，按此顺序）：\n"
            + "\n".join(
                f"{i + 1}. {c.claim}"
                + (f"（这件事大概会用到：{'、'.join(c.anchors)}）" if c.anchors else "")
                for i, c in enumerate(request.claims)
            )
        )
    user_parts.append("可引用的落点对象：\n" + "\n".join(anchor_lines))
    if request.unbacked_predicates:
        # 坦白语域（规则 4.18 v2.5，用户裁决 2026-08-29 晚选 B）：原口径"可以描述现象，不能给判断"
        # 实测把模型逼进元语言与伪因果（"拆除量直接挤压定制柜投影面积的可布设范围"——机制是编的；
        # "随空间拓扑实时耦合"——内核是坦白，包装成伪学术）。坦白对业主是真实的预期管理。
        user_parts.append(
            "这轮**没有背书**的题目——要提它就只许坦白，不许绕着它作分析：\n"
            + "\n".join(f"- {p}" for p in request.unbacked_predicates)
            + "\n坦白＝用大白话直说：这个**题目**这轮给不出可靠结论、取决于什么、等什么"
            "（只根据你拿到的信息说，**不要编原因**）。"
            "不许用「权重」「耦合」「锚定」这类行话把坦白包装成分析——业主读不懂那些词。"
            "**坦白只许用于上面这些题目和「求不出的落点」清单里的条目**："
            "凡是给了值的落点——包括标【未过门·建议口吻】的——**必须照常给出**，"
            "说「这项给不出数」而它明明有值，是被禁止的隐藏，会被判官记下；"
        )
    if gap_lines:
        user_parts.append(
            "本次求不出的落点（不要硬写，可坦白留待现场确认）：\n" + "\n".join(gap_lines)
        )
    if request.feedback:
        user_parts.append(
            f"上一稿（第 {request.attempt} 稿）被机检打回，逐条修正：\n"
            + "\n".join(f"- [{v.check}] {v.detail}" for v in request.feedback)
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_cards(raw: str) -> list[Card]:
    match = _JSON_BLOCK_RE.search(raw)
    if match is None:
        raise WriterOutputError("输出中未找到 JSON 数组")
    try:
        return _CARDS_ADAPTER.validate_json(match.group(0))
    except ValidationError as e:
        raise WriterOutputError(f"卡片结构不合法：{e}") from e


class LlmCardWriter:
    """LiteLLM 网关调用（openai 兼容 /chat/completions）。base/key 经环境变量，逻辑模型名固定。"""

    def __init__(self) -> None:
        self._base = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
        self._api_key = os.environ.get("LITELLM_API_KEY", "")

    async def write(self, request: WriterRequest) -> list[Card]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": COMPOSE_LOGICAL_MODEL,
                    "messages": build_messages(request),
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        return parse_cards(content)
