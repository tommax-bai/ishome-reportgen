"""卡片写作器：叙事推导+卡片写作的 LLM 执行件（gen-generated）。

prompt 只从报告数据包内的 persona 载荷与落点对象拼装——运行时不读任何人写的文本（规则 4.19）；
模型经 LiteLLM 网关按逻辑名调用（变化轴 3：`report-unit-compose.default`，
换模型改网关配置不改代码）。
输出为结构化 JSON 卡片：数字只经 {lkp-*} 占位（出口过检的零漂移前提）。

降档纪律在 prompt 侧的形态（规则 4.10/5.8）：落点按 ``presentation`` 分【可作支点】与
【降档·只可参考口吻】两档逐条标注；判断句题目按断言预算切成"这轮许说/这轮不许说"两张清单。
prompt 只是第一道，**不是门禁**——真正拦截在 :mod:`reportgen_worker.gate`
（判据下沉次序 schema > 规则 > prompt > 判官，图 v0.2 §3）。被隐藏的落点不进 prompt：
它们的 id 与名称一并不下发，写作器无从提起。
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
    backed_predicates: list[str] = []
    unbacked_predicates: list[str] = []
    feedback: list[Violation] = []
    attempt: int = 0


class WriterOutputError(Exception):
    """LLM 输出不可解析为卡片组——作为违规进入重写循环，不静默丢弃。"""


class CardWriter(Protocol):
    async def write(self, request: WriterRequest) -> list[Card]: ...


def build_messages(request: WriterRequest) -> list[dict[str, str]]:
    """prompt 拼装（纯函数，可单测）：素材全部来自 release 数据载荷。"""
    banned = "、".join(request.banned_terms) if request.banned_terms else "（无）"
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    system = (
        f"{request.persona.identity}\n"
        "写作纪律（违反即被机检打回）：\n"
        "1. 正文与主旨句不得出现任何数字字符；需要数字处写 {lkp-id} 占位符，"
        "渲染层会替换为求值结果。**中文数字同样算数**——"
        "「三到五倍」「不低于九十」「七十多厘米」「近半」都要换成占位符；"
        "纪律管的是数不是字形。（列举计数不算：「四个区域」「这三项」可以写）\n"
        "2. 只能引用下方给出的落点对象，不得自造数字或引用不存在的 lkp-。"
        "**一个占位符代表整条落点**：区间落点渲染出来就是区间（如「亮 3-5 倍」），"
        "不要拆成 {lkp-x-min}/{lkp-x-max}——那样会丢掉另一端，而上下限往往各管一条纪律"
        "（下限管够不够，上限管过不过）。句式跟着落点走：区间用「在…之间」「…到…」，"
        "带下限的用「不少于」；\n"
        "3. 落点分两档：标【可作支点】的可以拿来下判断；标【降档·只可参考口吻】的只能"
        "以区间、参考、待现场确认的口吻提到，**主旨句里不许出现它**；\n"
        "4. 不许把 lkp- 开头的内部编号写进正文或主旨句——业主不认识这些编号。"
        "要用它的数字就写 {lkp-id} 占位；要说这条没有依据背书，用人话说（如"
        "「这一项目前只能给参考范围」），不要点名编号；\n"
        f"5. 判断句只允许落在这几个题目上：{backed}。"
        "写了判断句就在 assertions 里声明用的是哪一个；其余题目这轮没有背书，不许下结论；\n"
        f"6. 禁词（一个都不能出现）：{banned}\n"
        "输出：JSON 数组，每个元素 "
        '{"thesis": 主旨句, "body": 正文, "number_refs": [引用的 lkp- 列表], '
        '"assertions": [声明使用的判断句题目]}，'
        "不要输出数组以外的任何内容。"
    )
    anchor_lines = [
        f"- {a.lkp_id}（{a.name}，{a.unit or '无单位'}）= {json.dumps(a.value, ensure_ascii=False)}"
        + (
            "【可作支点】"
            if a.presentation == "THESIS_SUPPORT"
            else "【降档·只可参考口吻，禁进主旨句】"
        )
        for a in request.anchors
    ]
    gap_lines = [f"- {g.lkp_id}：{g.reason}" for g in request.gaps]
    user_parts = [
        f"领域：{request.domain}",
        "这家人的情况（匿名）：" + json.dumps(request.profile.layout_features, ensure_ascii=False),
        "可引用的落点对象：\n" + "\n".join(anchor_lines),
    ]
    if request.unbacked_predicates:
        user_parts.append(
            "这轮**没有背书、不许下结论**的题目（可以描述现象，不能给判断）：\n"
            + "\n".join(f"- {p}" for p in request.unbacked_predicates)
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
