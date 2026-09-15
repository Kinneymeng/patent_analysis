from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Mapping

from .legacy import parse_technical_disclosure as parse_legacy_technical_disclosure
from .legacy import parse_title_proposal, validate_disclosure_title
from .models import (
    DisclosureDiagram,
    DisclosureFormula,
    DisclosurePreview,
    DisclosureSection,
    DisclosureSubsection,
    DisclosureTable,
    InventionCandidate,
    ModuleDefinition,
    ParameterDefinition,
    PriorArtResult,
    PriorArtStatus,
    TechnicalDisclosure,
)


SECTION_TITLES = {
    "一": "技术背景与现有方案",
    "二": "需要解决的技术问题",
    "三": "技术方案详细说明",
    "四": "技术效果与适用边界",
    "五": "技术关键点",
    "六": "实施例与验证方法",
}
FORBIDDEN_EXPRESSIONS = (
    "其特征在于",
    "根据权利要求",
    "独立权利要求",
    "从属权利要求",
    "所述第一",
    "所述第二",
)
DISCOURAGED_EXPRESSION = "包括但不限于"


def parse_invention_candidates(
    response: str | Mapping[str, Any],
) -> tuple[InventionCandidate, ...]:
    payload = mapping_payload(response)
    if set(payload) == {"标题"}:
        titles = parse_title_proposal(payload)
        return tuple(
            InventionCandidate(
                candidate_id=f"C{index}",
                title=title,
                technical_problem="待在完整方案预览中细化",
                innovation="候选技术方向",
                feasibility="待在完整方案预览中验证",
                recommended=index == 1,
                recommendation_reason="兼容旧版候选标题响应",
            )
            for index, title in enumerate(titles, start=1)
        )
    if set(payload) != {"候选方案"}:
        raise ValueError("候选方案响应必须且只能包含“候选方案”字段")
    values = payload["候选方案"]
    if not isinstance(values, list) or not 1 <= len(values) <= 5:
        raise ValueError("候选方案必须包含 1 至 5 项")
    candidates: list[InventionCandidate] = []
    for index, raw in enumerate(values, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"候选方案第 {index} 项必须是对象")
        title = required_string(raw, "标题", f"候选方案第 {index} 项")
        validate_disclosure_title(title)
        evidence = string_list(raw.get("材料依据", []), "材料依据", allow_empty=True)
        candidates.append(
            InventionCandidate(
                candidate_id=optional_string(raw.get("编号")) or f"C{index}",
                title=title,
                technical_problem=required_string(raw, "技术问题", f"候选方案第 {index} 项"),
                innovation=required_string(raw, "核心创新", f"候选方案第 {index} 项"),
                differences=required_string(raw, "初步区别", f"候选方案第 {index} 项"),
                feasibility=required_string(raw, "可实施性", f"候选方案第 {index} 项"),
                evidence_refs=tuple(evidence),
                recommended=bool(raw.get("推荐", False)),
                recommendation_reason=optional_string(raw.get("推荐理由")),
            )
        )
    if len({item.title.casefold() for item in candidates}) != len(candidates):
        raise ValueError("候选方案标题不得重复")
    if sum(item.recommended for item in candidates) > 1:
        raise ValueError("每组候选方案只能推荐一项")
    if not any(item.recommended for item in candidates):
        first = candidates[0]
        candidates[0] = InventionCandidate(**{**asdict(first), "recommended": True, "recommendation_reason": first.recommendation_reason or "技术闭环和材料依据较完整"})
    return tuple(candidates)


