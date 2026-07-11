import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_portable

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


if __name__ == "__main__":
    unittest.main()
