from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from ..extractors import ExtractionError, TextExtractor
from .models import EvidenceReference, MaterialDocument, MaterialScanResult


TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".adoc", ".csv", ".tsv",
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".kts", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs",
    ".go", ".rs", ".swift", ".m", ".mm", ".rb", ".php", ".scala",
    ".sql", ".sh", ".ps1", ".bat", ".cmd", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".xml", ".json", ".proto", ".graphql",
}
DOCUMENT_SUFFIXES = {".docx", ".pptx", ".ppsx", ".pdf"}
UNSUPPORTED_RELEVANT_SUFFIXES = {
    ".doc", ".ppt", ".rtf", ".xls", ".xlsx", ".png", ".jpg", ".jpeg",
    ".webp", ".bmp", ".gif", ".svg", ".step", ".stp", ".sldprt",
    ".sldasm", ".ipt", ".iam", ".prt", ".asm", ".catpart", ".slx", ".mdl",
}
SKIPPED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".venv", "venv", "env",
    "node_modules", "dist", "build", "target", ".next", ".cache",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".patent_summary_logs", ".patent_disclosure_work", "技术交底书",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials.json", "secrets.json", "token.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore"}
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_FILE_CHARS = 40_000
MAX_TOTAL_CHARS = 260_000
EVIDENCE_CHUNK_CHARS = 4_000


