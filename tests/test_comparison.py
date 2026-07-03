import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from patent_summarizer.cli import build_parser, compare as run_compare
from patent_summarizer.comparison import (
    ComparisonAnalyzer,
    PortfolioDataset,
    build_label_comparisons,
    load_or_build_portfolio,
)
from patent_summarizer.comparison_excel_export import write_comparison_workbook
from patent_summarizer.excel_export import ClassificationExportRow, write_classification_workbook
from patent_summarizer.taxonomy import Taxonomy, TaxonomyItem
from patent_summarizer.trend_analysis import TrendPatentRecord


def make_classification_row(
    filename="CN202411111111.md",
    patent_name="车辆控制方法",
    first_level="车辆运动控制内核",
    second_level="横向融合控制",
    confidence=0.8,
    year="2024",
    keywords="- EPS\n- 摩擦补偿",
):
    return ClassificationExportRow(
        filename=filename,
        patent_name=patent_name,
        source_path=f"D:/patents/{filename}",
        summary_path=f"D:/patents/{Path(filename).stem}.md",
        primary_first_level=first_level,
        primary_second_level=second_level,
        primary_confidence=confidence,
        candidate_summary=f"{first_level} / {second_level} ({confidence:.2f})",
        review_status="通过",
        reason="摘要匹配",
        metadata={"申请号": Path(filename).stem, "申请日": f"{year}-01-02"},
        sections={
            "著录信息": f"- 申请号：{Path(filename).stem}\n- 申请日：{year}-01-02",
            "专利主题": patent_name,
            "背景问题": "标定复杂。",
            "核心方案": "通过分段控制优化转向。",
            "关键创新点": "引入摩擦补偿。",
            "应用场景": "EPS。",
            "潜在分类关键词": keywords,
            "摘要全文": "全文",
        },
    )


def make_record(
    filename="CN202411111111.md",
    patent_name="车辆控制方法",
    first_level="车辆运动控制内核",
    second_level="横向融合控制",
    confidence=0.8,
    year="2024",
    keywords="- EPS\n- 摩擦补偿",
):
    return TrendPatentRecord(
        filename=filename,
        patent_name=patent_name,
        source_path=f"D:/patents/{filename}",
        summary_path=f"D:/patents/{Path(filename).stem}.md",
        primary_first_level=first_level,
        primary_second_level=second_level,
        primary_confidence=confidence,
        candidate_summary=f"{first_level} / {second_level} ({confidence:.2f})",
        review_status="通过",
        reason="摘要匹配",
        metadata={"申请号": Path(filename).stem, "申请日": f"{year}-01-02"},
        sections={
            "背景问题": "标定复杂。",
            "核心方案": "通过分段控制优化转向。",
            "关键创新点": "引入摩擦补偿。",
            "应用场景": "EPS。",
            "潜在分类关键词": keywords,
            "摘要全文": "全文",
        },
        inferred_year=year,
    )


