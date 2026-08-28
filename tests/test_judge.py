"""判官层（图 v0.2 §3 第二道）：prompt 只从包内 cr-+样例拼装、只报编号不改写、核实原句、
挂掉不阻塞成文。"""

from __future__ import annotations

import copy

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


def test_parse_tolerates_garbage_output() -> None:
    """判官输出不可解析 → 按无观察处理，不抛异常（第二道是加分项不是准入条件）。"""
    assert parse_observations("网关返回了一段废话", make_request()) == []
    assert parse_observations('[{"check": 1}]', make_request()) == []


async def test_observe_swallows_judge_failure() -> None:
    """判官挂掉 → 空观察，不抛出：判官不可用不能把能发的内容毙掉。"""

    class BrokenJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            raise RuntimeError("网关 502")

    assert await observe(BrokenJudge(), make_request()) == []


async def test_observe_skips_call_when_no_judge_checks() -> None:
    """本域无判官判据 → 一次调用都不发（冷启动期判官库很薄是常态，规则 4.18 宁薄勿撑）。"""

    class ExplodingJudge:
        async def review(self, request: JudgeRequest) -> list[JudgeObservation]:
            raise AssertionError("无判据时不应调用判官")

    request = make_request()
    assert await observe(ExplodingJudge(), request.model_copy(update={"checks": []})) == []


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