def scan_project_materials(
    roots: Iterable[Path],
    *,
    exclude_roots: Iterable[Path] = (),
    max_file_chars: int = MAX_FILE_CHARS,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> MaterialScanResult:
    normalized = tuple(_require_directory(Path(root)) for root in roots)
    if not normalized:
        raise ValueError("至少需要一个项目材料目录")
    excluded = tuple(_resolved(path) for path in exclude_roots)
    extractor = TextExtractor(ocr_enabled=False)
    candidates: list[tuple[int, Path, Path]] = []
    for root in normalized:
        for path in _walk(root, excluded):
            priority = _material_priority(path, root)
            candidates.append((priority, path, root))
    candidates.sort(key=lambda item: (item[0], str(item[1]).casefold()))

    documents: list[MaterialDocument] = []
    consumed = 0
    for priority, path, root in candidates:
        suffix = path.suffix.lower()
        relative = _display_path(path, root)
        if _is_sensitive(path):
            documents.append(MaterialDocument(path, "sensitive", priority, "skipped", message="凭据或密钥文件已跳过"))
            continue
        if suffix in UNSUPPORTED_RELEVANT_SUFFIXES:
            documents.append(
                MaterialDocument(path, suffix.lstrip(".") or "file", priority, "unsupported", message=_unsupported_message(suffix))
            )
            continue
        try:
            text, kind, images = _extract(path, extractor)
        except (ExtractionError, OSError, ValueError, RuntimeError) as exc:
            documents.append(MaterialDocument(path, suffix.lstrip(".") or "file", priority, "failed", message=str(exc)))
            continue

        cleaned = _normalize_text(text)
        if not cleaned:
            documents.append(MaterialDocument(path, kind, priority, "failed", message="未提取到可分析文字"))
            continue
        allowance = min(max_file_chars, max_total_chars - consumed)
        if allowance <= 0:
            documents.append(MaterialDocument(path, kind, priority, "skipped", message="已达到本次材料文字上限"))
            continue
        truncated = len(cleaned) > allowance
        if truncated:
            cleaned = cleaned[:allowance].rstrip() + "\n[该文件后续内容未完整读取]"
        consumed += len(cleaned)
        evidence = tuple(_evidence_chunks(cleaned, relative, kind))
        documents.append(
            MaterialDocument(
                path=path,
                kind=kind,
                priority=priority,
                status="read",
                text=cleaned,
                evidence=evidence,
                image_references=tuple(images),
                message="内容已截断" if truncated else "",
                truncated=truncated,
            )
        )
    return MaterialScanResult(normalized, tuple(documents))


def scan_manifest_payload(result: MaterialScanResult) -> dict:
    return {
        "roots": [str(path) for path in result.roots],
        "documents": [
            {
                "path": str(item.path),
                "kind": item.kind,
                "priority": item.priority,
                "status": item.status,
                "message": item.message,
                "truncated": item.truncated,
                "characters": len(item.text),
                "image_references": list(item.image_references),
                "evidence": [asdict(ref) for ref in item.evidence],
            }
            for item in result.documents
        ],
    }


def write_scan_manifest(result: MaterialScanResult, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scan_manifest_payload(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _walk(root: Path, excluded: tuple[Path, ...]):
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name.casefold() not in {item.casefold() for item in SKIPPED_DIRECTORIES}
            and not _is_excluded(current_path / name, excluded)
        ]
        for name in filenames:
            path = current_path / name
            if _is_excluded(path, excluded):
                continue
            suffix = path.suffix.lower()
            if (
                _is_sensitive(path)
                or suffix in TEXT_SUFFIXES
                or suffix in DOCUMENT_SUFFIXES
                or suffix in UNSUPPORTED_RELEVANT_SUFFIXES
            ):
                yield path


def _extract(path: Path, extractor: TextExtractor) -> tuple[str, str, list[str]]:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path), "source" if suffix not in {".md", ".markdown", ".txt", ".rst", ".adoc"} else "text", []
    if suffix == ".docx":
        text = extractor.extract(path)
        images = _docx_image_refs(path)
        return text, "docx", images
    if suffix in {".pptx", ".ppsx"}:
        return _extract_pptx(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    raise ValueError(f"不支持的材料格式：{suffix}")


def _read_text(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    byte_truncated = len(raw) > MAX_FILE_BYTES
    raw = raw[:MAX_FILE_BYTES]
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            value = raw.decode(encoding)
            return value + ("\n[该文件超过单文件字节上限，后续内容未完整读取]" if byte_truncated else "")
        except UnicodeDecodeError:
            continue
    value = raw.decode("utf-8", errors="replace")
    return value + ("\n[该文件超过单文件字节上限，后续内容未完整读取]" if byte_truncated else "")


def _extract_pptx(path: Path) -> tuple[str, str, list[str]]:
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError as exc:
        raise ExtractionError("PPTX 扫描需要 python-pptx") from exc
    presentation = Presentation(str(path))
    lines: list[str] = []
    images: list[str] = []
    for page_number, slide in enumerate(presentation.slides, start=1):
        lines.append(f"\n## 第 {page_number} 页")
        for shape in _walk_pptx_shapes(slide.shapes, MSO_SHAPE_TYPE):
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                label = f"第 {page_number} 页内嵌图片：{getattr(shape, 'name', '图片')}"
                images.append(label)
                lines.append(f"[图片引用：{label}；本工具未识别图片内容]")
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    lines.append(" | ".join((cell.text or "").strip().replace("\n", " ") for cell in row.cells))
            elif getattr(shape, "has_text_frame", False):
                value = (shape.text_frame.text or "").strip()
                if value:
                    lines.append(value)
        try:
            notes = slide.notes_slide.notes_text_frame
            note_text = (notes.text or "").strip() if notes is not None else ""
            if note_text:
                lines.append("备注：" + note_text)
        except (AttributeError, ValueError):
            pass
    return "\n\n".join(lines), "pptx", images


def _walk_pptx_shapes(shapes, shape_types):
    for shape in shapes:
        if shape.shape_type == shape_types.GROUP:
            yield from _walk_pptx_shapes(shape.shapes, shape_types)
        else:
            yield shape


def _extract_pdf(path: Path) -> tuple[str, str, list[str]]:
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    if pdfplumber is not None:
        pages: list[str] = []
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                pages.append(f"## 第 {number} 页\n{page.extract_text() or ''}")
        text = "\n\n".join(pages)
        if text.strip():
            return text, "pdf", []
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError("PDF 扫描需要 pdfplumber 或 pypdf") from exc
    reader = PdfReader(str(path))
    return "\n\n".join(
        f"## 第 {number} 页\n{page.extract_text() or ''}"
        for number, page in enumerate(reader.pages, start=1)
    ), "pdf", []


def _docx_image_refs(path: Path) -> list[str]:
    try:
        from docx import Document
        document = Document(path)
        count = len(document.inline_shapes)
        return [f"Word 内嵌图片 {index}（本工具未识别图片内容）" for index in range(1, count + 1)]
    except Exception:
        return []


def _evidence_chunks(text: str, path: str, kind: str):
    if kind == "source":
        yield from _source_evidence_chunks(text, path)
        return
    page_or_heading = "全文"
    chunk_index = 0
    buffer: list[str] = []
    size = 0
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped) or re.match(r"^第\s*\d+\s*页", stripped):
            page_or_heading = stripped.lstrip("# ")
        buffer.append(line)
        size += len(line) + 1
        if size >= EVIDENCE_CHUNK_CHARS:
            chunk_index += 1
            excerpt = "\n".join(buffer).strip()
            if excerpt:
                yield EvidenceReference(path, f"{page_or_heading}，片段 {chunk_index}", excerpt, kind)
            buffer, size = [], 0
    excerpt = "\n".join(buffer).strip()
    if excerpt:
        yield EvidenceReference(path, f"{page_or_heading}，片段 {chunk_index + 1}", excerpt, kind)


def _source_evidence_chunks(text: str, path: str):
    lines = text.splitlines()
    start = 1
    buffer: list[str] = []
    size = 0
    for number, line in enumerate(lines, start=1):
        buffer.append(line)
        size += len(line) + 1
        if size >= EVIDENCE_CHUNK_CHARS:
            excerpt = "\n".join(buffer).strip()
            if excerpt:
                yield EvidenceReference(path, f"代码行 {start}-{number}", excerpt, "source")
            start, buffer, size = number + 1, [], 0
    excerpt = "\n".join(buffer).strip()
    if excerpt:
        yield EvidenceReference(path, f"代码行 {start}-{len(lines)}", excerpt, "source")


def _material_priority(path: Path, root: Path) -> int:
    value = str(path.relative_to(root)).casefold()
    if any(word in value for word in ("专利", "patent", "交底", "创新点", "invention")):
        return 1
    if any(word in value for word in ("详细设计", "方案", "design", "spec", "proposal", "review")):
        return 2
    if path.suffix.lower() in TEXT_SUFFIXES - {".md", ".markdown", ".txt", ".rst", ".adoc"}:
        return 3
    if any(word in value for word in ("架构", "architecture", "系统设计", "overview")):
        return 4
    return 5


def _unsupported_message(suffix: str) -> str:
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg"}:
        return "已记录图片位置；首版不识别图片内容"
    if suffix in {".doc", ".ppt"}:
        return "旧版 Office 文件需要先另存为 DOCX 或 PPTX"
    if suffix in {".slx", ".mdl"}:
        return "已记录 Simulink 文件；首版不解析模型"
    if suffix in {".step", ".stp", ".sldprt", ".sldasm", ".ipt", ".iam", ".prt", ".asm", ".catpart"}:
        return "已记录 CAD 文件；首版不解析模型"
    return "首版不解析此格式"


def _is_sensitive(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
        or "secret" in name
        or "credential" in name
    )


def _require_directory(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"项目材料目录不存在：{path}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"项目材料路径不是目录：{path}")
    return resolved


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_excluded(path: Path, excluded: tuple[Path, ...]) -> bool:
    resolved = _resolved(path)
    return any(resolved == root or root in resolved.parents for root in excluded)
