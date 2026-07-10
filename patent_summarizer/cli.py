from __future__ import annotations

import argparse
from pathlib import Path

from .classification_pipeline import CheckpointStore, PatentClassificationPipeline, discover_classification_documents
from .comparison import ComparisonAnalyzer, load_or_build_portfolio
from .comparison_excel_export import write_comparison_workbook
from .config import ConfigError, load_config
from .discovery import discover_documents, inspect_summary_coverage
from .extractors import TextExtractor
from .excel_export import write_classification_workbook
from .llm import RateLimitedOpenAIClient
from .logging_utils import RunLogger
from .processor import PatentProcessor, ProcessingResult
from .progress import ProgressCallback, ProgressTracker
from .report_analysis import PatentLayoutReport, ReportCheckpointingClient, ReportSynthesisAnalyzer, combine_portfolios
from .report_docx import write_patent_layout_report
from .summarizer import PatentSummarizer
from .taxonomy import load_taxonomy
from .trend_analysis import TrendAnalyzer, TrendCheckpointStore, build_trend_groups, load_classification_workbook, trend_work_units
from .trend_excel_export import write_trend_workbook


def main(argv: list[str] | None = None, progress: ProgressCallback | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args, progress=progress)
    if args.command == "classify":
        return classify(args, progress=progress)
    if args.command == "analyze":
        return analyze(args, progress=progress)
    if args.command == "compare":
        return compare(args, progress=progress)
    if args.command == "report":
        return report(args, progress=progress)
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

    compare_parser = subparsers.add_parser("compare", help="Compare a target patent portfolio against external folders")
    compare_parser.add_argument("--target", required=True, type=Path, help="Target portfolio folder, usually A")
    compare_parser.add_argument("--target-label", help="Display label for target portfolio")
    compare_parser.add_argument(
        "--baseline",
        required=True,
        action="append",
        help="External portfolio folder, optionally as LABEL=PATH. Repeat for B/C/D.",
    )
    compare_parser.add_argument("--classes", type=Path, help="XLSX taxonomy used when a folder needs MD classification")
    compare_parser.add_argument("--output", required=True, type=Path, help="Output XLSX comparison report")
    compare_parser.add_argument("--recursive", action="store_true", help="Scan Markdown summaries recursively when classifying")
    compare_parser.add_argument("--refresh-classification", action="store_true", help="Rebuild classification workbooks from Markdown summaries")
    compare_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    compare_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    compare_parser.add_argument("--dry-run", action="store_true", help="List portfolios and planned comparisons without calling the API")

    report_parser = subparsers.add_parser("report", help="Generate a DOCX patent-layout report from portfolio folders")
    report_parser.add_argument("--target", required=True, type=Path, help="Target portfolio folder")
    report_parser.add_argument("--target-label", help="Display label for target portfolio")
    report_parser.add_argument(
        "--baseline",
        required=True,
        action="append",
        help="Competitor portfolio folder, optionally as LABEL=PATH. Repeat for each competitor.",
    )
    report_parser.add_argument("--classes", required=True, type=Path, help="XLSX taxonomy used for classification and the report appendix")
    report_parser.add_argument(
        "--template",
        type=Path,
        default=Path("output/doc/汽车底盘领域专利布局分析报告模板.docx"),
        help="DOCX template used for cover and document styles",
    )
    report_parser.add_argument("--output", required=True, type=Path, help="Output DOCX report")
    report_parser.add_argument("--recursive", action="store_true", help="Scan Markdown summaries recursively when classifying")
    report_parser.add_argument("--refresh-classification", action="store_true", help="Rebuild classification workbooks from Markdown summaries")
    report_parser.add_argument("--batch-size", type=int, default=40, help="Patents per LLM trend-analysis batch")
    report_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    report_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    report_parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing DOCX report")
    report_parser.add_argument("--dry-run", action="store_true", help="Validate report inputs without calling the API or writing files")
    return parser