def parse_disclosure_preview(
    response: str | Mapping[str, Any],
    expected_title: str,
    source_id: str,
    prior_art: PriorArtResult,
) -> DisclosurePreview:
    payload = mapping_payload(response)
    title = required_string(payload, "标题", "完整方案预览")
    if title != expected_title:
        raise ValueError("完整方案预览标题必须与用户确认标题逐字一致")
    problems = tuple(string_list(payload.get("技术问题"), "技术问题"))
    steps = tuple(string_list(payload.get("流程"), "流程"))
    key_points = tuple(string_list(payload.get("技术关键点"), "技术关键点"))
    evidence = tuple(string_list(payload.get("材料依据", []), "材料依据", allow_empty=True))
    assumptions = tuple(string_list(payload.get("假设", []), "假设", allow_empty=True))
    questions = tuple(string_list(payload.get("待确认问题", []), "待确认问题", allow_empty=True))
    raw_modules = payload.get("模块")
    if not isinstance(raw_modules, list) or len(raw_modules) < 2:
        raise ValueError("完整方案预览至少需要两个具体模块")
    modules: list[ModuleDefinition] = []
    for index, raw in enumerate(raw_modules, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"模块第 {index} 项必须是对象")
        inputs = tuple(string_list(raw.get("输入"), f"模块 {index} 输入"))
        outputs = tuple(string_list(raw.get("输出"), f"模块 {index} 输出"))
        modules.append(ModuleDefinition(
            name=required_string(raw, "名称", f"模块第 {index} 项"),
            responsibility=required_string(raw, "职责", f"模块第 {index} 项"),
            inputs=inputs,
            outputs=outputs,
        ))
    if len(steps) < 3:
        raise ValueError("完整方案预览至少需要三个连续流程步骤")
    raw_parameters = payload.get("建议参数", [])
    if not isinstance(raw_parameters, list):
        raise ValueError("建议参数必须是数组")
    parameters: list[ParameterDefinition] = []
    for index, raw in enumerate(raw_parameters, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"建议参数第 {index} 项必须是对象")
        is_example = bool(raw.get("示例", False))
        value = required_string(raw, "值或范围", f"建议参数第 {index} 项")
        if is_example and not any(marker in value for marker in ("示例", "建议", "待标定", "待验证")):
            value = "示例值：" + value
        parameters.append(ParameterDefinition(
            name=required_string(raw, "名称", f"建议参数第 {index} 项"),
            symbol=optional_string(raw.get("符号")),
            meaning=required_string(raw, "含义", f"建议参数第 {index} 项"),
            value_or_range=value,
            basis=required_string(raw, "依据", f"建议参数第 {index} 项"),
            is_example=is_example,
        ))
    return DisclosurePreview(
        source_id=source_id,
        title=title,
        technical_problems=problems,
        modules=tuple(modules),
        process_steps=steps,
        key_points=key_points,
        evidence_refs=evidence,
        suggested_parameters=tuple(parameters),
        assumptions=assumptions,
        open_questions=questions,
        prior_art=prior_art,
    )


def parse_technical_disclosure(
    response: str | Mapping[str, Any],
    expected_title: str,
) -> TechnicalDisclosure:
    payload = mapping_payload(response)
    if set(payload) == {"标题", "摘要", "发明内容"}:
        return parse_legacy_technical_disclosure(payload, expected_title)
    required_top = {"标题", "摘要", "章节", "图示", "参数表", "公式"}
    if set(payload) != required_top:
        missing = sorted(required_top - set(payload))
        extra = sorted(set(payload) - required_top)
        raise ValueError(f"完整交底书字段不匹配；缺少={missing}，多余={extra}")
    title = required_string(payload, "标题", "技术交底书")
    if title != expected_title:
        raise ValueError("模型返回标题必须与用户确认标题逐字一致")
    abstract = required_string(payload, "摘要", "技术交底书")
    if "\n" in abstract or "\r" in abstract:
        raise ValueError("摘要必须是单一自然段")
    if len(re.sub(r"\s+", "", abstract)) < 100:
        raise ValueError("摘要应至少包含 100 个非空白字符")
    sections = _parse_sections(payload["章节"])
    diagrams = _parse_diagrams(payload["图示"])
    tables = _parse_tables(payload["参数表"])
    formulas = _parse_formulas(payload["公式"])
    disclosure = TechnicalDisclosure(
        title=title,
        abstract=abstract,
        content_paragraphs=[paragraph for section in sections for paragraph in section.paragraphs],
        sections=sections,
        diagrams=diagrams,
        tables=tables,
        formulas=formulas,
    )
    validate_engineering_language(disclosure)
    _validate_cross_references(disclosure)
    return disclosure


