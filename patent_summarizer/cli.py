from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace
from pathlib import Path

from .classification_pipeline import CheckpointStore, PatentClassificationPipeline, discover_classification_documents
from .comparison import ComparisonAnalyzer, load_or_build_portfolio
from .comparison_excel_export import write_comparison_workbook
from .config import ConfigError, load_config
from .discovery import discover_documents, inspect_summary_coverage
from .disclosure_polish import PolishError, polish_disclosure_docx
from .disclosure import (
    CnipaPriorArtSearcher,
    ConflictPolicy,
    DisclosureAnalyzer,
    DisclosureCheckpointStore,
    DisclosureFailure,
    DisclosureGenerationResult,
    DisclosurePreviewReviewCallback,
    DisclosureTitleReviewCallback,
    PriorArtFailureAction,
    PriorArtFailureCallback,
    PriorArtResult,
    PriorArtSearchError,
    PriorArtStatus,
    attach_materials,
    disclosure_output_paths,
    existing_disclosure_bundle_complete,
    find_candidate,
    load_idea_source,
    load_project_source,
    load_report_sources,
    load_title_selections,
    disclosure_output_path,
    scan_project_materials,
    selected_work_items,
    TitleSelection,
    write_disclosure_bundle,
    write_scan_manifest,
)
from .extractors import TextExtractor
from .elephant_horse import (
    ElephantHorseAnalyzer,
    ElephantHorseCancelled,
    SearchFailureAction,
    SearchFailureCallback,
    default_elephant_horse_output,
    load_elephant_horse_inputs,
)
from .elephant_horse_docx import write_elephant_horse_report
from .excel_export import write_classification_workbook
from .llm import HttpResponseError, RateLimitedOpenAIClient
from .logging_utils import RunLogger
from .processor import PatentProcessor, ProcessingResult
from .progress import ProgressCallback, ProgressTracker
from .report_analysis import (
    PatentLayoutReport,
    ReportCheckpointingClient,
    ReportEnhancementAnalyzer,
    ReportReviewCallback,
    ReportSynthesisAnalyzer,
    combine_portfolios,
)
from .report_docx import derive_appendix_output, write_patent_layout_report
from .summarizer import PatentSummarizer
from .taxonomy import load_taxonomy
from .trend_analysis import TrendAnalyzer, TrendCheckpointStore, build_trend_groups, load_classification_workbook, trend_work_units
from .trend_excel_export import write_trend_workbook


REPORT_CANCELLED_EXIT_CODE = 130
DISCLOSURE_CANCELLED_EXIT_CODE = 130
ELEPHANT_HORSE_CANCELLED_EXIT_CODE = 130