def report(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
    target_root = args.target.resolve()
    target_label = args.target_label or target_root.name or "目标企业"
    baseline_specs = [_parse_baseline_spec(value) for value in args.baseline]
    labels = [target_label, *[label for label, _root in baseline_specs]]
    if len(labels) != len(set(labels)):
        print("Input error: target and competitor labels must be unique")
        return 2
    if not _validate_portfolio_summaries([(target_label, target_root), *baseline_specs]):
        return 2
    if args.batch_size <= 0:
        print("Input error: --batch-size must be positive")
        return 2
    if args.output.suffix.lower() != ".docx":
        print("Input error: --output must use the .docx extension")
        return 2
    if not args.classes.exists():
        print(f"Input error: taxonomy workbook does not exist: {args.classes}")
        return 2
    if not args.template.exists():
        print(f"Input error: DOCX template does not exist: {args.template}")
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Input error: output already exists: {args.output}; use --overwrite to replace it")
        return 2

    portfolio_specs = [(target_label, target_root), *baseline_specs]
    if args.dry_run:
        for label, root in portfolio_specs:
            try:
                source, count = _portfolio_dry_run_status(root, args.recursive, args.refresh_classification)
            except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
                print(f"Input error: {exc}")
                return 2
            print(f"PORTFOLIO\t{label}\t{root}\tclassification={source}\titems={count}")
        print(f"TEMPLATE\t{args.template.resolve()}")
        print(f"OUTPUT\t{args.output.resolve()}")
        print(f"REPORT\ttarget={target_label}\tcompetitors={len(baseline_specs)}\tbatch_size={args.batch_size}")
        return 0

    tracker = ProgressTracker("report", progress)
    tracker.preparing("正在准备报告", "正在检查分类结果")

    try:
        taxonomy = load_taxonomy(args.classes)
        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
    except (ConfigError, RuntimeError, FileNotFoundError) as exc:
        print(f"Config error: {exc}")
        return 2

    base_client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
    )
    log_dir = args.output.parent / ".patent_summary_logs"
    client = ReportCheckpointingClient(base_client, log_dir / f"{args.output.stem}.report.jsonl")

    classification_total = _portfolio_classification_units(portfolio_specs, args.recursive, args.refresh_classification)
    classified = 0

    def on_summary(summary_path: Path, started: bool) -> None:
        nonlocal classified
        if started:
            tracker.preparing("正在准备报告", f"正在分类：{classified} / {classification_total}")
        else:
            classified += 1
            tracker.preparing("正在准备报告", f"正在分类：{classified} / {classification_total}")

    try:
        target = load_or_build_portfolio(
            target_root,
            label=target_label,
            client=client,
            taxonomy=taxonomy,
            recursive=args.recursive,
            refresh_classification=args.refresh_classification,
            on_summary=on_summary,
        )
        print(f"PORTFOLIO\t{target.label}\tpatents={len(target.records)}\tclassification={target.classification_source}")
        baselines = []
        for label, root in baseline_specs:
            portfolio = load_or_build_portfolio(
                root,
                label=label,
                client=client,
                taxonomy=taxonomy,
                recursive=args.recursive,
                refresh_classification=args.refresh_classification,
                on_summary=on_summary,
            )
            baselines.append(portfolio)
            print(f"PORTFOLIO\t{portfolio.label}\tpatents={len(portfolio.records)}\tclassification={portfolio.classification_source}")

        industry = combine_portfolios("全部样本", [target, *baselines])
        trend_analyzer = TrendAnalyzer(client, batch_size=args.batch_size)
        trends = {}
        portfolios = [target, *baselines, industry]
        checkpoints = [TrendCheckpointStore(log_dir / f"{args.output.stem}.trend-{index}.jsonl") for index in range(1, len(portfolios) + 1)]
        trend_units = sum(
            trend_work_units(portfolio.records, args.batch_size, checkpoint)
            for portfolio, checkpoint in zip(portfolios, checkpoints)
        )
        comparison_units = len(baselines) + (1 if len(baselines) > 1 else 0)
        tracker.start(
            trend_units + comparison_units + 3 + 1,
            "正在分析趋势",
            f"0 / {trend_units} 个趋势分析单元",
        )

        def on_trend_operation(detail: str, started: bool) -> None:
            if started:
                tracker.set_stage("正在分析趋势", detail)
            else:
                tracker.advance("正在分析趋势", detail)

        for portfolio, checkpoint in zip(portfolios, checkpoints):
            if progress is None:
                trends[portfolio.label] = trend_analyzer.analyze(portfolio.records, checkpoint=checkpoint)
            else:
                trends[portfolio.label] = trend_analyzer.analyze(
                    portfolio.records,
                    checkpoint=checkpoint,
                    on_operation=on_trend_operation,
                )
            print(f"TREND\t{portfolio.label}\tgroups={len(trends[portfolio.label].reports)}")

        def on_pair(detail: str, started: bool) -> None:
            if started:
                tracker.set_stage("正在进行组合对比", detail)
            else:
                tracker.advance("正在进行组合对比", detail)

        if progress is None:
            comparison = ComparisonAnalyzer(client).analyze(target, baselines)
        else:
            comparison = ComparisonAnalyzer(client).analyze(target, baselines, on_pair=on_pair)
        print(f"COMPARE\treports={len(comparison.reports)}")

        def on_report_stage(detail: str, started: bool) -> None:
            if started:
                tracker.set_stage("正在生成报告", detail)
            else:
                tracker.advance("正在生成报告", detail)

        if progress is None:
            competitor_analysis, target_analysis, executive_summary = ReportSynthesisAnalyzer(client).analyze(
                target,
                baselines,
                trends,
                comparison,
            )
        else:
            competitor_analysis, target_analysis, executive_summary = ReportSynthesisAnalyzer(client).analyze(
                target,
                baselines,
                trends,
                comparison,
                on_stage=on_report_stage,
            )
        layout_report = PatentLayoutReport(
            target=target,
            baselines=baselines,
            industry=industry,
            trends=trends,
            comparison=comparison,
            competitor_analysis=competitor_analysis,
            target_analysis=target_analysis,
            executive_summary=executive_summary,
            taxonomy=[(item.first_level, item.second_level) for item in taxonomy.items],
        )
        tracker.set_stage("正在写入报告", "正在生成 DOCX 文件")
        write_patent_layout_report(
            layout_report,
            args.template,
            args.output,
            overwrite=args.overwrite,
        )
        tracker.advance("正在写入报告", "报告已写入 DOCX")
        tracker.succeed("报告生成完成", f"已生成 {args.output.name}")
    except (RuntimeError, FileNotFoundError, NotADirectoryError, ValueError, FileExistsError) as exc:
        print(f"Report error: {exc}")
        return 1

    print(
        f"Done. report={args.output}, target_patents={len(target.records)}, "
        f"competitor_patents={sum(len(item.records) for item in baselines)}"
    )
    return 0


