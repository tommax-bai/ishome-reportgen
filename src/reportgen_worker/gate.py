"""出口过检·规则层（图 v0.2 §3）：确定性机检，卡片不过检产生违规清单
（重写反馈/failed verdict 的依据）。

三类判据分离：

- **引擎纪律（gate-\\*）**：图 v0.2 §0 的硬性约束，代码即形态——数字只经 {lkp-*} 占位、
  占位必须可解析、必填非空、禁词零命中、客户语域禁裸 lkp- 标识名与裸项名、
  语域示范不得被逐字抄进正文、**正文不得与主旨句逐字相同**（临时护栏，治本是叙事推导那一步）、
  **记号旁边不替渲染层说话**（前不写边界词、后不补单位——记号渲出来是完整的说法）。
  不属 cr- 命名空间（cr- 是 release 数据，规则 4.10b）。
  **两层模型（规则 1.9，v2.8）**：一条落点＝若干项，正文可写 ``{lkp-x.项名}`` 引其中一项。
  ``single``/``range`` 只有一个匿名项，带项名即违规；其余五类的项名必须真实存在，
  打回提示**逐字列出该落点有哪几项**——灯光域六轮真跑 27/27 越界占位符都是"真落点 id +
  该落点 value 里一个真实的键"，模型缺的不是纪律是**合法写法**，而旧提示从不告诉它有哪些选择。
- **语域与标注门禁（gate-thesis-\\* / gate-assertion-\\* / gate-provenance-\\*）**：规则
  4.10a/4.10c/5.8 的消费侧强制。判定由求值线做完（anchors[].presentation 与
  anchors[].provenance.annotationRequired），本层只做**可确定性判定**的事：①卡片声明的断言预算
  谓词必须在本域 persona 的 assertion_budget 内，
  且其 requires 的 lkp- 全部已求值且非降档；②未过门/已过期落点被引用时，同页必须挂它的依据标注
  （规则 4.10c 标注必挂，v2.4 起替代隐藏档；页级比对在册级 :mod:`reportgen_worker.activities`）。
  **v2.4 拆掉的两件**：隐藏落点的引用拦截（隐藏档整档作废）与"主旨句支点必须是 THESIS_SUPPORT"
  （规则 4.10c 明文失效：未过门落点已可进主旨句，条件是随页标注）。断言预算那一条**不变**——
  它管的是"以什么底气说"，不是"能不能说"。
  **明确不做的**：判断句的语义识别——"这句话算不算判断句"没有确定性判据，机检不假实现。
  未声明 assertions 却写成判断句、参考口吻被写成断言口吻，全部归**判官层**（分域反例库，
  图 v0.2 §3 出口过检·判官层）。本层只保证"声明了就必须有背书"，不保证"没声明就不是断言"。
- **cr- 判据（release 数据物化执行）**：随报告数据包下发的 checks 中带 pattern 的文本级判据
  逐条跑；无 pattern 的类型（count_max/cross_field 等）本层暂不执行。
"""

from __future__ import annotations

import re

from reportgen_worker.models import (
    ITEM_NAME_RE,
    Card,
    NarrativeClaim,
    ProvenanceNote,
    ReportAnchor,
    ReportDataPackage,
    Violation,
)

PLACEHOLDER_RE = re.compile(r"\{(lkp-[a-z0-9-]+)(?:\.([a-z0-9-]+))?\}")
"""正文记号（规则 1.9 两层模型，v2.8）：``{lkp-x}`` 引整条，``{lkp-x.项名}`` 引其中一项。

``{lkp-x.min}`` 形态上匹配得上，**语义上一定不合法**——``min``/``max`` 是项的值形态不是项，
故 ``single``/``range`` 落点带项名一律违规（:func:`ref_violation`）。"引一端丢掉另一端"由此
**由结构堵死**：不是靠打回提示劝住，而是那条落点根本没有第二项可指。
"""
REF_SEPARATOR = "."
DIGIT_RE = re.compile(r"[0-9０-９]")
BARE_LKP_RE = re.compile(r"lkp-", re.IGNORECASE)

# 中文数字：真跑（2026-08-28）抓到的绕过——模型被禁裸数字后改写"亮三到五倍""不能低于九十"，
# 阿拉伯数字门禁一字未命中，但读者读到的仍是没有落点背书的数字。
#
# 判据形态经一次自我修正：初版只判"数字+量词"，结果**漏掉了它自己的立案样本**——"不能低于九十"
# 没有量词（自迭代回路首采 §五-1 实测，该句在四次跑里逐字出现且全部通过）。光判数字字符又会把
# "一般活动""一起""十分"打成违规。故收成三条互补形态，每条都要求数词处在**数量语境**里：
#   ① 数词 + 量词/单位（三到五倍、九十厘米）
#   ② 比较词 + 数词（不低于九十、达到三成）——补的正是立案样本这一类
#   ③ 数词 + 概数词 + 单位（七十多厘米、二十几万）——真跑漏过一次定位数字（0.75m 参考平面）
# 量词表按真实误报补，不做通用中文数字解析（不发明表达式语法）。
CHINESE_NUMERAL = "零一二三四五六七八九十百千两半"
# 量词只收**测量/选型**类：规则 2.3 数字三分法的射程是定位/选型/分析数字，**列举计数不在其内**。
# 故 个|条|项|次|遍|盏|种|层|档|点 一律不收——真跑实测"四个区域""这三项""这两点"会被打成违规，
# 那是把"数东西"当成"报数值"，拦下去等于让引擎写不成句（过拦与漏拦同样是失效）。
# 计数若确属选型数字（如"色温不超过三种"），由形态②的比较词兜住。
CHINESE_QUANTIFIER = (
    "倍|度|米|厘米|毫米|公分|平米|平方米|㎡|元|万|级|成|折|"
    "分之|秒|分钟|小时|天|周|月|年|K|lx|mm|cm|m"
)
CHINESE_COMPARATOR = (
    "低于|高于|不到|不足|达到|超过|至少|最多|多于|少于|大于|小于|将近|接近|大约|近|约"
)
CHINESE_APPROX = "多|几|来|余"
_NUM = f"[{CHINESE_NUMERAL}]+(?:到|至|~|-)?[{CHINESE_NUMERAL}]*"
CHINESE_NUMBER_RE = re.compile(
    f"(?:{_NUM}(?:{CHINESE_QUANTIFIER})"  # ① 数词+量词
    f"|(?:{CHINESE_COMPARATOR}){_NUM}"  # ② 比较词+数词（立案样本"不能低于九十"）
    f"|{_NUM}(?:{CHINESE_APPROX})(?:{CHINESE_QUANTIFIER}))"  # ③ 数词+概数+单位
)

