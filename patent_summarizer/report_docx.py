from __future__ import annotations

import os
import uuid
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .comparison import PortfolioDataset
from .report_analysis import PatentLayoutReport
from .trend_analysis import TrendAnalysisResult


BLUE = "1F4E78"
LIGHT_BLUE = "D9EAF7"
REPORT_FONT = "宋体"


def write_patent_layout_report(
    report: PatentLayoutReport,
    template: Path,
    output: Path,
    overwrite: bool = False,
) -> None:
    template = Path(template)
    output = Path(output)
    if not template.exists():
        raise FileNotFoundError(f"DOCX template does not exist: {template}")
    if output.suffix.lower() != ".docx":
        raise ValueError("Report output must use the .docx extension")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Report output already exists: {output}; use --overwrite to replace it")

    document = Document(template)
    _prepare_template(document, report)
    _build_report_body(document, report)
    _apply_report_font(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.docx")
    try:
        document.save(temporary)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _prepare_template(document: DocumentType, report: PatentLayoutReport) -> None:
    replacements = {
        "{{分析领域}}": report.domain or "未指定领域",
        "{{领域说明}}": report.domain_context or "未提供",
        "{{目标企业}}": report.target.label,
        "{{竞争对手}}": "、".join(item.label for item in report.baselines),
    }
    for paragraph in _template_paragraphs(document):
        for placeholder, value in replacements.items():
            for run in paragraph.runs:
                if placeholder in run.text:
                    run.text = run.text.replace(placeholder, value)
        if "DOCX 内容与章节模板" in paragraph.text:
            paragraph.text = paragraph.text.replace("DOCX 内容与章节模板", "专利布局分析报告")

    first_heading = next(
        (paragraph for paragraph in document.paragraphs if paragraph.style.name == "Heading 1"),
        None,
    )
    if first_heading is None:
        raise ValueError("DOCX template must contain at least one Heading 1 paragraph as the report-body boundary")
    body = document._element.body
    children = list(body)
    start = children.index(first_heading._element)
    for element in children[start:]:
        if element.tag == qn("w:sectPr"):
            continue
        body.remove(element)


def _template_paragraphs(document: DocumentType) -> Iterable:
    yield from document.paragraphs
    for table in document.tables:
        yield from _table_paragraphs(table)


def _table_paragraphs(table) -> Iterable:
    for row in table.rows:
        for cell in row.cells:
            yield from cell.paragraphs
            for nested_table in cell.tables:
                yield from _table_paragraphs(nested_table)


def _build_report_body(document: DocumentType, report: PatentLayoutReport) -> None:
    document.add_heading("目录", level=1)
    _add_table(
        document,
        ["章节", "内容"],
        [
            ["报告摘要", "管理层核心结论"],
            ["一", "样本范围内行业整体布局现状"],
            ["二", "主要竞争对手布局现状"],
            ["三", "主要竞争对手布局策略分析"],
            ["四", "主要竞争对手布局趋势研判"],
            ["五", f"{report.target.label}布局现状及优劣势分析"],
            ["六", f"{report.target.label}专利布局策略及建议"],
            ["七", "待布局的重点建议"],
            ["八", "专利组合构建策略建议"],
            ["附录", "分析依据与专利清单"],
        ],
    )

    _chapter(document, "报告摘要")
    for conclusion in report.executive_summary.conclusions:
        document.add_paragraph(conclusion, style="List Bullet")
    _evidence_note(document, report.executive_summary.evidence_limits)

    document.add_heading("分析范围与数据说明", level=1)
    _add_table(
        document,
        ["项目", "内容"],
        [
            ["分析领域", report.domain or "未指定领域"],
            ["领域说明", report.domain_context or "未提供"],
            ["目标组合", f"{report.target.label}（{report.target.root}）"],
            ["竞争对手", "、".join(item.label for item in report.baselines)],
            ["样本专利数", str(len(report.industry.records))],
            ["分析口径", "行业相关表述仅指本次上传并完成摘要、分类的样本范围。"],
            ["证据来源", "结构化 Markdown 摘要、分类结果、年度统计及代表性专利。"],
        ],
    )

    _chapter(document, "一、样本范围内行业整体布局现状")
    _portfolio_overview(document, report.industry, report.portfolio_overviews.industry_summary)
    _trend_sections(document, report.trends.get(report.industry.label))

    _chapter(document, "二、主要竞争对手的布局现状")
    for index, baseline in enumerate(report.baselines, start=1):
        document.add_heading(f"2.{index} {baseline.label}布局现状", level=2)
        _portfolio_overview(
            document,
            baseline,
            report.portfolio_overviews.company_summaries[baseline.label],
        )
        _trend_sections(document, report.trends.get(baseline.label))
    document.add_heading(f"2.{len(report.baselines) + 1} 主要竞争对手横向比较", level=2)
    _competitor_matrix(document, report.baselines)

    _chapter(document, "三、主要竞争对手的布局策略分析")
    document.add_heading("3.1 共性布局策略", level=2)
    _strategy_table(document, report.competitor_analysis.common_strategies)
    document.add_heading("3.2 企业差异化布局策略", level=2)
    _strategy_table(document, report.competitor_analysis.differentiated_strategies)
    _evidence_note(document, report.competitor_analysis.evidence_limits)

    _chapter(document, "四、主要竞争对手的布局趋势研判")
    _add_table(
        document,
        ["企业", "可能强化方向", "时间序列信号", "关键词及路线依据", "代表性专利", "证据限制"],
        [
            [item.company, item.direction, item.time_signal, item.route_evidence, "\n".join(item.representative_patents), item.evidence_limit]
            for item in report.competitor_analysis.forecasts
        ],
        font_size=8,
    )
    document.add_heading("4.3 未来竞争焦点", level=2)
    document.add_paragraph(report.competitor_analysis.future_focus)

    _chapter(document, f"五、{report.target.label}布局现状及优劣势分析")
    document.add_heading(f"5.1 {report.target.label}布局现状", level=2)
    _portfolio_overview(
        document,
        report.target,
        report.portfolio_overviews.company_summaries[report.target.label],
    )
    _trend_sections(document, report.trends.get(report.target.label))
    _comparison_sections(document, report)

    _chapter(document, f"六、{report.target.label}专利布局策略及建议")
    _add_table(
        document,
        ["策略", "目标", "重点技术领域", "优先顺序", "建议理由", "关联建议点"],
        [[item.name, item.objective, "、".join(item.technical_fields), item.priority, item.reason, "、".join(item.related_points)] for item in report.target_analysis.strategies],
        font_size=8,
    )

    _chapter(document, "七、待布局的重点建议")
    for index, point in enumerate(report.target_analysis.layout_points, start=1):
        document.add_heading(f"7.{index} {point.name}", level=2)
        _add_table(
            document,
            ["固定字段", "分析内容"],
            [
                ["建议布局点名称", point.name],
                ["所属技术分类", f"{point.first_level} / {point.second_level}"],
                ["主要技术问题", point.technical_problem],
                ["竞争对手布局", point.competitor_layout],
                [f"{report.target.label}现有布局及缺口", point.target_gap],
                ["建议优先级", point.priority],
                ["建议理由", point.reason],
                ["证据专利", "\n".join(point.evidence_patents)],
            ],
        )

    _chapter(document, "八、专利组合构建策略建议")
    for index, plan in enumerate(report.target_analysis.portfolio_plans, start=1):
        document.add_heading(f"8.{index} {plan.layout_point}组合构建", level=2)
        _add_table(
            document,
            ["组合维度", "建议内容"],
            [
                ["核心专利", plan.core_patent],
                ["外围专利分支", "\n".join(plan.peripheral_branches)],
                ["技术环节覆盖", "\n".join(plan.technical_links)],
                ["专利客体协同", "\n".join(plan.patent_subjects)],
                ["系列化或补充布局", plan.series_plan],
                ["跨布局点协同", plan.cross_point_synergy],
                ["证据依据", plan.evidence],
            ],
        )
    _evidence_note(document, report.target_analysis.evidence_limits)

    _chapter(document, "附录：分析依据与专利清单")
    document.add_heading("附录 A：分析样本与企业目录", level=2)
    _add_table(
        document,
        ["企业", "目录", "专利数", "分类来源"],
        [[item.label, str(item.root), str(len(item.records)), item.classification_source] for item in [report.target, *report.baselines]],
    )
    document.add_heading("附录 B：专利分类体系", level=2)
    _add_table(document, ["一级分类", "二级分类"], [list(item) for item in report.taxonomy])
    document.add_heading("附录 C：代表性专利与证据索引", level=2)
    rows = []
    for portfolio in [report.target, *report.baselines]:
        for record in portfolio.records:
            rows.append(
                [
                    portfolio.label,
                    f"{record.filename}\n{record.patent_name}",
                    record.primary_first_level,
                    record.primary_second_level,
                    record.inferred_year,
                    record.review_status,
                ]
            )
    _add_table(document, ["企业", "专利", "一级分类", "二级分类", "年份", "复核状态"], rows, font_size=8)
    _remove_trailing_empty_paragraph(document)


def _chapter(document: DocumentType, title: str) -> None:
    if document.paragraphs:
        document.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)
    document.add_heading(title, level=1)


