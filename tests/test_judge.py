"""判官层（图 v0.2 §3 第二道）：prompt 只从包内 cr-+样例拼装、只报编号不改写、核实原句、
挂掉不阻塞成文。"""

from __future__ import annotations

import copy
import json

import pytest

from reportgen_worker.judge import (
    JudgeRequest,
    blocking_check_ids,
    build_judge_messages,
    judge_checks,
    observe,
    parse_observations,
)
from reportgen_worker.models import Card, JudgeObservation, ReportDataPackage
from tests.support import PACKAGE_JSON, load_package

PACKAGE = load_package()
FABRICATED_CARD = Card(
    thesis="台面高按你的身高定。",
    body="你和你太太同时在厨房忙活的时候，台面高 {lkp-counter-height} 最省力。",
    number_refs=["lkp-counter-height"],
)


def make_request(cards: list[Card] | None = None) -> JudgeRequest:
    return JudgeRequest(
        domain="ergonomics",
        cards=cards if cards is not None else [FABRICATED_CARD],
        checks=judge_checks("ergonomics", PACKAGE),
        profile=PACKAGE.anonymous_profile,
        anchors=PACKAGE.domain_anchors("ergonomics"),
    )


def test_judge_checks_selects_only_example_bearing_and_not_retired() -> None:
    """判官只认带样例的判据：机检判据（无样例）不进 prompt，retired 停用的也不进。"""
    assert [c.asset_id for c in judge_checks("ergonomics", PACKAGE)] == ["cr-fabricated-fact"]
    assert judge_checks("lighting", PACKAGE) == []

    retired = copy.deepcopy(PACKAGE_JSON)
    retired["checksByDomain"]["ergonomics"][1]["status"] = "retired"
    assert judge_checks("ergonomics", ReportDataPackage.model_validate(retired)) == []


def test_legacy_check_without_judge_fields_has_no_interception_power() -> None:
    """V4 之前的快照无 examples/status：缺省 observing + 无样例，既不进 prompt 也无拦截权。"""
    legacy = PACKAGE.checks_by_domain["lighting"][0]
    assert legacy.examples == []
    assert legacy.status == "observing"


def test_first_batch_has_no_blocking_power() -> None:
    """规则 4.17 门禁二：首批一律 observing——机制在、拦截权不在。转正只能改数据。"""
    assert blocking_check_ids("ergonomics", PACKAGE) == set()

    promoted = copy.deepcopy(PACKAGE_JSON)
    promoted["checksByDomain"]["ergonomics"][1]["status"] = "active"
    assert blocking_check_ids("ergonomics", ReportDataPackage.model_validate(promoted)) == {
        "cr-fabricated-fact"
    }


def test_prompt_is_assembled_from_package_only_and_withholds_the_fix() -> None:
    """prompt 素材全部来自 release 数据；样例的 fixed 不下发——判官只报编号不改写。"""
    messages = build_judge_messages(make_request())
    system, user = messages[0]["content"], messages[1]["content"]

    assert "cr-fabricated-fact" in user
    assert "你和你太太" in user  # 反例原句
    assert "画像里没有家庭构成信息" in user  # 为什么错
    assert "两个人同时用的时候" not in user  # fixed：把改写答案递给判官＝请它越权
    assert "kitchen_shape" in user  # 输入面：画像即可知事实的全集（图 v0.2 §0）
    assert "台面高按你的身高定。" in user  # 待检文稿
    assert "不要给修改建议" in system
    # 判官不拿写作器的素材：persona 身份、断言预算、禁词一概不进判官 prompt（同源漂移的入口）
    assert "你在为这一家人校核他们家的尺寸。" not in user
    assert "人体工学" not in user


def test_parse_keeps_verifiable_observation() -> None:
    raw = """好的，我检查到一处：
    [{"check": "cr-fabricated-fact", "quote": "你和你太太同时在厨房忙活",
      "why": "画像没有家庭构成"}]
    """
    assert parse_observations(raw, make_request()) == [
        JudgeObservation(
            check="cr-fabricated-fact", quote="你和你太太同时在厨房忙活", why="画像没有家庭构成"
        )
    ]


def test_parse_drops_unknown_check_and_unverifiable_quote() -> None:
    """判官不得报一句没人写过的话：编号必须真下发过、原句必须在文稿里找得到。"""
    raw = """[{"check": "cr-made-up", "quote": "你和你太太", "why": "编号是判官自己造的"},
              {"check": "cr-fabricated-fact", "quote": "你的三个孩子", "why": "原句文稿里没有"}]"""
    assert parse_observations(raw, make_request()) == []


