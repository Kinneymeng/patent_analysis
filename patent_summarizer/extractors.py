from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class ExtractionError(RuntimeError):
    """Raised when a document cannot be converted to text."""


class TextExtractor:
    def __init__(self, ocr_enabled: bool = True, min_pdf_chars: int = 80):
        self.ocr_enabled = ocr_enabled
        self.min_pdf_chars = min_pdf_chars

    def extract(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return self._extract_docx(path)
        if suffix == ".pdf":
            return self._extract_pdf(path)
        raise ExtractionError(f"Unsupported file type: {path.suffix}")

    def _extract_docx(self, path: Path) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "document.md"
            try:
                result = subprocess.run(
                    ["pandoc", str(path), "-t", "markdown", "-o", str(output)],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
            except FileNotFoundError as exc:
                return self._extract_docx_with_pypandoc(path, exc)
            if result.returncode != 0:
                raise ExtractionError(f"Pandoc failed for {path}: {result.stderr.strip()}")
            return output.read_text(encoding="utf-8", errors="ignore")

    def _extract_docx_with_pypandoc(self, path: Path, original_error: FileNotFoundError) -> str:
        try:
            import pypandoc  # type: ignore
        except ImportError as exc:
            raise ExtractionError("DOCX extraction requires Pandoc or pypandoc-binary") from original_error or exc
        try:
            return str(pypandoc.convert_file(str(path), "markdown"))
        except Exception as exc:
            raise ExtractionError(f"pypandoc failed for {path}: {exc}") from exc

    def _extract_pdf(self, path: Path) -> str:
        text = self._extract_pdf_with_pdfplumber(path)
        if len(text.strip()) >= self.min_pdf_chars:
            return text

        pypdf_text = self._extract_pdf_with_pypdf(path)
        if len(pypdf_text.strip()) > len(text.strip()):
            text = pypdf_text
        if len(text.strip()) >= self.min_pdf_chars or not self.ocr_enabled:
            return text

        return self._extract_pdf_with_ocr(path)

    def _extract_pdf_with_pdfplumber(self, path: Path) -> str:
        try:
            import pdfplumber  # type: ignore
        except ImportError:
            return ""
        try:
            pages = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text() or "")
            return "\n\n".join(pages)
        except Exception as exc:
            raise ExtractionError(f"pdfplumber failed for {path}: {exc}") from exc

    def _extract_pdf_with_pypdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return ""
        try:
            reader = PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ExtractionError(f"pypdf failed for {path}: {exc}") from exc

    def _extract_pdf_with_ocr(self, path: Path) -> str:
        try:
            from pdf2image import convert_from_path  # type: ignore
            import pytesseract  # type: ignore
        except ImportError as exc:
            raise ExtractionError("OCR requires pdf2image and pytesseract") from exc
        try:
            pages = convert_from_path(str(path))
            return "\n\n".join(pytesseract.image_to_string(page, lang="chi_sim+eng") for page in pages)
        except Exception as exc:
            raise ExtractionError(f"OCR failed for {path}: {exc}") from exc