THESIS_SUPPORT = "THESIS_SUPPORT"
CALIBRATED = "calibrated"

# 打回话只有在"照做得到"时才是打回，否则是把重写轮数烧掉（坑单第 19 条）。两条写数判据原先
# 只说"换 {lkp-*} 占位"，而立案样本 `gate-chinese-numeral`「半小时」在本域**根本没有对应落点**——
# 模型照这句话做不到，两轮重写全烧在这一条上（2026-08-30 灯光章真跑，verdict=failed 的唯一违规）。
# 补的是**第二条出路**：禁的是没有背书的数，不是禁止说这件事，说不带数的说法照样成立。
_NO_ANCHOR_ROUTE = (
    "。两条路选一条：上面的落点清单里有能背书这个数的，就写它的记号；"
    "一条都没有，就把这句话改成不带数的说法——禁的是没有背书的数，不是禁止说这件事"
)

# 记号旁边的措辞：**在输入的地方给出这个空要填的值的特征，检测时检测内容合不合这个特征**
# （用户裁决 2026-08-30）。特征四种，各配一句要求与一道机检：
#
#   固定值        记号出的就是这个数        前面不许加边界词（加了＝编一个数据里没有的关系）
#   只有下限      记号出裸数                句子里必须写下限那一族的词
#   只有上限      记号出裸数                句子里必须写上限那一族的词
#   两端齐的区间  记号出"900–950"           不许加单侧边界词
#   （并列多项）  记号自带逐项说法          句子不写——见下"句子够不够得着"
#
# **为什么改成这样**（原先是渲染层把"不超过"一起渲出来、写手一律不许写边界词）：写手那句话的
# 语法主干正好落在洞里，十二跑里九跑它照样自己写了一遍，成品叠字
# `全屋灯光颜色种类不能多于 不超过 3 种 种。`。而它写的方向 16/17 是对的——**能力不是问题，
# 可核性才是**：边界词写错只是话错、数还是那个数，且验它是个是非题（数据里写着只有上界，
# 句子里就得有上界那一族的词）。**单位不这么办**：单位写错会改掉数的大小，且数据里的单位是
# "mm/Ra/×环境照度"这种内部写法、与人话逐字对不上，验不动——故单位仍由渲染层随值出
# （用户裁决 2026-08-30），记号后面再补一遍单位仍是违规。
#
# **句子够不够得着**是唯一的分界：一个记号只渲一个值时，写手的句子就在它旁边，边界说法归句子；
# 一个记号并列多项时（如淋浴净尺寸的宽/深各只给下限），句子够不着里面的每一项，仍由渲染层
# 逐项带上。表格与图框将来同理——那里"旁边"指列头，届时各配各的机检（触发条件：清单页族落地）。
#
# 词表是**正面清单**（"必须从这几个词里挑"），不是禁止清单。方向按**词族**分，正说反说归同一族：
#   下限一族  不低于 90（正说）／低于 90 就偏暗（反说）——两句都在说这条下限
#   上限一族  不超过 3（正说）／超过 3 就杂（反说）
# 表外的词按"没写"处理并把可选词逐字列出来——**正面清单漏一个词只多烧一轮重写，禁止清单漏一个
# 词缺陷就进成品**（今天上午那句叠字正是后者的产物）。失败方向不对称，故选前者。
BOUND_ROOTS_BY_SIDE = {
    "min": ("不低于", "至少", "不少于", "低于", "少于", "不小于", "小于", "最少", "起码", "下限"),
    "max": (
        "不超过",
        "最多",
        "不多于",
        "超过",
        "多于",
        "不高于",
        "高于",
        "不大于",
        "大于",
        "至多",
        "上限",
        "封顶",
    ),
}
"""每一侧可用的边界词，**表内第一个是最顺的说法**（打回话按这个顺序列给模型）。"""
_BOUND_SIDE_BY_ROOT = {root: side for side, roots in BOUND_ROOTS_BY_SIDE.items() for root in roots}
# 匹配按词根，长的优先（"不低于" 先于 "低于"，报出来的词面才是模型真写的那个）
BOUND_WORD_RE = re.compile("|".join(sorted(_BOUND_SIDE_BY_ROOT, key=len, reverse=True)))
_CLAUSE_TAIL_RE = re.compile(r"[^，。；：！？、\n!?,;:]*$")
"""记号所在的那个小句（自最近一个断句符之后）——判据的取值范围。"""
_BOUND_KEYS = frozenset({"min", "max"})
BOUND_SIDE_NAME = {"min": "下限", "max": "上限"}

