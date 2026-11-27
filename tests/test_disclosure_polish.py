import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from PIL import Image

from patent_summarizer.cli import main
from patent_summarizer.config import AppConfig
from patent_summarizer.disclosure.prompt_loader import load_prompt
from patent_summarizer.disclosure.validation import LEGACY_SECTION_TITLES, SECTION_TITLES, chapter_heading
from patent_summarizer.disclosure_docx import build_technical_disclosure_document
from patent_summarizer.disclosure.models import DisclosureSection, TechnicalDisclosure
from patent_summarizer.disclosure_polish import PolishError, polish_disclosure_docx
from patent_summarizer.progress import ProgressTracker


def _prose(document, text):
    paragraph = document.add_paragraph(text)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return paragraph


def _respond(system_prompt, user_prompt, changes=None):
    payload = json.loads(user_prompt)
    changes = changes or {}
    for item in payload["paragraphs"]:
        item["text"] = changes.get(item["text"], item["text"])
    return json.dumps({"paragraphs": payload["paragraphs"]}, ensure_ascii=False)


class DisclosurePolishTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "冗余制动技术交底书.DOCX"
        self.tracker = ProgressTracker("polish")

    def _legacy_source(self):
        disclosure = TechnicalDisclosure(
            "冗余制动控制方法", "控制器进行状态校验。", ("模块 A1 在 20 ms 内完成判断。",)
        )
        build_technical_disclosure_document(disclosure).save(self.source)

    def test_preserves_word_package_elements_while_polishing_six_chapters(self):
        document = Document()
        document.add_paragraph("研发技术交底书")
        document.add_paragraph("冗余制动控制方法")
        document.add_paragraph("摘要")
        _prose(document, "控制器进行状态校验。")
        for number, title in LEGACY_SECTION_TITLES.items():
            document.add_paragraph(f"{number}、{title}")
            _prose(document, "控制器在 20 ms 内进行状态校验。")

        picture = self.root / "图.png"
        Image.new("RGB", (3, 3)).save(picture)
        document.add_picture(str(picture))
        document.add_paragraph("图1 控制流程").alignment = WD_ALIGN_PARAGRAPH.CENTER
        _prose(document, "公式说明")
        document.add_paragraph()._element.append(OxmlElement("m:oMath"))
        document.add_table(rows=1, cols=1).cell(0, 0).text = "校验周期 20 ms"
        mixed = _prose(document, "含格式的")
        mixed.add_run("术语").bold = True
        _prose(document, "查看 https://example.test/help")
        numbered = _prose(document, "编号正文")
        numbered._element.get_or_add_pPr().append(OxmlElement("w:numPr"))
        document.save(self.source)
        original = self.source.read_bytes()

        changes = {"控制器进行状态校验。": "控制器校验状态。", "控制器在 20 ms 内进行状态校验。": "控制器在 20 ms 内校验状态。"}
        client = Mock()
        client.chat.side_effect = lambda system, user: _respond(system, user, changes)
        result = polish_disclosure_docx(self.source, client, self.tracker)

        self.assertEqual((result.changed, result.rejected, result.eligible), (7, 0, 7))
        self.assertEqual(self.source.read_bytes(), original)
        with ZipFile(self.source) as source_zip, ZipFile(result.output) as polished_zip:
            self.assertEqual(source_zip.namelist(), polished_zip.namelist())
            for name in source_zip.namelist():
                if name != "word/document.xml":
                    self.assertEqual(source_zip.read(name), polished_zip.read(name), name)
        polished = Document(result.output)
        for original_paragraph, polished_paragraph in zip(document.paragraphs, polished.paragraphs):
            original_text = original_paragraph.text
            self.assertEqual(polished_paragraph.text, changes.get(original_text, original_text))
            expected_xml = original_paragraph._element.xml
            if original_text in changes:
                expected_xml = expected_xml.replace(original_text, changes[original_text])
            self.assertEqual(polished_paragraph._element.xml, expected_xml)
        self.assertEqual(polished.tables[0].cell(0, 0).text, "校验周期 20 ms")
        requests = [json.loads(call.args[1]) for call in client.chat.call_args_list]
        self.assertEqual([request["chapter"] for request in requests],
                         ["摘要", *[f"{n}、{t}" for n, t in LEGACY_SECTION_TITLES.items()]])

        skill = load_prompt("humanizer-zh/SKILL.md")
        self.assertEqual(
            hashlib.sha256(skill.encode("utf-8")).hexdigest(),
            "3af2f533c85ab5f1bc7a7c2fb70e80b7b5f9dd42238271b431d94dfeffea33e3",
        )
        rules = skill.split("\n---\n", 1)[1].lstrip()
        for call in client.chat.call_args_list:
            self.assertEqual(call.args[0], f"{load_prompt('polish.md').rstrip()}\n\n{rules}")

    def test_polishes_new_four_chapter_manuscript(self):
        sentence = "控制器进行状态校验。"
        sections = tuple(DisclosureSection(number, title, (sentence,)) for number, title in SECTION_TITLES.items())
        disclosure = TechnicalDisclosure("冗余制动控制方法", sentence, sections=sections)
        build_technical_disclosure_document(disclosure).save(self.source)
        client = Mock()
        client.chat.side_effect = lambda system, user: _respond(system, user, {sentence: "控制器校验状态。"})

        result = polish_disclosure_docx(self.source, client, self.tracker)

        self.assertEqual((result.changed, result.rejected, result.eligible), (5, 0, 5))
        chapters = [chapter_heading(number, title) for number, title in SECTION_TITLES.items()]
        self.assertEqual([p.text for p in Document(result.output).paragraphs if p.style.name == "Heading 1"], chapters)
        self.assertEqual([json.loads(call.args[1])["chapter"] for call in client.chat.call_args_list],
                         ["摘要", *chapters])

    def test_unavailable_rules_stop_before_model_or_output(self):
        self._legacy_source()
        client = Mock()
        with patch("patent_summarizer.disclosure.prompt_loader.load_prompt", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(PolishError, "无法加载内置润色规则"):
                polish_disclosure_docx(self.source, client, self.tracker)
        client.chat.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_legacy_no_edit_collision_preserves_original_and_existing_copy(self):
        self._legacy_source()
        existing = self.source.with_name(f"{self.source.stem}_润色版.docx")
        existing.write_bytes(b"existing")
        client = Mock()
        client.chat.side_effect = _respond

        result = polish_disclosure_docx(self.source, client, self.tracker)

        self.assertEqual(result.output, self.source.with_name(f"{self.source.stem}_润色版_2.docx"))
        self.assertEqual((result.changed, result.rejected), (0, 0))
        self.assertEqual(existing.read_bytes(), b"existing")
        self.assertEqual(result.output.read_bytes(), self.source.read_bytes())

    def test_cli_keeps_failed_paragraph_and_reports_warning(self):
        self._legacy_source()
        changes = {"控制器进行状态校验。": "控制器校验状态。", "模块 A1 在 20 ms 内完成判断。": "模块 A2 在 25 s 内完成判断。"}
        log = StringIO()
        updates = []
        with patch("patent_summarizer.cli.load_config", return_value=AppConfig("https://example.test", "key", "test")), patch(
            "patent_summarizer.cli.RateLimitedOpenAIClient"
        ) as client, redirect_stdout(log):
            client.return_value.chat.side_effect = lambda system, user: _respond(system, user, changes)
            code = main(["polish", "--input", str(self.source)], progress=updates.append)

        self.assertEqual(code, 0)
        output = self.source.with_name(f"{self.source.stem}_润色版.docx")
        self.assertIn(str(output), log.getvalue())
        self.assertIn("1 段未通过校验", log.getvalue())
        self.assertEqual(updates[-1].stage, "润色完成（有警告）")
        paragraphs = Document(output).paragraphs
        self.assertEqual(paragraphs[2].text, "控制器校验状态。")
        self.assertEqual(paragraphs[4].text, "模块 A1 在 20 ms 内完成判断。")

    def test_missing_id_retries_once_and_later_failure_publishes_nothing(self):
        self._legacy_source()
        before = self.source.read_bytes()
        client = Mock()
        client.chat.side_effect = [
            '{"paragraphs":[{"id":3,"text":"控制器校验状态。"}]}',
            '{"paragraphs":[]}',
            "invalid JSON",
        ]

        with self.assertRaisesRegex(PolishError, "两次返回"):
            polish_disclosure_docx(self.source, client, self.tracker)

        self.assertEqual(client.chat.call_count, 3)
        self.assertIn("retry", json.loads(client.chat.call_args.args[1]))
        self.assertEqual(list(self.root.iterdir()), [self.source])
        self.assertEqual(self.source.read_bytes(), before)

    def test_api_failure_is_sanitized_and_leaves_no_output(self):
        self._legacy_source()
        log = StringIO()
        updates = []
        with patch("patent_summarizer.cli.load_config", return_value=AppConfig("https://example.test", "key", "test")), patch(
            "patent_summarizer.cli.RateLimitedOpenAIClient"
        ) as client, redirect_stdout(log):
            client.return_value.chat.side_effect = TimeoutError("包含交底书正文的服务商诊断")
            code = main(["polish", "--input", str(self.source)], progress=updates.append)

        self.assertEqual(code, 1)
        self.assertEqual(updates[-1].detail, "TimeoutError")
        self.assertNotIn("交底书正文", log.getvalue())
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_unrecognized_document_does_not_call_model(self):
        document = Document()
        document.add_paragraph("专利布局分析报告")
        document.save(self.source)
        client = Mock()

        with self.assertRaisesRegex(PolishError, "无法识别"):
            polish_disclosure_docx(self.source, client, self.tracker)

        client.chat.assert_not_called()