def run(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
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

    tracker = ProgressTracker("summarize", progress)
    tracker.start(len(items), "正在总结专利", f"0 / {len(items)}")

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
        finally:
            tracker.advance("正在总结专利", f"{success + failed} / {len(items)}")

    print(f"Done. success={success}, failed={failed}, pending={len(items)}")
    exit_code = 0 if failed == 0 else 1
    if exit_code == 0:
        tracker.succeed("批量总结完成", f"已完成 {success} 篇")
    return exit_code


def classify(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
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

    tracker = ProgressTracker("classify", progress)
    tracker.start(len(items) + 1, "正在分类专利", f"0 / {len(items)}")

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
            tracker.advance("正在分类专利", f"{len(rows) + failed} / {len(items)}")
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
        finally:
            if key not in successes:
                tracker.advance("正在分类专利", f"{len(rows) + failed} / {len(items)}")

    tracker.set_stage("正在写入分类结果", "正在生成 Excel 文件")
    write_classification_workbook(rows, args.output)
    tracker.advance("正在写入分类结果", "分类结果已写入 Excel")
    print(f"Done. rows={len(rows)}, failed={failed}, output={args.output}")
    exit_code = 0 if failed == 0 else 1
    if exit_code == 0:
        tracker.succeed("分类完成", f"已分类 {len(rows)} 篇")
    return exit_code


def analyze(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
    tracker = ProgressTracker("analyze", progress)
    tracker.preparing("正在准备趋势分析", "正在读取分类结果")
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
    model_units = trend_work_units(records, args.batch_size, checkpoint)
    tracker.start(model_units + 1, "正在分析趋势", f"0 / {model_units} 个分析单元")

    def on_trend_operation(detail: str, started: bool) -> None:
        if started:
            tracker.set_stage("正在分析趋势", detail)
        else:
            tracker.advance("正在分析趋势", detail)

    if progress is None:
        result = analyzer.analyze(records, checkpoint=checkpoint)
    else:
        result = analyzer.analyze(records, checkpoint=checkpoint, on_operation=on_trend_operation)
    tracker.set_stage("正在写入趋势结果", "正在生成 Excel 文件")
    write_trend_workbook(result, args.output)
    tracker.advance("正在写入趋势结果", "趋势结果已写入 Excel")
    tracker.succeed("趋势分析完成", f"已分析 {len(result.reports)} 个标签")
    print(f"Done. trend_groups={len(result.reports)}, patents={len(records)}, output={args.output}")
    return 0


def compare(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
    target_root = args.target.resolve()
    baseline_specs = [_parse_baseline_spec(value) for value in args.baseline]
    target_label = args.target_label or target_root.name or "A"
    portfolio_specs = [(target_label, target_root), *baseline_specs]

    labels = [label for label, _root in portfolio_specs]
    if len(labels) != len(set(labels)):
        print("Input error: target and competitor labels must be unique")
        return 2
    if not _validate_portfolio_summaries(portfolio_specs):
        return 2

    if args.dry_run:
        for label, root in portfolio_specs:
            try:
                source, count = _portfolio_dry_run_status(root, args.recursive, args.refresh_classification)
            except (FileNotFoundError, NotADirectoryError) as exc:
                print(f"Input error: {exc}")
                return 2
            print(f"PORTFOLIO\t{label}\t{root}\tclassification={source}\titems={count}")
        for baseline_label, _baseline_root in baseline_specs:
            print(f"COMPARE\t{target_label}\tvs\t{baseline_label}")
        if len(baseline_specs) > 1:
            print(f"COMPARE\t{target_label}\tvs\t外部总体")
        return 0

    tracker = ProgressTracker("compare", progress)
    tracker.preparing("正在准备对比组合", "正在检查分类结果")

    needs_taxonomy = args.refresh_classification or any(
        not (root / "patent_classification.xlsx").exists()
        for _label, root in portfolio_specs
    )
    if needs_taxonomy and args.classes is None:
        print("Config error: --classes is required when classification workbooks are missing or refreshed")
        return 2

    try:
        taxonomy = load_taxonomy(args.classes) if needs_taxonomy and args.classes is not None else None
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

    classification_total = _portfolio_classification_units(portfolio_specs, args.recursive, args.refresh_classification)
    classified = 0

    def on_summary(summary_path: Path, started: bool) -> None:
        nonlocal classified
        if started:
            tracker.preparing("正在准备对比组合", f"正在分类：{classified} / {classification_total}")
        else:
            classified += 1
            tracker.preparing("正在准备对比组合", f"正在分类：{classified} / {classification_total}")

    try:
        target = load_or_build_portfolio(
            target_root,
            label=target_label,
            client=client,
            taxonomy=taxonomy,
            recursive=args.recursive,
            refresh_classification=args.refresh_classification,
            on_summary=on_summary,
        )
        baselines = []
        for label, root in baseline_specs:
            baselines.append(load_or_build_portfolio(
                root,
                label=label,
                client=client,
                taxonomy=taxonomy,
                recursive=args.recursive,
                refresh_classification=args.refresh_classification,
                on_summary=on_summary,
            )
            )
    except (RuntimeError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 2

    comparison_units = len(baselines) + (1 if len(baselines) > 1 else 0)
    tracker.start(comparison_units + 1, "正在进行组合对比", f"0 / {comparison_units} 个对比对象")

    def on_pair(detail: str, started: bool) -> None:
        if started:
            tracker.set_stage("正在进行组合对比", detail)
        else:
            tracker.advance("正在进行组合对比", detail)

    analyzer = ComparisonAnalyzer(client)
    if progress is None:
        result = analyzer.analyze(target, baselines)
    else:
        result = analyzer.analyze(target, baselines, on_pair=on_pair)
    tracker.set_stage("正在写入对比结果", "正在生成 Excel 文件")
    write_comparison_workbook(result, args.output)
    tracker.advance("正在写入对比结果", "对比结果已写入 Excel")
    tracker.succeed("组合对比完成", f"已生成 {len(result.reports)} 份对比结果")
    print(
        f"Done. comparisons={len(result.reports)}, "
        f"target_patents={len(target.records)}, "
        f"baseline_patents={sum(len(item.records) for item in baselines)}, "
        f"output={args.output}"
    )
    return 0


def _suffixes_for_file_type(file_type: str) -> set[str] | None:
    if file_type == "pdf":
        return {".pdf"}
    if file_type == "docx":
        return {".docx"}
    return None


def _parse_baseline_spec(value: str) -> tuple[str, Path]:
    if "=" in value:
        label, raw_path = value.split("=", 1)
        path = Path(raw_path).resolve()
        return (label.strip() or path.name or str(path), path)
    path = Path(value).resolve()
    return (path.name or str(path), path)


def _validate_portfolio_summaries(portfolio_specs: list[tuple[str, Path]]) -> bool:
    failures: list[str] = []
    for label, root in portfolio_specs:
        try:
            coverage = inspect_summary_coverage(root, recursive=True)
        except (FileNotFoundError, NotADirectoryError) as exc:
            failures.append(f"{label}: {exc}")
            continue
        if coverage.document_count == 0:
            failures.append(f"{label}: 未发现 DOCX 专利文件（{root}）")
            continue
        if coverage.missing_summaries:
            missing = ", ".join(str(path) for path in coverage.missing_summaries[:10])
            remaining = len(coverage.missing_summaries) - 10
            suffix = f"，另有 {remaining} 篇" if remaining > 0 else ""
            failures.append(f"{label}: 缺少 {len(coverage.missing_summaries)} 篇摘要：{missing}{suffix}")
    if not failures:
        return True
    print("Input error: 专利目录摘要不完整，请先对所有专利目录运行“批量总结”后再继续。")
    for failure in failures:
        print(f"SUMMARY\t{failure}")
    return False


def _portfolio_dry_run_status(root: Path, recursive: bool, refresh_classification: bool) -> tuple[str, int]:
    if not root.exists():
        raise FileNotFoundError(f"Input path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {root}")
    classification = root / "patent_classification.xlsx"
    if classification.exists() and not refresh_classification:
        return "existing", len(load_classification_workbook(classification))
    paths = root.rglob("*.md") if recursive else root.glob("*.md")
    count = sum(1 for path in paths if ".patent_summary_logs" not in path.parts)
    return "needs-md-classification", count


def _portfolio_classification_units(
    portfolio_specs: list[tuple[str, Path]],
    recursive: bool,
    refresh_classification: bool,
) -> int:
    """Count summaries that must be visited before portfolio work can be planned."""

    total = 0
    for _label, root in portfolio_specs:
        classification = root / "patent_classification.xlsx"
        if classification.exists() and not refresh_classification:
            continue
        paths = root.rglob("*.md") if recursive else root.glob("*.md")
        total += sum(1 for path in paths if ".patent_summary_logs" not in path.parts)
    return total
