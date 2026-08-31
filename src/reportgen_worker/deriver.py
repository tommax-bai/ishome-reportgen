"""叙事推导：单元子图的第一步（图 v0.2 §3），在卡片写作之前。

它回答的问题只有一个：**这一域讲哪几件事**。产物是主张集（内部语域），不是文案、不含数字。

为什么单独一步、单独一个模块（不是给写作器加两句 prompt）：

- **设计里本来就有两步**，实现把它塌成了一步。代价在 2026-08-29 真跑里第一次看得见：ergonomics
  单元 23 条落点 → 23 张卡，22/22 正文与主旨句逐字相同、``assertions`` 0/22。成因不是模型笨——
  一次调用里最结构化的输入就是落点清单，顺着它一一对应是最省力解，"讲什么"没有任何环节负责。
- **语域异质**：推导是内部语域（"厨房的高度不该按平均身高定"），写作是客户语域（"你家台面…"）。
  两种语域塞进一次调用，输出必然是其中一种压过另一种——真跑里压过的那种就是"念数字"。
- **模块边界即隔离**：本模块与 :mod:`~reportgen_worker.writer` / :mod:`~reportgen_worker.judge`
  是 import-linter 里互不可见的同层兄弟。推导器拿不到判官的判据，也拿不到写作器的语域示范
  （persona 的 ✓ 句可抄性已实测，见 writer.judgment_pairs）——只拿身份、落点题名、断言预算题目。

**推导步看不见落点的值**（:class:`~reportgen_worker.models.AnchorBrief` 只有 id/名字/量纲/项名）：
图 v0.2 §3 要求这一步"不产生任何数字"，不给值是让它**结构性地产不出**，而不是叮嘱它别写。

v2.8（规则 1.9 两层模型）加进来的是**项名清单，不是值**：一条落点分几项、分的是哪几项，
决定的是"这一章讲几件事"——"卧室的灯光和客厅的灯光肯定会不一样"（用户裁决原话）正是这一步的题目。
不给项名，推导只能把分场景落点当成一件事，拆不拆留给写作步临场决定，而"讲什么"没有环节负责
恰恰是这一步存在的理由。项名与名字/量纲同类（是标签不是数），故"看不见值"这条**不破**——
拿着 general/reading 依然产不出任何一个数字。项名逐字进主张的风险由 :func:`parse_claims` 的
确定性校验兜住（同禁词、耦合词面、条目照抄那三条路径：prompt 里叮嘱无效已实测三次）。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from reportgen_worker.gate import (
    CHINESE_NUMBER_RE,
    DIGIT_RE,
    banned_route_of,
    banned_terms_block,
)
from reportgen_worker.models import (
    AnchorBrief,
    EvaluationProfile,
    GapRecord,
    NarrativeClaim,
    TriggeredRule,
)

DERIVE_LOGICAL_MODEL = "report-unit-derive.default"

# 落点间相互约束的措辞词面（用户裁决 2026-08-29 晚：落点间因果/耦合不得编造，规范 v2.5 §14.10）。
# 全部逐字来自真跑主张（5/5 命中那轮 + 词面进 prompt 被照抄那轮），不收想象词面。
# **词面不进 prompt**：把禁句写进指令，模型会照抄禁句本身——「得一起定」作为反例写进 prompt 的
# 那一轮，4/5 主张逐字带它（与示范句可抄性同病：模型不区分句子挂的是对钩还是叉）。
# prompt 只说抽象规则，词面在此确定性校验，命中即打回、理由走反馈循环。
COUPLING_PHRASES = ("得一起定", "一起定", "配着调", "互相让", "协调着看", "其实是一回事")
_CLAIMS_ADAPTER: TypeAdapter[list[NarrativeClaim]] = TypeAdapter(list[NarrativeClaim])
_JSON_BLOCK_RE = re.compile(r"\[.*\]", re.DOTALL)


class DeriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    identity: str
    """persona 的身份语域（**只有这一件**）：判断句样例与禁词都不给——样例的可抄性已实测，
    推导步抄了一句示范，写作步会把它当结论抄进卡片，两步一起被 gate-sample-verbatim-copy 打回。"""
    anchors: list[AnchorBrief]
    gaps: list[GapRecord] = []
    profile: EvaluationProfile
    banned_terms: list[str] = []
    """本域禁词（规则 4.15 双消费的第三个消费点，2026-08-29 真跑补上）。

    推导步的产物**逐字进写作 prompt**：主张里带一个禁词，写作器就会跟着写进卡片、被机检打回，
    而下一稿拿到的主张还是那句——真跑实测整单元连吃三稿死在同一个词（`净宽`）上。
    prompt 与门禁两处口径不一致，写作器会被反复打回却不知道该怎么改；
    这里是同一条纪律往上游多走一层。"""
    triggered_rules: list[TriggeredRule] = []
    """本户**已经触发**的规则条目（求值线判定完随包下发，成文线不重判触发）。

    它回答"这一章该讲到什么"——**这正是推导步的题目**，故落在这一步不落写作步：
    写作步拿到的是主张，讲什么已经定完了。逐字照抄的风险由 :func:`parse_claims` 的确定性校验
    兜住（同禁词那条路径；prompt 里叮嘱无效已实测三次）。"""
    backed_predicates: list[str] = []
    unbacked_predicates: list[str] = []
    banned_term_groups: dict[str, list[str]] = {}
    """禁词按"为什么禁"分组（同写作步）：缺省空＝旧包，退回一行平表下发。"""
    feedback: list[str] = []
    """上一次推导被打回的理由（重试时下发）：同写作那条循环的形态——不告诉它哪儿错了，
    它只会把同一句再写一遍（真跑实测：同一个禁词连吃三稿）。"""
    previous_claims: list[NarrativeClaim] = []
    """上一稿的主张，原样带回（用户裁决 2026-08-30，射程＝**所有裁判场**）。

    此前这一步只递一句错误文字、稿子本身不回传——与写作步 2026-08-30 早先修掉的是同一个毛病，
    只是漏在了这一步。真跑立案（2026-08-30 晚 w2）：「一起定」连吃三轮，整单元死在推导步。"""
    earlier_feedback: list[list[str]] = []
    """更早几稿的打回理由，由旧到新、不含最近这一稿（同写作步）：只带理由不带原稿，
    让它看见"这条我已经试过了"——缺的正是这句话。"""


class DeriverOutputError(Exception):
    """推导输出不可解析或为空——作为违规进入重写循环，不静默退回"没有主张"的老形态。

    **带着被打回的那一稿**（``claims``，解析得出来时）：打回要带原文是所有裁判场的统一形态
    （用户裁决 2026-08-30），而这一步的"原文"就是它刚写出来的那组主张。
    """

    def __init__(self, message: str, claims: Sequence[NarrativeClaim] = ()) -> None:
        super().__init__(message)
        self.claims = list(claims)


class NarrativeDeriver(Protocol):
    async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]: ...


def _rewrite_part(request: DeriveRequest) -> str:
    """打回段：**更早各轮的理由 + 上一稿原文 + 这一稿哪儿错**。

    形态出自用户裁决 2026-08-30，射程是**所有裁判场**。

    与写作步同一形态、同一理由：只递一句错误文字而不回传稿子，等于让它"逐条改"一份它看不见的
    东西；不给更早几轮，它看不见"这条我已经试过了"。真跑立案（2026-08-30 晚 w2）：「一起定」
    连吃三轮，整单元死在这一步——写作步早先修掉的毛病原样漏在这里。

    更早各轮**只留理由不留原稿**：原稿摆在眼前，改动会退化成在旧句子上挪字。
    """
    lines: list[str] = []
    if request.earlier_feedback:
        lines.append(
            "更早几稿也被打回过（只给理由，原稿不附——要的是换个说法，不是在旧句子上改字）："
        )
        for draft, reasons in enumerate(request.earlier_feedback, start=1):
            lines.append(f"  第 {draft} 稿：{'；'.join(reasons) or '（无）'}")
        lines.append("")
    if request.previous_claims:
        lines.append("上一稿在下面。**只改被打回的地方，没点到的主张原样抄回**（改动越少越好）：")
        lines.extend(f"  {i + 1}. {c.claim}" for i, c in enumerate(request.previous_claims))
    seen = {r for round_ in request.earlier_feedback for r in round_}
    lines.append("这一稿被打回的理由：")
    for f in request.feedback:
        again = " ← 前面几稿也栽在这条，上次那个改法没用，换一种说法" if f in seen else ""
        lines.append(f"  ✗ {f}{again}")
    return "\n".join(lines)


def build_derive_messages(request: DeriveRequest) -> list[dict[str, str]]:
    """推导 prompt（纯函数，可单测）：素材只有身份、落点题名、缺口、匿名画像、断言预算题目。"""
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    unbacked = "、".join(request.unbacked_predicates) if request.unbacked_predicates else "（无）"
    banned = banned_terms_block(request.banned_terms, request.banned_term_groups)
    system = (
        f"{request.identity}\n"
        "这一步你**不写给业主看**，你在决定这一章讲哪几件事。纪律：\n"
        "1. 每条主张要有**取舍**，不是标题。"
        "「讲讲台面高度」不是主张；「厨房的高度不该按平均身高定，该按主厨的身体定」是主张；\n"
        "2. **不许出现任何数字**（阿拉伯数字、中文数字、占位符都不要）——数字是下一步的事，"
        "你这一步连值都没拿到；\n"
        "3. **先把落点按「其实是同一件事」归组，再给每组写一条主张**——"
        "一个落点一条主张等于把落点表换了个排版，那正是要修的东西。"
        "归组的理由是**同属一件事**（同一件家具、同一个动作、同一个空间）——"
        "床面高和床侧净距同属「床」，**就该进同一条主张**，正文里各给各的理由；"
        "但**不许把同组落点写成相互约束的关系**（说 A 的取值牵制 B、两者要配合着调整之类）——"
        "数据里没有这种联动就不许说。归组照做，关系别编——这是两件事；"
        "每条主张挂上它这一组的落点 id（可以挂多条；说取舍不说数的主张也可以一条不挂）；\n"
        "3b. 落点是这一域**已经算出来的**东西，能归进某件事的就别丢在外面——"
        "宁可一条主张多带几个落点，也不要只挑几条讲、剩下的大半不提；\n"
        "3c. 条目分两档，**权重不同**：「这套户型触发的条目」是**这一章必须讲到的点**"
        "（每条都要落进某条主张里）；「通行做法条目」**可以讲到，但别为它挤掉这一户的事**——"
        "前者是这户独有的，后者对谁都成立。两档都**用你自己的话讲**："
        "条目是内部写法，逐字搬进主张会被机检打回。讲户型条目时带上它**为什么对这户成立**"
        "（条目后面括号里那句就是理由）——「因为你家阳台带家政位」这种话才是业主要看的，"
        "凭空说「阳台要留清洁位」不是；\n"
        "3d. 有的落点**分了项**（同一件事的不同场合、档位或分项，清单里逐条标着分几项）——"
        "这几项是分开讲还是合起来讲**由你定**：对这家人真是两回事（不同场合、不同档位）"
        "就分成两条主张，是一回事就一条主张里带着。"
        "项名是内部记号，主张里要用人话说那一项是什么场合、什么档位；\n"
        "4. 能下结论的题目只有这些："
        + backed
        + "；这些题目这轮**没有背书**，只能描述不能下判断："
        + unbacked
        + "。没背书的题目若要讲，只许作为**坦白主张**（用户裁决：坦白缺口）：用大白话直说"
        "「这件事这轮给不出可靠的数，取决于什么、等什么才算得出来」——"
        "不许绕着它作描述性分析，不许发明因果去填（「A 挤压 B」「随 X 耦合」这类都是编的）。"
        "**坦白只许用于这些题目和「求不出的落点」清单**：清单之外的落点都有算好的值，"
        "把有值的落点说成「给不出数」是被禁止的隐藏——值多软都要照讲，软的自会带标注；\n"
        "5. 讲几件事由这一域真有几件事决定——通常三到五件。宁可少讲一件讲透，"
        "不要为了铺满而拆出没有取舍的主张；\n"
        f"6. 这些词一个都不能出现（下一步要照着你的主张写，你用了它就会被机检打回）——{banned}\n"
        "**落点的题名里就带着其中一些**——带了的那几条已在下面逐行标出来。"
        "题名是内部标签不是说法，你在主张里要换成业主读得懂的话；\n"
        '输出：JSON 数组，每个元素 {"claim": 主张, "anchors": [用到的落点 id]}，'
        "不要输出数组以外的任何内容。"
    )

    # 名字撞禁词的落点**逐行点名**（数据驱动：banned ∩ name，算出来的不是编的）。全局那句
    # "名字里可能带着这些词"实测不够——ergonomics 的推导在「净宽」上带着反馈连吃三稿
    # （temperature=0 下，落点清单每行都写着"主通道净宽"，全局提醒压不过逐行复现）。
    def anchor_line(a: AnchorBrief) -> str:
        hits = "、".join(f"「{t}」" for t in request.banned_terms if t in a.name)
        note = f"（名字里的 {hits} 是内部词，勿写进主张）" if hits else ""
        # 分项落点逐行标出**分几项、哪几项**（规则 1.9，v2.8）：拆不拆是这一步的决定，
        # 不告诉它分了项，它连"可以拆"都不知道。给的是项名不是值——这一步照样产不出数字。
        items = (
            f"（分 {len(a.items)} 项：{'、'.join(a.items)}——项名是内部记号，主张里说人话）"
            if a.items
            else ""
        )
        return f"- {a.lkp_id}（{a.name}{'，' + a.unit if a.unit else ''}）{note}{items}"

    anchor_lines = [anchor_line(a) for a in request.anchors]
    user_parts = [
        f"领域：{request.domain}",
        # 户型特征**只下发依据文字不下发标记名**：键是内部标识符（`balcony_service` 这类），
        # 而主张逐字进写作 prompt，内部词面混进去就会出现在卡片上（客户语域禁内部编号）。
        # 值本身就是人话依据（"阳台内有洗衣机设备位"），够推导用且不带内部词面。
        "这套户型（匿名）："
        + ("；".join(request.profile.layout_features.values()) or "（暂无户型信息）"),
        # 只给题名与项名不给值：这一步不产生数字（图 v0.2 §3），不给值即产不出
        "本域可用的落点（只有题名和分项，值在下一步）：\n" + "\n".join(anchor_lines),
    ]
    # 按触发类型分档下发：户型条目是这户独有的（必讲），always 条目是通行做法（可讲）。
    # 真库实测 always 有 7 条且分布不均（照明 3／用材 2／造价 1／收纳 1），一律"必须讲到"
    # 等于给收敛最差的章再压三个通用话题——而"通用专业建议"正是这条线要摆脱的东西。
    by_layout = [r for r in request.triggered_rules if r.triggered_by.evidence]
    always_rules = [r for r in request.triggered_rules if not r.triggered_by.evidence]
    if by_layout:
        user_parts.append(
            "这套户型触发的条目（**必须讲到**，换成人话讲；括号里是它对这户成立的理由）：\n"
            + "\n".join(_rule_line(r) for r in by_layout)
        )
    if always_rules:
        user_parts.append(
            "通行做法条目（**可以讲到**，但别为它挤掉这一户的事）：\n"
            + "\n".join(_rule_line(r) for r in always_rules)
        )
    if request.feedback:
        user_parts.append(_rewrite_part(request))
    if request.gaps:
        user_parts.append(
            "这几条落点这次**没有值**。正文里一个字都不要提，**也不许为它单独写一张卡**——\n"
            "报告是一次性交付物，交到业主手上就是最终稿，里头不该有「以后再算给你」这种态：\n"
            "既不许写成「等你确认」「等你提供」，也不许写成「我们下一步补给你」。\n"
            "讲不了的就不讲（规则 4.18 宁薄勿撑）。也不许编数、不许绕着它作分析：\n"
            + "\n".join(f"- {g.lkp_id}：{g.reason}" for g in request.gaps)
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _rule_line(rule: TriggeredRule) -> str:
    """触发条目的下发行：内容 + 理由 + 依据。

    ``always`` 触发的条目没有依据（它对谁都成立），**不写括号**——写"因为：无"会诱出
    "根据通用规范"这类无依据的背书话术。
    """
    why = f"；{rule.rationale}" if rule.rationale else ""
    evidence = f"（因为这户：{rule.triggered_by.evidence}）" if rule.triggered_by.evidence else ""
    return f"- {rule.content}{why}{evidence}"


def parse_claims(
    raw: str,
    known_anchor_ids: set[str],
    banned_terms: Sequence[str] = (),
    triggered_rules: Sequence[TriggeredRule] = (),
    item_names: Sequence[str] = (),
    banned_groups: Mapping[str, list[str]] | None = None,
) -> list[NarrativeClaim]:
    """解析主张集，并**剔除推导步自造的落点 id**（保留主张本身）。

    自造 id 不当成致命错误：``anchors`` 是给写作器的建议，真正的引用校验在
    ``gate-number-ref-unresolved``（写作步，按本域全部落点判）。剔除是为了不把一个不存在的
    id 递进下一个 prompt——那等于请写作器去引用一条没有的落点。

    空数组是**失败**不是"没什么可讲"：一域有落点却推导不出一件事，说明这一步没工作；
    静默放行会让下一步退回没有主张的老形态，而那正是这一步要修的东西（绝不静默假成功）。

    五道确定性校验（耦合词面、禁词、条目照抄、**项名**、**数字**）走的是同一条理由：主张逐字进写作
    prompt，内部词面混进去就会出现在卡片上，而 prompt 里叮嘱压不住已实测三次。
    """
    match = _JSON_BLOCK_RE.search(raw)
    if match is None:
        raise DeriverOutputError("推导输出中未找到 JSON 数组")
    try:
        claims = _CLAIMS_ADAPTER.validate_json(match.group(0))
    except ValidationError as e:
        raise DeriverOutputError(f"主张结构不合法：{e}") from e
    cleaned = [
        claim.model_copy(update={"anchors": [a for a in claim.anchors if a in known_anchor_ids]})
        for claim in claims
        if claim.claim.strip()
    ]
    if not cleaned:
        raise DeriverOutputError(
            "推导没有产出任何主张",
            claims=cleaned,
        )
    # 数字：推导步纪律第 2 条明写"不许出现任何数字"，此前**只在 prompt 里叮嘱、没有校验**——
    # 而"prompt 叮嘱压不住"正是上面四道存在的理由，这一道漏了。真跑立案（2026-08-30 晚）：
    # 主张写"暖冷调子加起来不能超过三种"，逐字进写作 prompt，写作步照抄后被 gate-chinese-numeral
    # 打回；重写两轮拿到的主张还是那句——连吃三稿的老形态换了条判据重演。
    # 与写作步同一份正则（同一份口径，坑单三）：列举计数不在射程内（"四个区域"不算数）。
    numerals = sorted(
        {
            found.group(0)
            for pattern in (DIGIT_RE, CHINESE_NUMBER_RE)
            for c in cleaned
            if (found := pattern.search(c.claim))
        }
    )
    if numerals:
        raise DeriverOutputError(
            f"主张里写了数 {numerals}——数字是下一步的事，你这一步连值都没拿到。"
            "把那句话改成不带数的说法：要说这件事有个上限，就说「有上限」，别说上限是多少",
            claims=cleaned,
        )
    coupling = sorted({w for w in COUPLING_PHRASES for c in cleaned if w in c.claim})
    if coupling:
        raise DeriverOutputError(
            f"主张里写了落点间的相互约束措辞 {coupling}——两条尺寸同属一件事不等于互相牵制，"
            "并列着说、各给各的理由，别说它们要配合着定",
            claims=cleaned,
        )
    hits = sorted({t for t in banned_terms for c in cleaned if t in c.claim})
    if hits:
        # 打回按组给路，与写作步同形（gate.py 同名判据一直是这么打的）。此前这一步是**定死的一句**、
        # 不查 banned_route_of——2026-08-31 六章整册真跑，budget 推导三次全失败，三次拿到的都是同一句
        # 没有理由的「换人话重写」。路由话由 BANNED_GROUP_ROUTE 一处供两步，查不到组仍退兜底那句。
        routes = "；".join(f"「{t}」{banned_route_of(t, dict(banned_groups or {}))}" for t in hits)
        # 主张是内部语域，禁词表是客户语域的——**这里仍然用同一份表**，因为主张逐字进写作 prompt：
        # 真跑两次证明写作步兜不住（拿到带禁词的主张，连吃三稿都在同一个词上被打回，
        # 而下一稿拿到的主张还是那句）。判在这一步，写作步才有一份干净的骨架。
        raise DeriverOutputError(
            f"主张里出现禁词，下一步照抄就会被机检打回，按各自的改法重写——{routes}",
            claims=cleaned,
        )
    copied = sorted(
        {
            phrase
            for rule in triggered_rules
            for phrase in (rule.content, rule.rationale)
            if phrase and any(phrase in c.claim for c in cleaned)
        }
    )
    if copied:
        # 条目逐字照抄＝把内部写法搬进主张，而主张逐字进写作 prompt（示范句可抄性同病，
        # 三次真跑证明 prompt 里叮嘱压不住）。判在这一步，写作步才拿得到人话骨架。
        raise DeriverOutputError(
            f"主张逐字照抄了触发条目 {copied}——那是内部写法，用业主听得懂的话重讲一遍",
            claims=cleaned,
        )
    # 项名同路（规则 1.9 三"项名不进业主视野"，v2.8）：项名从这一版起进推导入参，
    # 而主张逐字进写作 prompt——内部记号混进去就会出现在卡片上。词边界匹配，
    # 不误伤 `low-E` 这类正当写法（前后接了字母数字连字符就不算）。
    claims_text = " ".join(c.claim for c in cleaned)
    leaked = sorted(
        {
            item
            for item in set(item_names)
            if re.search(
                rf"(?<![a-z0-9-]){re.escape(item)}(?![a-z0-9-])", claims_text, re.IGNORECASE
            )
        }
    )
    if leaked:
        raise DeriverOutputError(
            f"主张里写了分项记号 {leaked}——那是内部记号不是说法，"
            "用人话说那一项是什么场合、什么档位（下一步照抄就会写进卡片）",
            claims=cleaned,
        )
    return cleaned


class LlmNarrativeDeriver:
    """LiteLLM 网关调用（openai 兼容 /chat/completions），逻辑名 ``report-unit-derive.default``。

    单独一个逻辑名而不是复用写作那条（变化轴 3：``{任务}.{variant}``）：推导与写作是两个任务，
    换其中一个的模型不该牵动另一个。映射在 infra `litellm/config.yaml`，代码只认逻辑名。
    """

    def __init__(self) -> None:
        self._base = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
        self._api_key = os.environ.get("LITELLM_API_KEY", "")

    async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": DERIVE_LOGICAL_MODEL,
                    "messages": build_derive_messages(request),
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        return parse_claims(
            content,
            {a.lkp_id for a in request.anchors},
            request.banned_terms,
            request.triggered_rules,
            [item for a in request.anchors for item in a.items],
            request.banned_term_groups,
        )
