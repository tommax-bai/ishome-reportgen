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

import json
import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from reportgen_worker.models import AnchorBrief, EvaluationProfile, GapRecord, NarrativeClaim

DERIVE_LOGICAL_MODEL = "report-unit-derive.default"
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
    backed_predicates: list[str] = []
    unbacked_predicates: list[str] = []


class DeriverOutputError(Exception):
    """推导输出不可解析或为空——作为违规进入重写循环，不静默退回"没有主张"的老形态。"""


class NarrativeDeriver(Protocol):
    async def derive(self, request: DeriveRequest) -> list[NarrativeClaim]: ...


def build_derive_messages(request: DeriveRequest) -> list[dict[str, str]]:
    """推导 prompt（纯函数，可单测）：素材只有身份、落点题名、缺口、匿名画像、断言预算题目。"""
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    unbacked = "、".join(request.unbacked_predicates) if request.unbacked_predicates else "（无）"
    system = (
        f"{request.identity}\n"
        "这一步你**不写给业主看**，你在决定这一章讲哪几件事。纪律：\n"
        "1. 每条主张要有**取舍或因果**，不是标题。"
        "「讲讲台面高度」不是主张；「厨房的高度不该按平均身高定，该按主厨的身体定」是主张；\n"
        "2. **不许出现任何数字**（阿拉伯数字、中文数字、占位符都不要）——数字是下一步的事，"
        "你这一步连值都没拿到；\n"
        "3. 每条主张挂上它要用到的落点 id（从下面清单里选，可以挂多条，也可以一条不挂——"
        "有些主张说的是取舍不是数）。**一条主张通常要用到不止一个落点**：一个落点一条主张，"
        "等于把落点表换了个排版；\n"
        "4. 能下结论的题目只有这些："
        + backed
        + "；这些题目这轮**没有背书**，只能描述不能下判断："
        + unbacked
        + "。没背书的题目也可以成为主张，"
        "但那条主张说的应当是「这取决于什么」而不是「结论是什么」；\n"
        "5. 讲几件事由这一域真有几件事决定——通常三到五件。宁可少讲一件讲透，"
        "不要为了铺满而拆出没有取舍的主张；\n"
        '输出：JSON 数组，每个元素 {"claim": 主张, "anchors": [用到的落点 id]}，'
        "不要输出数组以外的任何内容。"
    )
    anchor_lines = [
        f"- {a.lkp_id}（{a.name}{'，' + a.unit if a.unit else ''}）" for a in request.anchors
    ]
    user_parts = [
        f"领域：{request.domain}",
        "这家人的情况（匿名）：" + json.dumps(request.profile.layout_features, ensure_ascii=False),
        # 只给题名不给值：这一步不产生数字（图 v0.2 §3），不给值即产不出
        "本域可用的落点（只有题名，值在下一步）：\n" + "\n".join(anchor_lines),
    ]
    if request.gaps:
        user_parts.append(
            "本次求不出的落点（「这件事现在还算不出来」本身可以是一条主张）：\n"
            + "\n".join(f"- {g.lkp_id}：{g.reason}" for g in request.gaps)
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_claims(raw: str, known_anchor_ids: set[str]) -> list[NarrativeClaim]:
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
        return parse_claims(content, {a.lkp_id for a in request.anchors})
