import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_portable
from docx import Document
from docx.oxml.ns import qn

from patent_summarizer.comparison import ComparisonResult, PortfolioDataset
from patent_summarizer.gui import (
    COPYRIGHT_TEXT,
    DESKTOP_EDITORIAL_PALETTE,
    MODE_ANALYZE,
    MODE_CLASSIFY,
    MODE_COMPARE,
    MODE_REPORT,
    MODE_SUMMARIZE,
    MODERN_APPEARANCE_MODE,
    MODERN_COLOR_THEME,
    MODE_OPTIONS,
    PortfolioInput,
    build_cli_argv,
    build_portfolio_cli_argv,
    default_output_path,
    mode_from_label,
    mode_button_style,
    progress_display,
)
from patent_summarizer.progress import ProgressUpdate
from patent_summarizer.report_analysis import (
    CompetitorAnalysis,
    ExecutiveSummary,
    PatentLayoutReport,
    PortfolioOverviewAnalysis,
    ReportSynthesisAnalyzer,
    TargetAnalysis,
    parse_portfolio_overview,
)
from patent_summarizer.report_docx import (
    REPORT_FONT,
    _apply_report_font,
    _build_report_body,
    _iter_paragraphs,
    _trend_sections,
)
from patent_summarizer.trend_analysis import TrendAnalysisResult, TrendGroupStats, TrendReport


