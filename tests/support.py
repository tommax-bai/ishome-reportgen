"""测试夹具：报告数据包（与 project-svc Jackson 序列化 camelCase 同形——契约对齐样本）。"""

from __future__ import annotations

from typing import Any

from reportgen_worker.models import ReportDataPackage

PACKAGE_JSON: dict[str, Any] = {
    "entitlement": "PAID",
    "domains": ["ergonomics", "lighting"],
    "releases": [
        {"domain": "ergonomics", "releaseTag": "ergonomics@v1"},
        {"domain": "lighting", "releaseTag": "lighting@v2"},
    ],
    "anchors": [
        {
            "lkpId": "lkp-counter-height",
            "name": "橱柜台面高",
            "numberClass": "selection",
            "unit": "mm",
            "value": {"min": 900, "max": 950},
            "basisTag": "ergonomics@v1",
            "source": "行业通行",
            "calibration": "draft",
            "degraded": True,
            "presentation": "REFERENCE_ONLY",
        },
        {
            "lkpId": "lkp-passage-main",
            "name": "主通道净宽",
            "numberClass": "analysis",
            "unit": "mm",
            "value": {"min": 900},
            "basisTag": "ergonomics@v1",
            "source": "GB 50352 条文",
            "calibration": "calibrated",
            "degraded": False,
            "presentation": "THESIS_SUPPORT",
        },
        {
            "lkpId": "lkp-illuminance-living",
            "name": "起居室照度标准值",
            "numberClass": "analysis",
            "unit": "lx",
            "value": {"general": 100, "reading": 300},
            "basisTag": "lighting@v2",
            "source": "GB 50034-2013 表5.2.1；GB/T 50034-2024 同值",
            "calibration": "calibrated",
            "degraded": False,
            "presentation": "THESIS_SUPPORT",
        },
    ],
    # PAID 侧被纪律拿掉的落点（规则 4.10）：点值降不成参考区间，值根本不下发
    "withheldAnchors": [
        {"lkpId": "lkp-wardrobe-rod", "basisTag": "ergonomics@v1", "reason": "no_range_form"}
    ],
    "gaps": [{"lkpId": "lkp-tv-distance", "reason": "missing_input", "detail": "屏高 × [3,4]"}],
    "personasByDomain": {
        "ergonomics": [
            {
                "assetId": "persona-ergonomics",
                "identity": "你在为这一家人校核他们家的尺寸。",
                "judgmentSamples": [],
                "assertionBudget": [
                    # 背书得起：requires 全部已求值且非降档
                    {"predicate": "通道净宽是否够", "requires": ["lkp-passage-main"]},
                    # 背书不起：唯一 requires 是降档落点（规则 4.10a 经验条目不得作断言背书）
                    {"predicate": "台面高度", "requires": ["lkp-counter-height"]},
                    # 背书不起：requires 落点根本没求出来（被隐藏）
                    {"predicate": "挂杆高度", "requires": ["lkp-wardrobe-rod"]},
                ],
                "bannedTerms": {"domain_extra": ["人体工学"]},
                "version": 1,
            }
        ],
        "lighting": [
            {
                "assetId": "persona-lighting",
                "identity": "你在为这一家人写他们家的灯光方案。",
                "judgmentSamples": [],
                "assertionBudget": [],
                "bannedTerms": {"domain_extra": ["照度", "显指"]},
                "version": 1,
            }
        ],
    },
    "checksByDomain": {
        "ergonomics": [
            {
                "assetId": "cr-weak-words",
                "checkType": "regex_deny",
                "scope": ["正文"],
                "pattern": "可能|建议考虑|也许",
                "requirement": None,
                "message": "分析级结论禁弱词（规则 5.9）",
                "decidedBy": "规范规则 5.9",
                "thresholdRefs": [],
                "version": 1,
            }
        ],
        "lighting": [],
    },
    "bannedTermsByDomain": {"ergonomics": ["依据", "综合考量"], "lighting": ["依据"]},
    "anonymousProfile": {
        "chiefHeightMm": 1700,
        "tallestHeightMm": 1780,
        "eyeHeightMm": None,
        "tvScreenHeightMm": None,
        "layoutFeatures": {"kitchen_shape": "U", "sunken_bathroom": "true"},
    },
}


def load_package() -> ReportDataPackage:
    return ReportDataPackage.model_validate(PACKAGE_JSON)