# persona 判断句样例（规则 4.13 之②）进 prompt 后的真跑副作用（2026-08-28）：模型把 ✓ 示范句
# **逐字抄进卡片**当成这家人的结论——示范是"怎么讲"的样本，不是"讲什么"的素材，抄过去就成了
# 一句没有落点背书、与这家人无关的断言。整句重合是确定性判据，故归本层；半句化用属语义，归判官层。
MIN_SAMPLE_LENGTH = 12


def placeholder_refs(text: str) -> set[str]:
    """正文里出现过的记号（花括号内逐字）：``lkp-x`` 或 ``lkp-x.项名``。"""
    return {
        match.group(1) + (f"{REF_SEPARATOR}{match.group(2)}" if match.group(2) else "")
        for match in PLACEHOLDER_RE.finditer(text)
    }


def split_ref(ref: str) -> tuple[str, str | None]:
    """记号 → （落点 id，项名或 None）。落点 id 里不含点号，故按第一个点号切即可。"""
    base, separator, item = ref.partition(REF_SEPARATOR)
    return base, item if separator else None


def anchor_id_of(ref: str) -> str:
    """记号 → 它指的落点（下游按落点粒度做的事都从这里取：依据标注、册级解析）。"""
    return split_ref(ref)[0]


def item_tokens(anchor: ReportAnchor) -> list[str]:
    """这条落点**可以逐字写进正文的记号**（分项落点用，供 prompt 与打回提示共用一份口径）。

    给整只记号而不是光给项名：模型要抄的是记号，列项名等于让它自己拼一次——
    真跑证据说的就是"想说的那句话没有合法写法"，那么合法写法就该逐字摆在它眼前。
    """
    return [f"{{{anchor.lkp_id}{REF_SEPARATOR}{item}}}" for item in anchor.item_names]


def _granularity_mismatch(ref: str, placeholders: set[str]) -> bool:
    """声明的记号与正文的记号**只差在粒度上**：同一条落点，一边写整条一边写某一项。

    判得这么窄是有代价意识的：放宽成"落点段相同即算粒度错"会把"声明了这一项、正文写了另一项"
    也算进来，而那一项有值却没露面正是假坦白要封的形态，得走另一支的话术。
    """
    base, item = split_ref(ref)
    if item is None:
        # 声明了整条，正文写的是它的某一项
        return any(p != base and anchor_id_of(p) == base for p in placeholders)
    # 声明了某一项，正文写的是整条
    return base in placeholders


def _unresolved_hint(ref: str, anchors_by_id: dict[str, ReportAnchor]) -> str:
    """落点段就认不出时的打回提示：从**本域真实落点**算出它想写的是哪条，不是套模板。

    两种真跑/预期形态都用连字符把第二段拼进了落点 id：``{lkp-x-min}``（区间拆两端，
    2026-08-28 实测）与 ``{lkp-x-reading}``（项名写成连字符，两层模型上线后的同族形态）。
    """
    for base in sorted(anchors_by_id, key=len, reverse=True):
        if not ref.startswith(f"{base}-"):
            continue
        anchor, suffix = anchors_by_id[base], ref[len(base) + 1 :]
        if suffix in anchor.item_names:
            return f"——项要用点号挂在落点后面，写 {{{base}{REF_SEPARATOR}{suffix}}}"
        if suffix in {"min", "max"} and not anchor.has_items:
            return (
                f"——{base} 只有一个值，写 {{{base}}} 就行：上下限各管一条纪律"
                "（下限管够不够，上限管过不过），拆一端会丢掉另一端"
            )
    return "（数字只能引用求值线产出）"


def ref_violation(label: str, ref: str, anchors_by_id: dict[str, ReportAnchor]) -> Violation | None:
    """一条记号的引用合法性（规则 1.9 两层模型，v2.8）：认不出即违规，认得出就说清合法写法。

    三种不合法逐条给**不同的话**，判据编号仍是同一条——语义没变（"引用解析不到"），
    变的只是它该被告知什么：

    - 落点段不认识 → 可能是把项/区间端拼进了 id，:func:`_unresolved_hint` 算出它想写什么；
    - ``single``/``range`` 带项名 → 这条落点只有一个匿名项，整条引用即可（``{lkp-x.min}``
      落在这一支：min/max 是值形态不是项）；
    - 分项落点写了不存在的项 → **逐字列出它有哪几项**。这一条是本轮最要紧的：灯光域六轮真跑
      27/27 的越界占位符都是"真落点 id + 该落点 value 里一个真实的键"，模型不是不守规矩，
      是想说的那句话没有合法写法；旧提示连吃三稿的原因也在此——它没告诉模型有哪些合法选择。
    """
    base, item = split_ref(ref)
    anchor = anchors_by_id.get(base)
    if anchor is None:
        return Violation(
            check="gate-number-ref-unresolved",
            detail=f"{label} 引用 {ref} 不在本域落点对象内{_unresolved_hint(ref, anchors_by_id)}",
        )
    if item is None:
        return None
    if not anchor.has_items:
        # min/max 这一支单独点破：它是这条落点值的两端（值形态），不是项——
        # 不说这一句，打回只会被理解成"项名写错了"，下一稿换个项名再来一遍。
        boundary = "（min/max 是这条落点值的两端，不是项）" if item in {"min", "max"} else ""
        return Violation(
            check="gate-number-ref-unresolved",
            detail=(
                f"{label} 引用 {ref}：{base}（{anchor.name}）只有一个值，没有项可指{boundary}——"
                f"写 {{{base}}} 引整条即可（值是区间就整条渲染成区间）"
            ),
        )
    if item not in anchor.item_names:
        choices = (
            "、".join(item_tokens(anchor)) or "（一项都列不出：该落点值形态与 valueKind 不符）"
        )
        return Violation(
            check="gate-number-ref-unresolved",
            detail=(
                f"{label} 引用 {ref}：{base}（{anchor.name}）没有「{item}」这一项。"
                f"它有这几项，要哪一项就逐字写哪一个：{choices}"
            ),
        )
    return None


