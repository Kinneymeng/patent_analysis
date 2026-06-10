import tempfile
import unittest
from pathlib import Path

from patent_summarizer.processor import PatentProcessor, ProcessingResult
from patent_summarizer.summarizer import split_text


class FakeExtractor:
    def __init__(self, text):
        self.text = text

    def extract(self, path):
        return self.text


class FakeSummarizer:
    def __init__(self):
        self.calls = []

    def summarize_document(self, filename, text):
        self.calls.append((filename, text))
        return f"# {filename}\n\nsummary"


class SummarizerTests(unittest.TestCase):
    def test_split_text_preserves_order_and_respects_max_chars(self):
        text = "第一段。\n\n第二段很长。\n\n第三段。"

        chunks = split_text(text, max_chars=8)

        self.assertEqual(chunks, ["第一段。", "第二段很长。", "第三段。"])

    def test_processor_writes_markdown_next_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "case.pdf"
            source.write_bytes(b"%PDF")
            output = Path(tmp) / "case.md"
            summarizer = FakeSummarizer()
            processor = PatentProcessor(FakeExtractor("专利文本"), summarizer)

            result = processor.process(source, output)

            self.assertEqual(result, ProcessingResult.SUCCESS)
            self.assertEqual(output.read_text(encoding="utf-8"), "# case.pdf\n\nsummary")
            self.assertEqual(summarizer.calls, [("case.pdf", "专利文本")])

    def test_processor_reports_empty_extraction_without_writing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "case.pdf"
            source.write_bytes(b"%PDF")
            output = Path(tmp) / "case.md"
            processor = PatentProcessor(FakeExtractor("   "), FakeSummarizer())

            result = processor.process(source, output)

            self.assertEqual(result, ProcessingResult.EMPTY_TEXT)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
