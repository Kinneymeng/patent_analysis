import tempfile
import unittest
from pathlib import Path

from patent_summarizer.discovery import discover_documents


class DiscoveryTests(unittest.TestCase):
    def test_discovers_supported_files_and_skips_existing_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "b.docx").write_bytes(b"docx")
            (root / "b.md").write_text("old", encoding="utf-8")
            (root / "c.txt").write_text("ignore", encoding="utf-8")

            found = list(discover_documents(root, recursive=False, overwrite=False))

        self.assertEqual([item.source.name for item in found], ["a.pdf"])
        self.assertEqual(found[0].output.name, "a.md")
        self.assertEqual(found[0].status, "pending")

    def test_recursive_discovery_keeps_nested_output_next_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            (nested / "case.docx").write_bytes(b"docx")

            found = list(discover_documents(root, recursive=True, overwrite=False))

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source, nested / "case.docx")
        self.assertEqual(found[0].output, nested / "case.md")

    def test_overwrite_includes_files_with_existing_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "a.md").write_text("old", encoding="utf-8")

            found = list(discover_documents(root, recursive=False, overwrite=True))

        self.assertEqual([item.source.name for item in found], ["a.pdf"])


if __name__ == "__main__":
    unittest.main()
