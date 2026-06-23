from __future__ import annotations

import argparse
from pathlib import Path

from .classification_pipeline import CheckpointStore, PatentClassificationPipeline, discover_classification_documents
from .config import ConfigError, load_config
from .discovery import discover_documents
from .extractors import TextExtractor
from .excel_export import write_classification_workbook
from .llm import RateLimitedOpenAIClient
from .logging_utils import RunLogger
from .processor import PatentProcessor, ProcessingResult
from .summarizer import PatentSummarizer
from .taxonomy import load_taxonomy
from .trend_analysis import TrendAnalyzer, TrendCheckpointStore, build_trend_groups, load_classification_workbook
from .trend_excel_export import write_trend_workbook


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args)
    if args.command == "classify":
        return classify(args)
    if args.command == "analyze":
        return analyze(args)
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
    run_parser.add_argument("--file-type", choices=["all", "pdf", "docx"], default="all", help="Document type to scan")

    classify_parser = subparsers.add_parser("classify", help="Classify documents into a two-level Excel taxonomy")
    classify_parser.add_argument("--input", required=True, type=Path, help="Folder containing PDF/DOCX documents")
    classify_parser.add_argument("--classes", required=True, type=Path, help="XLSX file with first-level and second-level categories")
    classify_parser.add_argument("--output", required=True, type=Path, help="Output XLSX classification report")
    classify_parser.add_argument("--recursive", action="store_true", help="Scan subfolders recursively")
    classify_parser.add_argument("--dry-run", action="store_true", help="List files without calling the API")
    classify_parser.add_argument("--limit", type=int, help="Process only the first N files")
    classify_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    classify_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    classify_parser.add_argument("--refresh-summaries", action="store_true", help="Regenerate Markdown summaries before classifying")
    classify_parser.add_argument("--file-type", choices=["all", "pdf", "docx"], default="all", help="Document type to scan")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze patent trends from a classification workbook")
    analyze_parser.add_argument("--classification", required=True, type=Path, help="Input XLSX classification report")
    analyze_parser.add_argument("--output", required=True, type=Path, help="Output XLSX trend report")
    analyze_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    analyze_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    analyze_parser.add_argument("--batch-size", type=int, default=40, help="Patents per LLM trend-analysis batch")
    analyze_parser.add_argument("--dry-run", action="store_true", help="List trend groups without calling the API")
    return parser


def run(args: argparse.Namespace) -> int:
    input_root = args.input.resolve()
    try:
        items = list(
            discover_documents(
                input_root,
                recursive=args.recursive,
                overwrite=args.overwrite,
                suffixes=_suffixes_for_file_type(args.file_type),
            )
        )
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


def classify(args: argparse.Namespace) -> int:
    input_root = args.input.resolve()
    try:
        items = list(
            discover_classification_documents(
                input_root,
                recursive=args.recursive,
                suffixes=_suffixes_for_file_type(args.file_type),
            )
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Input error: {exc}")
        return 2

    if args.limit is not None:
        items = items[: args.limit]

    if args.dry_run:
        for item in items:
            print(f"CLASSIFY\t{item.source}\t(summary: {item.output})")
        print(f"Files: {len(items)}")
        return 0

    try:
        taxonomy = load_taxonomy(args.classes)
        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
    except (ConfigError, RuntimeError, FileNotFoundError) as exc:
        print(f"Config error: {exc}")
        return 2

    client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
    )
    pipeline = PatentClassificationPipeline(
        TextExtractor(ocr_enabled=config.ocr_enabled),
        PatentSummarizer(client),
        client,
        taxonomy,
        refresh_summary=args.refresh_summaries,
    )
    checkpoint = CheckpointStore(input_root / ".patent_summary_logs" / "classifications.jsonl")
    successes = {} if args.refresh_summaries else checkpoint.load_successes()

    rows = []
    failed = 0
    for item in items:
        key = str(item.source)
        if key in successes:
            rows.append(successes[key])
            print(f"SKIP\t{item.source}")
            continue
        try:
            row = pipeline.process(item.source, item.output)
            checkpoint.record_success(row)
            rows.append(row)
            print(f"OK\t{item.source}")
        except Exception as exc:
            failed += 1
            checkpoint.record_failure(item.source, exc.__class__.__name__, str(exc))
            print(f"FAIL\t{item.source}\t{exc}")

    write_classification_workbook(rows, args.output)
    print(f"Done. rows={len(rows)}, failed={failed}, output={args.output}")
    return 0 if failed == 0 else 1


def analyze(args: argparse.Namespace) -> int:
    try:
        records = load_classification_workbook(args.classification)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Input error: {exc}")
        return 2

    groups = build_trend_groups(records)
    if args.dry_run:
        for stats, _records in groups:
            print(f"TREND\t{stats.first_level}\t{stats.second_level}\tpatents={stats.patent_count}")
        print(f"Trend groups: {len(groups)}")
        return 0

    try:
        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
    )
    analyzer = TrendAnalyzer(client, batch_size=args.batch_size)
    checkpoint = TrendCheckpointStore(args.output.parent / ".patent_summary_logs" / f"{args.output.stem}.jsonl")
    result = analyzer.analyze(records, checkpoint=checkpoint)
    write_trend_workbook(result, args.output)
    print(f"Done. trend_groups={len(result.reports)}, patents={len(records)}, output={args.output}")
    return 0


def _suffixes_for_file_type(file_type: str) -> set[str] | None:
    if file_type == "pdf":
        return {".pdf"}
    if file_type == "docx":
        return {".docx"}
    return None