class GuiCommandTests(unittest.TestCase):
    def test_copyright_text_uses_approved_owner_and_year(self):
        self.assertEqual(
            COPYRIGHT_TEXT,
            "© 2026 平台及模组开发部 · 线控底盘所 版权所有",
        )

    def test_builds_summarize_command_for_docx_only_portable_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            input_dir = Path(tmp) / "patents"

            argv = build_cli_argv(MODE_SUMMARIZE, input_dir, app_dir)

        self.assertEqual(
            argv,
            [
                "run",
                "--input",
                str(input_dir),
                "--recursive",
                "--file-type",
                "docx",
                "--env-file",
                str(app_dir / ".env"),
            ],
        )

    def test_builds_classify_command_with_bundled_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            input_dir = Path(tmp) / "patents"

            argv = build_cli_argv(MODE_CLASSIFY, input_dir, app_dir)

        self.assertEqual(
            argv,
            [
                "classify",
                "--input",
                str(input_dir),
                "--classes",
                str(app_dir / "class.xlsx"),
                "--output",
                str(input_dir / "patent_classification.xlsx"),
                "--recursive",
                "--file-type",
                "docx",
                "--env-file",
                str(app_dir / ".env"),
            ],
        )

    def test_builds_analyze_command_from_default_classification_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            input_dir = Path(tmp) / "patents"

            argv = build_cli_argv(MODE_ANALYZE, input_dir, app_dir)

        self.assertEqual(
            argv,
            [
                "analyze",
                "--classification",
                str(input_dir / "patent_classification.xlsx"),
                "--output",
                str(input_dir / "patent_trends.xlsx"),
                "--env-file",
                str(app_dir / ".env"),
            ],
        )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cli_argv("unknown", Path("D:/patents"), Path("D:/app"))

    def test_segmented_mode_options_map_labels_to_cli_modes(self):
        self.assertEqual(MODE_OPTIONS, ["批量总结", "分类", "趋势分析", "对比", "报告"])
        self.assertEqual(mode_from_label("批量总结"), MODE_SUMMARIZE)
        self.assertEqual(mode_from_label("分类"), MODE_CLASSIFY)
        self.assertEqual(mode_from_label("趋势分析"), MODE_ANALYZE)
        self.assertEqual(mode_from_label("对比"), MODE_COMPARE)
        self.assertEqual(mode_from_label("报告"), MODE_REPORT)

        with self.assertRaises(ValueError):
            mode_from_label("未知")

    def test_builds_compare_command_for_multiple_portfolios(self):
        app_dir = Path("D:/portable")
        target = PortfolioInput("长安汽车", Path("D:/patents/长安"))
        baselines = [
            PortfolioInput("理想汽车", Path("D:/patents/理想")),
            PortfolioInput("比亚迪", Path("D:/patents/比亚迪")),
        ]
        output = Path("D:/out/compare.xlsx")

        argv = build_portfolio_cli_argv(MODE_COMPARE, target, baselines, output, app_dir)

        self.assertEqual(
            argv,
            [
                "compare", "--target", str(target.root), "--target-label", "长安汽车",
                "--baseline", f"理想汽车={baselines[0].root}",
                "--baseline", f"比亚迪={baselines[1].root}",
                "--classes", str(app_dir / "class.xlsx"),
                "--output", str(output), "--recursive", "--env-file", str(app_dir / ".env"),
            ],
        )

    def test_builds_report_command_with_bundled_template_and_overwrite(self):
        app_dir = Path("D:/portable")
        target = PortfolioInput("长安汽车", Path("D:/patents/长安"))
        baseline = PortfolioInput("理想汽车", Path("D:/patents/理想"))
        output = Path("D:/out/report.docx")

        argv = build_portfolio_cli_argv(MODE_REPORT, target, [baseline], output, app_dir, overwrite=True)

        self.assertEqual(argv[0], "report")
        self.assertIn(str(app_dir / "汽车底盘领域专利布局分析报告模板.docx"), argv)
        self.assertIn("--overwrite", argv)

    def test_default_output_uses_safe_target_label(self):
        root = Path("D:/patents/target")

        compare_output = default_output_path(MODE_COMPARE, root, '长安:汽车?')
        report_output = default_output_path(MODE_REPORT, root, '长安:汽车?')

        self.assertEqual(compare_output, root / "长安_汽车__专利组合对比.xlsx")
        self.assertEqual(report_output, root / "长安_汽车__专利布局分析报告.docx")

    def test_modern_theme_uses_customtkinter_light_mode_without_blue_theme(self):
        self.assertEqual(MODERN_APPEARANCE_MODE, "light")
        self.assertEqual(MODERN_COLOR_THEME, "green")

    def test_desktop_editorial_palette_matches_design_tokens(self):
        self.assertEqual(DESKTOP_EDITORIAL_PALETTE["primary"], "#CC785C")
        self.assertEqual(DESKTOP_EDITORIAL_PALETTE["canvas"], "#FAF9F5")
        self.assertEqual(DESKTOP_EDITORIAL_PALETTE["dark"], "#181715")
        self.assertNotIn("#6750A4", DESKTOP_EDITORIAL_PALETTE.values())
        self.assertNotIn("#7D5260", DESKTOP_EDITORIAL_PALETTE.values())

    def test_unselected_mode_button_has_dark_readable_text(self):
        selected = mode_button_style(True)
        unselected = mode_button_style(False)

        self.assertEqual(selected["text_color"], "#FFFFFF")
        self.assertEqual(selected["fg_color"], DESKTOP_EDITORIAL_PALETTE["primary"])
        self.assertEqual(unselected["fg_color"], DESKTOP_EDITORIAL_PALETTE["surface_card"])
        self.assertEqual(unselected["text_color"], DESKTOP_EDITORIAL_PALETTE["ink"])
        self.assertNotEqual(unselected["text_color"], unselected["fg_color"])

    def test_progress_display_uses_running_success_and_error_colors(self):
        running = ProgressUpdate("summarize", "running", "正在总结专利", "", 2, 4)
        success = ProgressUpdate("summarize", "success", "批量总结完成", "已完成 4 篇", 4, 4)
        error = ProgressUpdate("summarize", "error", "运行异常结束", "退出码 1", 2, 4)

        running_detail, running_value, running_color = progress_display(running)
        success_detail, success_value, success_color = progress_display(success)
        error_detail, error_value, error_color = progress_display(error)

        self.assertEqual(running_detail, "2 / 4（50%）")
        self.assertEqual(running_value, 0.5)
        self.assertEqual(running_color, DESKTOP_EDITORIAL_PALETTE["primary"])
        self.assertEqual(success_value, 1.0)
        self.assertEqual(success_color, DESKTOP_EDITORIAL_PALETTE["success"])
        self.assertIn("2 / 4（50%）", error_detail)
        self.assertEqual(error_value, 0.5)
        self.assertEqual(error_color, DESKTOP_EDITORIAL_PALETTE["error"])

    def test_gui_module_can_be_loaded_as_pyinstaller_script_entry(self):
        module_globals = runpy.run_path(str(Path("patent_summarizer") / "gui.py"))

        self.assertIn("launch", module_globals)

    def test_main_panel_scrolls_configuration_progress_and_log_together(self):
        source = (Path("patent_summarizer") / "gui.py").read_text(encoding="utf-8")

        self.assertIn("panel = ctk.CTkScrollableFrame(", source)
        self.assertIn("self.page_scroll = panel", source)
        self.assertIn('self.config_frame = ctk.CTkFrame(panel, fg_color="transparent")', source)
        self.assertIn('progress_row = ctk.CTkFrame(panel, fg_color="transparent")', source)
        self.assertIn("log_panel = ctk.CTkFrame(panel,", source)
        self.assertIn("baseline_list = ctk.CTkScrollableFrame(", source)
        self.assertIn("self.log_text = ctk.CTkTextbox(", source)