def collect_banned_terms(domain: str, package: ReportDataPackage) -> list[str]:
    """公共禁词（包内已物化）+ persona 域内禁词（banned_terms 内字符串列表，如 domain_extra）。"""
    terms = set(package.banned_terms_by_domain.get(domain, []))
    for persona in package.personas_by_domain.get(domain, []):
        for value in persona.banned_terms.values():
            if isinstance(value, list):
                terms.update(t for t in value if isinstance(t, str))
    return sorted(terms)


def judgment_good_texts(domain: str, package: ReportDataPackage) -> list[str]:
    """本域判断句样例的**正例原文**（persona 四件之②的 ✓ 侧，规则 4.13）。

    形态不合的条目静默跳过（同 :func:`assertion_budget`）。太短的正例不收：示范句是整句，
    短语级重合是正常用词而非照抄，拿它判违规就成了过拦（过拦与漏拦同样是失效）。
    """
    texts: list[str] = []
    for persona in package.personas_by_domain.get(domain, []):
        for entry in persona.judgment_samples:
            if not isinstance(entry, dict):
                continue
            good = entry.get("good")
            if isinstance(good, str) and len(good.strip()) >= MIN_SAMPLE_LENGTH:
                texts.append(good.strip())
    return sorted(set(texts))


def annotation_required_anchors(package: ReportDataPackage) -> dict[str, ReportAnchor]:
    """全册范围内"进正文就必须随页标注"的落点（规则 4.10c）：lkp_id → 落点。

    **不切域**：册级校验按页比对，而页是按域成的——切域会让"某页引用了别域落点"这种情况漏检。
    要求集的口径是落点自己的 ``provenance``，不是页、不是卡片：谁被引用了谁就得有标注。
    """
    return {a.lkp_id: a for a in package.anchors if a.requires_annotation}


def provenance_note(anchor: ReportAnchor) -> ProvenanceNote:
    """落点 → 页上的依据标注（纯投影，零生成：字段原样搬，不拼接、不改写、不补空）。"""
    provenance = anchor.provenance
    return ProvenanceNote(
        lkp_id=anchor.lkp_id,
        source=provenance.source if provenance is not None else anchor.source,
        effective_from=provenance.effective_from if provenance is not None else None,
        effective_to=provenance.effective_to if provenance is not None else None,
        calibration=provenance.calibration if provenance is not None else anchor.calibration,
    )


def required_provenance_notes(
    cards: list[Card], domain: str, package: ReportDataPackage
) -> list[ProvenanceNote]:
    """本稿卡片实际引用的落点里，需要标注的那些（按 lkp_id 升序，确定性）。

    引用面取 ``number_refs`` 与正文占位符的**并集**：两者不一致本身是违规
    （``gate-number-ref-undeclared``），但标注要求不能等违规先被修好——过检没过的稿子不会成页，
    过了检的两者必然一致，取并集只是让本函数与门禁的执行次序无关。

    记号一律先取**落点段**（v2.8）：标注说的是"这个数从哪来、什么时候取的"，那是整条落点的属性，
    引其中一项不改变它的来源——按项标注等于同一份来源在页脚重复几遍。
    """
    required = annotation_required_anchors(package)
    referenced: set[str] = set()
    for card in cards:
        referenced |= {anchor_id_of(ref) for ref in card.number_refs}
        referenced |= {anchor_id_of(ref) for ref in placeholder_refs(f"{card.thesis}\n{card.body}")}
    return [provenance_note(required[ref]) for ref in sorted(referenced & set(required))]


def assertion_budget(domain: str, package: ReportDataPackage) -> dict[str, list[str]]:
    """本域断言预算：谓词 → 它需要的 lkp- 清单（persona 四件之三，规则 4.13/5.8）。

    多 persona 合并；形态不合的条目静默跳过——预算是 release 数据，损坏条目由资产回路的核验
    跑批负责，运行时不替它兜底。
    """
    budget: dict[str, list[str]] = {}
    for persona in package.personas_by_domain.get(domain, []):
        for entry in persona.assertion_budget:
            if not isinstance(entry, dict):
                continue
            predicate = entry.get("predicate")
            requires = entry.get("requires", [])
            if not isinstance(predicate, str) or not isinstance(requires, list):
                continue
            budget[predicate] = sorted({r for r in requires if isinstance(r, str)})
    return budget


def thesis_support_ids(domain: str, package: ReportDataPackage) -> set[str]:
    """本域可作判断句支点的落点（规则 4.10a：只有过可核性门的条目能背书断言）。"""
    return {a.lkp_id for a in package.domain_anchors(domain) if a.presentation == THESIS_SUPPORT}


def backed_predicates(domain: str, package: ReportDataPackage) -> list[str]:
    """本次背书得起的判断句谓词——requires 全部已求值且非降档，可被卡片声明使用。"""
    supported = thesis_support_ids(domain, package)
    budget = assertion_budget(domain, package)
    return sorted(
        p for p, requires in budget.items() if requires and supported.issuperset(requires)
    )


def unbacked_predicates(domain: str, package: ReportDataPackage) -> list[str]:
    """本次背书不起的谓词：写进 prompt 让写作器知道这几句这轮不许说（规则 4.18 宁薄勿撑）。"""
    backed = set(backed_predicates(domain, package))
    return sorted(p for p in assertion_budget(domain, package) if p not in backed)