def validate_engineering_language(disclosure: TechnicalDisclosure) -> None:
    text = "\n".join([disclosure.title, disclosure.abstract, *disclosure.all_paragraphs()])
    for phrase in FORBIDDEN_EXPRESSIONS:
        if phrase in text:
            raise ValueError(f"技术交底书不得包含权利要求式表达：{phrase}")
    if DISCOURAGED_EXPRESSION in text:
        raise ValueError("技术交底书原则上避免使用“包括但不限于”，请列出具体实现选项")
    count = text.count("所述")
    if count > max(4, len(text) // 900):
        raise ValueError(f"“所述”使用过多（{count} 次），请改用具体对象、信号或模块名称")


def validate_disclosure_against_preview(
    disclosure: TechnicalDisclosure,
    preview: DisclosurePreview,
) -> None:
    corpus = "\n".join(
        [
            disclosure.abstract,
            *disclosure.all_paragraphs(),
            *[item.mermaid for item in disclosure.diagrams],
            *[
                cell
                for table in disclosure.tables
                for row in (table.headers, *table.rows)
                for cell in row
            ],
            *[item.description for item in disclosure.formulas],
        ]
    )
    required_terms: list[tuple[str, str]] = []
    for module in preview.modules:
        required_terms.append(("模块", module.name))
        required_terms.extend(("模块输入", value) for value in module.inputs)
        required_terms.extend(("模块输出", value) for value in module.outputs)
    required_terms.extend(("技术关键点", value) for value in preview.key_points)
    required_terms.extend(("参数", value.name) for value in preview.suggested_parameters)
    required_terms.extend(
        ("参数符号", value.symbol)
        for value in preview.suggested_parameters
        if value.symbol
    )
    missing = [f"{kind}“{value}”" for kind, value in required_terms if value not in corpus]
    if missing:
        raise ValueError("正文遗漏或改写了用户确认的核心内容：" + "、".join(missing[:12]))

    architecture_sources = "\n".join(
        item.mermaid for item in disclosure.diagrams if item.kind == "系统架构图"
    )
    missing_modules = [
        module.name for module in preview.modules if module.name not in architecture_sources
    ]
    if missing_modules:
        raise ValueError("系统架构图遗漏用户确认的模块：" + "、".join(missing_modules))

    flow_sources = "\n".join(
        item.mermaid for item in disclosure.diagrams if item.kind == "流程图"
    )
    step_ids = [
        match.group(0).upper()
        for step in preview.process_steps
        if (match := re.match(r"\s*S\d+\b", step, flags=re.IGNORECASE))
    ]
    missing_steps = [step_id for step_id in step_ids if step_id not in flow_sources.upper()]
    if missing_steps:
        raise ValueError("流程图遗漏用户确认的步骤编号：" + "、".join(missing_steps))

    for parameter in preview.suggested_parameters:
        if parameter.is_example and parameter.value_or_range not in corpus:
            raise ValueError(
                f"示例参数“{parameter.name}”必须保留确认时的示例属性和值或范围"
            )

    prior_art = preview.prior_art
    if prior_art.status in {PriorArtStatus.DISABLED, PriorArtStatus.FAILED}:
        false_claims = ("已经查新", "经查新", "未发现相似专利", "没有相似专利")
        if any(value in corpus for value in false_claims):
            raise ValueError("查新未启用或未完成时不得声称已经查新或没有相似专利")
    elif prior_art.status == PriorArtStatus.EMPTY:
        overbroad_claims = ("未发现相似专利", "没有相似专利", "不存在相似专利")
        if any(value in corpus for value in overbroad_claims):
            raise ValueError("查新无结果只代表本次检索范围内未筛选出记录，不得断言没有相似专利")
        required_search_details = [prior_art.searched_at, *prior_art.keywords]
        missing_details = [value for value in required_search_details if value and value not in corpus]
        if missing_details:
            raise ValueError("查新无结果说明遗漏检索时间或检索词：" + "、".join(missing_details))
    elif prior_art.records:
        required_record_details = [prior_art.searched_at]
        for item in prior_art.records:
            required_record_details.extend((item.publication_number, item.title, item.url, *item.ipc_codes))
        missing_details = [value for value in required_record_details if value and value not in corpus]
        if missing_details:
            raise ValueError("查新对比遗漏检索信息或已选记录字段：" + "、".join(missing_details[:16]))


def validate_effect_claims(disclosure: TechnicalDisclosure, source_context: str) -> None:
    if not disclosure.is_structured:
        return
    effect = next((item for item in disclosure.sections if item.number == "四"), None)
    if effect is None:
        return
    text = "\n".join(
        [
            *effect.paragraphs,
            *[paragraph for subsection in effect.subsections for paragraph in subsection.paragraphs],
        ]
    )
    verified_markers = ("实测", "试验表明", "测试结果", "验证结果", "已经验证", "已验证")
    if any(marker in text for marker in verified_markers) and not any(
        marker in source_context for marker in verified_markers
    ):
        raise ValueError("来源材料没有验证记录，不得把预期技术效果写成已经实测或验证的结果")
    expected_markers = ("预期", "预计", "待验证", "建议", "示例", "目标")
    for sentence in re.split(r"[。；;\n]", text):
        values = re.findall(
            r"\d+(?:\.\d+)?\s*(?:%|％|ms|毫秒|倍|℃|°C)",
            sentence,
            flags=re.IGNORECASE,
        )
        unsupported = [value for value in values if value not in source_context]
        if unsupported and not any(marker in sentence for marker in expected_markers):
            raise ValueError(
                "来源材料不支持量化技术效果，必须标为预期或待验证："
                + "、".join(unsupported)
            )


def disclosure_to_payload(disclosure: TechnicalDisclosure) -> dict[str, Any]:
    if not disclosure.sections:
        return {"标题": disclosure.title, "摘要": disclosure.abstract, "发明内容": disclosure.content_paragraphs}
    return {
        "标题": disclosure.title,
        "摘要": disclosure.abstract,
        "章节": [
            {
                "编号": section.number,
                "标题": section.title,
                "段落": list(section.paragraphs),
                "小节": [
                    {"编号": item.number, "标题": item.title, "段落": list(item.paragraphs)}
                    for item in section.subsections
                ],
            }
            for section in disclosure.sections
        ],
        "图示": [
            {"章节": item.section_number, "类型": item.kind, "标题": item.title, "mermaid": item.mermaid}
            for item in disclosure.diagrams
        ],
        "参数表": [
            {"章节": item.section_number, "标题": item.title, "表头": list(item.headers), "行": [list(row) for row in item.rows]}
            for item in disclosure.tables
        ],
        "公式": [
            {"章节": item.section_number, "编号": item.label, "LaTeX": item.latex, "说明": item.description, "来源": item.origin, "材料依据": item.source_ref}
            for item in disclosure.formulas
        ],
    }


def mapping_payload(response: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    if not isinstance(response, str):
        raise TypeError("模型响应必须是 JSON 字符串或对象")
    raw = response.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型响应不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("模型响应顶层必须是 JSON 对象")
    return payload


def _parse_sections(raw: Any) -> tuple[DisclosureSection, ...]:
    if not isinstance(raw, list) or len(raw) != 6:
        raise ValueError("技术交底书必须包含六个正文章节")
    sections: list[DisclosureSection] = []
    for index, value in enumerate(raw, start=1):
        if not isinstance(value, Mapping):
            raise ValueError(f"第 {index} 章必须是对象")
        number = required_string(value, "编号", f"第 {index} 章")
        title = required_string(value, "标题", f"第 {index} 章")
        if SECTION_TITLES.get(number) != title:
            raise ValueError(f"章节必须按统一目录输出：{number}、{SECTION_TITLES.get(number, '未知章节')}")
        paragraphs = tuple(string_list(value.get("段落", []), f"第 {index} 章段落", allow_empty=True))
        raw_subsections = value.get("小节", [])
        if not isinstance(raw_subsections, list):
            raise ValueError(f"第 {index} 章小节必须是数组")
        subsections: list[DisclosureSubsection] = []
        for sub_index, sub in enumerate(raw_subsections, start=1):
            if not isinstance(sub, Mapping):
                raise ValueError(f"第 {index} 章第 {sub_index} 个小节必须是对象")
            subsections.append(DisclosureSubsection(
                required_string(sub, "编号", "小节"),
                required_string(sub, "标题", "小节"),
                tuple(string_list(sub.get("段落"), "小节段落")),
            ))
        if not paragraphs and not subsections:
            raise ValueError(f"第 {index} 章没有正文内容")
        sections.append(DisclosureSection(number, title, paragraphs, tuple(subsections)))
    if [item.number for item in sections] != list(SECTION_TITLES):
        raise ValueError("六章编号或顺序不正确")
    return tuple(sections)


def _parse_diagrams(raw: Any) -> tuple[DisclosureDiagram, ...]:
    if not isinstance(raw, list):
        raise ValueError("图示必须是数组")
    diagrams: list[DisclosureDiagram] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"图示第 {index} 项必须是对象")
        mermaid = required_string(item, "mermaid", f"图示第 {index} 项")
        _validate_mermaid(mermaid)
        diagrams.append(DisclosureDiagram(
            section_number=required_string(item, "章节", f"图示第 {index} 项"),
            kind=required_string(item, "类型", f"图示第 {index} 项"),
            title=required_string(item, "标题", f"图示第 {index} 项"),
            mermaid=mermaid,
        ))
    kinds = {item.kind for item in diagrams}
    if "系统架构图" not in kinds or "流程图" not in kinds:
        raise ValueError("完整交底书必须同时包含系统架构图和流程图")
    return tuple(diagrams)


def _parse_tables(raw: Any) -> tuple[DisclosureTable, ...]:
    if not isinstance(raw, list):
        raise ValueError("参数表必须是数组")
    tables: list[DisclosureTable] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"参数表第 {index} 项必须是对象")
        headers = tuple(string_list(item.get("表头"), f"参数表第 {index} 项表头"))
        rows_raw = item.get("行")
        if not isinstance(rows_raw, list):
            raise ValueError(f"参数表第 {index} 项的行必须是数组")
        rows: list[tuple[str, ...]] = []
        for row in rows_raw:
            if not isinstance(row, list) or len(row) != len(headers) or any(not isinstance(cell, str) or not cell.strip() for cell in row):
                raise ValueError(f"参数表第 {index} 项存在列数不匹配或空单元格")
            rows.append(tuple(cell.strip() for cell in row))
        tables.append(DisclosureTable(
            required_string(item, "章节", f"参数表第 {index} 项"),
            required_string(item, "标题", f"参数表第 {index} 项"),
            headers,
            tuple(rows),
        ))
    return tuple(tables)


