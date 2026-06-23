from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Protocol

from .classifier import ChatClient, classify_summary
from .discovery import DocumentItem, normalize_suffixes
from .excel_export import ClassificationExportRow
from .markdown_summary import parse_summary_sections
from .metadata import parse_bibliographic_metadata
from .processor import Extractor, Summarizer
from .taxonomy import Taxonomy


class ClassificationPipeline:
    def process(self, source: Path, summary_path: Path) -> ClassificationExportRow:
        ...


class PatentClassificationPipeline:
    def __init__(
        self,
        extractor: Extractor,
        summarizer: Summarizer,
        classifier_client: ChatClient,
        taxonomy: Taxonomy,
        max_candidates: int = 3,
        refresh_summary: bool = False,
    ):
        self.extractor = extractor
        self.summarizer = summarizer
        self.classifier_client = classifier_client
        self.taxonomy = taxonomy
        self.max_candidates = max_candidates
        self.refresh_summary = refresh_summary

    def process(self, source: Path, summary_path: Path) -> ClassificationExportRow:
        summary = self._summary(source, summary_path)
        sections = parse_summary_sections(summary)
        metadata = parse_bibliographic_metadata(sections.get("著录信息", ""))
        result = classify_summary(
            self.classifier_client,
            source.name,
            summary,
            self.taxonomy,
            max_candidates=self.max_candidates,
        )
        return ClassificationExportRow(
            filename=source.name,
            patent_name=sections.get("专利主题") or source.stem,
            source_path=str(source),
            summary_path=str(summary_path),
            primary_first_level=result.primary_first_level,
            primary_second_level=result.primary_second_level,
            primary_confidence=result.primary_confidence,
            candidate_summary=result.candidate_summary,
            review_status=result.review_status,
            reason=result.reason,
            sections=sections,
            metadata=metadata,
        )

    def _summary(self, source: Path, summary_path: Path) -> str:
        if summary_path.exists() and not self.refresh_summary:
            return summary_path.read_text(encoding="utf-8", errors="ignore")
        text = self.extractor.extract(source)
        if not text.strip():
            raise ValueError("No extractable text")
        summary = self.summarizer.summarize_document(source.name, text)
        summary_path.write_text(summary, encoding="utf-8")
        return summary


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = path

    def load_successes(self) -> dict[str, ClassificationExportRow]:
        successes: dict[str, ClassificationExportRow] = {}
        if not self.path.exists():
            return successes
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("status") != "success" or not isinstance(payload.get("row"), dict):
                continue
            row = _row_from_dict(payload["row"])
            successes[row.source_path] = row
        return successes

    def record_success(self, row: ClassificationExportRow) -> None:
        self._append({"status": "success", "source": row.source_path, "row": asdict(row)})

    def record_failure(self, source: Path, error_type: str, message: str) -> None:
        self._append({"status": "failed", "source": str(source), "error_type": error_type, "message": message})

    def _append(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def discover_classification_documents(
    root: Path,
    recursive: bool,
    suffixes: Iterable[str] | None = None,
) -> Iterable[DocumentItem]:
    if not root.exists():
        raise FileNotFoundError(f"Input path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {root}")

    allowed_suffixes = normalize_suffixes(suffixes)
    paths = root.rglob("*") if recursive else root.iterdir()
    for source in sorted(paths, key=lambda item: str(item).lower()):
        if not source.is_file() or source.suffix.lower() not in allowed_suffixes:
            continue
        yield DocumentItem(source=source, output=source.with_suffix(".md"), status="pending")


def _row_from_dict(payload: dict[str, object]) -> ClassificationExportRow:
    return ClassificationExportRow(
        filename=str(payload.get("filename", "")),
        patent_name=str(payload.get("patent_name", "")),
        source_path=str(payload.get("source_path", "")),
        summary_path=str(payload.get("summary_path", "")),
        primary_first_level=str(payload.get("primary_first_level", "")),
        primary_second_level=str(payload.get("primary_second_level", "")),
        primary_confidence=_optional_float(payload.get("primary_confidence")),
        candidate_summary=str(payload.get("candidate_summary", "")),
        review_status=str(payload.get("review_status", "")),
        reason=str(payload.get("reason", "")),
        sections=payload.get("sections") if isinstance(payload.get("sections"), dict) else {},
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