class PortableRuntimeFilesTests(unittest.TestCase):
    def test_copies_bundled_report_template_next_to_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            dist = Path(tmp) / "dist"
            template_dir = root / "output" / "doc"
            template_dir.mkdir(parents=True)
            dist.mkdir()
            (root / "class.xlsx").write_bytes(b"classes")
            (root / ".env.example").write_text("OPENAI_API_KEY=", encoding="utf-8")
            template = template_dir / build_portable.REPORT_TEMPLATE_NAME
            template.write_bytes(b"template")
            assets = root / "assets"
            assets.mkdir()
            (assets / build_portable.APP_ICON_NAME).write_bytes(b"icon")
            (root / build_portable.USER_GUIDE_NAME).write_text("guide", encoding="utf-8")

            with patch.object(build_portable, "ROOT", root):
                build_portable.copy_runtime_files(dist)

            self.assertEqual((dist / build_portable.REPORT_TEMPLATE_NAME).read_bytes(), b"template")
            self.assertEqual((dist / build_portable.APP_ICON_NAME).read_bytes(), b"icon")
            self.assertEqual((dist / build_portable.USER_GUIDE_NAME).read_text(encoding="utf-8"), "guide")
            self.assertEqual((dist / ".env").read_text(encoding="utf-8"), "OPENAI_API_KEY=")
            self.assertFalse((dist / ".env.example").exists())

    def test_missing_report_template_stops_portable_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            dist = Path(tmp) / "dist"
            root.mkdir()
            dist.mkdir()
            (root / "class.xlsx").write_bytes(b"classes")

            with patch.object(build_portable, "ROOT", root):
                with self.assertRaisesRegex(FileNotFoundError, "report template"):
                    build_portable.copy_runtime_files(dist)

    def test_pyinstaller_spec_uses_custom_application_icon(self):
        spec = Path("PatentSummarizer.spec").read_text(encoding="utf-8")

        self.assertIn('icon=str(ROOT / "assets" / "patent-summarizer.ico")', spec)

    def test_user_guide_covers_all_five_gui_tasks(self):
        guide = Path("使用说明.md").read_text(encoding="utf-8")

        for heading in ["## 四、批量总结", "## 五、分类", "## 六、趋势分析", "## 七、对比", "## 八、报告"]:
            self.assertIn(heading, guide)
        self.assertIn("前置条件", guide)
        self.assertIn("使用方法", guide)


