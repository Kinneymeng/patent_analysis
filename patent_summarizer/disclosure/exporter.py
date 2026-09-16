from __future__ import annotations

import os
import json
import re
import uuid
from zipfile import BadZipFile
from dataclasses import replace
from pathlib import Path

from docx import Document
from PIL import Image

from ..disclosure_docx import build_technical_disclosure_document, validate_technical_disclosure_docx
from .diagrams import render_disclosure_diagrams
from .math_to_omml import latex_to_omml_with_fallback
from .legacy import disclosure_output_path, validate_disclosure_title
from .models import DisclosureDiagnostic, DisclosureOutputPaths, TechnicalDisclosure
from .validation import validate_engineering_language


def disclosure_output_paths(output_dir: Path, title: str) -> DisclosureOutputPaths:
    validate_disclosure_title(title)
    stem = f"{title}_技术交底书"
    root = Path(output_dir)
    return DisclosureOutputPaths(
        docx=root / f"{stem}.docx",
        markdown=root / f"{stem}.md",
        assets_dir=root / f"{stem}_assets",
    )


def existing_disclosure_bundle_complete(paths: DisclosureOutputPaths) -> bool:
    if not paths.docx.is_file() or not paths.markdown.is_file():
        return False
    try:
        document = Document(paths.docx)
        document_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        markdown = paths.markdown.read_text(encoding="utf-8")
        required = (
            "一、技术背景与现有方案",
            "二、需要解决的技术问题",
            "三、技术方案详细说明",
            "四、技术效果与适用边界",
            "五、技术关键点",
            "六、实施例与验证方法",
        )
        if any(value not in document_text or value not in markdown for value in required):
            return False
        image_links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
        if not image_links:
            return False
        for link in image_links:
            image_path = paths.markdown.parent / Path(link.replace("/", os.sep))
            if not image_path.is_file():
                return False
            with Image.open(image_path) as image:
                image.verify()
        return True
    except (OSError, ValueError, BadZipFile, KeyError):
        return False


