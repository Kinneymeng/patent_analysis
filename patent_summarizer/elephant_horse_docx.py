from __future__ import annotations

import os
import uuid
from pathlib import Path

from docx import Document

from .elephant_horse import ElephantHorseReport
from .report_docx import _add_table, _apply_report_font, _evidence_note, _prepare_template


def write_elephant_horse_report(
    report: ElephantHorseReport,
    template: Path,
    output: Path,
    overwrite: bool = False,
) -> Path:
    template, output = Path(template), Path(output)
    if not template.exists():
        raise FileNotFoundError(f"DOCX 模板不存在：{template}")
    if output.suffix.lower() != ".docx":
        raise ValueError("象马组合输出必须使用 .docx 扩展名")
    if output.exists() and not overwrite:
        raise FileExistsError(f"输出已存在：{output}；请使用 --overwrite 覆盖")

    document = Document(template)
    _prepare_template(document, target_label=report.inputs.target.label)
    for paragraph in document.paragraphs:
        if "专利布局分析报告" in paragraph.text:
            paragraph.text = paragraph.text.replace("专利布局分析报告", "保护型象马组合布局报告")
    _build_body(document, report)
    _apply_report_font(document)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.docx")
    try:
        document.save(temporary)
        Document(temporary)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def _build_body(document, report: ElephantHorseReport) -> None:
    unique_local = {
        item.evidence_id: item for theme in report.themes for item in theme.local_evidence
    }
    fulltext_verified = sum(
        item.evidence_status == "全文已核验" for item in unique_local.values()
    )
    fulltext_fallback = sum(
        item.evidence_status.startswith("全文提取失败") for item in unique_local.values()
    )
    document.add_heading("目录", level=1)
    _add_table(
        document,
        ["章节", "内容"],
        [
            ["一", "管理层摘要"],
            ["二", "分析范围与证据等级"],
            ["三", "目标现有专利的象马角色映射"],
            ["四", "分主题核心差异与象马布局"],
            ["五", "跨主题协同与总体布局顺序"],
            ["六", "待补问题汇总"],
            ["七", "证据索引"],
        ],
    )

    document.add_heading("一、管理层摘要", level=1)
    for item in report.management_summary:
        document.add_paragraph(item, style="List Bullet")
    _evidence_note(document, f"管理层摘要整体采用“{report.overall_scope}”口径；不同主题可能具有不同证据等级。")

    document.add_heading("二、分析范围与证据等级", level=1)
    _add_table(
        document,
        ["项目", "内容"],
        [
            ["来源报告", str(report.inputs.report_path)],
            ["对应附录", str(report.inputs.appendix_path)],
            ["目标企业", f"{report.inputs.target.label}（{len(report.inputs.target.records)} 件）"],
            ["竞争对手", "、".join(f"{item.label}（{len(item.records)} 件）" for item in report.inputs.baselines)],
            ["摘要完整度", "；".join(f"{item.label} {item.summary_count}/{item.coverage_count}" for item in report.inputs.portfolio_sources)],
            ["摘要/全文核验数量", f"结构化摘要 {sum(item.summary_count for item in report.inputs.portfolio_sources)} 份；全文成功核验 {fulltext_verified} 件；全文提取失败并降级摘要 {fulltext_fallback} 件。按主题最多核验 3 件目标专利和 5 件竞品专利，跨主题重复专利复用提取结果。"],
            ["证据局限", "扩展检索仅支持样本层面的行业判断，不代表全行业穷尽，也不直接判断新颖性、创造性或专利价值。"],
        ],
    )
    _add_table(
        document,
        ["主题", "结论口径", "高相关外部专利", "申请人", "IPC小类分支", "门槛说明"],
        [[theme.name, theme.scope, str(len(theme.industry_coverage.records)), str(theme.industry_coverage.applicant_count), str(theme.industry_coverage.ipc_branch_count), theme.industry_coverage.threshold_note] for theme in report.themes],
        font_size=8,
    )

    document.add_heading("三、目标现有专利的象马角色映射", level=1)
    role_rows = []
    evidence_lookup = {
        item.evidence_id: item
        for theme in report.themes
        for item in theme.local_evidence
        if item.company == report.inputs.target.label
    }
    for theme in report.themes:
        for role in theme.existing_roles:
            evidence = evidence_lookup.get(role.evidence_id)
            role_rows.append([
                evidence.patent_name if evidence else role.evidence_id,
                evidence.publication_number if evidence else role.evidence_id,
                role.role,
                theme.name,
                role.rationale,
                role.gap,
                evidence.evidence_status if evidence else "证据引用待核对",
            ])
    _add_table(document, ["现有专利", "公开号/证据ID", "象马角色", "关联主题", "映射依据", "现有缺口", "证据状态"], role_rows or [["—", "—", "未形成组合关系", "—", "目标样本未映射出可用支撑", "需补充目标专利与实施材料", "摘要证据"]], font_size=8)

    document.add_heading("四、分主题核心差异与象马布局", level=1)
    for index, theme in enumerate(report.themes, 1):
        document.add_heading(f"4.{index} {theme.name}", level=2)
        _add_table(document, ["分析项", "结论"], [
            ["证据口径", f"{theme.scope}；{theme.industry_coverage.threshold_note}"],
            ["样本常见路线", theme.common_route],
            ["目标现有路线", theme.target_route],
            ["核心差异", theme.core_difference],
            ["技术效果", theme.technical_effect],
            ["置信等级", f"{theme.confidence}；{theme.confidence_basis}"],
        ])
        document.add_heading(f"4.{index}.1 规避路径分析", level=3)
        _add_table(document, ["路径类型", "可能规避路径", "应对建议", "证据状态"], [[item.path_type, item.possible_path, item.response, item.evidence_state] for item in theme.design_arounds] or [["替换/省略/等效", "材料不足，尚不能识别具体路径", "补充结构、流程和替代实施方式", "尚未检索"]], font_size=8)
        document.add_heading(f"4.{index}.2 象核心候选", level=3)
        _add_table(document, ["节点ID", "候选名称", "成熟度", "保护主题", "竞争依据", "实施依据", "证据ID", "优先级"], [[theme.elephant.node_id, theme.elephant.title, theme.elephant.status, theme.elephant.protection_theme, theme.elephant.competitive_basis, theme.elephant.implementation_basis, "、".join(theme.elephant.evidence_ids), theme.elephant.priority]], font_size=8)
        document.add_heading(f"4.{index}.3 推荐马节点及暂缓节点", level=3)
        _add_table(document, ["节点ID", "状态", "类别", "节点名称", "保护主题", "竞争依据", "实施依据", "证据ID", "优先级"], [[item.node_id, item.status, item.category, item.title, item.protection_theme, item.competitive_basis, item.implementation_basis, "、".join(item.evidence_ids), item.priority] for item in (*theme.horses, *theme.deferred)] or [["—", "暂缓", "—", "未形成可用马节点", "待补材料后判断", "现有证据不足", "材料未提供", "—", "低"]], font_size=7)
        document.add_heading(f"4.{index}.4 现有专利与新增布局位", level=3)
        _add_table(document, ["证据ID", "现有角色", "映射理由", "缺口/新增布局需要"], [[item.evidence_id, item.role, item.rationale, item.gap] for item in theme.existing_roles] or [["—", "未形成组合关系", "现有材料未映射出支撑专利", "补充可实施方案和关联专利"]], font_size=8)
        document.add_heading(f"4.{index}.5 手段×功效矩阵", level=3)
        _add_table(document, ["手段", "功效", "证据状态", "说明"], [[item.means, item.effect, item.evidence_state, item.note] for item in theme.matrix] or [["待补具体手段", "待验证技术功效", "尚未检索", "报告或目标专利未提供可实施方案"]], font_size=8)
        document.add_heading(f"4.{index}.6 申请优先级及组合关系", level=3)
        document.add_paragraph(f"申请顺序：{theme.application_order}")
        document.add_paragraph(f"组合关系：{theme.combination_relation}")
        document.add_heading(f"4.{index}.7 本主题待补问题", level=3)
        for question in theme.questions:
            document.add_paragraph(question, style="List Bullet")

    document.add_heading("五、跨主题协同与总体布局顺序", level=1)
    _add_table(document, ["协同维度", "识别结果"], [
        ["可共用象核心", "\n".join(report.shared_cores) or "尚未识别出有充分共同证据的共用象核心。"],
        ["重复马节点", "\n".join(report.duplicate_nodes) or "未发现标题层面的重复节点；仍需在形成实施方案后复核技术实质。"],
        ["上下游协同", "\n".join(report.upstream_downstream_links) or "现有材料不足，尚未识别明确上下游协同关系。"],
        ["建议批次", "\n".join(report.suggested_batches)],
    ])

    document.add_heading("六、待补问题汇总", level=1)
    _add_table(document, ["主题", "待补问题"], [[theme.name, "\n".join(theme.questions) or "暂无"] for theme in report.themes])

    document.add_page_break()
    document.add_heading("七、证据索引", level=1)
    _add_table(document, ["主题", "企业/申请人", "专利名称", "公开号/文件名", "来源位置", "使用层级", "全文或摘要证据状态", "公开链接"], [[item["主题"], item["企业"], item["专利名称"], item["公开号/文件名"], item["来源位置"], item["使用层级"], item["证据状态"], item["公开链接"]] for item in report.evidence_index], font_size=7)


__all__ = ["write_elephant_horse_report"]
