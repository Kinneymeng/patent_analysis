from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable, Mapping

from .models import (
    DisclosureDiagnostic,
    DisclosureFormula,
    DisclosurePreview,
    DisclosureSection,
    PriorArtStatus,
    TechnicalDisclosure,
)
from .validation import (
    DISCOURAGED_EXPRESSION,
    SECTION_TITLES,
    _parse_diagrams,
    _parse_formulas,
    _parse_tables,
    effect_claim_problem,
    mapping_payload,
    parse_disclosure_section,
    reconcile_disclosure_with_preview,
    split_claims,
    undefined_formula_symbols,
    unsupported_prior_art_claim,
    validate_disclosure_against_preview,
    validate_engineering_language,
)


def review_item(stage: str, code: str, message: str, action: str) -> DisclosureDiagnostic:
    return DisclosureDiagnostic(stage, code, message, action, requires_review=True)


def section_paragraphs(section: DisclosureSection) -> tuple[str, ...]:
    return (*section.paragraphs, *(p for sub in section.subsections for p in sub.paragraphs))


def claim_problem(text: str, source: str, preview: DisclosurePreview) -> str | None:
    if unsupported_prior_art_claim(text, preview.prior_art.status):
        return "查新结论超出已取得的检索记录"
    return effect_claim_problem(text, source)


def replace_unverified_claims(
    text: str, source: str, preview: DisclosurePreview
) -> tuple[str, list[str]]:
    claims = split_claims(text)
    pending = []
    for index, clause in enumerate(claims):
        reason = claim_problem(clause, source, preview)
        if reason:
            kind = "查新范围与结论" if "查新" in reason else "该项技术效果及数据"
            claims[index] = f"【待核验】{kind}需补充对应的原始记录"
            pending.append(f"{reason}（原句：{clause.strip()[:250]}）")
    return "".join(claims), pending


def normalize_section(section: DisclosureSection) -> DisclosureSection:
    return _map_section(section, lambda text: text.replace(DISCOURAGED_EXPRESSION, "包括"))


def salvage_section(response: str | Mapping[str, Any], number: str) -> DisclosureSection | None:
    try:
        raw = mapping_payload(response).get("章节")
        if not isinstance(raw, Mapping) or raw.get("编号") != number:
            return None

        def paragraphs(values):
            return [v for v in values if isinstance(v, str) and v.strip()] if isinstance(values, list) else []

        subsections = []
        if isinstance(raw.get("小节"), list):
            for item in raw["小节"]:
                if (
                    isinstance(item, Mapping)
                    and all(isinstance(item.get(key), str) and item[key].strip() for key in ("编号", "标题"))
                    and (content := paragraphs(item.get("段落")))
                ):
                    subsections.append({"编号": item["编号"], "标题": item["标题"], "段落": content})
        return normalize_section(parse_disclosure_section({"章节": {
            "编号": number, "标题": SECTION_TITLES[number], "段落": paragraphs(raw.get("段落")),
            "小节": subsections,
        }}, number))
    except (TypeError, ValueError):
        return None


def check_section(section: DisclosureSection, source: str, preview: DisclosurePreview) -> None:
    paragraphs = section_paragraphs(section)
    for paragraph in (*paragraphs, *(sub.title for sub in section.subsections)):
        for clause in split_claims(paragraph):
            if reason := claim_problem(clause, source, preview):
                raise ValueError(f"{reason}（原句：{clause.strip()[:250]}）")
    validate_engineering_language(TechnicalDisclosure("", "", list(paragraphs)))


def reviewable_section(
    section: DisclosureSection,
    source: str,
    preview: DisclosurePreview,
    stage: str,
) -> tuple[DisclosureSection, list[DisclosureDiagnostic]]:
    issues = []

    def redact(text: str) -> str:
        text, claims = replace_unverified_claims(text, source, preview)
        for reason in claims:
            # The original claim is retained in the stage checkpoint, not in
            # either deliverable or the summary printed at the front of it.
            issues.append(review_item(
                stage, "claim-evidence-pending", f"第{section.number}章{reason.split('（原句：')[0]}",
                "已在对应位置标明待核验，请补充原始试验或检索记录",
            ))
        return text

    result = _map_section(section, redact)
    try:
        validate_engineering_language(TechnicalDisclosure("", "", list(section_paragraphs(result))))
    except ValueError as exc:
        issues.append(review_item(
            stage, "engineering-style-pending", f"第{section.number}章：{exc}", "请复核工程文档表述",
        ))
        result = replace(result, paragraphs=("【待复核】本章部分措辞需调整。", *result.paragraphs))
    return result, issues


