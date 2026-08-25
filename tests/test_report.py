import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from openpyxl import Workbook

from patent_summarizer.cli import build_parser, report as run_report
from patent_summarizer.comparison import ComparisonResult, PortfolioDataset
from patent_summarizer.report_analysis import (
    CompetitorAnalysis,
    ExecutiveSummary,
    LayoutPoint,
    PatentLayoutReport,
    PortfolioOverviewAnalysis,
    PortfolioPlan,
    ReportCheckpointingClient,
    ReportSynthesisAnalyzer,
    StrategyItem,
    TargetAnalysis,
    TargetStrategyItem,
    TrendForecast,
    combine_portfolios,
    parse_competitor_analysis,
    parse_target_analysis,
)
from patent_summarizer.report_docx import write_patent_layout_report
from patent_summarizer.trend_analysis import TrendAnalysisResult, TrendPatentRecord


def make_record(filename="a.md", company="长安汽车"):
    return TrendPatentRecord(
        filename=filename,
        patent_name=f"{company}底盘控制方法",
        source_path=f"D:/{company}/{filename}",
        summary_path=f"D:/{company}/{filename}",
        primary_first_level="车辆运动控制",
        primary_second_level="横向控制",
        primary_confidence=0.9,
        candidate_summary="车辆运动控制 / 横向控制 (0.90)",
        review_status="通过",
        reason="摘要匹配",
        metadata={"申请号": Path(filename).stem, "申请日": "2024-01-01"},
        sections={"潜在分类关键词": "- EPS\n- 横向控制"},
        inferred_year="2024",
    )


def make_portfolio(label, root, filename):
    return PortfolioDataset(
        label=label,
        root=Path(root),
        classification_path=Path(root) / "patent_classification.xlsx",
        classification_source="existing",
        records=[make_record(filename, label)],
    )


def make_strategy_payload(companies):
    return {
        "策略名称": "持续强化",
        "核心特征": "连续布局",
        "适用企业": companies,
        "重点技术分类": ["横向控制"],
        "统计及连续性证据": "2024年1件",
        "代表性专利": ["b.md"],
        "组合中的作用": "形成重点方向",
    }


def make_competitor_payload(companies):
    return {
        "共性布局策略": [make_strategy_payload(companies)],
        "企业差异化策略": [],
        "竞争对手趋势研判": [],
        "未来竞争焦点": "横向控制",
        "证据局限": "仅基于上传样本",
    }


def make_overview_payload(label, summary=None):
    return {
        "对象": label,
        "整体布局现状": summary or f"{label}在当前样本中形成以横向控制为主的专利布局。",
    }


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def chat(self, system_prompt, user_prompt):
        self.calls += 1
        return self.response


class SequencedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