def _value_shape_violation(anchor: ReportAnchor) -> Violation | None:
    """``value`` 的形态与 ``value_kind`` 对不上（规则 1.9 一，v2.8）。

    为什么值得在这里拦一次：``value_kind`` 是**判定**不是描述——prompt 按它列合法记号、
    门禁按它判引用。数据里两者打架时，模型会照着一份错清单写，然后被一条它无从修正的违规打回。
    与既有 provenance 那两条同路：生产侧违约在最前面响亮报出，不烧一次 LLM 调用。

    ``min``/``max`` 出现在分项落点的项位上一并拦：项名闭集/词表里没有它们，
    而放它进来就等于把 ``{lkp-x.min}`` 变成合法写法——本裁决"由结构堵死"的那条缝正在这儿。
    """
    kind, value = anchor.value_kind, anchor.value
    if not anchor.has_items:
        if kind == "single" and isinstance(value, dict):
            return Violation(
                check="gate-anchor-value-shape",
                detail=f"{anchor.lkp_id} valueKind=single 的值必须是标量，收到字典 {sorted(value)}",
            )
        boundaries = set(value) if isinstance(value, dict) else set()
        if kind == "range" and (not boundaries or boundaries - {"min", "max"}):
            return Violation(
                check="gate-anchor-value-shape",
                detail=(
                    f"{anchor.lkp_id} valueKind=range 的值必须是 {{min,max}}（单边界只给一侧），"
                    f"收到 {sorted(boundaries) if boundaries else type(value).__name__}"
                ),
            )
        return None
    if not isinstance(value, dict) or not value:
        return Violation(
            check="gate-anchor-value-shape",
            detail=f"{anchor.lkp_id} valueKind={kind} 是分项落点，值必须是 项名→值 的字典",
        )
    boundary_as_item = sorted(set(value) & {"min", "max"})
    if boundary_as_item:
        return Violation(
            check="gate-anchor-value-shape",
            detail=(
                f"{anchor.lkp_id} 把 {boundary_as_item} 当成了项——min/max 是项的**值形态**不是项"
                "（规则 1.9 一）；一项的值是区间就写成该项名下的 {min,max}"
            ),
        )
    return None


def _item_name_violations(anchor: ReportAnchor) -> list[Violation]:
    """项名不合命名空间形态（规则 1.9 三）：ASCII 小写 kebab-case。

    消费侧只守**形态**不守词表：取值落在 tier 闭集还是 scenario 词表里，由资产回路的核验拒灌
    （词表是开集、不随包下发，在这里照抄一份等于把真源劈成两处）。形态这一半必须守——
    项名与落点标识同处一个记号，形态不合的项**结构性地引用不到**（记号正则匹配不上），
    它会以"prompt 里列着、一写就被打回"的形态耗掉整轮重写。
    """
    return [
        Violation(
            check="gate-anchor-item-name-invalid",
            detail=(
                f"{anchor.lkp_id} 的项名「{item}」不合命名空间——项名与落点标识同一套"
                "（ASCII 小写 kebab-case，规则 1.9 三）；这样的项写不进记号，正文引用不到它"
            ),
        )
        for item in anchor.item_names
        if not ITEM_NAME_RE.match(item)
    ]


# 渲染层对单边界值的**登记措辞**（用户裁决 2026-08-29 晚）。这两个词面是本仓与渲染层之间
# 仅有的共享字面：写作侧要能逐字告诉模型"你这个记号已经把这句话说完了"，就绕不开它们。
# 改渲染层的措辞要一并改这里（同"投影规则两处各写一遍"，坑单第 10 条——已知代价，非疏忽）。
_RENDER_BOUND_PHRASE = {"min": "不低于", "max": "不超过"}


BOUND_REQUIRED = "required"
"""这个记号只渲一个"只有一侧"的值：句子必须写方向正确的边界词。"""
BOUND_CARRIED = "carried"
"""这个记号并列多项、其中有"只有一侧"的：渲染层逐项带词，句子不写。"""
BOUND_ABSENT = "absent"
"""固定值或两端齐的区间：数据里没有单侧边界这层意思，句子不许写。"""


def bound_expectation(anchor: ReportAnchor, item_name: str | None = None) -> tuple[str, str | None]:
    """这个记号要填的值是什么特征 → （该不该写边界词，写哪一侧）。

    **写作侧与门禁侧共用这一份判定**：prompt 按它在每个空旁边写出特征
    （:func:`reportgen_worker.writer._wording_note`），门禁按它判内容合不合特征——
    用户裁决 2026-08-30 的原话就是这两句，故只能有一份实现。
    """
    value = anchor.value
    if item_name is not None:
        values = [value[item_name]] if isinstance(value, dict) and item_name in value else []
    elif anchor.has_items:
        values = list(value.values()) if isinstance(value, dict) else []
    else:
        values = [value]

    def one_sided(v: object) -> str | None:
        if isinstance(v, dict) and len(v) == 1 and set(v) <= _BOUND_KEYS:
            return str(next(iter(v)))
        return None

    if len(values) == 1:
        side = one_sided(values[0])
        return (BOUND_REQUIRED, side) if side else (BOUND_ABSENT, None)
    # 并列多项：只要有一项只给了一侧，这个记号渲出来就自带说法（句子够不着逐项）
    if any(one_sided(v) for v in values):
        return BOUND_CARRIED, None
    return BOUND_ABSENT, None


def _bound_choices(side: str) -> str:
    return "、".join(f"「{root}」" for root in BOUND_ROOTS_BY_SIDE[side])


