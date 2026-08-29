"""出口过检·判官层（图 v0.2 §3 的第二道，规则层之后）。

规则层管**判得出的**：占位符、裸数字、禁词、pattern 逐字比对。判官层管**判不出的**：语义违规——
"这句算不算判断句""这条家庭事实是不是编出来的"没有确定性判据（规则 4.10c 已写明机检不假实现）。
判据本身仍是 release 数据：判官读的是随包下发的 cr- 判据 + 挂在其下的反例样例（规则 4.10b：
纪律的唯一形态是 check，反例库不新建第三类命名空间），**运行时不读任何人写的文本**（规则 4.19）。

四条纪律，逐条在代码里有对应形态：

- **只报编号不改写**：输出模型 :class:`~reportgen_worker.models.JudgeObservation` 只有
  ``check/quote/why`` 三个字段且 ``extra=forbid``——没有放改写建议的位置。样例的 ``fixed``
  一并**不进 prompt**：把改好的答案递到判官手里等于请它越权，而判官与写手同源，改写即漂移。
- **观察态**（规则 4.17 门禁二）：拦不拦由数据侧 ``status`` 决定，代码里没有"要不要拦"的分支。
  首批样例一律 ``observing`` → 判出的问题只记录不拦截。
- **判官挂掉不阻塞成文**：:func:`observe` 吞掉一切异常返回空清单。判官不可用是判官的事故，
  不是这份内容的事故——把能发的内容因为第二道没跑成而毙掉，等于让可用性问题伪装成质量问题。
- **判官不得报一句没人写过的话**：观察的 ``quote`` 必须在被检卡片正文里找得到、``check`` 必须是
  本次真下发过的判据，否则丢弃——判官编造原句与它正在判的缺陷是同一种病。
- **分批送审 + 计数留痕**（2026-08-29 新增）：一次送审只给 :data:`JUDGE_BATCH_SIZE` 张卡，
  每批各问一次、逐批吞异常；每次送审产出一份 :class:`~reportgen_worker.models.JudgeRun` 台账
  （判据 × 份 × 批大小 × 触发）。理由见两处 docstring：灵敏度随卡片数塌陷是实测事实，
  而"0 命中"与"很干净"在没有台账时是同一个数。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from reportgen_worker.models import (
    Card,
    CheckAsset,
    EvaluationProfile,
    JudgeCheckCount,
    JudgeObservation,
    JudgeRun,
    ReportAnchor,
    ReportDataPackage,
)

JUDGE_LOGICAL_MODEL = "report-unit-judge.default"
_OBSERVATIONS_ADAPTER: TypeAdapter[list[JudgeObservation]] = TypeAdapter(list[JudgeObservation])
_JSON_BLOCK_RE = re.compile(r"\[.*\]", re.DOTALL)
_QUOTE_TRIM = " \t\r\n“”\"'『』「」…。，、"

# 外部标准号（国标/行标/国际标准）。判官的输入面里**没有任何标准原文**——prompt 只给它判据与样例、
# 匿名画像、落点名、待检文稿（:func:`build_judge_messages`，落点的 source 都不给）。所以 ``why``
# 里出现一个输入面里没有的标准号，它只可能是判官自己编的。
_STANDARD_CITATION_RE = re.compile(
    r"(?:GB\s*/?\s*T?\s*\d[\d.\-–—]*|JGJ\s*/?\s*T?\s*\d[\d.\-–—]*|ISO\s*\d+|EN\s*\d+)"
)

OBSERVING = "observing"
ACTIVE = "active"
RETIRED = "retired"

JUDGE_BATCH_SIZE = 6
"""判官每批送审的卡片数。

**临时值，唯一有实测支撑的那个数**（真跑 2026-08-29）：同一份文稿、同模型、同判据、
``temperature=0``，整单元 22 张一次送审 → 观察 0 条；取同一份的**前 6 张**再问一次 → 6 条命中，
且判官没有报错、没有解析失败，就是返回了空数组。即：**灵敏度随一次送审的卡片数塌陷**。

为什么先分批再对照跑（裁决 2026-08-29）：现状等于没查，任何合理批量都严格优于 22 张 0 命中；
对照跑（6/11/22 各一次）是分批之后的**第一次校准**，不是前置条件。N 与阈值等有数据再定
（同"卡片数上限 6~8"被撤回的理由：阈值不能拍脑袋）。校准要用的数据由 :class:`JudgeRun` 台账攒。

