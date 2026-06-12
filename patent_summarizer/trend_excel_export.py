from __future__ import annotations

from pathlib import Path

from .metadata import BIBLIOGRAPHIC_FIELD_NAMES
from .trend_analysis import TrendAnalysisResult, TrendPatentRecord, TrendReport


OVERVIEW_HEADERS = [
    "主一级分类",
    "主二级分类",
    "专利数",
    "年份范围",
    "年度峰值",
    "平均置信度",
    "复核数",
    "热点关键词",
    "发展现状摘要",
]

YEAR_TREND_HEADERS = ["主一级分类", "主二级分类", "年份", "专利数"]

REPORT_HEADERS = [
    "主一级分类",
    "主二级分类",
    "专利数",
    "发展现状",
    "发展阶段",
    "主要技术路线",
    "热点方向",
    "机会与空白",
    "风险/不确定性",
    "代表专利",
    "证据局限",
]

DETAIL_HEADERS = [
    "文件名",
    "专利名称",
    "主一级分类",
    "主二级分类",
    "主分类置信度",
    "复核状态",
    *BIBLIOGRAPHIC_FIELD_NAMES,
    "推断年份",
    "文件路径",
    "摘要路径",
]


def write_trend_workbook(result: TrendAnalysisResult, output: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("openpyxl>=3.1 is required to write trend workbooks") from exc

    wb = Workbook()
    overview = wb.active
    overview.title = "标签总览"
    overview.append(OVERVIEW_HEADERS)
    for report in result.reports:
        overview.append(_overview_values(report))

    year_ws = wb.create_sheet("年度趋势")
    year_ws.append(YEAR_TREND_HEADERS)
    for report in result.reports:
        for year, count in _sorted_year_counts(report.stats.year_counts):
            year_ws.append([report.stats.first_level, report.stats.second_level, year, count])

    report_ws = wb.create_sheet("趋势报告")
    report_ws.append(REPORT_HEADERS)
    for report in result.reports:
        report_ws.append(_report_values(report))

    detail_ws = wb.create_sheet("专利明细")
    detail_ws.append(DETAIL_HEADERS)
    for record in result.records:
        detail_ws.append(_detail_values(record))

    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for ws in wb.worksheets:
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        _size_columns(ws)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)


def _overview_values(report: TrendReport) -> list[object]:
    return [
        report.stats.first_level,
        report.stats.second_level,
        report.stats.patent_count,
        report.stats.year_range,
        report.stats.peak_year,
        report.stats.average_confidence,
        report.stats.review_count,
        "\n".join(report.stats.top_keywords),
        report.status_summary,
    ]


def _report_values(report: TrendReport) -> list[object]:
    return [
        report.stats.first_level,
        report.stats.second_level,
        report.stats.patent_count,
        report.status_summary,
        report.development_stage,
        report.technical_routes,
        report.hot_topics,
        report.opportunities,
        report.risks,
        report.representative_patents,
        report.evidence_limits,
    ]


def _detail_values(record: TrendPatentRecord) -> list[object]:
    return [
        record.filename,
        record.patent_name,
        record.primary_first_level,
        record.primary_second_level,
        record.primary_confidence,
        record.review_status,
        *[record.metadata.get(name, "") for name in BIBLIOGRAPHIC_FIELD_NAMES],
        record.inferred_year,
        record.source_path,
        record.summary_path,
    ]


def _sorted_year_counts(year_counts: dict[str, int]) -> list[tuple[str, int]]:
    known = sorted((year, count) for year, count in year_counts.items() if year != "未知")
    unknown = [("未知", year_counts["未知"])] if "未知" in year_counts else []
    return known + unknown


def _size_columns(ws) -> None:
    for column in ws.columns:
        header = str(column[0].value or "")
        letter = column[0].column_letter
        if header in {"文件路径", "摘要路径"}:
            ws.column_dimensions[letter].width = 48
        elif header in {"发展现状摘要", "发展现状", "主要技术路线", "热点方向", "机会与空白", "风险/不确定性", "代表专利", "证据局限"}:
            ws.column_dimensions[letter].width = 42
        elif header in {"热点关键词", "专利名称", "申请人", "发明人"}:
            ws.column_dimensions[letter].width = 30
        else:
            ws.column_dimensions[letter].width = 18