def main(
    argv: list[str] | None = None,
    progress: ProgressCallback | None = None,
    report_review: ReportReviewCallback | None = None,
    disclosure_title_review: DisclosureTitleReviewCallback | None = None,
    disclosure_preview_review: DisclosurePreviewReviewCallback | None = None,
    disclosure_prior_art_failure: PriorArtFailureCallback | None = None,
    elephant_horse_search_failure: SearchFailureCallback | None = None,
) -> int:
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
        return report(args, progress=progress, report_review=report_review)
    if args.command == "elephant-horse":
        return elephant_horse(
            args,
            progress=progress,
            search_failure=elephant_horse_search_failure,
        )
    if args.command == "disclosure":
        return disclosure(
            args,
            progress=progress,
            title_review=disclosure_title_review,
            preview_review=disclosure_preview_review,
            prior_art_failure=disclosure_prior_art_failure,
        )
    if args.command == "polish":
        return polish(args, progress=progress)
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

    elephant_parser = subparsers.add_parser(
        "elephant-horse",
        help="Generate one protective elephant-horse patent-layout DOCX from an analysis report",
    )
    elephant_parser.add_argument("--report", required=True, type=Path, help="Existing patent-layout analysis report DOCX")
    elephant_parser.add_argument("--output", required=True, type=Path, help="Output protective elephant-horse DOCX")
    elephant_parser.add_argument("--expanded-search", action="store_true", help="Run optional two-pass CNIPA industry search for every theme")
    elephant_parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output DOCX")
    elephant_parser.add_argument(
        "--search-failure",
        choices=("fail", "continue"),
        default="fail",
        help="CLI behavior if an expanded search fails (default: fail)",
    )
    elephant_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    elephant_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    elephant_resume = elephant_parser.add_mutually_exclusive_group()
    elephant_resume.add_argument("--resume", dest="resume", action="store_true", default=True, help="Reuse the last valid prompt cache (default)")
    elephant_resume.add_argument("--no-resume", dest="resume", action="store_false", help="Ignore prior model prompt cache")

    disclosure_parser = subparsers.add_parser(
        "disclosure",
        help="Generate reviewed engineering disclosure documents (DOCX + Markdown)",
    )
    disclosure_source = disclosure_parser.add_mutually_exclusive_group(required=True)
    disclosure_source.add_argument("--report", type=Path, help="Existing patent-layout analysis report DOCX")
    disclosure_source.add_argument("--idea", help="Direct technical idea text")
    disclosure_source.add_argument("--idea-file", type=Path, help="UTF-8 text file containing a direct technical idea")
    disclosure_source.add_argument("--project-dir", type=Path, help="Project directory used as the primary invention source")
    disclosure_parser.add_argument("--material-dir", action="append", default=[], type=Path, help="Supplementary project-material directory; repeatable")
    disclosure_parser.add_argument("--prior-art", action="store_true", help="Run optional CNIPA invention prior-art search")
    disclosure_parser.add_argument(
        "--prior-art-failure",
        choices=("fail", "skip"),
        default="fail",
        help="CLI behavior when optional prior-art search fails (default: fail)",
    )
    disclosure_parser.add_argument("--output-dir", required=True, type=Path, help="Directory for generated DOCX, Markdown, and diagram assets")
    disclosure_parser.add_argument(
        "--titles-file",
        type=Path,
        help="UTF-8 JSON array of explicit source_id/titles selections; otherwise the recommended candidate is used",
    )
    disclosure_conflicts = disclosure_parser.add_mutually_exclusive_group()
    disclosure_conflicts.add_argument("--skip-existing", action="store_true", help="Skip existing same-name DOCX or Markdown files")
    disclosure_conflicts.add_argument("--overwrite", action="store_true", help="Replace existing same-name DOCX and Markdown after validation")
    disclosure_parser.add_argument("--api-rpm", type=int, help="Override PATENT_API_RPM")
    disclosure_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    disclosure_parser.add_argument("--dry-run", action="store_true", help="Validate sources and list planned title groups without calling the API")
    disclosure_resume = disclosure_parser.add_mutually_exclusive_group()
    disclosure_resume.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Resume validated disclosure stages from the output-side work directory (default)",
    )
    disclosure_resume.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Ignore semantic workflow checkpoints and regenerate model stages",
    )

    polish_parser = subparsers.add_parser("polish", help="Polish a generated engineering disclosure DOCX")
    polish_parser.add_argument("--input", required=True, type=Path, help="Existing technical disclosure DOCX")
    polish_parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env config file")
    return parser


def polish(args: argparse.Namespace, progress: ProgressCallback | None = None) -> int:
    tracker = ProgressTracker("polish", progress)
    tracker.preparing("正在准备润色", "正在读取技术交底书 DOCX")
    try:
        config = load_config(env_path=args.env_file)
        client = RateLimitedOpenAIClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            rpm=config.api_rpm,
            max_retries=config.max_retries,
            timeout=config.api_timeout_seconds,
        )
        result = polish_disclosure_docx(args.input, client, tracker)
    except (ConfigError, PolishError) as exc:
        print(f"FAIL\t{type(exc).__name__}\t{exc}")
        tracker.fail("润色失败", str(exc))
        return 2 if isinstance(exc, ConfigError) else 1
    except Exception as exc:
        # API response bodies can contain source text. Report the error type/status only.
        detail = type(exc).__name__
        if isinstance(exc, HttpResponseError):
            detail += f" (HTTP {exc.status_code})"
        print(f"FAIL\t{detail}")
        tracker.fail("润色失败", detail)
        return 1

    print(f"OK\t{result.output}")
    if result.rejected:
        print(f"WARNING\t{result.rejected} 段未通过校验，已保留原文。")
    if not result.changed:
        print("INFO\t没有可采用的改写，副本保留原文。" if result.eligible else "INFO\t未发现可润色的普通正文段落，已保存原文副本。")
    stage = "润色完成（有警告）" if result.rejected else "润色完成"
    tracker.succeed(stage, str(result.output))
    return 0