**不是 prompt 问题**：判官 prompt 里"宁可漏报也不要凑数"那句一并留着不动——前 6 张带着同一句
指令判出了 6 条，删句证据不足。分批是这一轮唯一的变量。
"""

logger = logging.getLogger(__name__)


class JudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    cards: list[Card]
    checks: list[CheckAsset]
    profile: EvaluationProfile
    anchors: list[ReportAnchor]


class Judge(Protocol):
    async def review(self, request: JudgeRequest) -> list[JudgeObservation]: ...


def judge_checks(domain: str, package: ReportDataPackage) -> list[CheckAsset]:
    """本域判官判据：带反例样例且未停用的。

    ``retired`` 不进 prompt（停用即不再判，留档只为可回滚）；``observing`` 与 ``active`` 都判，
    区别只在判出来之后拦不拦（:func:`blocking_check_ids`）。无样例的判据判官不看——判官只认样例，
    拿一句 message 让它自由发挥就成了模型自评分（规则 4.17 明禁以模型自评替代信号验证）。
    """
    return [
        c for c in package.checks_by_domain.get(domain, []) if c.examples and c.status != RETIRED
    ]


def blocking_check_ids(domain: str, package: ReportDataPackage) -> set[str]:
    """有拦截权的判据（``status=active``）——**唯一**决定判官能不能否掉一稿的地方。

    首批一律 observing，故这里返回空集：机制在、权限不在。转正走 release 发版（规则 4.17 门禁二），
    不改代码。
    """
    return {c.asset_id for c in judge_checks(domain, package) if c.status == ACTIVE}


def build_judge_messages(request: JudgeRequest) -> list[dict[str, str]]:
    """判官 prompt（纯函数，可单测）：每一段都是包内 release 数据，没有一句人写的判据文本。

    给判官看四样：①判据与反例样例（不含 ``fixed``）②本单元合法的输入面（匿名画像 + 落点名）——
    "编造事实"这类判据不给输入全集就判不了 ③待检卡片 ④输出格式。不给它 persona、不给它写作纪律：
    判官的判据只能来自 cr-，多给一条它就会按那条自由发挥。
    """
    check_blocks = []
    for check in request.checks:
        lines = [f"{check.asset_id}：{check.message}"]
        if check.requirement:
            lines.append(f"  判据：{check.requirement}")
        lines += [f"  反例：「{e.bad}」——{e.why}" for e in check.examples]
        check_blocks.append("\n".join(lines))
    card_blocks = [
        f"[{i}] 主旨句：{c.thesis}\n    正文：{c.body}" for i, c in enumerate(request.cards)
    ]
    anchor_names = "、".join(f"{a.lkp_id}（{a.name}）" for a in request.anchors) or "（无）"
    system = (
        "你是出口过检的判官。你只做一件事：拿下面的判据逐条对照文稿，指出违反了哪一条。\n"
        "硬性纪律：\n"
        "1. 只报判据编号、命中的原句片段、为什么违反。**不要给修改建议，不要重写任何句子**——"
        "改写是写作者的活，你改了就没人能判你改得对不对；\n"
        "2. 原句片段必须从文稿里**逐字复制**，不许概括、不许改写、不许拼接；\n"
        "3. 只用下面列出的判据，判据以外的问题（写得好不好、够不够详细）一律不报；\n"
        "4. 没有命中就返回空数组。宁可漏报也不要凑数——报错一条的代价高于漏报一条。\n"
        '输出：JSON 数组，每个元素 {"check": 判据编号, "quote": 原句片段, "why": 为什么违反}，'
        "不要输出数组以外的任何内容。"
    )
    user_parts = [
        f"领域：{request.domain}",
        "判据（每条附真实反例）：\n" + "\n".join(check_blocks),
        # 输入面：写作者当时能知道的全部事实。画像之外的关于这家人的事实，必然是编的（图 v0.2 §0）
        "写作者当时拿到的全部输入——这家人的情况（匿名）："
        + json.dumps(request.profile.model_dump(by_alias=True), ensure_ascii=False)
        + f"\n可引用的落点对象：{anchor_names}",
        "待检文稿：\n" + "\n".join(card_blocks),
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_observations(raw: str, request: JudgeRequest) -> list[JudgeObservation]:
    """解析并核实判官输出：编号必须是本次下发过的、原句必须在文稿里找得到。

    这两道不是防模型笨，是防**判官自己编**——它正在判的就是"说出没有依据的话"，它自己犯同一件事
    时没有理由留着。核实不过的条目直接丢弃并记一条日志（观察态下多报一条比漏报一条更贵：
    误报会污染入册门禁的触发率统计，让不该转正的判据看起来在工作）。
    """
    match = _JSON_BLOCK_RE.search(raw)
    if match is None:
        logger.warning("判官输出中未找到 JSON 数组，本单元按无观察处理")
        return []
    try:
        parsed = _OBSERVATIONS_ADAPTER.validate_json(match.group(0))
    except ValidationError as e:
        logger.warning("判官输出结构不合法，本单元按无观察处理：%s", e)
        return []
    known = {c.asset_id for c in request.checks}
    corpus = "\n".join(f"{c.thesis}\n{c.body}" for c in request.cards)
    inputs = _input_face(request)
    observations = []
    for item in parsed:
        quote = item.quote.strip(_QUOTE_TRIM)
        fabricated = _fabricated_citations(item.why, inputs)
        if item.check not in known:
            logger.warning("判官报了未下发的判据 %s，丢弃", item.check)
        elif not quote or quote not in corpus:
            logger.warning("判官的原句在文稿里找不到（%s），丢弃", item.check)
        elif fabricated:
            logger.warning("判官引了输入面里没有的标准号 %s（%s），丢弃", fabricated, item.check)
        else:
            observations.append(item)
    return observations


def _normalized(text: str) -> str:
    return re.sub(r"[\s/]", "", text).upper()


def _input_face(request: JudgeRequest) -> str:
    """判官这一次真看得见的全部文本（归一化）：判据与样例 + 落点名 + 画像 + 待检文稿。

    与"编造家庭事实"判据同一条论证形式（图 v0.2 §0）：把可知面封闭起来，面外的东西必然无源。
    """
    parts = [c.thesis + c.body for c in request.cards]
    parts += [f"{a.lkp_id}{a.name}" for a in request.anchors]
    parts.append(json.dumps(request.profile.model_dump(), ensure_ascii=False))
    for check in request.checks:
        parts += [check.asset_id, check.message, check.requirement or ""]
        parts += [e.bad + e.why for e in check.examples]
    return _normalized("\n".join(parts))


def _fabricated_citations(why: str, inputs: str) -> list[str]:
    """``why`` 里引了输入面之外的外部标准号 → 这条观察是编的（2026-08-29 真跑立案）。

    立案样本逐字：判官报 `床面高度建议在{lkp-bed-height}之间` 违规，理由写
    *"ergonomics 领域中 lkp-bed-height 实际为单点推荐值（依据国标 GB/T 3328-2016 床具高度条款）"*
    ——而包里这条落点逐字是 ``{"min":450,"max":500}``，是区间；那个标准号也从没进过它的输入面。
    **判官正在判的就是"说没有依据的话"，它自己犯同一件事时没有理由留着**（同"原句必须找得到"）。

    只认外部标准号，不碰 ``规则 x.y``/``§`` 这类内部条文号——后者本来就在判据 message 里，
    判官复述它是正常的。**丢弃而不是标记**：观察态下多报一条比漏报一条更贵，误报会污染门禁二的
    触发率，让不该转正的判据看起来在工作（同 :func:`parse_observations` 既有两道核实的口径）。
    """
    return [
        m.group(0)
        for m in _STANDARD_CITATION_RE.finditer(why)
        if _normalized(m.group(0)) not in inputs
    ]


class LlmJudge:
    """LiteLLM 网关调用（openai 兼容 /chat/completions），逻辑名 ``report-unit-judge.default``。

    与写作器同底模是已知风险（判官与写手同源必漂，规则 4.17）——防漂移不靠换模型，靠三道入册门禁；
    真要换判官模型只改网关配置一行，代码不动（变化轴 3）。
    """

    def __init__(self) -> None:
        self._base = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
        self._api_key = os.environ.get("LITELLM_API_KEY", "")

    async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": JUDGE_LOGICAL_MODEL,
                    "messages": build_judge_messages(request),
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        return parse_observations(content, request)


def card_batches(cards: list[Card], size: int = JUDGE_BATCH_SIZE) -> list[list[Card]]:
    """按送审批量切卡片（顺序切分，不打乱）：保持原顺序是为了让同一份文稿的分批结果可复现。"""
    return [cards[i : i + size] for i in range(0, len(cards), size)]


async def observe(
    judge: Judge, request: JudgeRequest, batch_size: int = JUDGE_BATCH_SIZE
) -> tuple[list[JudgeObservation], JudgeRun | None]:
    """**分批**跑判官并**逐批吞掉异常**，返回观察清单 + 计数台账。

    两条纪律各管一半：

    - **判官不阻塞**（既有）：第二道过检是加分项不是准入条件——网关超时、判官模型下线、输出解析
      不了，结果都只是"这一份少了些观察数据"，不是"这一份不能发"。反过来做会让可用性问题伪装成
      质量问题，而观察态本身还没有拦截权（规则 4.17 门禁二）。**异常吞在批这一级**：一批问不成
      不该连累其余批次，但丢了几批要记进台账（``batches_failed``）。
    - **计数载体**（新增，裁决 2026-08-29）：台账是规则 4.17 门禁二唯一的数据来源。
      **0 命中与"很干净"在数据上不可区分**——真跑已经证明这不是假想（22 张 0 命中那次），
      台账把"问了几批、多大一批、每条判据中了几次"记下来，那次静默 0 才不会被当成合格样本。

    批次**顺序执行**不并发：判官调用是观察不是产能瓶颈，并发只会让网关限流成为新的批失败来源。
    """
    if not request.checks or not request.cards:
        return [], None
    batches = card_batches(request.cards, batch_size)
    observations: list[JudgeObservation] = []
    failed = 0
    for index, batch in enumerate(batches):
        try:
            observations += await judge.review(request.model_copy(update={"cards": batch}))
        except Exception:
            failed += 1
            logger.warning(
                "判官第 %d/%d 批不可用，该批无观察数据（不影响 verdict）",
                index + 1,
                len(batches),
                exc_info=True,
            )
    run = JudgeRun(
        cards_reviewed=len(request.cards),
        batch_size=batch_size,
        batches=len(batches),
        batches_failed=failed,
        checks=[
            JudgeCheckCount(
                check=check.asset_id,
                version=check.version,
                status=check.status,
                hits=sum(1 for o in observations if o.check == check.asset_id),
            )
            for check in request.checks
        ],
    )
    return observations, run
