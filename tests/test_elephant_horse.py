from __future__ import annotations

import tempfile
import unittest
import os
import shutil
from pathlib import Path

from docx import Document

from patent_summarizer.cli import build_parser
from patent_summarizer.elephant_horse import (
    ElephantHorseAnalyzer,
    IndustryCoverage,
    IndustryPatentEvidence,
    SCOPE_EXPANDED,
    SCOPE_SAMPLE,
    default_elephant_horse_output,
    load_elephant_horse_inputs,
)
from patent_summarizer.elephant_horse_docx import write_elephant_horse_report
from patent_summarizer.excel_export import ClassificationExportRow, write_classification_workbook
from patent_summarizer.markdown_summary import SUMMARY_SECTION_NAMES


class _FakeClient:
    def chat(self, _system_prompt: str, user_prompt: str):
        evidence_id = user_prompt.split('"证据ID": "', 1)[1].split('"', 1)[0]
        return {
            "优先级": "高",
            "样本常见路线": "竞品主要采用分散控制路线。",
            "目标现有路线": "目标样本已有集中控制证据。",
            "核心差异": "目标路线强调统一协调。",
            "技术效果": "材料仅支持协调关系，量化效果待补。",
            "置信等级": "中",
            "置信依据": f"依据 {evidence_id}，未做额外检索。",
            "现有专利角色": [{"证据ID": evidence_id, "角色": "象核心支撑", "理由": "支撑核心路线", "缺口": "替代实现待补"}],
            "象候选": {"标题": "统一协调核心", "类别": "核心", "保护主题": "协调原理和通用架构", "竞争依据": "竞品路线分散", "实施依据": "材料未提供：控制流程待补", "证据ID": [evidence_id], "优先级": "高", "状态": "象布局基础待补"},
            "推荐马": [{"标题": "场景适配节点", "类别": "场景", "保护主题": "特定场景适配", "竞争依据": "样本存在场景分化", "实施依据": "材料未提供：场景参数待补", "证据ID": [evidence_id], "优先级": "中", "状态": "推荐"}],
            "暂缓节点": [{"标题": "下游诊断节点", "类别": "下游", "保护主题": "结果诊断", "竞争依据": "证据较弱", "实施依据": "材料未提供", "证据ID": [], "优先级": "低", "状态": "暂缓"}],
            "规避路径": [
                {"类型": "替换", "可能路径": "替换控制模块", "应对建议": "补充等价模块", "证据状态": "尚未检索"},
                {"类型": "省略", "可能路径": "省略中间步骤", "应对建议": "确认必要性", "证据状态": "尚未检索"},
                {"类型": "等效", "可能路径": "采用等效信号", "应对建议": "覆盖信号类别", "证据状态": "尚未检索"},
            ],
            "手段功效矩阵": [{"手段": "统一协调", "功效": "一致性", "证据状态": "尚未检索", "说明": "需补实验与替代方案"}],
            "申请顺序": "先补足象核心，再提交场景马。",
            "组合关系": "象覆盖通用协调，马覆盖具体场景。",
            "待补问题": ["核心控制结构是什么？", "完整流程是什么？", "技术效果如何验证？", "有哪些替代实施方式？"],
        }


class _FakeExtractor:
    def extract(self, path: Path) -> str:
        return f"全文：{path.name}"


class _FailingExtractor:
    def extract(self, _path: Path) -> str:
        raise RuntimeError("模拟全文提取失败")


