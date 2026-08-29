"""卡片写作器：叙事推导+卡片写作的 LLM 执行件（gen-generated）。

prompt 只从报告数据包内的 persona 载荷与落点对象拼装——运行时不读任何人写的文本（规则 4.19）；
模型经 LiteLLM 网关按逻辑名调用（变化轴 3：`report-unit-compose.default`，
换模型改网关配置不改代码）。
输出为结构化 JSON 卡片：数字只经 {lkp-*} 占位（出口过检的零漂移前提）。

语域纪律在 prompt 侧的形态（规则 4.10a/4.10c/5.8）：落点按 ``presentation`` 分【可作支点】与
【未过门·建议口吻】两档逐条标注；判断句题目按断言预算切成"这轮许说/这轮不许说"两张清单。
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
    CHINESE_NUMBER_RE,
    DIGIT_RE,
    PLACEHOLDER_RE,
)
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


def build_messages(request: WriterRequest) -> list[dict[str, str]]:
    """prompt 拼装（纯函数，可单测）：素材全部来自 release 数据载荷。"""
    banned = "、".join(request.banned_terms) if request.banned_terms else "（无）"
    backed = "、".join(request.backed_predicates) if request.backed_predicates else "（无）"
    # 对照句对是"这么写不行 → 这么写才对"的示范，模型最吃这个形态；放在纪律之后、输出格式之前，
    # 让它先读完规则再看规则长什么样。下发的每一句（含 ✗）都自己过得了机检（见 judgment_pairs）。
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
        "**一个占位符代表整条落点**：区间落点渲染出来就是区间（如「亮 3-5 倍」），"
        "不要拆成 {lkp-x-min}/{lkp-x-max}——那样会丢掉另一端，而上下限往往各管一条纪律"
        "（下限管够不够，上限管过不过）。句式跟着落点走：区间用「在…之间」「…到…」，"
        "带下限的用「不少于」；\n"
        "3. 落点分两档：标【可作支点】的可以拿来下判断；标【未过门·建议口吻】的照常用，"
        "**主旨句里也可以出现**，但语域限「我们建议…」「按行业通行做法…」，"
        "不许写成「国标要求」「必须」这类标准口吻——它没有外部依据背书；\n"
        "4. 不许把 lkp- 开头的内部编号写进正文或主旨句——业主不认识这些编号。"
        "要用它的数字就写 {lkp-id} 占位；要说这条没有依据背书，用人话说（如"
        "「这一项目前只能给参考范围」），不要点名编号；\n"
        f"5. 判断句只允许落在这几个题目上：{backed}。"
        "写了判断句就在 assertions 里声明用的是哪一个；其余题目这轮没有背书，不许下结论；\n"
        f"6. 禁词（一个都不能出现）：{banned}\n"
        "7. 没有外部依据或已过期的落点，它的**来源与取数时间由系统自动挂在这一页上**，"
        "你不要在正文里写来源、标准号或日期——写了既是裸数字违规，也会和系统挂的那份对不上；\n"
        f"{samples}"
        "输出：JSON 数组，每个元素 "
        '{"thesis": 主旨句, "body": 正文, "number_refs": [引用的 lkp- 列表], '
        '"assertions": [声明使用的判断句题目]}，'
        "不要输出数组以外的任何内容。"
    )
    anchor_lines = [
        f"- {a.lkp_id}（{a.name}，{a.unit or '无单位'}）= {json.dumps(a.value, ensure_ascii=False)}"
        + ("【可作支点】" if a.presentation == "THESIS_SUPPORT" else "【未过门·建议口吻】")
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
