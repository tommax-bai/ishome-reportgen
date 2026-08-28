"""卡片写作器：叙事推导+卡片写作的 LLM 执行件（gen-generated）。

prompt 只从报告数据包内的 persona 载荷与落点对象拼装——运行时不读任何人写的文本（规则 4.19）；
模型经 LiteLLM 网关按逻辑名调用（变化轴 3：`report-unit-compose.default`，
换模型改网关配置不改代码）。
输出为结构化 JSON 卡片：数字只经 {lkp-*} 占位（出口过检的零漂移前提）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from reportgen_worker.models import (
    Card,
    EvaluationProfile,
    GapRecord,
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
    anchors: list[ReportAnchor]
    gaps: list[GapRecord]
    profile: EvaluationProfile
    banned_terms: list[str]
    feedback: list[Violation] = []
    attempt: int = 0


class WriterOutputError(Exception):
    """LLM 输出不可解析为卡片组——作为违规进入重写循环，不静默丢弃。"""


class CardWriter(Protocol):
    async def write(self, request: WriterRequest) -> list[Card]: ...


def build_messages(request: WriterRequest) -> list[dict[str, str]]:
    """prompt 拼装（纯函数，可单测）：素材全部来自 release 数据载荷。"""
    banned = "、".join(request.banned_terms) if request.banned_terms else "（无）"
    system = (
        f"{request.persona.identity}\n"
        "写作纪律（违反即被机检打回）：\n"
        "1. 正文与主旨句不得出现任何数字字符；需要数字处写 {lkp-id} 占位符，"
        "渲染层会替换为求值结果；\n"
        "2. 只能引用下方给出的落点对象，不得自造数字或引用不存在的 lkp-；\n"
        "3. 标记为「未背书」的落点只能以参考口吻提及，不得作为判断句的支点；\n"
        f"4. 禁词（一个都不能出现）：{banned}\n"
        "输出：JSON 数组，每个元素 "
        '{"thesis": 主旨句, "body": 正文, "number_refs": [引用的 lkp- 列表]}，'
        "不要输出数组以外的任何内容。"
    )
    anchor_lines = [
        f"- {a.lkp_id}（{a.name}，{a.unit or '无单位'}）= {json.dumps(a.value, ensure_ascii=False)}"
        + ("【未背书，只可参考口吻】" if a.degraded else "")
        for a in request.anchors
    ]
    gap_lines = [f"- {g.lkp_id}：{g.reason}" for g in request.gaps]
    user_parts = [
        f"领域：{request.domain}",
        "这家人的情况（匿名）：" + json.dumps(request.profile.layout_features, ensure_ascii=False),
        "可引用的落点对象：\n" + "\n".join(anchor_lines),
    ]
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
