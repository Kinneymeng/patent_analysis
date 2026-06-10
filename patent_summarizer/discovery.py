from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {".pdf", ".docx"}


@dataclass(frozen=True)
class DocumentItem:
    source: Path
    output: Path
    status: str


def discover_documents(root: Path, recursive: bool, overwrite: bool) -> Iterable[DocumentItem]:
    if not root.exists():
        raise FileNotFoundError(f"Input path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {root}")

    paths = root.rglob("*") if recursive else root.iterdir()
    for source in sorted(paths, key=lambda item: str(item).lower()):
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        output = source.with_suffix(".md")
        if output.exists() and not overwrite:
            continue
        yield DocumentItem(source=source, output=output, status="pending")