def _portfolio_overview(
    document: DocumentType,
    portfolio: PortfolioDataset,
    overview_summary: str,
) -> None:
    years = Counter(record.inferred_year or "未知" for record in portfolio.records)
    labels = Counter(
        (record.primary_first_level, record.primary_second_level)
        for record in portfolio.records
        if record.primary_second_level.strip()
    )
    _add_table(
        document,
        ["组合", "专利数", "有效分类数", "覆盖年份", "技术标签数"],
        [[portfolio.label, str(len(portfolio.records)), str(sum(labels.values())), _year_range(years), str(len(labels))]],
    )
    overview = document.add_paragraph()
    label = overview.add_run("整体布局现状：")
    label.bold = True
    overview.add_run(overview_summary)
    document.add_heading("年度分布", level=2)
    _add_table(document, ["年份", "专利数"], [[year, str(count)] for year, count in sorted(years.items())])
    document.add_heading("重点技术分类", level=2)
    _add_table(
        document,
        ["一级分类", "二级分类", "专利数", "占比"],
        [
            [first, second, str(count), f"{count / len(portfolio.records):.1%}" if portfolio.records else "0.0%"]
            for (first, second), count in labels.most_common()
        ],
    )


def _trend_sections(document: DocumentType, result: TrendAnalysisResult | None) -> None:
    if result is None or not result.reports:
        document.add_paragraph("该组合未形成可用的标签趋势报告。")
        return
    document.add_heading("主要技术路线、热点与机会", level=2)
    for item in result.reports:
        category_heading = document.add_heading(
            f"{item.stats.first_level} / {item.stats.second_level}",
            level=3,
        )
        category_heading.paragraph_format.keep_with_next = True
        fields = [
            ("发展现状/阶段", f"{item.status_summary}\n{item.development_stage}"),
            ("主要技术路线", item.technical_routes),
            ("热点方向", item.hot_topics),
            ("机会与空白", item.opportunities),
            ("代表专利", item.representative_patents),
        ]
        for label, value in fields:
            field_heading = document.add_heading(label, level=4)
            field_heading.paragraph_format.keep_with_next = True
            document.add_paragraph(str(value or ""))