def _adjacent_wording_violations(
    label: str, text: str, anchors_by_id: dict[str, ReportAnchor]
) -> list[Violation]:
    """记号旁边的措辞合不合这个空的特征（四种特征与词表见 :data:`BOUND_ROOTS_BY_SIDE` 上方）。

    **判据按"哪里不对"分条，不按"哪个记号"分条**：该写没写、写反了方向、不该写却写了，
    三种的改法各不相同，合成一条就等于给模型一句它得自己拆开的话
    （判据名字与语义必须一致，同 ``gate-lkp-identifier-leak`` 与 ``gate-item-name-leak`` 的分法）。
    单位那一条独立：单位永远由渲染层随值出，与边界词的归属无关。

    按**出现位置**扫原文，不走 :func:`placeholder_refs` 那套集合：判的就是记号前后那几个字，
    去重即丢位置。打回话把**那一小句原文逐字带上**——同一个记号在一张卡里可以错两处
    （立案那张卡就是：主旨句里一处、正文里一处），只报一条的话模型改完第一处第二处还在，
    而重写只有两轮。逐字相同的两处才合并（那是同一个错的两遍）。
    """
    violations: list[Violation] = []
    seen: set[str] = set()

    def _add(check: str, detail: str) -> None:
        if detail not in seen:
            seen.add(detail)
            violations.append(Violation(check=check, detail=detail))

    for match in PLACEHOLDER_RE.finditer(text):
        anchor = anchors_by_id.get(match.group(1))
        if anchor is None:
            continue  # 落点认不出已由 ref_violation 报过，同一处不报两遍
        token, unit = match.group(0), anchor.unit
        clause_match = _CLAUSE_TAIL_RE.search(text[: match.start()])
        clause = clause_match.group(0) if clause_match else ""
        written = BOUND_WORD_RE.search(clause)
        kind, side = bound_expectation(anchor, match.group(2))

        if kind == BOUND_REQUIRED:
            assert side is not None
            if written is None:
                _add(
                    "gate-bound-word-missing",
                    f"{label} 「{clause}{token}」这个空要填的值**只给了{BOUND_SIDE_NAME[side]}**，"
                    f"记号出来是裸的数（带单位），句子里必须写清这是{BOUND_SIDE_NAME[side]}——"
                    f"从这几个词里挑一个放在记号前面：{_bound_choices(side)}。"
                    "不写的话业主会把它读成「刚好就是这个数」",
                )
            elif _BOUND_SIDE_BY_ROOT[written.group(0)] != side:
                other = BOUND_SIDE_NAME["min" if side == "max" else "max"]
                _add(
                    "gate-bound-word-direction",
                    f"{label} 「{clause}{token}」写的是「{written.group(0)}」（{other}的说法），"
                    f"而这个空要填的值**只给了{BOUND_SIDE_NAME[side]}**——方向反了，"
                    f"意思正好说拧。改成这几个之一：{_bound_choices(side)}",
                )
        elif written is not None:
            why = (
                "这个记号并列了好几项，每一项的边界说法它自己会带上，你再写一遍就是叠字"
                if kind == BOUND_CARRIED
                else "这个空要填的是一个**确定的数**（或一个两端都给了的范围），"
                "数据里根本没有单侧边界这层意思，写了等于替数据下一个它没给的判断"
            )
            _add(
                "gate-bound-word-before-ref",
                f"{label} 「{clause}{token}」记号前写了边界词「{written.group(0)}」——{why}。"
                f"删掉这个词即可（「按 {token} 做」「留出 {token} 的空间」）",
            )

        if unit and text[match.end() :].lstrip(" \u3000\t").startswith(unit):
            _add(
                "gate-unit-after-ref",
                f"{label} 「{token} {unit}」记号后又写了一遍单位「{unit}」——"
                "单位由记号自己带出来（写错单位会改掉这个数的大小，故它不交给你写），"
                "删掉记号后面那个单位即可",
            )
    return violations


def run_package_gate(domain: str, package: ReportDataPackage) -> list[Violation]:
    """写作前的生产方契约守卫：数据包本身违约的，不必烧一次 LLM 调用才发现。

    v2.4 起守的是**标注纪律的上游**：求值线声称某落点不必标注，但它根本没过可核性门——
    这一条若放过去，页级比对门禁会跟着一起放过（要求集是从 provenance 读的），
    整条标注链路就被生产侧一个字段悄悄关掉了。故在最前面拦一次，方向偏严。

    v2.8 加两条同路的（值的两层模型）：``value`` 形态与 ``value_kind`` 对不上、项名不合命名空间。
    """
    violations: list[Violation] = []
    for anchor in package.anchors:
        shape = _value_shape_violation(anchor)
        if shape is not None:
            violations.append(shape)
        violations.extend(_item_name_violations(anchor))
        provenance = anchor.provenance
        if provenance is None:
            continue
        if provenance.calibration != anchor.calibration:
            violations.append(
                Violation(
                    check="gate-provenance-inconsistent",
                    detail=(
                        f"{anchor.lkp_id} provenance.calibration={provenance.calibration} "
                        f"与落点 calibration={anchor.calibration} 不一致——两处同源，不该有两个答案"
                    ),
                )
            )
        elif not provenance.annotation_required and provenance.calibration != CALIBRATED:
            violations.append(
                Violation(
                    check="gate-provenance-inconsistent",
                    detail=(
                        f"{anchor.lkp_id} 未过可核性门（{provenance.calibration}）却标称无需标注"
                        "——标注必挂是硬约束（规则 4.10c），生产侧判定违约"
                    ),
                )
            )
    return violations