def elephant_horse(
    args: argparse.Namespace,
    progress: ProgressCallback | None = None,
    search_failure: SearchFailureCallback | None = None,
) -> int:
    tracker = ProgressTracker("elephant-horse", progress)
    tracker.preparing("正在准备象马组合", "正在校验报告、附录和专利证据")
    try:
        if args.output.suffix.lower() != ".docx":
            raise ValueError("--output 必须使用 .docx 扩展名")
        if args.output.resolve() == args.report.resolve():
            raise ValueError("--output 不能覆盖输入分析报告")
        if args.output.exists() and not args.overwrite:
            raise FileExistsError(f"输出已存在：{args.output}；请使用 --overwrite 覆盖")
        inputs = load_elephant_horse_inputs(args.report)
        expected = default_elephant_horse_output(args.report, inputs.target.label)
        print(f"INPUT\treport={inputs.report_path}\tappendix={inputs.appendix_path}")
        for item in inputs.portfolio_sources:
            print(f"PORTFOLIO\t{item.label}\tpatents={item.coverage_count}\tsummaries={item.summary_count}\troot={item.root}")
        print(f"THEMES\t{len(inputs.sources)}")
        print(f"DEFAULT_OUTPUT\t{expected}")

        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
        log_dir = args.output.parent / ".patent_summary_logs"
        debug_path = log_dir / f"{args.output.stem}.llm-debug.jsonl"
        base_client = RateLimitedOpenAIClient(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            rpm=config.api_rpm,
            max_retries=config.max_retries,
            timeout=config.api_timeout_seconds,
            debug_path=debug_path,
        )
        client = (
            ReportCheckpointingClient(base_client, log_dir / f"{args.output.stem}.elephant-horse.jsonl")
            if args.resume
            else base_client
        )
        failure_callback = search_failure
        if failure_callback is None and args.search_failure == "continue":
            failure_callback = lambda _title, _error: SearchFailureAction.CONTINUE
        tracker.start(len(inputs.sources) + 1, "正在分析象马组合", "开始处理全部待布局建议点")

        def on_theme(detail: str, started: bool) -> None:
            if started:
                tracker.set_stage("正在分析象马组合", detail)
            else:
                tracker.advance("正在分析象马组合", detail)

        result = ElephantHorseAnalyzer(client).analyze(
            inputs,
            expanded_search=args.expanded_search,
            search_failure=failure_callback,
            on_theme=on_theme,
        )
        template = _elephant_horse_template(args.env_file)
        tracker.set_stage("正在写入象马组合报告", "正在生成单一 DOCX")
        output = write_elephant_horse_report(result, template, args.output, overwrite=args.overwrite)
        tracker.advance("正在写入象马组合报告", "DOCX 已写入")
        tracker.succeed("象马组合报告生成完成", str(output))
    except ElephantHorseCancelled as exc:
        tracker.cancel("象马组合生成已取消", "未写入输出 DOCX")
        print(f"CANCELLED\t{exc}")
        return ELEPHANT_HORSE_CANCELLED_EXIT_CODE
    except (ConfigError, RuntimeError, FileNotFoundError, NotADirectoryError, ValueError, FileExistsError) as exc:
        print(f"Elephant-horse error: {exc}")
        return 1
    print(f"Done. elephant_horse_report={output}, themes={len(result.themes)}, scope={result.overall_scope}")
    return 0


def _elephant_horse_template(env_file: Path) -> Path:
    name = "汽车底盘领域专利布局分析报告模板.docx"
    candidates = [
        Path(env_file).resolve().parent / name,
        Path.cwd() / "output" / "doc" / name,
        Path.cwd() / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"未找到内置报告模板：{name}")


