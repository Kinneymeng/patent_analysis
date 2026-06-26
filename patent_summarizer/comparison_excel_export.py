from __future__ import annotations

import re
from pathlib import Path

from .comparison import ComparisonReport, ComparisonResult, LabelComparison, PortfolioDataset
from .metadata import BIBLIOGRAPHIC_FIELD_NAMES


OVERVIEW_HEADERS = [
    "A组合",
    "外部组合",
    "A专利数",
    "外部专利数",
    "标签数",
    "整体结论",
    "A布局概况",
    "相比外部的优势",
    "相比外部的短板",
    "差异化判断",
    "是否主流布局策略",
    "证据局限",
]

LABEL_HEADERS = [
    "对比",
    "主一级分类",
    "主二级分类",
    "A专利数",
    "外部专利数",
    "A占比",
    "外部占比",
    "占比差",
    "判断",
    "建议优先级分",
    "A年份范围",
    "外部年份范围",
    "A峰值年份",
    "外部峰值年份",
    "A关键词",
    "外部关键词",
    "A代表专利",
    "外部代表专利",
]

RECOMMENDATION_HEADERS = ["对比", "优先级", "建议布局点", "来源字段"]
YEAR_HEADERS = ["对比", "组合", "主一级分类", "主二级分类", "年份", "专利数"]
DETAIL_HEADERS = [
    "组合",
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
SOURCE_HEADERS = ["组合", "目录", "分类表", "分类来源", "专利数", "标签数"]


def write_comparison_workbook(result: ComparisonResult, output: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise RuntimeError("openpyxl>=3.1 is required to write comparison workbooks") from exc

    wb = Workbook()
    overview = wb.active
    overview.title = "对比总览"
    overview.append(OVERVIEW_HEADERS)
    for report in result.reports:
        overview.append(_overview_values(report))

    label_ws = wb.create_sheet("标签对比")
    label_ws.append(LABEL_HEADERS)
    for report in result.reports:
        for item in report.label_comparisons:
            label_ws.append(_label_values(item))

    recommendation_ws = wb.create_sheet("布局建议")
    recommendation_ws.append(RECOMMENDATION_HEADERS)
    for report in result.reports:
        for index, recommendation in enumerate(_split_recommendations(report.recommendations), start=1):
            recommendation_ws.append([report.comparison_name, index, recommendation, "建议布局点"])

    year_ws = wb.create_sheet("年度对比")
    year_ws.append(YEAR_HEADERS)
    for report in result.reports:
        for item in report.label_comparisons:
            for year, count in _sorted_year_counts(item.target_year_counts):
                year_ws.append([report.comparison_name, result.target.label, item.first_level, item.second_level, year, count])
            for year, count in _sorted_year_counts(item.baseline_year_counts):
                year_ws.append([report.comparison_name, report.baseline_label, item.first_level, item.second_level, year, count])

    detail_ws = wb.create_sheet("专利明细")
    detail_ws.append(DETAIL_HEADERS)
    for portfolio in [result.target, *result.baselines]:
        for record in portfolio.records:
            detail_ws.append(
                [
                    portfolio.label,
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
            )

    source_ws = wb.create_sheet("数据来源")
    source_ws.append(SOURCE_HEADERS)
    for portfolio in [result.target, *result.baselines, *([result.combined_baseline] if result.combined_baseline else [])]:
        source_ws.append(_source_values(portfolio))

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


def _overview_values(report: ComparisonReport) -> list[object]:
    return [
        report.target_label,
        report.baseline_label,
        _target_total(report.label_comparisons),
        _baseline_total(report.label_comparisons),
        len(report.label_comparisons),
        report.overall_conclusion,
        report.target_layout,
        report.advantages,
        report.weaknesses,
        report.differentiation,
        report.mainstream_judgement,
        report.evidence_limits,
    ]


def _label_values(item: LabelComparison) -> list[object]:
    return [
        item.comparison_name,
        item.first_level,
        item.second_level,
        item.target_count,
        item.baseline_count,
        item.target_share,
        item.baseline_share,
        item.share_delta,
        item.category,
        item.priority_score,
        item.target_year_range,
        item.baseline_year_range,
        item.target_peak_year,
        item.baseline_peak_year,
        "\n".join(item.target_keywords),
        "\n".join(item.baseline_keywords),
        "\n".join(item.target_representatives),
        "\n".join(item.baseline_representatives),
    ]


def _source_values(portfolio: PortfolioDataset) -> list[object]:
    labels = {(record.primary_first_level, record.primary_second_level) for record in portfolio.records if record.primary_second_level}
    return [
        portfolio.label,
        str(portfolio.root),
        str(portfolio.classification_path),
        portfolio.classification_source,
        len(portfolio.records),
        len(labels),
    ]


def _split_recommendations(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*\u2022]|\d+[\.、])\s*", "", raw).strip()
        if cleaned:
            lines.append(cleaned)
    return lines or ([text.strip()] if text.strip() else [])


def _sorted_year_counts(year_counts: dict[str, int]) -> list[tuple[str, int]]:
    known = sorted((year, count) for year, count in year_counts.items() if year != "未知")
    unknown = [("未知", year_counts["未知"])] if "未知" in year_counts else []
    return known + unknown


def _target_total(items: list[LabelComparison]) -> int:
    return sum(item.target_count for item in items)


def _baseline_total(items: list[LabelComparison]) -> int:
    return sum(item.baseline_count for item in items)


def _size_columns(ws) -> None:
    for column in ws.columns:
        header = str(column[0].value or "")
        letter = column[0].column_letter
        if header in {"整体结论", "A布局概况", "相比外部的优势", "相比外部的短板", "差异化判断", "是否主流布局策略", "证据局限", "建议布局点"}:
            ws.column_dimensions[letter].width = 42
        elif header in {"A关键词", "外部关键词", "A代表专利", "外部代表专利", "专利名称", "申请人", "发明人"}:
            ws.column_dimensions[letter].width = 30
        elif header in {"目录", "分类表", "文件路径", "摘要路径"}:
            ws.column_dimensions[letter].width = 48
        else:
            ws.column_dimensions[letter].width = 18