def test_parse_drops_observation_citing_a_standard_it_never_saw() -> None:
    """判官在 why 里引了输入面之外的标准号 → 丢弃（2026-08-29 真跑立案，逐字复现）。

    判官的输入面里没有任何标准原文（连落点的 source 都不给），引一个就是编的——而它正在判的
    就是"说没有依据的话"。真样本：说 lkp-bed-height "实际为单点推荐值（依据国标 GB/T 3328-2016）"，
    而包里这条逐字是 min/max 区间。
    """
    raw = json.dumps(
        [
            {
                "check": "cr-fabricated-fact",
                "quote": "你和你太太同时在厨房忙活",
                "why": "lkp-counter-height 实际为单点推荐值（依据国标 GB/T 3328-2016 条款）",
            }
        ],
        ensure_ascii=False,
    )
    assert parse_observations(raw, make_request()) == []


def test_parse_keeps_internal_rule_reference() -> None:
    """内部条文号照收：`规则 5.8`、`§2.3` 本来就在判据 message 里，判官复述它是正常的。"""
    raw = json.dumps(
        [
            {
                "check": "cr-fabricated-fact",
                "quote": "你和你太太同时在厨房忙活",
                "why": "画像里没有家庭构成信息（规则 4.3 溯源纪律）",
            }
        ],
        ensure_ascii=False,
    )
    assert len(parse_observations(raw, make_request())) == 1


def test_parse_tolerates_garbage_output() -> None:
    """判官输出不可解析 → 按无观察处理，不抛异常（第二道是加分项不是准入条件）。"""
    assert parse_observations("网关返回了一段废话", make_request()) == []
    assert parse_observations('[{"check": 1}]', make_request()) == []


async def test_observe_swallows_judge_failure() -> None:
    """判官挂掉 → 空观察，不抛出：判官不可用不能把能发的内容毙掉。台账仍记，且记下丢了几批。"""

    class BrokenJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            raise RuntimeError("网关 502")

    observations, run = await observe(BrokenJudge(), make_request())
    assert observations == []
    assert run is not None
    # "问不成"与"很干净"必须在数据上分得开——这正是台账存在的理由
    assert run.batches_failed == run.batches == 1
    assert [c.hits for c in run.checks] == [0]


async def test_observe_skips_call_when_no_judge_checks() -> None:
    """本域无判官判据 → 一次调用都不发（冷启动期判官库很薄是常态，规则 4.18 宁薄勿撑）。

    没送审就没有"份"：台账为 None，不记一条 0 命中——那会把"没问过"混进触发率的分母。
    """

    class ExplodingJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            raise AssertionError("无判据时不应调用判官")

    request = make_request()
    assert await observe(ExplodingJudge(), request.model_copy(update={"checks": []})) == ([], None)


async def test_observe_batches_cards_and_counts_per_check() -> None:
    """分批送审（真跑实测：22 张一次问 0 命中、前 6 张问出 6 条），并按判据计数。

    这里只钉**分批这件事发生了**与**计数口径**；N 取多少要等对照跑的数据（裁决⑨"有数据再定"）。
    """
    seen: list[int] = []

    class CountingJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            seen.append(len(request.cards))
            return [
                JudgeObservation(check="cr-fabricated-fact", quote=c.thesis, why="逐批各报一条")
                for c in request.cards[:1]
            ]

    cards = [FABRICATED_CARD.model_copy(update={"thesis": f"第 {i} 张"}) for i in range(14)]
    observations, run = await observe(CountingJudge(), make_request(cards), batch_size=6)

    assert seen == [6, 6, 2]  # 顺序切分，不打乱、不丢尾批
    assert len(observations) == 3
    assert run is not None
    assert (run.cards_reviewed, run.batch_size, run.batches, run.batches_failed) == (14, 6, 3, 0)
    assert [(c.check, c.status, c.version, c.hits) for c in run.checks] == [
        ("cr-fabricated-fact", "observing", 1, 3)
    ]


async def test_observe_keeps_other_batches_when_one_fails() -> None:
    """一批问不成不连累其余批次：异常吞在批这一级，丢了几批记进台账。"""

    class FlakyJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("网关 502")
            return [
                JudgeObservation(
                    check="cr-fabricated-fact", quote=request.cards[0].thesis, why="第二批"
                )
            ]

    cards = [FABRICATED_CARD.model_copy(update={"thesis": f"第 {i} 张"}) for i in range(8)]
    observations, run = await observe(FlakyJudge(), make_request(cards), batch_size=6)

    assert len(observations) == 1
    assert run is not None
    assert (run.batches, run.batches_failed) == (2, 1)


@pytest.mark.parametrize("field", ["fixed", "suggestion"])
def test_observation_has_no_place_for_a_rewrite(field: str) -> None:
    """结构性守卫：JudgeObservation 只有 check/quote/why，改写建议**没有字段可落**。"""
    with pytest.raises(ValueError):
        JudgeObservation.model_validate(
            {
                "check": "cr-fabricated-fact",
                "quote": "你和你太太",
                "why": "编造",
                field: "改成两个人",
            }
        )