class ReportDocxTests(unittest.TestCase):
    def test_portfolio_overview_requires_exact_object_and_text(self):
        valid = {
            "对象": "目标企业",
            "整体布局现状": "目标企业集中布局制动系统。",
        }

        result = parse_portfolio_overview(valid, "目标企业")

        self.assertEqual("目标企业", result.label)
        self.assertEqual("目标企业集中布局制动系统。", result.summary)

        invalid_cases = [
            (
                {
                    **valid,
                    "对象": "其他企业",
                },
                "对象必须为",
            ),
            (
                {
                    **valid,
                    "整体布局现状": "",
                },
                "必须是非空文本",
            ),
            (
                {
                    **valid,
                    "整体布局现状": ["错误类型"],
                },
                "必须是字符串",
            ),
            (
                {
                    "对象": "目标企业",
                },
                "缺少必填字段",
            ),
        ]
        for payload, error in invalid_cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ValueError, error):
                    parse_portfolio_overview(payload, "目标企业")

    def test_invalid_portfolio_overviews_use_existing_repair_flow(self):
        invalid = {
            "对象": "错误企业",
            "整体布局现状": "错误概述",
        }
        valid = {
            "对象": "目标企业",
            "整体布局现状": "目标概述",
        }

        class SequenceClient:
            def __init__(self):
                self.responses = [invalid, valid]
                self.prompts = []

            def chat(self, system_prompt, user_prompt):
                self.prompts.append(user_prompt)
                return self.responses.pop(0)

        client = SequenceClient()
        result = ReportSynthesisAnalyzer(client)._validated_chat(
            "整体布局现状",
            "生成布局概述",
            lambda response: parse_portfolio_overview(response, "目标企业"),
        )

        self.assertEqual("目标概述", result.summary)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("对象必须为", client.prompts[1])

    def test_report_analysis_generates_each_overview_in_its_own_call(self):
        target = PortfolioDataset("目标企业", Path("target"), Path(), "test", [])
        baseline = PortfolioDataset("竞争对手", Path("baseline"), Path(), "test", [])
        industry = PortfolioDataset("全部样本", Path("industry"), Path(), "combined", [])

        def trend_for(label):
            stats = TrendGroupStats(
                first_level=label,
                second_level="测试分类",
                patent_count=1,
                year_counts={"2024": 1},
                year_range="2024-2024",
                peak_year="2024",
                average_confidence=0.9,
                review_count=0,
                top_keywords=[label],
                representative_patents=[f"{label}专利"],
            )
            return TrendAnalysisResult(
                reports=[
                    TrendReport(
                        stats=stats,
                        status_summary=f"{label}现状",
                        development_stage="成长期",
                        technical_routes=f"{label}路线",
                        hot_topics=f"{label}热点",
                        opportunities=f"{label}机会",
                        risks="样本有限",
                        representative_patents=f"{label}专利",
                        evidence_limits="仅基于样本",
                    )
                ],
                records=[],
            )

        trends = {label: trend_for(label) for label in ["全部样本", "目标企业", "竞争对手"]}
        responses = [
            {
                "对象": "全部样本",
                "整体布局现状": "样本概述",
            },
            {
                "对象": "目标企业",
                "整体布局现状": "目标概述",
            },
            {
                "对象": "竞争对手",
                "整体布局现状": "竞品概述",
            },
            {
                "共性布局策略": [],
                "企业差异化策略": [],
                "竞争对手趋势研判": [],
                "未来竞争焦点": "制动系统",
                "证据局限": "仅基于样本",
            },
            {
                "长安总体策略": [],
                "待布局建议点": [],
                "专利组合策略": [],
                "证据局限": "仅基于样本",
            },
            {
                "管理层结论": ["结论一", "结论二", "结论三"],
                "总体证据局限": "仅基于样本",
            },
        ]

        class SequenceClient:
            def __init__(self):
                self.prompts = []

            def chat(self, system_prompt, user_prompt):
                self.prompts.append(user_prompt)
                return responses.pop(0)

        client = SequenceClient()
        overviews, _competitor, _target_analysis, _executive = ReportSynthesisAnalyzer(client).analyze(
            target,
            [baseline],
            industry,
            trends,
            ComparisonResult(target, [baseline], baseline, []),
        )

        self.assertEqual("样本概述", overviews.industry_summary)
        self.assertEqual("目标概述", overviews.company_summaries["目标企业"])
        self.assertEqual("竞品概述", overviews.company_summaries["竞争对手"])
        self.assertEqual(6, len(client.prompts))
        self.assertIn("对象名称：全部样本", client.prompts[0])
        self.assertIn("对象名称：目标企业", client.prompts[1])
        self.assertIn("对象名称：竞争对手", client.prompts[2])
        self.assertNotIn("竞争对手", client.prompts[1])
        self.assertNotIn("目标企业", client.prompts[2])
        self.assertIn("全部样本路线", client.prompts[0])
        self.assertNotIn("目标企业路线", client.prompts[0])
        self.assertIn("目标企业路线", client.prompts[1])
        self.assertNotIn("竞争对手路线", client.prompts[1])
        self.assertIn("竞争对手路线", client.prompts[2])
        self.assertNotIn("全部样本路线", client.prompts[2])
        self.assertEqual(3, sum("指定对象撰写" in prompt for prompt in client.prompts))
        self.assertIn("目标企业实际名称为“目标企业”", client.prompts[4])
        self.assertIn("不得将目标企业写成长安汽车", client.prompts[4])

    def test_report_places_three_overviews_after_summary_tables(self):
        target = PortfolioDataset("目标企业", Path("target"), Path(), "test", [])
        baseline = PortfolioDataset("竞争对手", Path("baseline"), Path(), "test", [])
        industry = PortfolioDataset("全部样本", Path("industry"), Path(), "combined", [])
        report = PatentLayoutReport(
            target=target,
            baselines=[baseline],
            industry=industry,
            trends={},
            comparison=ComparisonResult(target, [baseline], baseline, []),
            portfolio_overviews=PortfolioOverviewAnalysis(
                industry_summary="行业概述",
                company_summaries={"目标企业": "目标概述", "竞争对手": "竞品概述"},
            ),
            competitor_analysis=CompetitorAnalysis([], [], [], "未来焦点", "证据有限"),
            target_analysis=TargetAnalysis([], [], [], "证据有限"),
            executive_summary=ExecutiveSummary(["结论一", "结论二", "结论三"], "证据有限"),
            taxonomy=[],
        )
        document = Document()

        _build_report_body(document, report)

        body_children = list(document._element.body)
        overview_paragraphs = {
            paragraph.text: paragraph
            for paragraph in document.paragraphs
            if paragraph.text in {
                "整体布局现状：行业概述",
                "整体布局现状：竞品概述",
                "整体布局现状：目标概述",
            }
        }
        self.assertEqual(3, len(overview_paragraphs))
        for text, paragraph in overview_paragraphs.items():
            index = body_children.index(paragraph._element)
            preceding = index - 1
            while preceding >= 0 and body_children[preceding].tag == qn("w:p"):
                preceding_text = "".join(body_children[preceding].itertext()).strip()
                if preceding_text:
                    break
                preceding -= 1
            self.assertEqual(qn("w:tbl"), body_children[preceding].tag, text)
            document_paragraphs = document.paragraphs
            paragraph_index = next(
                position
                for position, item in enumerate(document_paragraphs)
                if item._element is paragraph._element
            )
            following = next(item for item in document_paragraphs[paragraph_index + 1 :] if item.text.strip())
            self.assertEqual("年度分布", following.text, text)
            self.assertTrue(paragraph.runs[0].bold)

    def test_trend_sections_use_nested_headings_instead_of_table(self):
        stats = TrendGroupStats(
            first_level="制动系统",
            second_level="线控制动",
            patent_count=3,
            year_counts={"2024": 3},
            year_range="2024-2024",
            peak_year="2024",
            average_confidence=0.9,
            review_count=0,
            top_keywords=["制动"],
            representative_patents=["CN1"],
        )
        report = TrendReport(
            stats=stats,
            status_summary="处于持续布局状态",
            development_stage="成长期",
            technical_routes="电机与液压协同",
            hot_topics="冗余控制",
            opportunities="故障降级",
            risks="样本有限",
            representative_patents="CN1",
            evidence_limits="仅基于输入样本",
        )
        document = Document()

        _trend_sections(document, TrendAnalysisResult(reports=[report], records=[]))

        self.assertEqual(0, len(document.tables))
        headings = [
            (paragraph.style.name, paragraph.text)
            for paragraph in document.paragraphs
            if paragraph.style.name.startswith("Heading")
        ]
        self.assertEqual(
            [
                ("Heading 2", "主要技术路线、热点与机会"),
                ("Heading 3", "制动系统 / 线控制动"),
                ("Heading 4", "发展现状/阶段"),
                ("Heading 4", "主要技术路线"),
                ("Heading 4", "热点方向"),
                ("Heading 4", "机会与空白"),
                ("Heading 4", "代表专利"),
            ],
            headings,
        )
        body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        for expected in ["处于持续布局状态", "成长期", "电机与液压协同", "冗余控制", "故障降级", "CN1"]:
            self.assertIn(expected, body_text)

    def test_report_font_applies_to_styles_body_tables_headers_and_footers(self):
        document = Document()
        title = document.add_heading("报告标题", level=1)
        title.runs[0].font.name = "微软雅黑"
        document.add_table(rows=1, cols=1).cell(0, 0).text = "表格内容"
        document.sections[0].header.paragraphs[0].text = "页眉"
        document.sections[0].footer.paragraphs[0].text = "页脚"

        _apply_report_font(document)

        for style_name in ["Normal", "Title", "Heading 1", "Heading 4", "List Bullet", "Header", "Footer"]:
            style = document.styles[style_name]
            self.assertEqual(REPORT_FONT, style.font.name)
            fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
            for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
                self.assertEqual(REPORT_FONT, fonts.get(qn(f"w:{attribute}")))

        containers = [document, document.sections[0].header, document.sections[0].footer]
        for container in containers:
            for paragraph in _iter_paragraphs(container):
                for run in paragraph.runs:
                    self.assertEqual(REPORT_FONT, run.font.name)
                    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
                    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
                        self.assertEqual(REPORT_FONT, fonts.get(qn(f"w:{attribute}")))


if __name__ == "__main__":
    unittest.main()
