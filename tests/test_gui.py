import runpy
import tempfile
import unittest
from pathlib import Path

from patent_summarizer.gui import (
    GOOGLE_MATERIAL_PALETTE,
    MODE_ANALYZE,
    MODE_CLASSIFY,
    MODE_SUMMARIZE,
    MODERN_APPEARANCE_MODE,
    MODERN_COLOR_THEME,
    MODE_OPTIONS,
    build_cli_argv,
    mode_from_label,
    mode_button_style,
)


class GuiCommandTests(unittest.TestCase):
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
        self.assertEqual(MODE_OPTIONS, ["批量总结", "分类", "趋势分析"])
        self.assertEqual(mode_from_label("批量总结"), MODE_SUMMARIZE)
        self.assertEqual(mode_from_label("分类"), MODE_CLASSIFY)
        self.assertEqual(mode_from_label("趋势分析"), MODE_ANALYZE)

        with self.assertRaises(ValueError):
            mode_from_label("未知")

    def test_modern_theme_uses_customtkinter_light_mode_without_blue_theme(self):
        self.assertEqual(MODERN_APPEARANCE_MODE, "light")
        self.assertEqual(MODERN_COLOR_THEME, "green")

    def test_google_material_palette_uses_primary_secondary_tertiary_roles(self):
        self.assertEqual(GOOGLE_MATERIAL_PALETTE["primary"], "#6750A4")
        self.assertEqual(GOOGLE_MATERIAL_PALETTE["secondary"], "#625B71")
        self.assertEqual(GOOGLE_MATERIAL_PALETTE["tertiary"], "#7D5260")
        self.assertNotIn("#2563eb", GOOGLE_MATERIAL_PALETTE.values())
        self.assertNotIn("#1d4ed8", GOOGLE_MATERIAL_PALETTE.values())

    def test_unselected_mode_button_has_dark_readable_text(self):
        selected = mode_button_style(True)
        unselected = mode_button_style(False)

        self.assertEqual(selected["text_color"], "#FFFFFF")
        self.assertEqual(unselected["text_color"], GOOGLE_MATERIAL_PALETTE["on_surface"])
        self.assertNotEqual(unselected["text_color"], unselected["fg_color"])

    def test_gui_module_can_be_loaded_as_pyinstaller_script_entry(self):
        module_globals = runpy.run_path(str(Path("patent_summarizer") / "gui.py"))

        self.assertIn("launch", module_globals)


if __name__ == "__main__":
    unittest.main()
