from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable, Mapping

from .models import (
    DisclosureDiagnostic,
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
    mapping_payload,
    parse_disclosure_section,
    reconcile_disclosure_with_preview,
    split_claims,
    unsupported_prior_art_claim,
    validate_disclosure_against_preview,
    validate_engineering_language,
)


def review_item(stage: str, code: str, message: str, action: str) -> DisclosureDiagnostic:
    return DisclosureDiagnostic(stage, code, message, action, requires_review=True)


def section_paragraphs(section: DisclosureSection) -> tuple[str, ...]:
    return (*section.paragraphs, *(p for sub in section.subsections for p in sub.paragraphs))


def _check_prior_art(text: str, preview: DisclosurePreview) -> None:
    for clause in split_claims(text):
        if unsupported_prior_art_claim(clause, preview.prior_art.status):
            raise ValueError("查新结论超出已取得的检索记录")


def _without_unsupported_prior_art(text: str, preview: DisclosurePreview) -> str:
    parts = split_claims(text)
    return "".join(
        "".join(parts[index:index + 2])
        for index in range(0, len(parts), 2)
        if not unsupported_prior_art_claim(parts[index], preview.prior_art.status)
    ).strip()


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


def check_section(section: DisclosureSection, preview: DisclosurePreview) -> None:
    paragraphs = section_paragraphs(section)
    for paragraph in (*paragraphs, *(sub.title for sub in section.subsections)):
        _check_prior_art(paragraph, preview)
    validate_engineering_language(TechnicalDisclosure("", "", list(paragraphs)))


def reviewable_section(
    section: DisclosureSection,
    preview: DisclosurePreview,
    stage: str,
) -> tuple[DisclosureSection, list[DisclosureDiagnostic]]:
    issues = []

    def redact(text: str) -> str:
        filtered = _without_unsupported_prior_art(text, preview)
        if filtered != text.strip():
            issues.append(review_item(
                stage, "prior-art-claim-pending", f"第{section.number}章含超出检索记录的查新结论",
                "已去除该结论，请核对检索记录",
            ))
        return filtered

    result = _map_section(section, redact)
    result = replace(
        result,
        paragraphs=tuple(p for p in result.paragraphs if p),
        subsections=tuple(replace(sub, paragraphs=tuple(p for p in sub.paragraphs if p))
                          for sub in result.subsections if sub.title and any(sub.paragraphs)),
    )
    try:
        validate_engineering_language(TechnicalDisclosure("", "", list(section_paragraphs(result))))
    except ValueError as exc:
        issues.append(review_item(
            stage, "engineering-style-pending", f"第{section.number}章：{exc}", "请复核工程文档表述",
        ))
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


def fallback_section(number: str, preview: DisclosurePreview) -> DisclosureSection | None:
    confirmed = {
        "二": preview.technical_problems,
        "四": preview.process_steps,
    }.get(number, ())
    if confirmed:
        return DisclosureSection(number, SECTION_TITLES[number], confirmed)
    return None


def prepare_artifacts(
    document: TechnicalDisclosure,
    raw: Mapping[str, Any],
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
        _check_prior_art(abstract, preview)
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
                abstract = _without_unsupported_prior_art(original, preview)
            if not abstract:
                abstract = f"{preview.title}。{'。'.join(preview.technical_problems)}。"
            issues.append(review_item("artifacts", "abstract-pending", str(exc), "请复核摘要"))
        else:
            cache["摘要"] = abstract

    def parse_one(field, parse_items, payload):
        entries = payload.get(field)
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError(f"{field}纠错须返回一项")
        item, = parse_items(entries)
        if item.section_number not in SECTION_TITLES:
            raise ValueError(f"{field}引用了不存在的章节 {item.section_number}")
        text = {
            "图示": lambda: (item.title, item.mermaid),
            "参数表": lambda: (item.title, *item.headers, *(cell for row in item.rows for cell in row)),
            "公式": lambda: (item.description,),
        }[field]()
        for value in text:
            _check_prior_art(value, preview)
        return item, entries[0]

    groups = {}
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
        labels = set()
        for index, entry in enumerate(raw_entries):
            parser = lambda payload: parse_one(field, parse_items, payload)
            try:
                result = parser({field: [entry]})
            except (TypeError, ValueError) as exc:
                result = repair(field, entry, str(exc), parser) if field == "参数表" else None
                if result is None:
                    issues.append(review_item(
                        "artifacts", "invalid-artifact", f"{field}第 {index + 1} 项：{exc}",
                        "已保留其他有效项目，请补充该项",
                    ))
                    continue
                entries[index] = result[1]
            if field == "公式":
                label = result[0].label
                if label in labels:
                    issues.append(review_item(
                        "artifacts", "formula-label-duplicate", f"{label} 的编号重复", "请核对公式编号与引用",
                    ))
                    continue
                labels.add(label)
            items.append((index, result[0]))

        if field == "图示":
            for kind in ("系统架构图", "流程图"):
                if not any(item.kind == kind for _, item in items):
                    issues.append(review_item("artifacts", "diagram-missing", f"缺少{kind}", "请补充图示"))
        cache[field] = entries
        groups[field] = items

    return replace(
        document,
        abstract=abstract,
        diagrams=tuple(item for _, item in groups["图示"]),
        tables=tuple(item for _, item in groups["参数表"]),
        formulas=tuple(item for _, item in groups["公式"]),
        diagnostics=tuple(issues),
    ), cache


def complete_staged_document(
    document: TechnicalDisclosure,
    preview: DisclosurePreview,
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
        missing = [field for field in dict.fromkeys(fields) if field and field not in corpus]
        if missing:
            text = "本次查新记录：" + "；".join(missing)
            if prior_art.status == PriorArtStatus.EMPTY:
                text += "。本次检索范围内未筛选出记录。"
            sections[1] = replace(sections[1], paragraphs=(*sections[1].paragraphs, text))
    if not any(re.search(r"异常|边界|缺失|超时|失效|无效", p)
               for section in sections for p in section_paragraphs(section)):
        issues.append(review_item("section-四", "limits-pending", "正文缺少异常工况或适用边界", "请补充"))
    document = replace(document, sections=tuple(sections),
                       content_paragraphs=[p for section in sections for p in section.paragraphs],
                       prior_art_status=prior_art.status)
    try:
        validate_disclosure_against_preview(document, preview, allow_missing_diagrams=True)
    except ValueError as exc:
        issues.append(review_item(
            "assembly", "confirmed-content-pending", str(exc), "请核对正文、图表及已确认方案",
        ))
    if not any(item.code == "engineering-style-pending" for item in issues):
        try:
            validate_engineering_language(document)
        except ValueError as exc:
            issues.append(review_item("assembly", "engineering-style-pending", str(exc), "请复核工程文档表述"))
    return replace(document, diagnostics=tuple(dict.fromkeys(issues)))