class ReportCliTests(unittest.TestCase):
    def test_parser_accepts_report_arguments(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "report",
                "--target",
                "D:/长安",
                "--target-label",
                "长安汽车",
                "--baseline",
                "理想汽车=D:/理想",
                "--classes",
                "class.xlsx",
                "--template",
                "template.docx",
                "--domain",
                "工业机器人",
                "--domain-context",
                "协作机器人末端执行与运动控制",
                "--output",
                "report.docx",
                "--recursive",
                "--refresh-classification",
                "--batch-size",
                "20",
                "--overwrite",
                "--dry-run",
            ]
        )

        self.assertEqual(args.command, "report")
        self.assertEqual(args.target_label, "长安汽车")
        self.assertEqual(args.baseline, ["理想汽车=D:/理想"])
        self.assertEqual(args.classes, Path("class.xlsx"))
        self.assertEqual(args.template, Path("template.docx"))
        self.assertEqual(args.domain, "工业机器人")
        self.assertEqual(args.domain_context, "协作机器人末端执行与运动控制")
        self.assertEqual(args.output, Path("report.docx"))
        self.assertEqual(args.batch_size, 20)
        self.assertTrue(args.overwrite)
        self.assertTrue(args.dry_run)

    def test_report_stops_before_config_when_a_portfolio_has_no_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            baseline = root / "baseline"
            target.mkdir()
            baseline.mkdir()
            (target / "a.docx").write_bytes(b"docx")
            (target / "a.md").write_text("summary", encoding="utf-8")
            (baseline / "b.md").write_text("summary", encoding="utf-8")
            classes = root / "class.xlsx"
            classes.touch()
            template = root / "template.docx"
            template.touch()
            args = Namespace(
                target=target,
                target_label="A",
                domain="工业机器人",
                domain_context="",
                baseline=[f"B={baseline}"],
                classes=classes,
                template=template,
                output=root / "report.docx",
                recursive=True,
                refresh_classification=False,
                batch_size=40,
                api_rpm=None,
                env_file=root / ".env",
                overwrite=False,
                dry_run=False,
            )
            stdout = StringIO()

            with patch("patent_summarizer.cli.load_config") as load_config, redirect_stdout(stdout):
                exit_code = run_report(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("未发现 DOCX", stdout.getvalue())
        load_config.assert_not_called()


class ReportCheckpointTests(unittest.TestCase):
    def test_checkpoint_client_reuses_response_for_identical_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            delegate = FakeClient({"value": "cached"})
            client = ReportCheckpointingClient(delegate, Path(tmp) / "report.jsonl")

            first = client.chat("system", "user")
            second = client.chat("system", "user")

        self.assertEqual(first, {"value": "cached"})
        self.assertEqual(second, first)
        self.assertEqual(delegate.calls, 1)

    def test_invalid_response_is_repaired_and_replaced_in_cache(self):
        bad_response = json.dumps(make_competitor_payload([]), ensure_ascii=False)
        good_response = json.dumps(make_competitor_payload(["理想汽车"]), ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.jsonl"
            delegate = SequencedClient([bad_response, good_response])
            analyzer = ReportSynthesisAnalyzer(ReportCheckpointingClient(delegate, path))

            result = analyzer._validated_chat(
                "竞争对手布局策略",
                "请生成竞争对手分析 JSON",
                parse_competitor_analysis,
            )

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(["理想汽车"], result.common_strategies[0].companies)
        self.assertEqual(2, len(delegate.calls))
        invalid_records = [record for record in records if record.get("invalid") is True]
        self.assertEqual(1, len(invalid_records))
        self.assertEqual(bad_response, invalid_records[0]["invalid_response"])


class ReportAnalysisTests(unittest.TestCase):
    def test_combines_portfolios_without_losing_source_records(self):
        target = make_portfolio("长安汽车", "D:/长安", "a.md")
        baseline = make_portfolio("理想汽车", "D:/理想", "b.md")

        combined = combine_portfolios("全部样本", [target, baseline])

        self.assertEqual(combined.label, "全部样本")
        self.assertEqual([record.filename for record in combined.records], ["a.md", "b.md"])
        self.assertEqual(combined.classification_source, "combined")

    def test_synthesis_analyzer_runs_three_strict_analysis_stages(self):
        competitor_payload = {
            "共性布局策略": [
                {
                    "策略名称": "持续强化",
                    "核心特征": "连续布局",
                    "适用企业": ["理想汽车"],
                    "重点技术分类": ["横向控制"],
                    "统计及连续性证据": "2024年1件",
                    "代表性专利": ["b.md"],
                    "组合中的作用": "形成重点方向",
                }
            ],
            "企业差异化策略": [],
            "竞争对手趋势研判": [
                {
                    "企业": "理想汽车",
                    "可能强化方向": "横向控制",
                    "时间序列信号": "2024年出现",
                    "关键词及技术路线依据": "EPS",
                    "代表性专利": ["b.md"],
                    "证据限制": "样本有限",
                }
            ],
            "未来竞争焦点": "横向控制",
            "证据局限": "仅基于上传样本",
        }
        target_payload = {
            "目标总体策略": [
                {
                    "策略名称": "优势巩固",
                    "目标": "持续强化",
                    "重点技术领域": ["横向控制"],
                    "优先顺序": "高",
                    "建议理由": "已有专利基础",
                    "关联建议点": ["横向协同控制"],
                }
            ],
            "待布局建议点": [
                {
                    "名称": "横向协同控制",
                    "一级分类": "车辆运动控制",
                    "二级分类": "横向控制",
                    "主要技术问题": "多系统协同",
                    "竞争对手布局": "理想已有布局",
                    "目标现有布局及缺口": "覆盖不足",
                    "优先级": "高",
                    "建议理由": "竞品连续申请",
                    "证据专利": ["a.md", "b.md"],
                }
            ],
            "专利组合策略": [
                {
                    "对应布局点": "横向协同控制",
                    "核心专利": "协同控制方法",
                    "外围专利分支": ["状态估计"],
                    "技术环节覆盖": ["感知", "决策"],
                    "专利客体协同": ["方法", "系统"],
                    "系列化或补充布局": "形成系列申请",
                    "跨布局点协同": "与制动协同",
                    "证据依据": "a.md、b.md",
                }
            ],
            "证据局限": "仅基于上传样本",
        }
        executive_payload = {
            "管理层结论": ["结论一", "结论二", "结论三"],
            "总体证据局限": "仅基于上传样本",
        }
        client = SequencedClient(
            [
                make_overview_payload("全部样本"),
                make_overview_payload("长安汽车"),
                make_overview_payload("理想汽车"),
                competitor_payload,
                target_payload,
                executive_payload,
            ]
        )
        analyzer = ReportSynthesisAnalyzer(client)
        target = make_portfolio("长安汽车", "D:/长安", "a.md")
        baseline = make_portfolio("理想汽车", "D:/理想", "b.md")
        comparison = ComparisonResult(target, [baseline], None, [])
        stages = []

        overviews, competitor, target_result, executive = analyzer.analyze(
            target,
            [baseline],
            {"长安汽车": TrendAnalysisResult([], target.records), "理想汽车": TrendAnalysisResult([], baseline.records)},
            comparison,
            domain="工业机器人",
            domain_context="协作机器人末端执行与运动控制",
            on_stage=lambda detail, started: stages.append((detail, started)),
        )

        self.assertEqual(len(client.calls), 6)
        self.assertEqual([started for _detail, started in stages], [True, False] * 6)
        self.assertIn("横向控制", overviews.industry_summary)
        self.assertEqual(competitor.forecasts[0].company, "理想汽车")
        self.assertEqual(target_result.layout_points[0].name, "横向协同控制")
        self.assertEqual(len(executive.conclusions), 3)
        self.assertIn("分析领域：工业机器人", client.calls[0][0])
        self.assertIn("目标企业：长安汽车", client.calls[0][0])


class ReportDryRunTests(unittest.TestCase):
    def test_dry_run_checks_markdown_inputs_without_creating_classifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "长安"
            baseline = root / "理想"
            target.mkdir()
            baseline.mkdir()
            (target / "a.docx").write_bytes(b"docx")
            (target / "a.md").write_text("# a", encoding="utf-8")
            (baseline / "b.docx").write_bytes(b"docx")
            (baseline / "b.md").write_text("# b", encoding="utf-8")
            classes = root / "class.xlsx"
            classes.touch()
            template = root / "template.docx"
            document = Document()
            document.add_heading("封面", 0)
            document.add_heading("目录", 1)
            document.save(template)
            output = root / "report.docx"
            args = Namespace(
                target=target,
                target_label="长安汽车",
                domain="工业机器人",
                domain_context="",
                baseline=[f"理想汽车={baseline}"],
                classes=classes,
                template=template,
                output=output,
                recursive=False,
                refresh_classification=False,
                batch_size=40,
                api_rpm=None,
                env_file=root / ".env",
                overwrite=False,
                dry_run=True,
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = run_report(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("classification=needs-md-classification", stdout.getvalue())
        self.assertFalse((target / "patent_classification.xlsx").exists())
        self.assertFalse(output.exists())


class ReportCommandTests(unittest.TestCase):
    def test_report_command_generates_docx_and_checkpoint(self):
        competitor_payload = {
            "共性布局策略": [{"策略名称": "持续强化", "核心特征": "连续布局", "适用企业": ["理想汽车"], "重点技术分类": ["横向控制"], "统计及连续性证据": "2024年1件", "代表性专利": ["b.md"], "组合中的作用": "形成重点方向"}],
            "企业差异化策略": [],
            "竞争对手趋势研判": [{"企业": "理想汽车", "可能强化方向": "横向控制", "时间序列信号": "2024年出现", "关键词及技术路线依据": "EPS", "代表性专利": ["b.md"], "证据限制": "样本有限"}],
            "未来竞争焦点": "横向控制",
            "证据局限": "仅基于上传样本",
        }
        target_payload = {
            "目标总体策略": [{"策略名称": "优势巩固", "目标": "持续强化", "重点技术领域": ["横向控制"], "优先顺序": "高", "建议理由": "已有基础", "关联建议点": ["横向协同控制"]}],
            "待布局建议点": [{"名称": "横向协同控制", "一级分类": "车辆运动控制", "二级分类": "横向控制", "主要技术问题": "多系统协同", "竞争对手布局": "理想已有布局", "目标现有布局及缺口": "覆盖不足", "优先级": "高", "建议理由": "竞品连续申请", "证据专利": ["a.md", "b.md"]}],
            "专利组合策略": [{"对应布局点": "横向协同控制", "核心专利": "协同控制方法", "外围专利分支": ["状态估计"], "技术环节覆盖": ["感知", "决策"], "专利客体协同": ["方法", "系统"], "系列化或补充布局": "系列申请", "跨布局点协同": "与制动协同", "证据依据": "a.md、b.md"}],
            "证据局限": "仅基于上传样本",
        }
        executive_payload = {"管理层结论": ["结论一", "结论二", "结论三"], "总体证据局限": "仅基于上传样本"}
        technology_payload = {
            "典型技术补充": [
                {"研判序号": 1, "典型技术": ["电机直驱制动", "制动力协调控制"]}
            ]
        }
        chapter_summary_payload = {
            "第四章总结": ["第四章结论一", "第四章结论二"],
            "第五章总结": ["第五章结论一", "第五章结论二"],
            "第六章总结": ["第六章结论一", "第六章结论二"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_root = root / "长安"
            baseline_root = root / "理想"
            target_root.mkdir()
            baseline_root.mkdir()
            (target_root / "a.docx").write_bytes(b"docx")
            (target_root / "a.md").write_text("# a", encoding="utf-8")
            (baseline_root / "b.docx").write_bytes(b"docx")
            (baseline_root / "b.md").write_text("# b", encoding="utf-8")
            target = make_portfolio("长安汽车", target_root, "a.md")
            baseline = make_portfolio("理想汽车", baseline_root, "b.md")
            classes = root / "class.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["一级分类", "二级分类"])
            sheet.append(["车辆运动控制", "横向控制"])
            workbook.save(classes)
            template = root / "template.docx"
            document = Document()
            document.add_heading("汽车底盘领域专利布局分析报告", 0)
            document.add_paragraph("DOCX 内容与章节模板", style="Subtitle")
            document.add_heading("目录与章节逻辑", 1)
            document.save(template)
            output = root / "report.docx"
            args = Namespace(
                target=target_root,
                target_label="长安汽车",
                domain="工业机器人",
                domain_context="协作机器人末端执行与运动控制",
                baseline=[f"理想汽车={baseline_root}"],
                classes=classes,
                template=template,
                output=output,
                recursive=False,
                refresh_classification=False,
                batch_size=40,
                api_rpm=None,
                env_file=root / ".env",
                overwrite=False,
                dry_run=False,
            )
            fake_client = SequencedClient(
                [
                    make_overview_payload("全部样本"),
                    make_overview_payload("长安汽车"),
                    make_overview_payload("理想汽车"),
                    competitor_payload,
                    target_payload,
                    executive_payload,
                    technology_payload,
                    chapter_summary_payload,
                ]
            )

            with (
                patch("patent_summarizer.cli.load_config", return_value=SimpleNamespace(base_url="url", api_key="key", model="model", api_rpm=10, max_retries=1, ocr_enabled=False)),
                patch("patent_summarizer.cli.RateLimitedOpenAIClient", return_value=fake_client),
                patch("patent_summarizer.cli.load_or_build_portfolio", side_effect=[target, baseline]),
                patch("patent_summarizer.cli.TrendAnalyzer.analyze", side_effect=lambda records, checkpoint=None: TrendAnalysisResult([], list(records))),
                patch("patent_summarizer.cli.ComparisonAnalyzer.analyze", return_value=ComparisonResult(target, [baseline], None, [])),
            ):
                exit_code = run_report(args)

            rendered = Document(output)
            text = "\n".join(paragraph.text for paragraph in rendered.paragraphs)
            checkpoint = output.parent / ".patent_summary_logs" / "report.report.jsonl"
            checkpoint_exists = checkpoint.exists()
            appendix_exists = output.with_name("report_附录.docx").exists()

        self.assertEqual(exit_code, 0)
        self.assertIn("横向协同控制", text)
        self.assertTrue(checkpoint_exists)
        self.assertTrue(appendix_exists)
        self.assertEqual(len(fake_client.calls), 8)


class ReportDocumentationTests(unittest.TestCase):
    def test_project_declares_docx_dependency_and_readme_workflow(self):
        root = Path(__file__).resolve().parents[1]
        project = (root / "pyproject.toml").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn("python-docx", project)
        self.assertIn("## 生成 DOCX 专利布局报告", readme)
        self.assertIn("report --dry-run", readme)
        self.assertIn("样本范围", readme)


class ReportResponseTests(unittest.TestCase):
    def test_competitor_response_normalizes_company_string_and_filters_target(self):
        payload = make_competitor_payload("目标企业、理想、蔚来汽车")

        result = parse_competitor_analysis(
            payload,
            allowed_companies={"理想汽车", "蔚来汽车"},
        )

        self.assertEqual(
            ["理想汽车", "蔚来汽车"],
            result.common_strategies[0].companies,
        )

    def test_competitor_response_drops_only_out_of_scope_differentiated_strategy(self):
        payload = make_competitor_payload(["理想汽车"])
        payload["共性布局策略"] = []
        payload["企业差异化策略"] = [
            make_strategy_payload(["理想汽车"]),
            make_strategy_payload(["目标企业"]),
        ]

        result = parse_competitor_analysis(
            payload,
            allowed_companies={"理想汽车", "蔚来汽车"},
        )

        self.assertEqual(1, len(result.differentiated_strategies))
        self.assertEqual(["理想汽车"], result.differentiated_strategies[0].companies)

    def test_competitor_response_requires_all_top_level_fields(self):
        with self.assertRaisesRegex(ValueError, "竞争对手趋势研判"):
            parse_competitor_analysis(
                {
                    "共性布局策略": [],
                    "企业差异化策略": [],
                    "未来竞争焦点": "线控底盘",
                    "证据局限": "样本有限",
                }
            )

    def test_target_response_rejects_forbidden_feasibility_judgement(self):
        payload = {
            "目标总体策略": [
                {
                    "策略名称": "优势巩固",
                    "目标": "持续强化",
                    "重点技术领域": ["横向控制"],
                    "优先顺序": "高",
                    "建议理由": "现有专利集中",
                    "关联建议点": ["横向协同控制"],
                }
            ],
            "待布局建议点": [
                {
                    "名称": "横向协同控制",
                    "一级分类": "车辆运动控制",
                    "二级分类": "横向控制",
                    "主要技术问题": "技术可行性判断",
                    "竞争对手布局": "竞品持续申请",
                    "目标现有布局及缺口": "已有基础但覆盖不足",
                    "优先级": "高",
                    "建议理由": "竞品占比较高",
                    "证据专利": ["a.md"],
                }
            ],
            "专利组合策略": [
                {
                    "对应布局点": "横向协同控制",
                    "核心专利": "控制方法",
                    "外围专利分支": ["状态估计"],
                    "技术环节覆盖": ["感知", "决策"],
                    "专利客体协同": ["方法", "系统"],
                    "系列化或补充布局": "连续申请",
                    "跨布局点协同": "与制动协同",
                    "证据依据": "a.md",
                }
            ],
            "证据局限": "样本有限",
        }

        with self.assertRaisesRegex(ValueError, "技术可行性判断"):
            parse_target_analysis(payload)


class ReportDocxTests(unittest.TestCase):
    def test_writer_replaces_template_instructions_with_formal_report(self):
        target = make_portfolio("示例公司", "D:/目标", "a.md")
        baseline = make_portfolio("同行企业", "D:/竞品", "b.md")
        industry = PortfolioDataset(
            label="全部样本",
            root=Path("D:/samples"),
            classification_path=Path(),
            classification_source="combined",
            records=target.records + baseline.records,
        )
        competitor_analysis = CompetitorAnalysis(
            common_strategies=[
                StrategyItem("持续强化", "连续布局", ["理想汽车"], ["横向控制"], "2024年1件", ["b.md"], "形成重点方向")
            ],
            differentiated_strategies=[],
            forecasts=[TrendForecast("理想汽车", "横向控制", "2024年出现", "EPS", ["b.md"], "样本有限")],
            future_focus="横向控制竞争可能增强",
            evidence_limits="仅基于上传样本",
        )
        target_analysis = TargetAnalysis(
            strategies=[TargetStrategyItem("优势巩固", "持续强化", ["横向控制"], "高", "现有基础", ["横向协同控制"])],
            layout_points=[LayoutPoint("横向协同控制", "车辆运动控制", "横向控制", "多系统协同", "理想已有布局", "长安覆盖不足", "高", "竞品持续申请", ["a.md", "b.md"])],
            portfolio_plans=[PortfolioPlan("横向协同控制", "协同控制方法", ["状态估计"], ["感知", "决策"], ["方法", "系统"], "形成系列申请", "与制动方向协同", "a.md、b.md")],
            evidence_limits="仅基于上传样本",
        )
        report = PatentLayoutReport(
            target=target,
            baselines=[baseline],
            industry=industry,
            trends={
                "示例公司": TrendAnalysisResult(reports=[], records=target.records),
                "同行企业": TrendAnalysisResult(reports=[], records=baseline.records),
                "全部样本": TrendAnalysisResult(reports=[], records=industry.records),
            },
            comparison=ComparisonResult(target, [baseline], None, []),
            portfolio_overviews=PortfolioOverviewAnalysis(
                industry_summary="全部样本主要集中在横向控制。",
                company_summaries={"示例公司": "示例公司主要集中在横向控制。", "同行企业": "同行企业主要集中在横向控制。"},
            ),
            competitor_analysis=competitor_analysis,
            target_analysis=target_analysis,
            executive_summary=ExecutiveSummary(["样本主要集中在横向控制。"], "仅基于上传样本"),
            taxonomy=[("车辆运动控制", "横向控制")],
            domain="工业机器人",
            domain_context="协作机器人末端执行与运动控制",
        )

        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template.docx"
            output = Path(tmp) / "report.docx"
            document = Document()
            document.add_heading("{{分析领域}}专利布局分析报告", 0)
            document.add_paragraph("{{目标企业}}与{{竞争对手}}专利组合分析", style="Subtitle")
            document.add_heading("目录与章节逻辑", 1)
            document.add_paragraph("这是应被删除的模板说明。")
            document.save(template)

            write_patent_layout_report(report, template, output)
            rendered = Document(output)
            text = "\n".join(p.text for p in rendered.paragraphs)
            final_body_element = list(rendered._element.body)[-2]

        self.assertIn("工业机器人专利布局分析报告", text)
        self.assertIn("示例公司与同行企业专利组合分析", text)
        self.assertIn("一、样本范围内行业整体布局现状", text)
        self.assertIn("2.1 同行企业布局现状", text)
        self.assertIn("横向协同控制", text)
        self.assertNotIn("这是应被删除的模板说明", text)
        self.assertNotIn("汽车底盘", text)
        self.assertNotIn("理想汽车", text)
        self.assertTrue(final_body_element.tag.endswith("}p"))
        self.assertTrue(rendered.paragraphs[-1].text.strip(), "report must not end with a trailing blank paragraph")


if __name__ == "__main__":
    unittest.main()
