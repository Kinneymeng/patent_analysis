import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from patent_summarizer.classification_pipeline import (
    CheckpointStore,
    PatentClassificationPipeline,
    discover_classification_documents,
)
from patent_summarizer.classifier import parse_classification_response
from patent_summarizer.cli import build_parser
from patent_summarizer.excel_export import EXPORT_HEADERS, ClassificationExportRow, write_classification_workbook
from patent_summarizer.markdown_summary import parse_summary_sections
from patent_summarizer.metadata import BIBLIOGRAPHIC_FIELD_NAMES
from patent_summarizer.taxonomy import Taxonomy, TaxonomyItem, load_taxonomy


class TaxonomyTests(unittest.TestCase):
    def test_loads_merged_first_level_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "classes.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.append(["方向", "细化分类"])
            ws.append(["自适应与机器学习", "底盘与智驾域的控制目标融合"])
            ws.append([None, "感知算法融合"])
            ws.merge_cells("A2:A3")
            wb.save(path)

            taxonomy = load_taxonomy(path)

        self.assertEqual(
            taxonomy.items,
            [
                TaxonomyItem("自适应与机器学习", "底盘与智驾域的控制目标融合"),
                TaxonomyItem("自适应与机器学习", "感知算法融合"),
            ],
        )


class MarkdownSummaryTests(unittest.TestCase):
    def test_parses_known_summary_sections(self):
        markdown = """# CN001.pdf
## 著录信息
- 申请号：CN202411111111
- 申请日：2024年1月2日
- 公开（公告）号：未提及
- 公开（公告）日：未提及
- 申请人：示例公司
- 发明人：张三
- IPC/CPC：B62D

## 专利主题
一种车辆控制方法。

## 技术领域
底盘控制。

## 潜在分类关键词
- 智驾
- 底盘

## 摘要依据与不确定点
文本清晰。
"""

        sections = parse_summary_sections(markdown)

        self.assertEqual(sections["专利主题"], "一种车辆控制方法。")
        self.assertIn("申请号：CN202411111111", sections["著录信息"])
        self.assertEqual(sections["技术领域"], "底盘控制。")
        self.assertEqual(sections["潜在分类关键词"], "- 智驾\n- 底盘")
        self.assertEqual(sections["摘要全文"], markdown.strip())


class ClassificationParsingTests(unittest.TestCase):
    def test_parses_fenced_json_and_selects_primary_candidate(self):
        taxonomy = [
            TaxonomyItem("自适应与机器学习", "底盘与智驾域的控制目标融合"),
            TaxonomyItem("车辆运动控制内核", "与智驾域协同的控制策略分工"),
        ]
        response = """```json
{
  "classifications": [
    {
      "一级分类": "自适应与机器学习",
      "二级分类": "底盘与智驾域的控制目标融合",
      "confidence": 0.82,
      "reason": "摘要提到控制目标融合"
    },
    {
      "一级分类": "车辆运动控制内核",
      "二级分类": "与智驾域协同的控制策略分工",
      "confidence": 0.51,
      "reason": "也涉及智驾协同"
    }
  ]
}
```"""

        result = parse_classification_response(response, taxonomy)

        self.assertEqual(result.primary_first_level, "自适应与机器学习")
        self.assertEqual(result.primary_second_level, "底盘与智驾域的控制目标融合")
        self.assertEqual(result.primary_confidence, 0.82)
        self.assertEqual(result.review_status, "通过")
        self.assertIn("与智驾域协同的控制策略分工", result.candidate_summary)
        self.assertIn("摘要提到控制目标融合", result.reason)

    def test_marks_low_confidence_primary_for_review(self):
        taxonomy = [TaxonomyItem("自适应与机器学习", "感知算法融合")]
        response = {
            "classifications": [
                {
                    "一级分类": "自适应与机器学习",
                    "二级分类": "感知算法融合",
                    "confidence": 0.52,
                    "reason": "关键词相关但证据有限",
                }
            ]
        }

        result = parse_classification_response(response, taxonomy)

        self.assertEqual(result.primary_second_level, "感知算法融合")
        self.assertEqual(result.review_status, "需人工复核")

    def test_leaves_primary_blank_when_below_minimum_confidence(self):
        taxonomy = [TaxonomyItem("自适应与机器学习", "感知算法融合")]
        response = {
            "classifications": [
                {
                    "一级分类": "自适应与机器学习",
                    "二级分类": "感知算法融合",
                    "confidence": 0.31,
                    "reason": "证据不足",
                }
            ]
        }

        result = parse_classification_response(response, taxonomy)

        self.assertEqual(result.primary_second_level, "")
        self.assertEqual(result.review_status, "需人工复核")


class ExcelExportTests(unittest.TestCase):
    def test_writes_expected_headers_and_row_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.xlsx"
            row = ClassificationExportRow(
                filename="CN001.pdf",
                patent_name="一种车辆控制方法。",
                source_path="D:/patents/CN001.pdf",
                summary_path="D:/patents/CN001.md",
                primary_first_level="自适应与机器学习",
                primary_second_level="底盘与智驾域的控制目标融合",
                primary_confidence=0.82,
                candidate_summary="自适应与机器学习 / 底盘与智驾域的控制目标融合 (0.82)",
                review_status="通过",
                reason="摘要提到控制目标融合",
                sections={"专利主题": "一种车辆控制方法。", "技术领域": "底盘控制。", "摘要全文": "全文"},
                metadata={"申请号": "CN202411111111", "申请日": "2024-01-02", "申请人": "示例公司"},
            )

            write_classification_workbook([row], output)

            wb = load_workbook(output)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            values = [cell.value for cell in ws[2]]
            by_header = dict(zip(headers, values))

        self.assertEqual(headers, EXPORT_HEADERS)
        for header in BIBLIOGRAPHIC_FIELD_NAMES:
            self.assertIn(header, headers)
        self.assertEqual(by_header["文件名"], "CN001.pdf")
        self.assertEqual(by_header["专利名称"], "一种车辆控制方法。")
        self.assertEqual(by_header["申请号"], "CN202411111111")
        self.assertEqual(by_header["申请日"], "2024-01-02")
        self.assertEqual(by_header["主一级分类"], "自适应与机器学习")
        self.assertEqual(by_header["主二级分类"], "底盘与智驾域的控制目标融合")
        self.assertEqual(by_header["专利主题"], "一种车辆控制方法。")
        self.assertEqual(by_header["技术领域"], "底盘控制。")


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, path):
        self.calls.append(path)
        return "原文"


