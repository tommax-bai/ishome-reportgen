"""报告数据包契约对齐：camelCase 解析、匿名结构守卫、本域切片。"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tests.support import PACKAGE_JSON, load_package


def test_parses_java_shaped_camel_case_json() -> None:
    package = load_package()
    assert package.domains == ["ergonomics", "lighting"]
    assert package.releases[1].release_tag == "lighting@v2"
    assert package.personas_by_domain["lighting"][0].asset_id == "persona-lighting"
    assert package.checks_by_domain["ergonomics"][0].decided_by == "规范规则 5.9"


def test_rejects_user_identity_fields() -> None:
    """匿名性结构守卫（图 v0.2 §0）：任何用户/项目标识字段直接解析失败。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["userId"] = "u-123"
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(tainted)

    tainted_profile = copy.deepcopy(PACKAGE_JSON)
    tainted_profile["anonymousProfile"]["projectId"] = "p-456"
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(tainted_profile)


def test_domain_anchor_slice() -> None:
    package = load_package()
    assert [a.lkp_id for a in package.domain_anchors("lighting")] == ["lkp-illuminance-living"]
    assert [a.lkp_id for a in package.domain_anchors("ergonomics")] == [
        "lkp-counter-height",
        "lkp-passage-main",
        "lkp-wardrobe-rod",
    ]


def test_parses_gate_fields() -> None:
    """门禁字段随包：权益档、呈现档位；withheldAnchors 恒空（v2.4 取消隐藏档）。"""
    package = load_package()
    assert package.entitlement == "PAID"
    assert [a.presentation for a in package.domain_anchors("ergonomics")] == [
        "REFERENCE_ONLY",
        "THESIS_SUPPORT",
        "REFERENCE_ONLY",
    ]
    assert package.withheld_anchors == []


def test_rejects_unknown_presentation_tier() -> None:
    """呈现档位是闭集：来路不明的档位不许被静默当成可用（fail loud）。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["presentation"] = "SUPPORTING"
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(tainted)


def test_rejects_withheld_presentation_from_stale_producer() -> None:
    """v2.4 起 WITHHELD 不是合法档位：还在隐藏的生产方**整包解析失败**，比放它进来安全。

    隐藏本身已经是违约而不再是纪律——收下它意味着这一份报告里有一批建议被悄悄扣着不发，
    而消费侧已经没有任何门禁会为此报警。
    """
    stale = copy.deepcopy(PACKAGE_JSON)
    stale["anchors"][0]["presentation"] = "WITHHELD"
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(stale)


def test_parses_provenance_fields() -> None:
    """标注纪律随包（规则 4.10c，v2.4）：来源/时效/可核性状态 + 强制位 annotationRequired。"""
    package = load_package()
    assert package.evaluated_on == "2026-08-29"

    counter = package.domain_anchors("ergonomics")[0]
    assert counter.provenance is not None
    assert counter.provenance.source == "行业通行"
    assert counter.provenance.annotation_required is True
    assert counter.requires_annotation is True

    passage = package.domain_anchors("ergonomics")[1]
    assert passage.requires_annotation is False
    assert passage.provenance is not None
    assert passage.provenance.effective_from == "2019-09-01"


def test_requires_annotation_falls_back_for_legacy_package() -> None:
    """老包无 provenance：按 calibration 回退判定——多标一行是页脚变长，漏标是纪律失效。"""
    legacy = copy.deepcopy(PACKAGE_JSON)
    legacy["anchors"][0].pop("provenance")
    package = load_package().__class__.model_validate(legacy)

    assert package.domain_anchors("ergonomics")[0].requires_annotation is True
    assert package.evaluated_on == "2026-08-29"