def _parse_formulas(raw: Any) -> tuple[DisclosureFormula, ...]:
    if not isinstance(raw, list):
        raise ValueError("公式必须是数组")
    result: list[DisclosureFormula] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"公式第 {index} 项必须是对象")
        latex = required_string(item, "LaTeX", f"公式第 {index} 项")
        _validate_latex(latex)
        origin = required_string(item, "来源", f"公式第 {index} 项").casefold()
        if origin not in {"source", "agent"}:
            raise ValueError("公式来源必须是 source 或 agent")
        source_ref = optional_string(item.get("材料依据"))
        if origin == "source" and not source_ref:
            raise ValueError("材料公式必须填写材料依据")
        result.append(DisclosureFormula(
            required_string(item, "章节", f"公式第 {index} 项"),
            required_string(item, "编号", f"公式第 {index} 项"),
            latex,
            required_string(item, "说明", f"公式第 {index} 项"),
            origin,
            source_ref,
        ))
    return tuple(result)


def _validate_mermaid(value: str) -> None:
    if len(value) > 12_000:
        raise ValueError("Mermaid 图示过长")
    if not re.match(r"^\s*(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)\b", value, re.IGNORECASE):
        raise ValueError("图示只支持 Mermaid flowchart/graph 语法")
    if re.search(r"(?i)<script|javascript:|\bclick\b|\bhref\b|%%\{|\bclassDef\b.*url", value):
        raise ValueError("Mermaid 图示包含不允许的脚本、链接或初始化指令")


