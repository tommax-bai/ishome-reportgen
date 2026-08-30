"""测试夹具：报告数据包（与 project-svc Jackson 序列化 camelCase 同形——契约对齐样本）。"""

from __future__ import annotations

from typing import Any

from reportgen_worker.models import ReportDataPackage

PACKAGE_JSON: dict[str, Any] = {
    "entitlement": "PAID",
    # 求值基准日（v2.4）：时效越界判定的基准随包下发，成文线不看运行时时钟
    "evaluatedOn": "2026-08-29",
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
            # 两层模型（规则 1.9，v2.8）：一个匿名项、值是区间 → 只能整条引用 {lkp-counter-height}
            "valueKind": "range",
            "value": {"min": 900, "max": 950},
            "basisTag": "ergonomics@v1",
            "source": "行业通行",
            "calibration": "draft",
            "degraded": True,
            # 标注必挂（规则 4.10c，v2.4）：未过门 → 进正文就得同页标出来源与取数时间
            "provenance": {
                "source": "行业通行",
                "effectiveFrom": None,
                "effectiveTo": None,
                "calibration": "draft",
                "annotationRequired": True,
            },
            "presentation": "REFERENCE_ONLY",
        },
        {
            "lkpId": "lkp-passage-main",
            "name": "主通道净宽",
            "numberClass": "analysis",
            "unit": "mm",
            # 单边界只给一侧——仍是 range 的一个匿名项，不是"名叫 min 的项"
            "valueKind": "range",
            "value": {"min": 900},
            "basisTag": "ergonomics@v1",
            "source": "GB 50352 条文",
            "calibration": "calibrated",
            "degraded": False,
            "provenance": {
                "source": "GB 50352 条文",
                "effectiveFrom": "2019-09-01",
                "effectiveTo": None,
                "calibration": "calibrated",
                "annotationRequired": False,
            },
            "presentation": "THESIS_SUPPORT",
        },
        {
            # v2.4 之前它是"被隐藏"的典型（点值降不成参考区间）：现在照常下发，语域降为建议口吻，
            # 风险由同页标注承接——它同时是"未过门落点仍撑不起断言预算"的样本（挂杆高度谓词）
            "lkpId": "lkp-wardrobe-rod",
            "name": "衣柜挂杆高",
            "numberClass": "selection",
            "unit": "mm",
            # v2.8：单值不再套 {"v": …} 的壳（那层壳让 v 看着像一个项）——标量就是标量
            "valueKind": "single",
            "value": 2136,
            "basisTag": "ergonomics@v1",
            "source": "行业通行",
            "calibration": "draft",
            "degraded": True,
            "provenance": {
                "source": "行业通行",
                "effectiveFrom": None,
                "effectiveTo": None,
                "calibration": "draft",
                "annotationRequired": True,
            },
            "presentation": "REFERENCE_ONLY",
        },
        {
            "lkpId": "lkp-illuminance-living",
            "name": "起居室照度标准值",
            "numberClass": "analysis",
            "unit": "lx",
            # 分场景落点（规则 1.9，v2.8）：两项各是一个数，正文可引用其中一项
            # ——立案样本本尊（"沙发旁读书那块要单独加亮"六轮真跑写不出来的那句话）
            "valueKind": "scenario",
            "value": {"general": 100, "reading": 300},
            # 元信息出 value（规则 1.9 二）：参考平面各归各的字段，不与项同层
            "referencePlane": "0.75m 水平面",
            "basisTag": "lighting@v2",
            "source": "GB 50034-2013 表5.2.1；GB/T 50034-2024 同值",
            "calibration": "calibrated",
            "degraded": False,
            "provenance": {
                "source": "GB 50034-2013 表5.2.1；GB/T 50034-2024 同值",
                "effectiveFrom": "2024-10-01",
                "effectiveTo": None,
                "calibration": "calibrated",
                "annotationRequired": False,
            },
            "presentation": "THESIS_SUPPORT",
        },
    ],
    # v2.4（2026-08-29）取消隐藏档后恒空：字段按契约"只增不删"保留，永远没有内容
    "withheldAnchors": [],
    "gaps": [{"lkpId": "lkp-tv-distance", "reason": "missing_input", "detail": "屏高 × [3,4]"}],
    "personasByDomain": {
        "ergonomics": [
            {
                "assetId": "persona-ergonomics",
                "identity": "你在为这一家人校核他们家的尺寸。",
                # persona 四件之②（规则 4.13）：好/坏对照句对。reason 是 cr- 编号——只给判官层，
                # 不下发给写作器（业主语域里没有编号）。后两条形态不合：损坏条目归核验跑批，
                # 运行时静默跳过不兜底（同 assertionBudget 的既有写法）。
                "judgmentSamples": [
                    # 两句都自己过得了机检 → 下发
                    {
                        "bad": "根据人体工程学原理，台面高度宜设定为标准值。",
                        "good": "台面做到这个高度，你切菜时手腕是平的，不用弓腰。",
                        "reason": "cr-methodology-language",
                    },
                    {"bad": "主通道净宽建议不小于标准值。", "reason": "cr-weak-word"},
                    "整条不是对象",
                    # 正例自己违反数字纪律（样例编译早于该纪律）：照着写就是照着违规
                    {
                        "bad": "衣柜挂杆高度按身高折算，符合取物高度。",
                        "good": "挂杆按你的身高定在 1200 这个高度，抬手就够得着。",
                        "reason": "cr-unit-translation",
                    },
                    # 反例带数字也丢：真跑实证模型连反例里的裸数字一并照抄
                    {
                        "bad": "餐厅吊灯下沿距桌面700-800mm。",
                        "good": "那盏灯吊到坐下来抬头不刺眼、起身不撞头的高度。",
                        "reason": "cr-unit-translation",
                    },
                    # 反例带禁词是它的本职，但禁词零容忍——摆进上下文换不来等价示范价值
                    {
                        "bad": "综合考量后，台面高度取中位值。",
                        "good": "台面按主厨的身体定，不取平均值。",
                        "reason": "cr-methodology-language",
                    },
                ],
                "assertionBudget": [
                    # 背书得起：requires 全部已求值且非降档
                    {"predicate": "通道净宽是否够", "requires": ["lkp-passage-main"]},
                    # 背书不起：唯一 requires 是降档落点（规则 4.10a 经验条目不得作断言背书）
                    {"predicate": "台面高度", "requires": ["lkp-counter-height"]},
                    # 背书不起：requires 落点未过可核性门（v2.4 起它照常下发，但撑不起断言）
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
                "examples": [],
                "status": "active",
                "version": 1,
            },
            # 判官层判据（规则 4.17）：反例样例挂 cr- 之下，首批观察态——判出的问题只记录不拦截
            {
                "assetId": "cr-fabricated-fact",
                "checkType": "semantic_judge",
                "scope": ["正文"],
                "pattern": None,
                "requirement": "关于这家人的事实只能来自匿名画像",
                "message": "编造输入之外的家庭事实（规则 4.3；图 v0.2 §0）",
                "decidedBy": "规范规则 4.3 + 图 v0.2 §0",
                "thresholdRefs": [],
                "examples": [
                    {
                        "bad": "你和你太太",
                        "why": "画像里没有家庭构成信息，「太太」是模型自己添的人",
                        "fixed": "两个人同时用的时候",
                    }
                ],
                "status": "observing",
                "version": 1,
            },
        ],
        # V4 之前发布的快照没有 examples/status 两字段：模型侧缺省 = 空样例 + observing
        "lighting": [
            {
                "assetId": "cr-legacy-no-judge-fields",
                "checkType": "cross_field",
                "message": "老快照条目（无 examples/status）",
                "decidedBy": "规范规则 4.10b",
                "version": 1,
            }
        ],
    },
    "bannedTermsByDomain": {"ergonomics": ["依据", "综合考量"], "lighting": ["依据"]},
    # 锁定清单（图 v0.2 §2）：dom-lighting 的主产物含 art-ceiling-lighting-plan（prec-schematic），
    # 图框必挂 DISCLAIM_P1（规范 §7 + 规则 2.1）；ergonomics 本轮无必挂文案 → 键缺席即无要求。
    "lockedTextsByDomain": {"lighting": ["DISCLAIM_P1"]},
    "anonymousProfile": {
        "chiefHeightMm": 1700,
        "tallestHeightMm": 1780,
        "eyeHeightMm": None,
        "tvScreenHeightMm": None,
        "layoutFeatures": {"kitchen_shape": "U", "sunken_bathroom": "true"},
        # 城市档（裁决 2026-08-29）：求值线按它选单价档，成文线只收下不下发进 prompt
        "cityTier": "一线",
    },
}


def load_package() -> ReportDataPackage:
    return ReportDataPackage.model_validate(PACKAGE_JSON)
