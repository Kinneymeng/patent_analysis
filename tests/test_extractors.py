import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from patent_summarizer.extractors import TextExtractor


class DocxExtractorTests(unittest.TestCase):
    def test_docx_extraction_falls_back_to_pypandoc_when_system_pandoc_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "case.docx"
            source.write_bytes(b"docx")
            calls = []

            fake_pypandoc = types.ModuleType("pypandoc")

            def convert_file(path, to):
                calls.append((path, to))
                return "fallback markdown"

            fake_pypandoc.convert_file = convert_file

            with patch("patent_summarizer.extractors.subprocess.run", side_effect=FileNotFoundError):
                with patch.dict(sys.modules, {"pypandoc": fake_pypandoc}):
                    text = TextExtractor().extract(source)

        self.assertEqual(text, "fallback markdown")
        self.assertEqual(calls, [(str(source), "markdown")])


if __name__ == "__main__":
    unittest.main()