class FakeClassificationClient:
    def __init__(self):
        self.calls = []

    def chat(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return {
            "classifications": [
                {
                    "一级分类": "车辆运动控制内核",
                    "二级分类": "横向融合控制",
                    "confidence": 0.88,
                    "reason": "摘要匹配横向融合控制",
                }
            ]
        }


class FakeComparisonClient:
    def __init__(self):
        self.calls = []

    def chat(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return {
            "整体结论": "A 在横向融合控制有基础，但外部在冗余制动更集中。",
            "A布局概况": "A 覆盖横向融合控制。",
            "相比外部的优势": "A 在 EPS 方向占比更高。",
            "相比外部的短板": "A 缺少冗余制动布局。",
            "差异化判断": "A 有一定差异化。",
            "是否主流布局策略": "A 覆盖部分主流方向，但不完整。",
            "建议布局点": "1. 冗余制动控制；2. 横纵向协同安全降级。",
            "证据局限": "仅基于分类表和摘要。",
        }


class CompareCliTests(unittest.TestCase):
    def test_parser_accepts_compare_command_arguments(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "compare",
                "--target",
                "D:/A",
                "--target-label",
                "A",
                "--baseline",
                "B=D:/B",
                "--baseline",
                "D:/C",
                "--classes",
                "class.xlsx",
                "--output",
                "a_vs_external.xlsx",
                "--recursive",
                "--refresh-classification",
                "--dry-run",
            ]
        )

        self.assertEqual(args.command, "compare")
        self.assertEqual(args.target, Path("D:/A"))
        self.assertEqual(args.target_label, "A")
        self.assertEqual(args.baseline, ["B=D:/B", "D:/C"])
        self.assertEqual(args.classes, Path("class.xlsx"))
        self.assertEqual(args.output, Path("a_vs_external.xlsx"))
        self.assertTrue(args.recursive)
        self.assertTrue(args.refresh_classification)
        self.assertTrue(args.dry_run)

    def test_compare_stops_before_config_when_a_docx_summary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            baseline = root / "baseline"
            target.mkdir()
            baseline.mkdir()
            (target / "a.docx").write_bytes(b"docx")
            (baseline / "b.docx").write_bytes(b"docx")
            (baseline / "b.md").write_text("summary", encoding="utf-8")
            args = Namespace(
                target=target,
                target_label="A",
                baseline=[f"B={baseline}"],
                classes=root / "class.xlsx",
                output=root / "comparison.xlsx",
                recursive=True,
                refresh_classification=False,
                api_rpm=None,
                env_file=root / ".env",
                dry_run=False,
            )
            stdout = StringIO()

            with patch("patent_summarizer.cli.load_config") as load_config, redirect_stdout(stdout):
                exit_code = run_compare(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("a.md", stdout.getvalue())
        self.assertIn("批量总结", stdout.getvalue())
        load_config.assert_not_called()

    def test_compare_rejects_duplicate_portfolio_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            baseline = root / "baseline"
            target.mkdir()
            baseline.mkdir()
            for folder, stem in [(target, "a"), (baseline, "b")]:
                (folder / f"{stem}.docx").write_bytes(b"docx")
                (folder / f"{stem}.md").write_text("summary", encoding="utf-8")
            args = Namespace(
                target=target,
                target_label="同名组合",
                baseline=[f"同名组合={baseline}"],
                classes=root / "class.xlsx",
                output=root / "comparison.xlsx",
                recursive=True,
                refresh_classification=False,
                api_rpm=None,
                env_file=root / ".env",
                dry_run=False,
            )
            stdout = StringIO()

            with patch("patent_summarizer.cli.load_config") as load_config, redirect_stdout(stdout):
                exit_code = run_compare(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("labels must be unique", stdout.getvalue())
        load_config.assert_not_called()


class PortfolioLoadingTests(unittest.TestCase):
    def test_load_or_build_portfolio_prefers_existing_classification_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            workbook = folder / "patent_classification.xlsx"
            write_classification_workbook([make_classification_row()], workbook)
            client = FakeClassificationClient()

            portfolio = load_or_build_portfolio(
                folder,
                label="B",
                client=client,
                taxonomy=None,
                recursive=False,
                refresh_classification=False,
            )

        self.assertEqual(portfolio.label, "B")
        self.assertEqual(portfolio.classification_source, "existing")
        self.assertEqual(len(portfolio.records), 1)
        self.assertEqual(client.calls, [])

    def test_load_or_build_portfolio_classifies_markdown_when_workbook_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "case.md").write_text(
                """# case
## 著录信息
- 申请号：CN202411111111
- 申请日：2024-01-02
## 专利主题
车辆控制方法
## 技术领域
底盘控制
## 背景问题
标定复杂。
## 核心方案
通过分段控制优化转向。
## 关键创新点
引入摩擦补偿。
## 应用场景
EPS。
## 潜在分类关键词
- EPS
""",
                encoding="utf-8",
            )
            client = FakeClassificationClient()
            taxonomy = Taxonomy([TaxonomyItem("车辆运动控制内核", "横向融合控制")])

            portfolio = load_or_build_portfolio(
                folder,
                label="A",
                client=client,
                taxonomy=taxonomy,
                recursive=False,
                refresh_classification=False,
            )

            workbook = folder / "patent_classification.xlsx"
            wb = load_workbook(workbook)
            ws = wb["分类结果"]

        self.assertEqual(portfolio.classification_source, "generated")
        self.assertEqual(len(portfolio.records), 1)
        self.assertEqual(portfolio.records[0].primary_second_level, "横向融合控制")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(ws["A2"].value, "case.md")


class ComparisonAggregationTests(unittest.TestCase):
    def test_build_label_comparisons_marks_gaps_strengths_and_differentiation(self):
        target = PortfolioDataset(
            label="A",
            root=Path("D:/A"),
            classification_path=Path("D:/A/patent_classification.xlsx"),
            classification_source="existing",
            records=[
                make_record(filename="a1.md", second_level="横向融合控制"),
                make_record(filename="a2.md", second_level="横向融合控制"),
                make_record(filename="a3.md", second_level="线控转向冗余", keywords="- SBW"),
            ],
        )
        baseline = PortfolioDataset(
            label="B",
            root=Path("D:/B"),
            classification_path=Path("D:/B/patent_classification.xlsx"),
            classification_source="existing",
            records=[
                make_record(filename="b1.md", second_level="横向融合控制"),
                make_record(filename="b2.md", second_level="冗余制动控制", keywords="- EMB"),
                make_record(filename="b3.md", second_level="冗余制动控制", keywords="- EMB"),
                make_record(filename="b4.md", second_level="冗余制动控制", keywords="- EMB"),
            ],
        )

        comparisons = build_label_comparisons(target, baseline)
        by_label = {item.second_level: item for item in comparisons}

        self.assertEqual(by_label["横向融合控制"].category, "A优势")
        self.assertEqual(by_label["线控转向冗余"].category, "A差异化")
        self.assertEqual(by_label["冗余制动控制"].category, "A短板")
        self.assertGreater(by_label["冗余制动控制"].priority_score, by_label["线控转向冗余"].priority_score)

    def test_comparison_analyzer_creates_individual_and_combined_reports(self):
        client = FakeComparisonClient()
        analyzer = ComparisonAnalyzer(client)
        target = PortfolioDataset(
            label="A",
            root=Path("D:/A"),
            classification_path=Path("D:/A/patent_classification.xlsx"),
            classification_source="existing",
            records=[make_record(filename="a1.md")],
        )
        baselines = [
            PortfolioDataset(
                label="B",
                root=Path("D:/B"),
                classification_path=Path("D:/B/patent_classification.xlsx"),
                classification_source="existing",
                records=[make_record(filename="b1.md", second_level="冗余制动控制")],
            ),
            PortfolioDataset(
                label="C",
                root=Path("D:/C"),
                classification_path=Path("D:/C/patent_classification.xlsx"),
                classification_source="existing",
                records=[make_record(filename="c1.md", second_level="横向融合控制")],
            ),
        ]

        result = analyzer.analyze(target, baselines)

        self.assertEqual([report.baseline_label for report in result.reports], ["B", "C", "外部总体"])
        self.assertEqual(len(client.calls), 3)
        self.assertIn("A 在横向融合控制", result.reports[0].overall_conclusion)


class ComparisonExcelExportTests(unittest.TestCase):
    def test_writes_comparison_workbook(self):
        client = FakeComparisonClient()
        analyzer = ComparisonAnalyzer(client)
        target = PortfolioDataset(
            label="A",
            root=Path("D:/A"),
            classification_path=Path("D:/A/patent_classification.xlsx"),
            classification_source="existing",
            records=[make_record(filename="a1.md")],
        )
        baselines = [
            PortfolioDataset(
                label="B",
                root=Path("D:/B"),
                classification_path=Path("D:/B/patent_classification.xlsx"),
                classification_source="existing",
                records=[make_record(filename="b1.md", second_level="冗余制动控制")],
            )
        ]
        result = analyzer.analyze(target, baselines)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "comparison.xlsx"
            write_comparison_workbook(result, output)
            wb = load_workbook(output)

        self.assertEqual(wb.sheetnames, ["对比总览", "标签对比", "布局建议", "年度对比", "专利明细", "数据来源"])
        self.assertEqual(wb["对比总览"]["A2"].value, "A")
        self.assertEqual(wb["对比总览"]["B2"].value, "B")
        self.assertEqual(wb["布局建议"]["A2"].value, "A vs B")


if __name__ == "__main__":
    unittest.main()
