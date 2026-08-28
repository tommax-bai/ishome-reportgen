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
    assert [a.lkp_id for a in package.domain_anchors("ergonomics")] == ["lkp-counter-height"]