class FakeSummarizer:
    def __init__(self):
        self.calls = []

    def summarize_document(self, filename, text):
        self.calls.append((filename, text))
        return f"# {filename}\n## 专利主题\n生成摘要\n## 技术领域\n底盘控制"


class FakeClassifierClient:
    def chat(self, _system, _user):
        return {
            "classifications": [
                {
                    "一级分类": "自适应与机器学习",
                    "二级分类": "感知算法融合",
                    "confidence": 0.88,
                    "reason": "摘要匹配",
                }
            ]
        }


class ClassificationPipelineTests(unittest.TestCase):
    def test_discovers_documents_even_when_markdown_summary_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "a.md").write_text("old", encoding="utf-8")
            (root / "b.docx").write_bytes(b"docx")

            found = list(discover_classification_documents(root, recursive=False))

        self.assertEqual([item.source.name for item in found], ["a.pdf", "b.docx"])
        self.assertEqual([item.output.name for item in found], ["a.md", "b.md"])

    def test_pipeline_reuses_existing_markdown_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "case.pdf"
            summary = root / "case.md"
            source.write_bytes(b"%PDF")
            summary.write_text("# case.pdf\n## 专利主题\n既有摘要", encoding="utf-8")
            extractor = FakeExtractor()
            summarizer = FakeSummarizer()
            pipeline = PatentClassificationPipeline(
                extractor,
                summarizer,
                FakeClassifierClient(),
                Taxonomy([TaxonomyItem("自适应与机器学习", "感知算法融合")]),
            )

            row = pipeline.process(source, summary)

        self.assertEqual(extractor.calls, [])
        self.assertEqual(summarizer.calls, [])
        self.assertEqual(row.patent_name, "既有摘要")
        self.assertEqual(row.primary_second_level, "感知算法融合")

    def test_pipeline_generates_missing_markdown_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "case.pdf"
            summary = root / "case.md"
            source.write_bytes(b"%PDF")
            extractor = FakeExtractor()
            summarizer = FakeSummarizer()
            pipeline = PatentClassificationPipeline(
                extractor,
                summarizer,
                FakeClassifierClient(),
                Taxonomy([TaxonomyItem("自适应与机器学习", "感知算法融合")]),
            )

            row = pipeline.process(source, summary)

            self.assertTrue(summary.exists())
            self.assertEqual(extractor.calls, [source])
            self.assertEqual(summarizer.calls, [("case.pdf", "原文")])
            self.assertEqual(row.patent_name, "生成摘要")

    def test_checkpoint_store_recovers_latest_successful_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "classifications.jsonl"
            row = ClassificationExportRow(
                filename="case.pdf",
                patent_name="摘要",
                source_path="D:/case.pdf",
                summary_path="D:/case.md",
                primary_first_level="自适应与机器学习",
                primary_second_level="感知算法融合",
                primary_confidence=0.88,
                candidate_summary="自适应与机器学习 / 感知算法融合 (0.88)",
                review_status="通过",
                reason="摘要匹配",
                sections={"专利主题": "摘要", "摘要全文": "全文"},
            )
            store = CheckpointStore(path)

            store.record_success(row)
            recovered = CheckpointStore(path).load_successes()

        self.assertEqual(recovered["D:/case.pdf"].primary_second_level, "感知算法融合")


class CliTests(unittest.TestCase):
    def test_parser_accepts_classify_command_arguments(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "classify",
                "--input",
                "D:/patents",
                "--classes",
                "class.xlsx",
                "--output",
                "result.xlsx",
                "--recursive",
                "--limit",
                "10",
                "--dry-run",
                "--refresh-summaries",
            ]
        )

        self.assertEqual(args.command, "classify")
        self.assertEqual(args.input, Path("D:/patents"))
        self.assertEqual(args.classes, Path("class.xlsx"))
        self.assertEqual(args.output, Path("result.xlsx"))
        self.assertTrue(args.recursive)
        self.assertEqual(args.limit, 10)
        self.assertTrue(args.dry_run)
        self.assertTrue(args.refresh_summaries)

    def test_parser_accepts_analyze_command_arguments(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "analyze",
                "--classification",
                "patent_classification.xlsx",
                "--output",
                "patent_trends.xlsx",
                "--api-rpm",
                "6",
                "--batch-size",
                "20",
                "--dry-run",
            ]
        )

        self.assertEqual(args.command, "analyze")
        self.assertEqual(args.classification, Path("patent_classification.xlsx"))
        self.assertEqual(args.output, Path("patent_trends.xlsx"))
        self.assertEqual(args.api_rpm, 6)
        self.assertEqual(args.batch_size, 20)
        self.assertTrue(args.dry_run)


if __name__ == "__main__":
    unittest.main()
