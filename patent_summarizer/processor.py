from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Protocol


class Extractor(Protocol):
    def extract(self, path: Path) -> str:
        ...


class Summarizer(Protocol):
    def summarize_document(self, filename: str, text: str) -> str:
        ...


class ProcessingResult(str, Enum):
    SUCCESS = "success"
    EMPTY_TEXT = "empty_text"


class PatentProcessor:
    def __init__(self, extractor: Extractor, summarizer: Summarizer):
        self.extractor = extractor
        self.summarizer = summarizer

    def process(self, source: Path, output: Path) -> ProcessingResult:
        text = self.extractor.extract(source)
        if not text.strip():
            return ProcessingResult.EMPTY_TEXT
        summary = self.summarizer.summarize_document(source.name, text)
        output.write_text(summary, encoding="utf-8")
        return ProcessingResult.SUCCESS