class ElephantHorseTests(unittest.TestCase):
    def test_cli_parser_and_gui_argv(self):
        args = build_parser().parse_args([
            "elephant-horse", "--report", "a.docx", "--output", "b.docx",
            "--expanded-search", "--no-resume", "--overwrite", "--search-failure", "continue",
        ])
        self.assertEqual("elephant-horse", args.command)
        self.assertTrue(args.expanded_search)
        self.assertFalse(args.resume)

    def test_industry_threshold_requires_ten_three_two(self):
        records = tuple(
            IndustryPatentEvidence(
                f"CN{index:09d}A", f"专利{index}", f"申请人{index % 3}", "摘要",
                ("B60W10/00",) if index < 5 else ("G05B13/00",),
                f"https://epub.cnipa.gov.cn/patent/CN{index:09d}A", 3,
            )
            for index in range(10)
        )
        coverage = IndustryCoverage(True, records)
        self.assertTrue(coverage.qualifies)
        self.assertEqual(SCOPE_EXPANDED, coverage.scope)
        self.assertFalse(IndustryCoverage(True, records[:9]).qualifies)
        self.assertEqual(SCOPE_SAMPLE, IndustryCoverage(True, records[:9]).scope)
        same_applicant = tuple(
            IndustryPatentEvidence(item.publication_number, item.title, "同一申请人", item.abstract, item.ipc_codes, item.url, item.relevance_score)
            for item in records
        )
        self.assertFalse(IndustryCoverage(True, same_applicant).qualifies)
        one_branch = tuple(
            IndustryPatentEvidence(item.publication_number, item.title, item.applicant, item.abstract, ("B60W10/00",), item.url, item.relevance_score)
            for item in records
        )
        self.assertFalse(IndustryCoverage(True, one_branch).qualifies)

    def test_load_analyze_and_write_single_docx(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "target"
            competitor = root / "competitor"
            target.mkdir()
            competitor.mkdir()
            self._write_portfolio(target, "目标专利", "CN100000001A")
            self._write_portfolio(competitor, "竞品专利", "CN100000002A")
            report = root / "布局报告.docx"
            appendix = root / "布局报告_附录.docx"
            self._write_report(report)
            self._write_appendix(appendix, target, competitor)

            inputs = load_elephant_horse_inputs(report)
            self.assertEqual("目标企业", inputs.target.label)
            self.assertEqual(1, len(inputs.sources))
            result = ElephantHorseAnalyzer(_FakeClient(), extractor=_FakeExtractor()).analyze(inputs)
            self.assertEqual(1, len(result.themes))
            self.assertEqual(1, len(result.themes[0].horses))
            self.assertEqual("象布局基础待补", result.themes[0].elephant.status)
            self.assertIn("尚未检索", result.themes[0].matrix[0].evidence_state)
            repeated = ElephantHorseAnalyzer(_FakeClient(), extractor=_FakeExtractor()).analyze(inputs)
            self.assertEqual(result.themes[0].elephant.node_id, repeated.themes[0].elephant.node_id)
            fallback = ElephantHorseAnalyzer(_FakeClient(), extractor=_FailingExtractor()).analyze(inputs)
            self.assertTrue(any(item.evidence_status.startswith("全文提取失败") for item in fallback.themes[0].local_evidence))

            template = root / "template.docx"
            template_doc = Document()
            template_doc.add_heading("专利布局分析报告", 0)
            template_doc.add_paragraph("目标企业与主要竞争对手专利组合分析")
            template_doc.add_heading("DOCX 内容与章节模板", 1)
            template_doc.save(template)
            output = default_elephant_horse_output(report, inputs.target.label)
            write_elephant_horse_report(result, template, output)
            self.assertTrue(output.exists())
            self.assertFalse(output.with_name(f"{output.stem}_附录.docx").exists())
            rendered = Document(output)
            text = "\n".join(paragraph.text for paragraph in rendered.paragraphs)
            for heading in ("一、管理层摘要", "二、分析范围与证据等级", "三、目标现有专利的象马角色映射", "四、分主题核心差异与象马布局", "五、跨主题协同与总体布局顺序", "六、待补问题汇总", "七、证据索引"):
                self.assertIn(heading, text)
            retained = os.environ.get("ELEPHANT_HORSE_RENDER_SMOKE")
            if retained:
                shutil.copy2(output, retained)

    def test_missing_summary_blocks_start(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "target"
            competitor = root / "competitor"
            target.mkdir()
            competitor.mkdir()
            self._write_portfolio(target, "目标专利", "CN100000001A")
            self._write_portfolio(competitor, "竞品专利", "CN100000002A")
            (competitor / "竞品专利.md").unlink()
            report = root / "布局报告.docx"
            self._write_report(report)
            self._write_appendix(root / "布局报告_附录.docx", target, competitor)
            with self.assertRaisesRegex(ValueError, "缺少 1 篇"):
                load_elephant_horse_inputs(report)

    def _write_portfolio(self, root: Path, name: str, publication: str) -> None:
        source = root / f"{name}.docx"
        doc = Document()
        doc.add_paragraph("专利原文")
        doc.save(source)
        summary = root / f"{name}.md"
        summary.write_text("# 核心方案\n统一控制方案\n# 关键创新点\n协调控制", encoding="utf-8")
        sections = {field: "统一控制方案" for field in SUMMARY_SECTION_NAMES}
        write_classification_workbook([
            ClassificationExportRow(
                filename=source.name, patent_name=name, source_path=str(source), summary_path=str(summary),
                primary_first_level="控制", primary_second_level="协调", primary_confidence=0.9,
                candidate_summary="控制/协调", review_status="通过", reason="测试", sections=sections,
                metadata={"公开（公告）号": publication, "申请人": root.name},
            )
        ], root / "patent_classification.xlsx")

    def _write_report(self, path: Path) -> None:
        doc = Document()
        doc.add_heading("五、目标布局", 1)
        doc.add_paragraph("目标已有统一协调控制路线。")
        doc.add_heading("六、布局策略", 1)
        doc.add_paragraph("建议构建核心与场景分支。")
        doc.add_heading("七、待布局的重点建议", 1)
        doc.add_heading("7.1 协调控制", 2)
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = "固定字段", "分析内容"
        rows = [
            ("建议布局点名称", "协调控制"), ("所属技术分类", "控制 / 协调"),
            ("主要技术问题", "多模块协调"), ("竞争对手布局", "分散控制"),
            ("目标企业现有布局及缺口", "已有控制专利但替代实现不足"), ("建议优先级", "高"),
            ("建议理由", "形成组合核心"), ("证据专利", "目标专利；竞品专利"),
        ]
        for key, value in rows:
            cells = table.add_row().cells
            cells[0].text, cells[1].text = key, value
        doc.add_heading("八、专利组合构建策略建议", 1)
        doc.add_heading("8.1 协调控制组合构建", 2)
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = "组合维度", "建议内容"
        for key, value in [
            ("核心专利", "统一协调核心"), ("外围专利分支", "场景适配"), ("技术环节覆盖", "感知到控制"),
            ("专利客体协同", "方法与装置"), ("系列化或补充布局", "分批申请"),
            ("跨布局点协同", "与诊断节点协同"), ("证据依据", "目标及竞品样本"),
        ]:
            cells = table.add_row().cells
            cells[0].text, cells[1].text = key, value
        doc.save(path)

    def _write_appendix(self, path: Path, target: Path, competitor: Path) -> None:
        doc = Document()
        table = doc.add_table(rows=1, cols=4)
        for index, header in enumerate(("企业", "目录", "专利数", "分类来源")):
            table.rows[0].cells[index].text = header
        for label, root in (("目标企业", target), ("竞争企业", competitor)):
            cells = table.add_row().cells
            for index, value in enumerate((label, str(root), "1", "existing")):
                cells[index].text = value
        doc.save(path)


if __name__ == "__main__":
    unittest.main()
