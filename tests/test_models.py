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


def test_parses_city_tier_as_market_parameter() -> None:
    """城市档随匿名画像下发（裁决 2026-08-29）：市场参数不是身份，故进得了 extra=forbid 的模型。

    缺席也照常解析——老包（未升级的生产方）没有这个字段，造价章降回全国粗档区间，不是解析失败。
    """
    package = load_package()
    assert package.anonymous_profile.city_tier == "一线"

    legacy = copy.deepcopy(PACKAGE_JSON)
    legacy["anonymousProfile"].pop("cityTier")
    assert load_package().__class__.model_validate(legacy).anonymous_profile.city_tier is None


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


def test_package_accepts_triggered_rules_and_slices_them_by_domain() -> None:
    """消费侧先建：生产侧还没发这个字段之前，成文线就得认得它（同 provenance 那批的顺序纪律）。"""
    raw = copy.deepcopy(PACKAGE_JSON)
    raw["triggeredRulesByDomain"] = {
        "storage": [
            {
                "assetId": "rule-practice-storage-balcony-cleaning",
                "layer": "tier-practice",
                "content": "阳台留清洁工具位（含插座）",
                "rationale": "吸尘器和拖把要有固定的家，还要能充电",
                "severity": "recommended",
                "calibration": "draft",
                "triggeredBy": {
                    "type": "layout_feature",
                    "feature": "balcony_service",
                    "evidence": "阳台内有洗衣机设备位",
                },
            }
        ]
    }

    package = load_package().__class__.model_validate(raw)

    assert (
        package.domain_triggered_rules("storage")[0].triggered_by.evidence == "阳台内有洗衣机设备位"
    )
    assert package.domain_triggered_rules("lighting") == []  # 无该域条目＝空，不是 KeyError


def test_package_without_triggered_rules_still_parses() -> None:
    """缺省空 = 生产方未升级的旧包，**不是"未触发"**：宽进，不据此拦截。"""
    assert load_package().triggered_rules_by_domain == {}


# ---------------------------------------------------------------------------
# 两层模型（规则 1.9，v2.8）：valueKind 七值闭集 + 项名 + 元信息出 value
# ---------------------------------------------------------------------------


def test_parses_value_kind_and_reference_plane() -> None:
    """值构成随包下发；参考平面从 value 里搬出来，成了自己的字段（规则 1.9 二）。"""
    package = load_package()
    illuminance = package.domain_anchors("lighting")[0]

    assert illuminance.value_kind == "scenario"
    assert illuminance.reference_plane == "0.75m 水平面"
    assert package.domain_anchors("ergonomics")[2].value == 2136  # 单值＝标量，v 壳退场


def test_rejects_package_without_value_kind() -> None:
    """值构成是必填不是可推断项：``{"min": 900}`` 到底是区间还是"名叫 min 的项"，靠猜
    就等于把"不靠推断"这条裁决在消费侧还回去（同 presentation 的收窄纪律）。"""
    legacy = copy.deepcopy(PACKAGE_JSON)
    legacy["anchors"][0].pop("valueKind")
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(legacy)


def test_rejects_unknown_value_kind() -> None:
    """七值闭集之外整包解析失败：认不出构成类别，prompt 写不对、引用也判不对。"""
    tainted = copy.deepcopy(PACKAGE_JSON)
    tainted["anchors"][0]["valueKind"] = "matrix"
    with pytest.raises(ValidationError):
        load_package().__class__.model_validate(tainted)


def test_item_names_follow_the_value_kind_not_the_key_shape() -> None:
    """项名由 valueKind 判定：``range`` 的 min/max **不是项**，分项落点的键才是。"""
    package = load_package()
    counter, _, rod = package.domain_anchors("ergonomics")

    assert counter.value_kind == "range"
    assert counter.has_items is False
    assert counter.item_names == []  # min/max 是值形态，不是项
    assert rod.has_items is False

    illuminance = package.domain_anchors("lighting")[0]
    assert illuminance.has_items is True
    assert illuminance.item_names == ["general", "reading"]  # 生产方给的顺序，不重排


def test_anchor_brief_carries_item_names_but_no_value() -> None:
    """推导步的入参：多了项名，**仍然没有值**——项名是标签不是数（规则 1.9 三）。"""
    from reportgen_worker.models import AnchorBrief

    brief = AnchorBrief.of(load_package().domain_anchors("lighting")[0])

    assert brief.items == ["general", "reading"]
    assert "value" not in brief.model_dump()