def _validate_latex(value: str) -> None:
    if len(value) > 4_000:
        raise ValueError("公式过长")
    if value.count("{") != value.count("}"):
        raise ValueError("公式花括号不配对")
    if re.search(r"\\(?:write18|input|include|openout|read|html|href)\b", value, re.IGNORECASE):
        raise ValueError("公式包含不允许的 LaTeX 命令")


def _validate_cross_references(disclosure: TechnicalDisclosure) -> None:
    valid_sections = set(SECTION_TITLES)
    for item in (*disclosure.diagrams, *disclosure.tables, *disclosure.formulas):
        if item.section_number not in valid_sections:
            raise ValueError(f"图表或公式引用了不存在的章节：{item.section_number}")
    text = "\n".join(disclosure.all_paragraphs())
    if "输入" not in text or "输出" not in text:
        raise ValueError("详细技术方案必须明确说明输入和输出")
    if not any(word in text for word in ("异常", "边界", "缺失", "超时", "失效", "无效")):
        raise ValueError("详细技术方案必须说明异常或边界条件")
    labels = [item.label for item in disclosure.formulas]
    if len(labels) != len(set(labels)):
        raise ValueError("公式编号不得重复")
    definition_text = "\n".join(
        [
            text,
            *[
                cell
                for table in disclosure.tables
                for row in (table.headers, *table.rows)
                for cell in row
            ],
            *[item.description for item in disclosure.formulas],
        ]
    ).replace("{", "").replace("}", "")
    for formula in disclosure.formulas:
        missing_symbols = [
            symbol
            for symbol in _latex_symbols(formula.latex)
            if symbol not in definition_text
        ]
        if missing_symbols:
            raise ValueError(
                f"{formula.label}存在未定义符号：" + "、".join(missing_symbols)
            )


def _latex_symbols(latex: str) -> list[str]:
    without_commands = re.sub(r"\\[A-Za-z]+", " ", latex)
    values = re.findall(r"(?<![A-Za-z0-9])([A-Za-z](?:_\{?[A-Za-z0-9]+\}?)?)(?![A-Za-z0-9])", without_commands)
    result: list[str] = []
    for value in values:
        normalized = value.replace("{", "").replace("}", "")
        if normalized not in result:
            result.append(normalized)
    return result


def required_string(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}的“{key}”必须是非空字符串")
    return value.strip()


def optional_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{name}必须是{'可为空的' if allow_empty else '非空'}字符串数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name}中的每一项必须是非空字符串")
    return [item.strip() for item in value]


__all__ = [
    "disclosure_to_payload",
    "mapping_payload",
    "parse_disclosure_preview",
    "parse_invention_candidates",
    "parse_technical_disclosure",
    "parse_title_proposal",
    "validate_disclosure_title",
    "validate_engineering_language",
]
