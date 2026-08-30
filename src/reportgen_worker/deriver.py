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

**推导步看不见落点的值**（:class:`~reportgen_worker.models.AnchorBrief` 只有 id/名字/量纲）：
图 v0.2 §3 要求这一步"不产生任何数字"，不给值是让它**结构性地产不出**，而不是叮嘱它别写。
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

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
    feedback: list[str] = []
    """上一次推导被打回的理由（重试时下发）：同写作那条循环的形态——不告诉它哪儿错了，
    它只会把同一句再写一遍（真跑实测：同一个禁词连吃三稿）。"""


class DeriverOutputError(Exception):
    """推导输出不可解析或为空——作为违规进入重写循环，不静默退回"没有主张"的老形态。"""


class NarrativeDeriver(Protocol):
    async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]: ...


def build_derive_messages(request: DeriveRequest) -> list[dict[str, str]]:
    """推导 prompt（纯函数，可单测）：素材只有身份、落点题名、缺口、匿名画像、断言预算题目。"""
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    unbacked = "、".join(request.unbacked_predicates) if request.unbacked_predicates else "（无）"
    banned = "、".join(request.banned_terms) if request.banned_terms else "（无）"
    system = (
        f"{request.identity}\n"
        "这一步你**不写给业主看**，你在决定这一章讲哪几件事。纪律：\n"
        "1. 每条主张要有**取舍**，不是标题。"
        "「讲讲台面高度」不是主张；「厨房的高度不该按平均身高定，该按主厨的身体定」是主张；\n"
        "2. **不许出现任何数字**（阿拉伯数字、中文数字、占位符都不要）——数字是下一步的事，"
        "你这一步连值都没拿到；\n"
        "3. **先把落点按「其实是同一件事」归组，再给每组写一条主张**——"
        "一个落点一条主张等于把落点表换了个排版，那正是要修的东西。"
        "归组的依据是**同属一件事**（同一件家具、同一个动作、同一个空间）——"
        "床面高和床侧净距同属「床」，**就该进同一条主张**，正文里各给各的理由；"
        "但**不许把同组落点写成相互约束的关系**（说 A 的取值牵制 B、两者要配合着调整之类）——"
        "数据里没有这种联动就不许说。归组照做，关系别编——这是两件事；"
        "每条主张挂上它这一组的落点 id（可以挂多条；说取舍不说数的主张也可以一条不挂）；\n"
        "3b. 落点是这一域**已经算出来的**东西，能归进某件事的就别丢在外面——"
        "宁可一条主张多带几个落点，也不要只挑几条讲、剩下的大半不提；\n"
        "3c. 「这套户型触发的条目」是**这一章必须讲到的点**——每条都要落进某条主张里，"
        "但**用你自己的话讲**：那些条目是内部写法，逐字搬进主张会被机检打回。"
        "讲的时候带上它**为什么对这户成立**（条目后面括号里那句就是依据）——"
        "「因为你家阳台带家政位」这种话才是业主要看的，凭空说「阳台要留清洁位」不是；\n"
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
        f"6. 这些词一个都不能出现（下一步要照着你的主张写，你用了它就会被机检打回）：{banned}。"
        "**落点的名字里可能就带着这些词**——那是内部标签不是说法，"
        "你在主张里要换成人话（例如别写「净宽」，写「能不能并排走过去」）；\n"
        '输出：JSON 数组，每个元素 {"claim": 主张, "anchors": [用到的落点 id]}，'
        "不要输出数组以外的任何内容。"
    )

    # 名字撞禁词的落点**逐行点名**（数据驱动：banned ∩ name，算出来的不是编的）。全局那句
    # "名字里可能带着这些词"实测不够——ergonomics 的推导在「净宽」上带着反馈连吃三稿
    # （temperature=0 下，落点清单每行都写着"主通道净宽"，全局提醒压不过逐行复现）。
    def anchor_line(a: AnchorBrief) -> str:
        hits = "、".join(f"「{t}」" for t in request.banned_terms if t in a.name)
        note = f"（名字里的 {hits} 是内部词，勿写进主张）" if hits else ""
        return f"- {a.lkp_id}（{a.name}{'，' + a.unit if a.unit else ''}）{note}"

    anchor_lines = [anchor_line(a) for a in request.anchors]
    user_parts = [
        f"领域：{request.domain}",
        # 户型特征**只下发依据文字不下发标记名**：键是内部标识符（`balcony_service` 这类），
        # 而主张逐字进写作 prompt，内部词面混进去就会出现在卡片上（客户语域禁内部编号）。
        # 值本身就是人话依据（"阳台内有洗衣机设备位"），够推导用且不带内部词面。
        "这套户型（匿名）："
        + ("；".join(request.profile.layout_features.values()) or "（暂无户型信息）"),
        # 只给题名不给值：这一步不产生数字（图 v0.2 §3），不给值即产不出
        "本域可用的落点（只有题名，值在下一步）：\n" + "\n".join(anchor_lines),
    ]
    if request.triggered_rules:
        user_parts.append(
            "这套户型触发的条目（必须讲到，换成人话讲；括号里是它对这户成立的依据）：\n"
            + "\n".join(_rule_line(r) for r in request.triggered_rules)
        )
    if request.feedback:
        user_parts.append(
            "上一稿被打回，逐条改：\n" + "\n".join(f"- {f}" for f in request.feedback)
        )
    if request.gaps:
        user_parts.append(
            "本次求不出的落点（「这件事现在还算不出来」本身可以是一条主张）：\n"
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
) -> list[NarrativeClaim]:
    """解析主张集，并**剔除推导步自造的落点 id**（保留主张本身）。

    自造 id 不当成致命错误：``anchors`` 是给写作器的建议，真正的引用校验在
    ``gate-number-ref-unresolved``（写作步，按本域全部落点判）。剔除是为了不把一个不存在的
    id 递进下一个 prompt——那等于请写作器去引用一条没有的落点。

    空数组是**失败**不是"没什么可讲"：一域有落点却推导不出一件事，说明这一步没工作；
    静默放行会让下一步退回没有主张的老形态，而那正是这一步要修的东西（绝不静默假成功）。
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
        raise DeriverOutputError("推导没有产出任何主张")
    coupling = sorted({w for w in COUPLING_PHRASES for c in cleaned if w in c.claim})
    if coupling:
        raise DeriverOutputError(
            f"主张里写了落点间的相互约束措辞 {coupling}——两条尺寸同属一件事不等于互相牵制，"
            "并列着说、各给各的理由，别说它们要配合着定"
        )
    hits = sorted({t for t in banned_terms for c in cleaned if t in c.claim})
    if hits:
        # 主张是内部语域，禁词表是客户语域的——**这里仍然用同一份表**，因为主张逐字进写作 prompt：
        # 真跑两次证明写作步兜不住（拿到带禁词的主张，连吃三稿都在同一个词上被打回，
        # 而下一稿拿到的主张还是那句）。判在这一步，写作步才有一份干净的骨架。
        raise DeriverOutputError(
            f"主张里出现禁词 {hits}——这几个词下一步照抄就会被机检打回，换人话重写"
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
            f"主张逐字照抄了触发条目 {copied}——那是内部写法，用业主听得懂的话重讲一遍"
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
        )