def report(
    args: argparse.Namespace,
    progress: ProgressCallback | None = None,
    report_review: ReportReviewCallback | None = None,
) -> int:
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
    appendix_output = derive_appendix_output(args.output)
    if not args.classes.exists():
        print(f"Input error: taxonomy workbook does not exist: {args.classes}")
        return 2
    if not args.template.exists():
        print(f"Input error: DOCX template does not exist: {args.template}")
        return 2
    existing_outputs = [path for path in (args.output, appendix_output) if path.exists()]
    if existing_outputs and not args.overwrite:
        paths = "；".join(str(path) for path in existing_outputs)
        print(f"Input error: output already exists: {paths}; use --overwrite to replace it")
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
        print(f"APPENDIX\t{appendix_output.resolve()}")
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

    log_dir = args.output.parent / ".patent_summary_logs"
    debug_path = log_dir / f"{args.output.stem}.llm-debug.jsonl"
    base_client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
        timeout=config.api_timeout_seconds,
        debug_path=debug_path,
    )
    client = ReportCheckpointingClient(base_client, log_dir / f"{args.output.stem}.report.jsonl")
    print(f"LLM_DEBUG\t{debug_path}")

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
        overview_units = len(baselines) + 2
        tracker.start(
            trend_units + comparison_units + overview_units + 3 + 2 + 1,
            "正在分析趋势",
            "已完成准备，开始分析",
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

        def on_validation_retry(stage: str, retry: int, error: str) -> None:
            print(f"REPORT_REPAIR\t{stage}\tretry={retry}\terror={error}")

        report_analyzer = ReportSynthesisAnalyzer(client, on_validation_retry=on_validation_retry)

        if progress is None:
            portfolio_overviews, competitor_analysis, target_analysis, executive_summary = report_analyzer.analyze(
                target,
                baselines,
                industry,
                trends,
                comparison,
            )
        else:
            portfolio_overviews, competitor_analysis, target_analysis, executive_summary = report_analyzer.analyze(
                target,
                baselines,
                industry,
                trends,
                comparison,
                on_stage=on_report_stage,
            )
        enhancements = ReportEnhancementAnalyzer(
            client,
            on_validation_retry=on_validation_retry,
        ).analyze(
            target,
            trends,
            comparison,
            portfolio_overviews,
            competitor_analysis,
            target_analysis,
            executive_summary=executive_summary,
            on_stage=on_report_stage if progress is not None else None,
        )
        if report_review is not None:
            tracker.set_stage("等待人工复核", "请复核第四、第五、第六章结论摘要")
            reviewed_summaries = report_review(enhancements.chapter_summaries)
            if reviewed_summaries is None:
                tracker.cancel("报告生成已取消", "未写入正文和附录")
                print("CANCELLED\t人工复核已取消，未写入正文和附录")
                return REPORT_CANCELLED_EXIT_CODE
            enhancements = replace(enhancements, chapter_summaries=reviewed_summaries)
        layout_report = PatentLayoutReport(
            target=target,
            baselines=baselines,
            industry=industry,
            trends=trends,
            comparison=comparison,
            portfolio_overviews=portfolio_overviews,
            competitor_analysis=competitor_analysis,
            target_analysis=target_analysis,
            executive_summary=executive_summary,
            taxonomy=[(item.first_level, item.second_level) for item in taxonomy.items],
            enhancements=enhancements,
        )
        tracker.set_stage("正在写入报告", "正在生成 DOCX 文件")
        report_output, appendix_output = write_patent_layout_report(
            layout_report,
            args.template,
            args.output,
            overwrite=args.overwrite,
        )
        tracker.advance("正在写入报告", "正文和附录已写入 DOCX")
        tracker.succeed("报告生成完成", "正文和附录已保存")
    except (RuntimeError, FileNotFoundError, NotADirectoryError, ValueError, FileExistsError) as exc:
        print(f"Report error: {exc}")
        print(f"LLM debug: {debug_path}")
        return 1

    print(
        f"Done. report={report_output}, appendix={appendix_output}, target_patents={len(target.records)}, "
        f"competitor_patents={sum(len(item.records) for item in baselines)}"
    )
    return 0


def disclosure(
    args: argparse.Namespace,
    progress: ProgressCallback | None = None,
    title_review: DisclosureTitleReviewCallback | None = None,
    preview_review: DisclosurePreviewReviewCallback | None = None,
    prior_art_failure: PriorArtFailureCallback | None = None,
) -> int:
    tracker = ProgressTracker("disclosure", progress)
    tracker.preparing("正在准备研发技术交底书", "正在读取技术来源和项目材料")
    try:
        output_dir = args.output_dir.resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise NotADirectoryError(f"输出路径不是目录：{output_dir}")
        scan_result = None
        if args.report is not None:
            sources = load_report_sources(args.report.resolve())
            source_key = args.report.stem
            material_roots = [path.resolve() for path in args.material_dir]
        elif args.project_dir is not None:
            material_roots = [args.project_dir.resolve(), *[path.resolve() for path in args.material_dir]]
            scan_result = scan_project_materials(material_roots, exclude_roots=[output_dir])
            sources = load_project_source(scan_result)
            source_key = f"project-{hashlib.sha256('|'.join(str(path) for path in material_roots).encode('utf-8')).hexdigest()[:12]}"
            material_roots = []
        else:
            if args.idea_file is not None:
                idea = args.idea_file.read_text(encoding="utf-8-sig")
            else:
                idea = args.idea or ""
            sources = load_idea_source(idea)
            source_key = f"manual-{hashlib.sha256(idea.encode('utf-8')).hexdigest()[:12]}"
            material_roots = [path.resolve() for path in args.material_dir]
        if material_roots:
            scan_result = scan_project_materials(material_roots, exclude_roots=[output_dir])
            sources = attach_materials(sources, scan_result)
        explicit_selections = load_title_selections(args.titles_file) if args.titles_file else None
        if explicit_selections is not None:
            selected_work_items(explicit_selections, sources)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 2

    if args.dry_run:
        for source in sources:
            print(f"DISCLOSURE_SOURCE\t{source.source_id}\t{source.label}")
        if scan_result is not None:
            for item in scan_result.documents:
                print(f"MATERIAL\t{item.status}\t{item.kind}\t{item.path}\t{item.message}")
        if explicit_selections is not None:
            for source, title in selected_work_items(explicit_selections, sources):
                print(f"DISCLOSURE_TITLE\t{source.source_id}\t{title}")
        else:
            print(f"CANDIDATE_PROPOSALS\tgroups={len(sources)}\tper_group=1..5\tdefault=recommended")
        print(f"PRIOR_ART\t{'enabled' if args.prior_art else 'disabled'}")
        print(f"OUTPUT_DIR\t{output_dir}")
        return 0

    try:
        config = load_config(env_path=args.env_file, api_rpm=args.api_rpm)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    log_dir = output_dir / ".patent_summary_logs"
    debug_path = log_dir / f"{source_key}.disclosure.llm-debug.jsonl"
    base_client = RateLimitedOpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        rpm=config.api_rpm,
        max_retries=config.max_retries,
        timeout=config.api_timeout_seconds,
        debug_path=debug_path,
    )
    client = (
        ReportCheckpointingClient(
            base_client,
            log_dir / f"{source_key}.disclosure.jsonl",
        )
        if args.resume
        else base_client
    )

    def on_validation_retry(stage: str, retry: int, error: str) -> None:
        print(f"DISCLOSURE_REPAIR\t{stage}\tretry={retry}\terror={error}")

    def on_validation_warning(stage: str, error: str) -> None:
        print(f"WARNING\t{stage}\t{error}\t已按本地容错规则继续生成")

    checkpoint_store = DisclosureCheckpointStore(
        output_dir / ".patent_disclosure_work" / source_key / "workflow",
        model=config.model,
        read_enabled=args.resume,
    )
    analyzer = DisclosureAnalyzer(
        client,
        on_validation_retry=on_validation_retry,
        on_validation_warning=on_validation_warning,
        checkpoint_store=checkpoint_store,
    )

    def perform_prior_art_search(
        source_id: str,
        title: str,
        terms: tuple[str, ...],
    ) -> PriorArtResult | None:
        tracker.set_stage("正在执行可选查新", title)
        while True:
            try:
                result = CnipaPriorArtSearcher().search(terms)
                print(
                    f"PRIOR_ART\t{source_id}\t{result.status.value}\t"
                    f"records={len(result.records)}\t{result.message}"
                )
                return result
            except PriorArtSearchError as exc:
                action = _prior_art_failure_action(args, prior_art_failure, title, str(exc))
                if action == PriorArtFailureAction.RETRY:
                    continue
                if action == PriorArtFailureAction.SKIP:
                    result = PriorArtResult(
                        PriorArtStatus.FAILED,
                        keywords=terms,
                        message=f"查新失败后由用户明确跳过：{exc}",
                    )
                    print(f"PRIOR_ART\t{source_id}\tfailed-skipped\t{exc}")
                    return result
                tracker.cancel("研发技术交底书生成已取消", "查新失败后取消")
                print(f"CANCELLED\t查新失败后取消：{exc}")
                return None
    try:
        if scan_result is not None:
            manifest = output_dir / ".patent_disclosure_work" / source_key / "material_manifest.json"
            write_scan_manifest(scan_result, manifest)
            for item in scan_result.documents:
                print(f"MATERIAL\t{item.status}\t{item.kind}\t{item.path}\t{item.message}")
            print(f"MATERIAL_MANIFEST\t{manifest}")

        proposals: list = []
        if explicit_selections is None:
            tracker.start(len(sources), "正在挖掘候选发明方案", "每条来源生成 1 至 5 个有实质差异的方案")
            for source in sources:
                tracker.set_stage("正在挖掘候选发明方案", f"{source.source_id} {source.label}")
                proposal = analyzer.propose_titles(source)
                proposals.append(proposal)
                for candidate in proposal.candidates:
                    print(f"CANDIDATE\t{source.source_id}\t{candidate.candidate_id}\trecommended={str(candidate.recommended).lower()}\t{candidate.title}")
                tracker.advance("正在挖掘候选发明方案", f"已完成 {source.source_id}")
            selection_key = (
                checkpoint_store.key(
                    "\n".join(source.context for source in sources),
                    "confirmed-selections",
                    {"source_ids": [source.source_id for source in sources]},
                )
                if checkpoint_store is not None
                else None
            )
            cached_selection = (
                checkpoint_store.load_payload(selection_key, "confirmed-selections")
                if checkpoint_store is not None and selection_key is not None
                else None
            )
            if cached_selection is not None:
                selections = [
                    TitleSelection(
                        str(item["source_id"]),
                        [str(title) for title in item["titles"]],
                    )
                    for item in cached_selection.get("selections", [])
                    if isinstance(item, dict)
                    and item.get("source_id")
                    and isinstance(item.get("titles"), list)
                ]
                conflict_policy = _disclosure_conflict_policy(args)
                print("RESUME\tconfirmed-selections")
            elif title_review is not None:
                tracker.set_stage("等待第一次确认", "请选择、编辑或融合候选发明方案")
                reviewed = title_review(proposals, output_dir)
                if reviewed is None:
                    tracker.cancel("研发技术交底书生成已取消", "未生成正文、DOCX 或 Markdown")
                    print("CANCELLED\t候选方案确认已取消，未生成正文、DOCX 或 Markdown")
                    return DISCLOSURE_CANCELLED_EXIT_CODE
                selections = reviewed.selections
                conflict_policy = ConflictPolicy(reviewed.conflict_policy)
            else:
                selections = _recommended_disclosure_selections(proposals)
                conflict_policy = _disclosure_conflict_policy(args)
            if (
                cached_selection is None
                and checkpoint_store is not None
                and selection_key is not None
            ):
                checkpoint_store.save_payload(
                    selection_key,
                    "confirmed-selections",
                    {
                        "selections": [
                            {"source_id": item.source_id, "titles": list(item.titles)}
                            for item in selections
                        ],
                        "conflict_policy": conflict_policy.value,
                    },
                )
        else:
            selections = explicit_selections
            conflict_policy = _disclosure_conflict_policy(args)

        work_items = selected_work_items(selections, sources)
        if not work_items:
            tracker.cancel("研发技术交底书生成已取消", "没有保留任何发明方案")
            print("CANCELLED\t没有保留任何发明方案，未生成正文、DOCX 或 Markdown")
            return DISCLOSURE_CANCELLED_EXIT_CODE

        conflicts = []
        resumed_complete = []
        for _source, title in work_items:
            paths = disclosure_output_paths(output_dir, title)
            if paths.docx.exists() or paths.markdown.exists():
                if (
                    args.resume
                    and conflict_policy == ConflictPolicy.ERROR
                    and existing_disclosure_bundle_complete(paths)
                ):
                    resumed_complete.append(paths.docx)
                else:
                    conflicts.append(paths.docx)
        if conflicts and conflict_policy == ConflictPolicy.ERROR:
            paths = "；".join(str(path) for path in conflicts)
            raise FileExistsError(f"技术交底书已经存在：{paths}")

        skipped = set(resumed_complete)
        if conflict_policy == ConflictPolicy.SKIP:
            skipped.update(conflicts)
        pending = [
            (source, title, disclosure_output_paths(output_dir, title))
            for source, title in work_items
            if disclosure_output_paths(output_dir, title).docx not in skipped
        ]
        for path in sorted(skipped, key=str):
            reason = "resume-complete" if path in resumed_complete else "existing"
            print(f"SKIP\t{reason}\t{path}")

        proposal_map = {proposal.source.source_id: proposal for proposal in proposals}
        confirmed_items = []
        tracker.start(
            len(pending),
            "正在完善选中的技术方案",
            "依次执行可选查新并形成完整方案预览",
        )
        for source, title, paths in pending:
            candidate = find_candidate(proposal_map[source.source_id], title) if source.source_id in proposal_map else None
            preview = analyzer.load_confirmed_preview(
                source,
                title,
                candidate,
                prior_art_enabled=args.prior_art,
            )
            if preview is not None:
                print(f"RESUME\t{source.source_id}\t{title}\tconfirmed-preview")
            else:
                prior_art = PriorArtResult.disabled()
                if args.prior_art:
                    terms = analyzer.plan_search_terms(source, title, candidate)
                    searched = perform_prior_art_search(source.source_id, title, terms)
                    if searched is None:
                        return DISCLOSURE_CANCELLED_EXIT_CODE
                    prior_art = searched

                tracker.set_stage("正在形成完整技术方案", title)
                preview = analyzer.build_preview(source, title, candidate=candidate, prior_art=prior_art)
                if preview_review is not None:
                    while True:
                        tracker.set_stage("等待第二次确认", "请核对技术方案、假设、示例参数和待确认问题")
                        revised = preview_review(preview)
                        if revised is None:
                            tracker.cancel("研发技术交底书生成已取消", "第二次确认已取消，未生成正文")
                            print("CANCELLED\t完整技术方案确认已取消，未生成正文、DOCX 或 Markdown")
                            return DISCLOSURE_CANCELLED_EXIT_CODE
                        if revised.title != title or revised.source_id != source.source_id:
                            raise ValueError("完整技术方案确认结果不得改变来源编号或已确认标题")
                        if args.prior_art and _preview_search_signature(revised) != _preview_search_signature(preview):
                            print(f"PRIOR_ART_REQUERY\t{source.source_id}\t核心机制已修改，重新规划检索主题")
                            terms = analyzer.plan_search_terms_for_preview(source, revised)
                            searched = perform_prior_art_search(source.source_id, title, terms)
                            if searched is None:
                                return DISCLOSURE_CANCELLED_EXIT_CODE
                            preview = replace(revised, prior_art=searched)
                            continue
                        preview = revised
                        break
                analyzer.save_confirmed_preview(
                    source,
                    preview,
                    candidate,
                    prior_art_enabled=args.prior_art,
                )
            confirmed_items.append((source, title, paths, preview))
            tracker.advance("完整技术方案已经确认", title)

        tracker.start(len(confirmed_items) * 3, "正在生成研发技术交底书", "开始逐篇撰写、独立审校和图文导出")
        written: list[Path] = []
        failures: list[DisclosureFailure] = []
        warnings = []
        for source, title, paths, preview in confirmed_items:
            completed_units = 0
            try:
                tracker.set_stage("正在撰写研发技术交底书", title)
                draft = analyzer.draft(source, title, preview)
                tracker.advance("正在撰写研发技术交底书", f"{title} 初稿完成")
                completed_units += 1

                tracker.set_stage("正在独立技术审校", title)
                reviewed_disclosure = analyzer.review(source, draft, preview)
                tracker.advance("正在独立审校", f"{title} 审校完成")
                completed_units += 1

                tracker.set_stage("正在生成图示并核验双格式文件", title)
                bundle = write_disclosure_bundle(
                    reviewed_disclosure,
                    output_dir,
                    overwrite=conflict_policy == ConflictPolicy.OVERWRITE,
                )
                written.append(bundle.docx)
                for diagnostic in getattr(bundle, "diagnostics", ()):
                    warnings.append(diagnostic)
                    print(
                        f"DISCLOSURE_WARNING\t{source.source_id}\t{title}\t"
                        f"{diagnostic.code}\t{diagnostic.message}\t{diagnostic.action}"
                    )
                warning_count = len(getattr(bundle, "diagnostics", ()))
                tracker.advance(
                    "正在核验并写入双格式文件",
                    f"{title} 已保存" + (f"（警告 {warning_count} 项）" if warning_count else ""),
                )
                completed_units += 1
                status = "OK_WITH_WARNINGS" if warning_count else "OK"
                print(f"{status}\t{source.source_id}\t{bundle.docx}")
                print(f"MARKDOWN\t{source.source_id}\t{bundle.markdown}")
            except Exception as exc:
                failures.append(DisclosureFailure(source.source_id, title, str(exc)))
                print(f"FAIL\t{source.source_id}\t{title}\t{exc}")
            finally:
                remaining = 3 - completed_units
                if remaining:
                    tracker.advance("研发技术交底书处理失败", title, units=remaining)

        result = DisclosureGenerationResult(
            written=written,
            skipped=sorted(skipped, key=str),
            failures=failures,
            warnings=warnings,
        )
        if result.failures:
            tracker.fail(
                "部分研发技术交底书生成失败",
                f"成功 {len(result.written)}，跳过 {len(result.skipped)}，失败 {len(result.failures)}",
            )
            print(
                f"Done. written={len(result.written)}, skipped={len(result.skipped)}, "
                f"failed={len(result.failures)}, output_dir={output_dir}"
            )
            return 1
        tracker.succeed(
            "研发技术交底书生成完成" if not result.warnings else "研发技术交底书生成完成（有警告）",
            f"已保存 {len(result.written)} 份，跳过 {len(result.skipped)} 份，警告 {len(result.warnings)} 项",
        )
        print(
            f"Done. written={len(result.written)}, skipped={len(result.skipped)}, "
            f"failed=0, warnings={len(result.warnings)}, output_dir={output_dir}"
        )
        return 0
    except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError) as exc:
        tracker.fail("研发技术交底书生成失败", str(exc))
        print(f"Disclosure error: {exc}")
        print(f"LLM debug: {debug_path}")
        return 1