def _map_section(section: DisclosureSection, transform: Callable[[str], str]) -> DisclosureSection:
    return replace(
        section,
        paragraphs=tuple(transform(item) for item in section.paragraphs),
        subsections=tuple(
            replace(sub, title=transform(sub.title), paragraphs=tuple(transform(p) for p in sub.paragraphs))
            for sub in section.subsections
        ),
    )


def fallback_section(number: str, preview: DisclosurePreview) -> DisclosureSection:
    confirmed = {
        "二": preview.technical_problems,
        "五": preview.key_points,
    }.get(number, ())
    if number == "三":
        confirmed = (
            *(f"{module.name}。职责：{module.responsibility}。"
              f"输入：{'、'.join(module.inputs)}。输出：{'、'.join(module.outputs)}。"
              for module in preview.modules),
            *preview.process_steps,
            *(f"{item.name} {item.symbol}：{item.value_or_range}。"
              f"{item.meaning}。依据：{item.basis}。" for item in preview.suggested_parameters),
        )
    return DisclosureSection(number, SECTION_TITLES[number], (
        *confirmed, "【待补】本章撰写和纠错未完成，请补充相应的实现、边界或验证细节。",
    ))


def prepare_artifacts(
    document: TechnicalDisclosure,
    raw: Mapping[str, Any],
    source: str,
    preview: DisclosurePreview,
    repair: Callable[[str, Any, str, Callable[[Mapping[str, Any]], Any]], Any],
) -> tuple[TechnicalDisclosure, dict[str, Any]]:
    issues = list(document.diagnostics)
    cache = dict(raw)

    def parse_abstract(payload: Mapping[str, Any]) -> str:
        abstract = payload.get("摘要")
        if not isinstance(abstract, str) or not abstract.strip():
            raise ValueError("缺少可读摘要")
        abstract = " ".join(abstract.splitlines()).strip().replace(DISCOURAGED_EXPRESSION, "包括")
        for clause in split_claims(abstract):
            if problem := claim_problem(clause, source, preview):
                raise ValueError(problem)
        if len(re.sub(r"\s+", "", abstract)) < 100:
            raise ValueError("摘要不足 100 个非空白字符")
        return abstract

    try:
        abstract = parse_abstract(raw)
    except ValueError as exc:
        abstract = repair("摘要", raw.get("摘要"), str(exc), parse_abstract)
        if abstract is None:
            original = raw.get("摘要")
            if isinstance(original, str) and original.strip():
                original = " ".join(original.splitlines()).strip()
                abstract, _ = replace_unverified_claims(original, source, preview)
            else:
                abstract = f"{preview.title}。{'。'.join(preview.technical_problems)}。"
            issues.append(review_item("artifacts", "abstract-pending", str(exc), "请复核摘要"))
        else:
            cache["摘要"] = abstract

    def parse_one(field, parse_items, payload, *, expected_kind=None):
        entries = payload.get(field)
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError(f"{field}纠错须返回一项")
        item, = parse_items(entries)
        if item.section_number not in SECTION_TITLES:
            raise ValueError(f"{field}引用了不存在的章节 {item.section_number}")
        if expected_kind is not None and item.kind != expected_kind:
            raise ValueError(f"应提供{expected_kind}")
        text = {
            "图示": lambda: (item.title, item.mermaid),
            "参数表": lambda: (item.title, *item.headers, *(cell for row in item.rows for cell in row)),
            "公式": lambda: (item.description,),
        }[field]()
        for value in text:
            for clause in split_claims(value):
                if problem := claim_problem(clause, source, preview):
                    raise ValueError(problem)
        return item, entries[0]

    groups = {}
    repaired_formulas = set()
    for field, parse_items in (
        ("图示", lambda entries: _parse_diagrams(entries, require_kinds=False)),
        ("参数表", _parse_tables),
        ("公式", _parse_formulas),
    ):
        raw_entries = raw.get(field, [])
        if not isinstance(raw_entries, list):
            raw_entries = [raw_entries]
        entries = list(raw_entries)
        items = []
        for index, entry in enumerate(raw_entries):
            parser = lambda payload: parse_one(field, parse_items, payload)
            try:
                result = parser({field: [entry]})
            except (TypeError, ValueError) as exc:
                result = repair(field, entry, str(exc), parser)
                if result is None:
                    issues.append(review_item(
                        "artifacts", "invalid-artifact", f"{field}第 {index + 1} 项：{exc}",
                        "已保留其他有效项目，请补充该项",
                    ))
                    continue
                entries[index] = result[1]
                if field == "公式":
                    repaired_formulas.add(index)
            items.append((index, result[0]))

        if field == "图示":
            for kind in ("系统架构图", "流程图"):
                if any(item.kind == kind for _, item in items):
                    continue
                # An invalid entry of this kind has already had its repair attempt.
                attempted = any(isinstance(entry, Mapping) and entry.get("类型") == kind
                                for entry in raw_entries)
                result = None if attempted else repair(
                    field, {"类型": kind}, f"缺少{kind}",
                    lambda payload: parse_one(field, parse_items, payload, expected_kind=kind),
                )
                if result is None:
                    issues.append(review_item("artifacts", "diagram-missing", f"缺少{kind}", "请补充图示"))
                    continue
                items.append((len(entries), result[0]))
                entries.append(result[1])
        cache[field] = entries
        groups[field] = items

    document = replace(
        document,
        abstract=abstract,
        diagrams=tuple(item for _, item in groups["图示"]),
        tables=tuple(item for _, item in groups["参数表"]),
        formulas=tuple(item for _, item in groups["公式"]),
    )
    document = reconcile_disclosure_with_preview(replace(document, diagnostics=tuple(issues)), preview)
    issues = list(document.diagnostics)
    formulas = []
    missing = {id(formula): symbols for formula, symbols in undefined_formula_symbols(document)}
    labels = set()
    for index, formula in groups["公式"]:
        if formula.label in labels:
            issues.append(review_item(
                "artifacts", "formula-label-duplicate", f"{formula.label} 的编号重复", "请核对公式编号与引用",
            ))
            continue
        labels.add(formula.label)
        symbols = missing.get(id(formula))
        if symbols:
            reason = f"{formula.label}存在未定义符号：{'、'.join(symbols)}"

            def parse_definition(payload: Mapping[str, Any]) -> tuple[DisclosureFormula, dict]:
                item, entry = parse_one("公式", _parse_formulas, payload)
                if replace(item, description=formula.description) != formula:
                    raise ValueError("公式符号纠错只能补充说明，须保留原式及来源")
                if undefined_formula_symbols(replace(document, formulas=(item,))):
                    raise ValueError(reason)
                # A model-written meaning is a proposed definition unless
                # explicitly grounded in the input or existing manuscript.
                evidence = source + "\n" + "\n".join(document.all_paragraphs())
                original = formula.description
                addition = item.description.removeprefix(original).strip(" 。；;")
                if (
                    not item.description.startswith(original) or not addition
                    or addition not in evidence
                ):
                    raise ValueError("补充的符号说明缺少可核对的材料依据")
                return item, entry

            result = None if index in repaired_formulas else repair(
                "公式", cache["公式"][index], reason, parse_definition,
            )
            if result is None:
                issues.append(review_item("artifacts", "formula-symbols-pending", reason, "请补充符号定义"))
                formula = replace(formula, description=f"{formula.description} 【待复核】{reason}。")
            else:
                formula, cache["公式"][index] = result
        formulas.append(formula)
    return replace(document, formulas=tuple(formulas), diagnostics=tuple(issues)), cache


