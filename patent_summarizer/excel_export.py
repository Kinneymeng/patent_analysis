from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .markdown_summary import SUMMARY_SECTION_NAMES


EXPORT_HEADERS = [
    "文件名",
    "专利名称",
    "文件路径",
    "摘要路径",
    "主一级分类",
    "主二级分类",
    "主分类置信度",
    "候选分类",
    "复核状态",
    "分类理由",
    *SUMMARY_SECTION_NAMES,
    "摘要全文",
]


@dataclass(frozen=True)
class ClassificationExportRow:
    filename: str
    patent_name: str
    source_path: str
    summary_path: str
    primary_first_level: str
    primary_second_level: str
    primary_confidence: float | None
    candidate_summary: str
    review_status: str
    reason: str
    sections: dict[str, str]


def write_classification_workbook(rows: list[ClassificationExportRow], output: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("openpyxl>=3.1 is required to write classification workbooks") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "分类结果"
    ws.append(EXPORT_HEADERS)
    for row in rows:
        ws.append(_row_values(row))

    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for column in ws.columns:
        letter = column[0].column_letter
        if letter in {"C", "D", "T"}:
            ws.column_dimensions[letter].width = 48
        elif letter in {"H", "J"}:
            ws.column_dimensions[letter].width = 36
        else:
            ws.column_dimensions[letter].width = 18
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def _row_values(row: ClassificationExportRow) -> list[object]:
    return [
        row.filename,
        row.patent_name,
        row.source_path,
        row.summary_path,
        row.primary_first_level,
        row.primary_second_level,
        row.primary_confidence,
        row.candidate_summary,
        row.review_status,
        row.reason,
        *[row.sections.get(name, "") for name in SUMMARY_SECTION_NAMES],
        row.sections.get("摘要全文", ""),
    ]