def _recommended_disclosure_selections(proposals) -> list[TitleSelection]:
    selections: list[TitleSelection] = []
    for proposal in proposals:
        recommended = [item.title for item in proposal.candidates if item.recommended]
        titles = recommended[:1] or proposal.titles[:1]
        selections.append(TitleSelection(proposal.source.source_id, titles))
    return selections


def _prior_art_failure_action(
    args: argparse.Namespace,
    callback: PriorArtFailureCallback | None,
    title: str,
    error: str,
) -> PriorArtFailureAction:
    if callback is not None:
        return PriorArtFailureAction(callback(title, error))
    if args.prior_art_failure == "skip":
        return PriorArtFailureAction.SKIP
    raise PriorArtSearchError(error)


def _preview_search_signature(preview) -> tuple:
    return (
        preview.technical_problems,
        tuple(
            (
                module.name,
                module.responsibility,
                module.inputs,
                module.outputs,
            )
            for module in preview.modules
        ),
        preview.process_steps,
        preview.key_points,
    )


def _delivery_repairable(error: Exception) -> bool:
    message = str(error)
    return any(
        marker in message
        for marker in ("图示", "图片", "Mermaid", "公式", "LaTeX", "参数表", "DOCX", "Markdown")
    )


def _disclosure_conflict_policy(args: argparse.Namespace) -> ConflictPolicy:
    if args.overwrite:
        return ConflictPolicy.OVERWRITE
    if args.skip_existing:
        return ConflictPolicy.SKIP
    return ConflictPolicy.ERROR


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
    tracker.start(len(items), "正在总结专利")

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
        timeout=config.api_timeout_seconds,
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
            tracker.advance("正在总结专利")

    print(f"Done. success={success}, failed={failed}, pending={len(items)}")
    exit_code = 0 if failed == 0 else 1
    if exit_code == 0:
        tracker.succeed("批量总结完成", "结果已保存")
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
    tracker.start(len(items) + 1, "正在分类专利")

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
        timeout=config.api_timeout_seconds,
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
            tracker.advance("正在分类专利")
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
                tracker.advance("正在分类专利")

    tracker.set_stage("正在写入分类结果", "正在生成 Excel 文件")
    write_classification_workbook(rows, args.output)
    tracker.advance("正在写入分类结果", "分类结果已写入 Excel")
    print(f"Done. rows={len(rows)}, failed={failed}, output={args.output}")
    exit_code = 0 if failed == 0 else 1
    if exit_code == 0:
        tracker.succeed("分类完成", "结果已保存")
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
        timeout=config.api_timeout_seconds,
    )
    analyzer = TrendAnalyzer(client, batch_size=args.batch_size)
    checkpoint = TrendCheckpointStore(args.output.parent / ".patent_summary_logs" / f"{args.output.stem}.jsonl")
    model_units = trend_work_units(records, args.batch_size, checkpoint)
    tracker.start(model_units + 1, "正在分析趋势", "已完成准备，开始分析")

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
    tracker.succeed("趋势分析完成", "结果已保存")
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
        timeout=config.api_timeout_seconds,
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
    tracker.start(comparison_units + 1, "正在进行组合对比", "已完成准备，开始对比")

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
    tracker.succeed("组合对比完成", "结果已保存")
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