def complete_staged_document(
    document: TechnicalDisclosure,
    preview: DisclosurePreview,
    source: str,
) -> TechnicalDisclosure:
    document = reconcile_disclosure_with_preview(document, preview)
    prior_art = preview.prior_art
    sections = list(document.sections)
    issues = list(document.diagnostics)
    if prior_art.status in {PriorArtStatus.EMPTY, PriorArtStatus.SUCCESS, PriorArtStatus.PARTIAL}:
        corpus = "\n".join([document.abstract, *document.all_paragraphs()])
        fields = [prior_art.searched_at]
        if prior_art.status == PriorArtStatus.EMPTY:
            fields.extend(prior_art.keywords)
        for record in prior_art.records:
            fields.extend((record.publication_number, record.title, record.url, *record.ipc_codes))
        if any(field and field not in corpus for field in fields):
            text = "本次查新记录：" + "；".join(field for field in fields if field)
            if prior_art.status == PriorArtStatus.EMPTY:
                text += "。本次检索范围内未筛选出记录。"
            sections[0] = replace(sections[0], paragraphs=(*sections[0].paragraphs, text))
    for index, section in enumerate(sections):
        sections[index], pending = reviewable_section(section, source, preview, f"section-{section.number}")
        issues.extend(pending)

    for issue in issues:
        if issue.code in {"diagram-missing", "invalid-artifact", "formula-label-duplicate"}:
            index = 2
            core = sections[index]
            marker = f"【待补】{issue.message}。"
            if marker not in core.paragraphs:
                sections[index] = replace(core, paragraphs=(*core.paragraphs, marker))

    abstract, pending = replace_unverified_claims(document.abstract, source, preview)
    if pending:
        issues.append(review_item("artifacts", "abstract-evidence-pending", "摘要含待核验结论", "请补充依据"))
    core_text = "\n".join(section_paragraphs(sections[2]))
    if "输入" not in core_text or "输出" not in core_text:
        core, pending = reviewable_section(fallback_section("三", preview), source, preview, "section-三")
        issues.extend(pending)
        sections[2] = replace(sections[2], paragraphs=(*sections[2].paragraphs, *core.paragraphs))
        issues.append(review_item("section-三", "interfaces-pending", "已补入确认的输入输出", "请复核细化设计"))
    if not any(re.search(r"异常|边界|缺失|超时|失效|无效", p)
               for section in sections for p in section_paragraphs(section)):
        sections[2] = replace(sections[2], paragraphs=(
            *sections[2].paragraphs, "【待补】异常工况与适用边界。"
        ))
        issues.append(review_item("section-三", "limits-pending", "正文缺少异常工况或适用边界", "请补充"))
    document = replace(document, abstract=abstract, sections=tuple(sections),
                       content_paragraphs=[p for section in sections for p in section.paragraphs],
                       prior_art_status=prior_art.status)
    try:
        validate_disclosure_against_preview(document, preview, allow_missing_diagrams=True)
    except ValueError:
        # The confirmed wording is available even if the assembled text was
        # rephrased. Include it at the point where the reviewer needs it.
        core, pending = reviewable_section(fallback_section("三", preview), source, preview, "section-三")
        issues.extend(pending)
        confirmed_points, pending = reviewable_section(
            fallback_section("五", preview), source, preview, "section-五",
        )
        issues.extend(pending)
        key_points = sections[4]
        sections[2] = replace(sections[2], paragraphs=(*sections[2].paragraphs, *core.paragraphs))
        sections[4] = replace(key_points, paragraphs=(*key_points.paragraphs, *confirmed_points.paragraphs[:-1]))
        issues.append(review_item(
            "assembly", "confirmed-content-pending", "正文或图表与已确认方案仍有待核对项",
            "已补入可核对的模块、输入输出、流程和关键点，请复核",
        ))
        document = replace(
            document, sections=tuple(sections),
            content_paragraphs=[p for section in sections for p in section.paragraphs],
        )
    if not any(item.code == "engineering-style-pending" for item in issues):
        try:
            validate_engineering_language(document)
        except ValueError as exc:
            issues.append(review_item("assembly", "engineering-style-pending", str(exc), "请复核工程文档表述"))
    return replace(document, diagnostics=tuple(dict.fromkeys(issues)))
