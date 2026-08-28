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
    JudgeObservation,
    ReportAnchor,
    ReportDataPackage,
)

JUDGE_LOGICAL_MODEL = "report-unit-judge.default"
_OBSERVATIONS_ADAPTER: TypeAdapter[list[JudgeObservation]] = TypeAdapter(list[JudgeObservation])
_JSON_BLOCK_RE = re.compile(r"\[.*\]", re.DOTALL)
_QUOTE_TRIM = " \t\r\n“”\"'『』「」…。，、"

OBSERVING = "observing"
ACTIVE = "active"
RETIRED = "retired"

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
    observations = []
    for item in parsed:
        quote = item.quote.strip(_QUOTE_TRIM)
        if item.check not in known:
            logger.warning("判官报了未下发的判据 %s，丢弃", item.check)
        elif not quote or quote not in corpus:
            logger.warning("判官的原句在文稿里找不到（%s），丢弃", item.check)
        else:
            observations.append(item)
    return observations


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


async def observe(judge: Judge, request: JudgeRequest) -> list[JudgeObservation]:
    """跑判官并**吞掉一切异常**：判官不可用不能把能发的内容毙掉。

    第二道过检是加分项不是准入条件——网关超时、判官模型下线、输出解析不了，结果都只是"这一份没有
    观察数据"，不是"这一份不能发"。反过来做会让可用性问题伪装成质量问题，而观察态本身就还没有
    拦截权（规则 4.17 门禁二），此时因判官挂掉而 failed 更是无从谈起。
    """
    if not request.checks:
        return []
    try:
        return await judge.review(request)
    except Exception:
        logger.warning("判官不可用，本单元无观察数据（不影响 verdict）", exc_info=True)
        return []
