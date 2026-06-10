from __future__ import annotations

import argparse
from pathlib import Path

from .config import ConfigError, load_config
from .discovery import discover_documents
from .extractors import TextExtractor
from .llm import RateLimitedOpenAIClient
from .logging_utils import RunLogger
from .processor import PatentProcessor, ProcessingResult
from .summarizer import PatentSummarizer


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patent_summarizer", description="Batch summarize Chinese patent PDF/DOCX documents.")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Process documents in a folder")
    run_parser.add_argument("--input", required=True, type=Path, help="Folder containing PDF/DOCX documents")
    run_parser.add_argument("--recursive", action="store_true", help="Scan subfolders recursively")
    run_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing same-name Markdown files")
    run_parser.add_argument("--dry-run", action="store_true", help="List files and estimate minimum API requests without calling the API")
    run_parser.add_argument("--limit", type=int, help="Process only the first N pending files")
    run_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    run_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    return parser


def run(args: argparse.Namespace) -> int:
    input_root = args.input.resolve()
    try:
        items = list(discover_documents(input_root, recursive=args.recursive, overwrite=args.overwrite))
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Input error: {exc}")
        return 2

    if args.limit is not None:
        items = items[: args.limit]

    if args.dry_run:
        for item in items:
            print(f"PENDING\t{item.source}\t->\t{item.output}")
        print(f"Pending files: {len(items)}")
        print(f"Minimum API requests: {len(items)}")
        return 0

    try:
        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    logger = RunLogger(input_root)
    client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
    )
    processor = PatentProcessor(
        TextExtractor(ocr_enabled=config.ocr_enabled),
        PatentSummarizer(client),
    )

    success = 0
    failed = 0
    for item in items:
        logger.event(source=str(item.source), output=str(item.output), status="started")
        try:
            result = processor.process(item.source, item.output)
            if result == ProcessingResult.SUCCESS:
                success += 1
                logger.event(source=str(item.source), output=str(item.output), status="success")
                print(f"OK\t{item.source}")
            else:
                failed += 1
                logger.failure(item.source, result.value, "No extractable text")
                print(f"EMPTY\t{item.source}")
        except Exception as exc:
            failed += 1
            logger.failure(item.source, exc.__class__.__name__, str(exc))
            print(f"FAIL\t{item.source}\t{exc}")

    print(f"Done. success={success}, failed={failed}, pending={len(items)}")
    return 0 if failed == 0 else 1
