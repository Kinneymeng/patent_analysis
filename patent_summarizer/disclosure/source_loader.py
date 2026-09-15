from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .legacy import load_idea_source, load_report_sources
from .models import DisclosureSourceItem, EvidenceReference, MaterialDocument, MaterialScanResult


MAX_ATTACHED_DOCUMENTS = 10
MAX_ATTACHED_CHARS = 70_000
_STOP_TERMS = {
    "一种", "技术", "方案", "系统", "方法", "装置", "模块", "进行", "实现",
    "以及", "相关", "建议", "布局", "组合", "用户", "直接", "思路",
}


def load_project_source(scan_result: MaterialScanResult) -> list[DisclosureSourceItem]:
    if not scan_result.readable:
        raise ValueError("项目目录中没有读取到可用于技术分析的文字材料")
    evidence = tuple(ref for document in scan_result.readable for ref in document.evidence)
    context = _material_context(scan_result.readable, MAX_ATTACHED_CHARS)
    context += _image_reference_context(scan_result.documents)
    roots = "、".join(path.name or str(path) for path in scan_result.roots)
    return [
        DisclosureSourceItem(
            source_id="project",
            label=f"项目材料：{roots}",
            summary="从项目文档与源码中提取可实施技术事实并挖掘发明方案",
            context=context,
            evidence=evidence,
        )
    ]


def attach_materials(
    sources: list[DisclosureSourceItem],
    scan_result: MaterialScanResult | None,
) -> list[DisclosureSourceItem]:
    if scan_result is None:
        return sources
    readable = scan_result.readable
    if not readable:
        image_context = _image_reference_context(scan_result.documents)
        if not image_context:
            return sources
        return [replace(source, context=source.context + image_context) for source in sources]
    result: list[DisclosureSourceItem] = []
    for source in sources:
        selected = _select_relevant_documents(source, readable)
        material_text = _material_context(selected, MAX_ATTACHED_CHARS)
        combined = (
            f"{source.context}\n\n"
            "【项目扫描补充材料】\n"
            "以下片段仅作为技术事实来源。应优先采用与当前主题直接相关的内容；"
            "不得把无关材料混入方案，也不得把图片位置当作已识别的图片内容。\n\n"
            f"{material_text}"
            f"{_image_reference_context(selected)}"
        )
        evidence = source.evidence + tuple(ref for item in selected for ref in item.evidence)
        result.append(replace(source, context=combined, evidence=evidence))
    return result


def source_evidence_catalog(source: DisclosureSourceItem) -> str:
    if not source.evidence:
        return "（没有独立材料定位；依据用户直接输入或分析报告内容）"
    return "\n".join(
        f"[{index}] {item.path}｜{item.locator}"
        for index, item in enumerate(source.evidence, start=1)
    )


def _select_relevant_documents(
    source: DisclosureSourceItem,
    documents: tuple[MaterialDocument, ...],
) -> tuple[MaterialDocument, ...]:
    terms = _source_terms(f"{source.label}\n{source.summary}")
    scored: list[tuple[int, int, str, MaterialDocument]] = []
    for document in documents:
        haystack = f"{document.path.name}\n{document.text[:30_000]}".casefold()
        score = sum(haystack.count(term) for term in terms)
        scored.append((-score, document.priority, str(document.path).casefold(), document))
    scored.sort(key=lambda item: item[:3])
    positive = [item[3] for item in scored if item[0] < 0]
    fallback = [item[3] for item in scored]
    selected = positive[:MAX_ATTACHED_DOCUMENTS] if positive else fallback[:4]
    return tuple(selected)


def _source_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for value in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{1,}|[\u4e00-\u9fff]{2,12}", text):
        folded = value.casefold()
        if folded not in _STOP_TERMS:
            terms.add(folded)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,12}", value):
            terms.update(value[index:index + 2] for index in range(len(value) - 1))
    return terms


def _material_context(documents: tuple[MaterialDocument, ...], max_chars: int) -> str:
    blocks: list[str] = []
    consumed = 0
    for document in documents:
        header = f"### 材料：{document.path}\n类型：{document.kind}；读取状态：{'部分读取' if document.truncated else '完整读取'}\n"
        allowance = max_chars - consumed - len(header)
        if allowance <= 0:
            break
        content = document.text[:allowance]
        blocks.append(header + content)
        consumed += len(header) + len(content)
    return "\n\n".join(blocks)


def _image_reference_context(documents: tuple[MaterialDocument, ...]) -> str:
    references: list[str] = []
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg"}
    for document in documents:
        references.extend(f"- {document.path}｜{value}" for value in document.image_references)
        if document.status == "unsupported" and document.path.suffix.lower() in image_suffixes:
            references.append(f"- {document.path}｜独立图片文件")
    if not references:
        return ""
    return (
        "\n\n【图片位置记录】\n"
        "以下条目只记录图片位置和引用。本工具没有识别图片内容，不得根据文件名推断图中技术事实。\n"
        + "\n".join(references)
    )


__all__ = [
    "attach_materials",
    "load_idea_source",
    "load_project_source",
    "load_report_sources",
    "source_evidence_catalog",
]