def _competitor_matrix(document: DocumentType, portfolios: Sequence[PortfolioDataset]) -> None:
    all_labels = sorted(
        {
            (record.primary_first_level, record.primary_second_level)
            for portfolio in portfolios
            for record in portfolio.records
            if record.primary_second_level.strip()
        }
    )
    counters = {
        portfolio.label: Counter((record.primary_first_level, record.primary_second_level) for record in portfolio.records)
        for portfolio in portfolios
    }
    _add_table(
        document,
        ["一级分类", "二级分类", *[item.label for item in portfolios]],
        [[first, second, *[str(counters[item.label][(first, second)]) for item in portfolios]] for first, second in all_labels],
        font_size=8,
    )


def _strategy_table(document: DocumentType, strategies: Iterable) -> None:
    _add_table(
        document,
        ["策略名称", "核心特征", "适用企业", "重点分类", "统计及连续性证据", "代表性专利", "组合中的作用"],
        [[item.name, item.core_feature, "、".join(item.companies), "、".join(item.technical_categories), item.statistical_evidence, "\n".join(item.representative_patents), item.portfolio_role] for item in strategies],
        font_size=8,
    )


def _comparison_sections(document: DocumentType, report: PatentLayoutReport) -> None:
    if not report.comparison.reports:
        document.add_paragraph("未形成可用的组合对比报告。")
        return
    _add_table(
        document,
        ["对比对象", "布局概况", "优势", "短板", "差异化", "与样本主流关系", "建议布局点"],
        [[item.baseline_label, item.target_layout, item.advantages, item.weaknesses, item.differentiation, item.mainstream_judgement, item.recommendations] for item in report.comparison.reports],
        font_size=8,
    )


def _add_table(
    document: DocumentType,
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    font_size: int = 9,
) -> None:
    row_list = list(rows)
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = True
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = str(header)
        _shade_cell(cell, BLUE)
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(font_size)
    for row_index, values in enumerate(row_list):
        cells = table.add_row().cells
        for column, value in enumerate(values):
            cells[column].text = str(value or "")
            if row_index % 2:
                _shade_cell(cells[column], "F4F8FB")
            for paragraph in cells[column].paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(font_size)
    document.add_paragraph()


def _shade_cell(cell, color: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), color)


def _evidence_note(document: DocumentType, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(f"证据边界：{text}")
    run.italic = True
    run.font.color.rgb = RGBColor(89, 89, 89)


def _apply_report_font(document: DocumentType) -> None:
    for style in document.styles:
        if style.type not in {WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER}:
            continue
        style.font.name = REPORT_FONT
        _set_run_fonts(style.element.get_or_add_rPr())

    containers = [document]
    for section in document.sections:
        containers.extend(
            [
                section.header,
                section.first_page_header,
                section.even_page_header,
                section.footer,
                section.first_page_footer,
                section.even_page_footer,
            ]
        )
    for container in containers:
        for paragraph in _iter_paragraphs(container):
            for run in paragraph.runs:
                run.font.name = REPORT_FONT
                _set_run_fonts(run._element.get_or_add_rPr())


def _iter_paragraphs(container):
    yield from container.paragraphs
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_paragraphs(cell)


def _set_run_fonts(run_properties) -> None:
    fonts = run_properties.get_or_add_rFonts()
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{attribute}"), REPORT_FONT)


def _year_range(years: Counter[str]) -> str:
    known = sorted(year for year in years if year != "未知")
    return f"{known[0]}-{known[-1]}" if known else "未知"


def _remove_trailing_empty_paragraph(document: DocumentType) -> None:
    if not document.paragraphs or document.paragraphs[-1].text.strip():
        return
    paragraph = document.paragraphs[-1]._element
    paragraph.getparent().remove(paragraph)