def run_unit_gate(
    cards: list[Card],
    domain: str,
    package: ReportDataPackage,
    claims: list[NarrativeClaim] | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    # 卡片按主张组织（图 v0.2 §3）：卡片多于主张 = 多出来的那张没有主张来源，即又在按落点排版。
    # **阈值来自推导步自己的产出，不是拍出来的数**——"每单元卡片上限 6~8"那种无据阈值已被撤回
    # （裁决 2026-08-29）。少于主张数不拦：讲不动的那件事宁可不讲（规则 4.18 宁薄勿撑）。
    # claims 为 None/空＝没有推导步（老形态与单测夹具），此时不判——不能凭空要求它有主张。
    if claims and len(cards) > len(claims):
        violations.append(
            Violation(
                check="gate-cards-exceed-claims",
                detail=(
                    f"{len(cards)} 张卡多于 {len(claims)} 条主张——一件事一张卡，"
                    "多出来的那张没有主张来源（按落点一条一张即是这一步要修的形态）"
                ),
            )
        )
    domain_anchors = package.domain_anchors(domain)
    anchors_by_id = {a.lkp_id: a for a in domain_anchors}
    anchor_ids = set(anchors_by_id)
    # 项名不进业主视野（规则 1.9 三明文）：读者看到的是正文里的人话与渲染出的数值。
    # 记号里的项名不算——那是渲染契约，与 {lkp-*} 同理，故判据跑的是**剥掉记号之后**的正文。
    item_name_res = {
        item: re.compile(rf"(?<![a-z0-9-]){re.escape(item)}(?![a-z0-9-])", re.IGNORECASE)
        for anchor in domain_anchors
        for item in anchor.item_names
    }
    # 断言预算核验仍要它（规则 5.8：谓词 requires 必须全部过可核性门）——v2.4 拆掉的是
    # "主旨句支点必须过门"，不是"断言必须有背书"
    supported = thesis_support_ids(domain, package)
    banned = collect_banned_terms(domain, package)
    budget = assertion_budget(domain, package)
    good_samples = judgment_good_texts(domain, package)
    pattern_checks = [c for c in package.checks_by_domain.get(domain, []) if c.pattern]

    for index, card in enumerate(cards):
        label = f"card[{index}]"
        text = f"{card.thesis}\n{card.body}"
        if not card.thesis.strip() or not card.body.strip():
            violations.append(
                Violation(check="gate-required-field", detail=f"{label} thesis/body 空")
            )
        # 正文逐字等于主旨句 = 这张卡没有承载任何推导，等于把落点表换了个排版（违规则 1.6
        # "不以无内容的密度充数"，图 v0.2 §3 卡片是叙事推导的产物而非落点表的另一种形态）。
        # 真跑立案：2026-08-29 ergonomics 单元 verdict=ok，22 张卡 22/22 逐字相同。
        #
        # **这是临时护栏，不是处置**：治本是补上叙事推导那一步（先定讲哪几件事，卡片按事组织），
        # 只加这条机检，模型补一句填充话就绕过去了——把看得见的退化变成看不见的填充。留它的理由
        # 只有一条：**"正文逐字等于主旨句"没有正当用例**，逐字比对零误判，代价是零。
        # 判据形态严格限于**逐字相同**：不做相似度、不设阈值（阈值无数据依据，同卡片数上限那条）。
        elif card.thesis.strip() == card.body.strip():
            violations.append(
                Violation(
                    check="gate-thesis-body-duplicate",
                    detail=(
                        f"{label} 正文与主旨句逐字相同——主旨句说结论，正文要说"
                        "为什么是这个数、它管的是哪一刻；重复一遍等于这张卡什么都没讲"
                    ),
                )
            )

        placeholders = placeholder_refs(text)
        # 引用合法性按**记号**判（v2.8 两层模型）：落点段认不认识、项名指不指得到，
        # 三种不合法各给各的话，判据编号仍是"引用解析不到"这一条（语义没变）。
        violations.extend(
            v
            for v in (
                ref_violation(label, ref, anchors_by_id)
                for ref in sorted(placeholders | set(card.number_refs))
            )
            if v is not None
        )
        undeclared = placeholders - set(card.number_refs)
        if undeclared:
            violations.append(
                Violation(
                    check="gate-number-ref-undeclared",
                    detail=f"{label} 占位符未在 number_refs 声明：{sorted(undeclared)}",
                )
            )
        # 对称的另一半（2026-08-29 晚补，真跑立案）：number_refs 是**占位符全集声明**（契约原文），
        # 声明了却没用同样违约。立案形态＝假坦白——卡片把五个有值落点声明进 refs、正文写
        # "这五项目前给不出可靠数值"、零占位符：值被藏起来了，而 v2.4 禁止隐藏。原先只查
        # "用了没声明"，这一半漏着，假坦白就从缝里过了检。
        unused = set(card.number_refs) - placeholders
        # v2.8 起这里有两种成因，话必须分开说：**粒度写错**（同一条落点，正文写整条 refs 写项、
        # 或反过来）与**真的没引用**。不分开的代价是给粒度错误配上一句"有值的落点不许说给不出"——
        # 那句话在这一支是错的，而反馈循环里一句对不上的打回等于没打回。
        # 注意"声明了 reading、正文只写了 general"**不算**粒度错：那一项有值却没露面，
        # 正是假坦白要封的形态，它归下面那一支。
        mismatched = sorted(r for r in unused if _granularity_mismatch(r, placeholders))
        if mismatched:
            violations.append(
                Violation(
                    check="gate-number-ref-unused",
                    detail=(
                        f"{label} number_refs 与正文的记号粒度对不上：{mismatched}"
                        "——refs 逐字写正文里的那个记号（正文写 {lkp-x.项名} 就声明 lkp-x.项名）；"
                        f"本卡正文实际写了：{sorted(placeholders)}"
                    ),
                )
            )
        absent = sorted(set(unused) - set(mismatched))
        if absent:
            violations.append(
                Violation(
                    check="gate-number-ref-unused",
                    detail=(
                        f"{label} number_refs 声明了却未在正文引用：{absent}"
                        "——refs 是占位符全集声明；这些落点（或落点里的这几项）有值，"
                        "要么引用它，要么别声明（有值的不许说「给不出」，那是被禁止的隐藏）"
                    ),
                )
            )

        # 记号旁边的措辞（边界词在前、单位在后）：跑**原文**且要位置，故不与下面剥占位的那批同路。
        violations.extend(_adjacent_wording_violations(label, text, anchors_by_id))

        # v2.4 拆除：原 gate-thesis-degraded-anchor（主旨句支点必须是 THESIS_SUPPORT）。
        # 规则 4.10c 明文把它作废——未过门落点已可进主旨句，条件是随页标注。判断句的纪律
        # 由下面的断言预算承接（"以什么底气说"），标注纪律由册级页面比对承接（"标没标"）。
        for predicate in card.assertions:
            requires = budget.get(predicate)
            if requires is None:
                violations.append(
                    Violation(
                        check="gate-assertion-not-budgeted",
                        detail=(
                            f"{label} 声明谓词「{predicate}」不在本域断言预算内"
                            f"（预算内的：{sorted(budget)}，规则 4.13/5.8）"
                        ),
                    )
                )
                continue
            missing = [r for r in requires if r not in anchor_ids]
            degraded = [r for r in requires if r in anchor_ids and r not in supported]
            if missing or degraded:
                violations.append(
                    Violation(
                        check="gate-assertion-unbacked",
                        detail=(
                            f"{label} 谓词「{predicate}」背书不足：缺失 {missing}、降档 {degraded}"
                            "——断言预算要求 requires 全部已求值且非降档（规则 5.8/4.10a）"
                        ),
                    )
                )

        stripped = PLACEHOLDER_RE.sub("", text)
        if DIGIT_RE.search(stripped):
            violations.append(
                Violation(
                    check="gate-digit-outside-ref",
                    detail=f"{label} 正文出现裸数字（数字只能经 {{lkp-*}} 占位引用落点对象）"
                    f"{_NO_ANCHOR_ROUTE}",
                )
            )
        chinese_number = CHINESE_NUMBER_RE.search(stripped)
        if chinese_number:
            violations.append(
                Violation(
                    check="gate-chinese-numeral",
                    detail=f"{label} 正文以中文数字写数（「{chinese_number.group(0)}」）"
                    f"——数字纪律管的是数不是字形{_NO_ANCHOR_ROUTE}",
                )
            )
        # 客户语域禁内部标识名：{lkp-*} 是渲染契约，裸 lkp- 是把内部命名空间漏给业主看
        if BARE_LKP_RE.search(stripped):
            violations.append(
                Violation(
                    check="gate-lkp-identifier-leak",
                    detail=(
                        f"{label} 正文出现裸 lkp- 标识名——内部落点编号不进客户语域；"
                        "要引用数字写 {lkp-id} 占位，要说这条没背书就用人话说"
                    ),
                )
            )
        # 同一条纪律的项名一半（规则 1.9 三"项名不进业主视野"，v2.8）：项名是与落点标识同一套的
        # 内部标签，业主不认识 general/high-vs-medium。它与裸 lkp- 分成两条判据是因为
        # **语义不同**：那条判的是落点编号泄漏，这条判的是项名泄漏，判据名字与语义必须一致。
        # 词边界匹配（前后不接 ASCII 字母数字连字符）：`low-E 玻璃` 这类正当写法不误伤。
        for item, item_re in item_name_res.items():
            if item_re.search(stripped):
                violations.append(
                    Violation(
                        check="gate-item-name-leak",
                        detail=(
                            f"{label} 正文出现分项记号「{item}」——项名是内部标签不进客户语域；"
                            "要那一项的数就写 {lkp-id.项名} 占位，那一项是什么场合用人话说"
                        ),
                    )
                )

        for term in banned:
            if term in text:
                violations.append(
                    Violation(check="gate-banned-term", detail=f"{label} 命中禁词「{term}」")
                )

        for sample in good_samples:
            if sample in text:
                violations.append(
                    Violation(
                        check="gate-sample-verbatim-copy",
                        detail=(
                            f"{label} 逐字抄了语域示范「{sample}」——示范给的是怎么讲，不是讲什么；"
                            "照抄等于把一句与这家人无关、也没有落点背书的话当成结论"
                        ),
                    )
                )

        for check in pattern_checks:
            assert check.pattern is not None
            # 判据跑**原文**不跑剥占位后的文本（2026-08-29 晚改）：cr-bound-word-before-placeholder
            # 的 pattern 就是「边界词 + {lkp-」——剥掉占位符它永远打不中。原先剥占位是防 pattern
            # 误中占位符内部，但占位符体是 kebab-case ASCII，中不了任何中文/数字/量纲 pattern，
            # 剥与不剥只对"引用占位符本身"的判据有区别——而那正是要能中的。
            hit = re.search(check.pattern, text) is not None
            # check_type 决定 pattern 的语义，此前被整个忽略（凡带 pattern 一律"命中即违规"），
            # 于是 regex_require_annotation（"出现工程量纲**则要求**配翻译"）被反着执行。
            # 当前因裸数字已禁而不会命中，是休眠 bug——自迭代回路首采 §五-2 抓到。
            # require 类的"是否配了翻译"本身判不确定（语义），故只在**命中且本卡没有任何落点引用**时
            # 判违规：引用了落点即视为已挂翻译，其余归判官层，机检不假实现。
            if check.check_type == "regex_require_annotation":
                if hit and not card.number_refs:
                    violations.append(
                        Violation(
                            check=check.asset_id,
                            detail=f"{label} {check.message}（命中量纲但本卡未引用任何落点）",
                        )
                    )
            elif hit:
                violations.append(
                    Violation(check=check.asset_id, detail=f"{label} {check.message}")
                )

    return violations