def write_disclosure_bundle(
    disclosure: TechnicalDisclosure,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> DisclosureOutputPaths:
    paths = disclosure_output_paths(output_dir, disclosure.title)
    conflicts = [path for path in (paths.docx, paths.markdown) if path.exists()]
    if conflicts and not overwrite:
        raise FileExistsError("技术交底书已经存在：" + "；".join(str(path) for path in conflicts))
    validate_engineering_language(disclosure)
    paths.docx.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged_assets = paths.assets_dir.with_name(f".{paths.assets_dir.name}.{token}.tmp")
    temporary_md = paths.markdown.with_name(f".{paths.markdown.stem}.{token}.tmp.md")
    temporary_docx = paths.docx.with_name(f".{paths.docx.stem}.{token}.tmp.docx")
    diagnostics = list(disclosure.diagnostics)
    for formula in disclosure.formulas:
        formula_result = latex_to_omml_with_fallback(formula.latex)
        if formula_result.mode != "structured":
            diagnostics.append(
                DisclosureDiagnostic(
                    stage="formula-export",
                    code=f"formula-{formula_result.mode}",
                    message=f"{formula.label} 无法转换为结构化 Word 公式：{formula_result.error}",
                    action=(
                        "已写入 Word 可编辑线性公式，Markdown 保留原始 LaTeX"
                        if formula_result.mode == "linear"
                        else "已写入原始 LaTeX 文本并标记需人工复核"
                    ),
                )
            )
    try:
        rendered = (
            render_disclosure_diagrams(disclosure, staged_assets, diagnostics=diagnostics)
            if disclosure.is_structured
            else disclosure
        )
        published = _with_published_image_paths(rendered, paths.assets_dir)
        markdown = disclosure_to_markdown(published, paths.markdown)
        validate_disclosure_markdown(
            markdown,
            published,
            paths.markdown,
            check_images=False,
        )
        temporary_md.write_text(markdown, encoding="utf-8")
        if temporary_md.read_text(encoding="utf-8") != markdown:
            raise ValueError("技术交底书 Markdown 写入后内容不一致")
        document = build_technical_disclosure_document(rendered)
        document.save(temporary_docx)
        validate_technical_disclosure_docx(temporary_docx, rendered)
        if not overwrite and (paths.docx.exists() or paths.markdown.exists()):
            raise FileExistsError(f"技术交底书已经存在：{paths.docx} 或 {paths.markdown}")
        _publish_bundle(
            rendered,
            temporary_md,
            temporary_docx,
            paths,
            token,
        )
    finally:
        for path in (temporary_md, temporary_docx):
            if path.exists():
                path.unlink()
        if staged_assets.exists():
            for path in staged_assets.iterdir():
                if path.is_file():
                    path.unlink()
            staged_assets.rmdir()
    final_paths = replace(paths, diagnostics=tuple(diagnostics))
    try:
        _write_diagnostics(output_dir, disclosure.title, final_paths.diagnostics)
    except OSError as exc:
        final_paths = replace(
            final_paths,
            diagnostics=(
                *final_paths.diagnostics,
                DisclosureDiagnostic(
                    stage="diagnostics",
                    code="diagnostics-write-failed",
                    message=f"内部诊断记录写入失败：{exc}",
                    action="交付文件已经通过验证并正常发布",
                ),
            ),
        )
    return final_paths


def disclosure_to_markdown(disclosure: TechnicalDisclosure, markdown_path: Path) -> str:
    if not disclosure.is_structured:
        lines = [f"# {disclosure.title}", "", "## 摘要", "", disclosure.abstract, "", "## 发明内容", ""]
        for paragraph in disclosure.content_paragraphs:
            lines.extend([paragraph, ""])
        return "\n".join(lines).rstrip() + "\n"
    lines = ["# 研发技术交底书", "", f"## {disclosure.title}", "", "### 摘要", "", disclosure.abstract, ""]
    for section in disclosure.sections:
        lines.extend([f"## {section.number}、{section.title}", ""])
        for paragraph in section.paragraphs:
            lines.extend([paragraph, ""])
        for subsection in section.subsections:
            lines.extend([f"### {subsection.number} {subsection.title}", ""])
            for paragraph in subsection.paragraphs:
                lines.extend([paragraph, ""])
        for diagram in (item for item in disclosure.diagrams if item.section_number == section.number):
            lines.extend([f"#### {diagram.title}", "", "<details>", "<summary>Mermaid 图源</summary>", "", "```mermaid", diagram.mermaid, "```", "", "</details>", ""])
            if diagram.image_path is None:
                raise ValueError(f"图示尚未成功生成：{diagram.title}")
            relative = _relative_link(diagram.image_path, markdown_path.parent)
            lines.extend([f"![{diagram.title}]({relative})", ""])
        for formula in (item for item in disclosure.formulas if item.section_number == section.number):
            lines.extend([formula.description, "", "```math", formula.latex, "```", "", formula.label, ""])
        for table in (item for item in disclosure.tables if item.section_number == section.number):
            lines.extend([f"#### {table.title}", "", "| " + " | ".join(table.headers) + " |", "| " + " | ".join("---" for _ in table.headers) + " |"])
            for row in table.rows:
                lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_disclosure_markdown(
    markdown: str,
    disclosure: TechnicalDisclosure,
    markdown_path: Path,
    *,
    check_images: bool = True,
) -> None:
    if not markdown.startswith("# "):
        raise ValueError("技术交底书 Markdown 缺少主标题")
    if disclosure.is_structured:
        positions = [markdown.find(f"## {section.number}、{section.title}") for section in disclosure.sections]
        if any(value < 0 for value in positions) or positions != sorted(positions):
            raise ValueError("技术交底书 Markdown 六章目录缺失或错序")
        if markdown.count("```mermaid") != len(disclosure.diagrams):
            raise ValueError("技术交底书 Markdown 的 Mermaid 图源数量不匹配")
        for diagram in disclosure.diagrams:
            if diagram.image_path is None or (check_images and not diagram.image_path.exists()):
                raise ValueError(f"技术交底书 Markdown 引用的图片不存在：{diagram.title}")
    validate_engineering_language(disclosure)


def _relative_link(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _with_published_image_paths(
    disclosure: TechnicalDisclosure,
    assets_dir: Path,
) -> TechnicalDisclosure:
    diagrams = tuple(
        replace(
            item,
            image_path=assets_dir / item.image_path.name if item.image_path else None,
        )
        for item in disclosure.diagrams
    )
    return replace(disclosure, diagrams=diagrams)


def _publish_bundle(
    rendered: TechnicalDisclosure,
    temporary_md: Path,
    temporary_docx: Path,
    paths: DisclosureOutputPaths,
    token: str,
) -> None:
    assets_existed = paths.assets_dir.exists()
    paths.assets_dir.mkdir(parents=True, exist_ok=True)
    sources_and_destinations = [
        *[
            (item.image_path, paths.assets_dir / item.image_path.name)
            for item in rendered.diagrams
            if item.image_path is not None
        ],
        (temporary_md, paths.markdown),
        (temporary_docx, paths.docx),
    ]
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for source, destination in sources_and_destinations:
            if source is None or not source.exists():
                raise FileNotFoundError(f"待发布文件不存在：{source}")
            if destination.exists():
                backup = destination.with_name(f".{destination.name}.{token}.backup")
                os.replace(destination, backup)
                backups[destination] = backup
            os.replace(source, destination)
            published.append(destination)
    except Exception:
        for destination in reversed(published):
            if destination.exists():
                destination.unlink()
            backup = backups.pop(destination, None)
            if backup is not None and backup.exists():
                os.replace(backup, destination)
        for destination, backup in list(backups.items()):
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
        if not assets_existed and paths.assets_dir.exists() and not any(paths.assets_dir.iterdir()):
            paths.assets_dir.rmdir()
        raise
    else:
        for backup in backups.values():
            if backup.exists():
                backup.unlink()


def _write_diagnostics(
    output_dir: Path,
    title: str,
    diagnostics: tuple[DisclosureDiagnostic, ...],
) -> None:
    work_dir = Path(output_dir) / ".patent_disclosure_work" / "diagnostics"
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / f"{title}.json"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = [
        {
            "stage": item.stage,
            "code": item.code,
            "severity": item.severity.value,
            "message": item.message,
            "action": item.action,
        }
        for item in diagnostics
    ]
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "disclosure_output_path",
    "disclosure_output_paths",
    "existing_disclosure_bundle_complete",
    "disclosure_to_markdown",
    "validate_disclosure_markdown",
    "write_disclosure_bundle",
]
