from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .classification_pipeline import CheckpointStore
from .classifier import ChatClient, classify_summary
from .excel_export import ClassificationExportRow, write_classification_workbook
from .markdown_summary import parse_summary_sections
from .metadata import parse_bibliographic_metadata
from .prompts import COMPARISON_ANALYSIS_PROMPT, COMPARISON_SYSTEM_PROMPT
from .taxonomy import Taxonomy
from .trend_analysis import TrendPatentRecord, load_classification_workbook


COMPARISON_REPORT_FIELDS = [
    "整体结论",
    "A布局概况",
    "相比外部的优势",
    "相比外部的短板",
    "差异化判断",
    "是否主流布局策略",
    "建议布局点",
    "证据局限",
]


class ComparisonChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class PortfolioDataset:
    label: str
    root: Path
    classification_path: Path
    classification_source: str
    records: list[TrendPatentRecord]


@dataclass(frozen=True)
class LabelComparison:
    target_label: str
    baseline_label: str
    first_level: str
    second_level: str
    target_count: int
    baseline_count: int
    target_share: float
    baseline_share: float
    share_delta: float
    category: str
    priority_score: float
    target_year_counts: dict[str, int]
    baseline_year_counts: dict[str, int]
    target_year_range: str
    baseline_year_range: str
    target_peak_year: str
    baseline_peak_year: str
    target_keywords: list[str]
    baseline_keywords: list[str]
    target_representatives: list[str]
    baseline_representatives: list[str]

    @property
    def comparison_name(self) -> str:
        return f"{self.target_label} vs {self.baseline_label}"


@dataclass(frozen=True)
class ComparisonReport:
    target_label: str
    baseline_label: str
    baseline_root: Path
    label_comparisons: list[LabelComparison]
    overall_conclusion: str
    target_layout: str
    advantages: str
    weaknesses: str
    differentiation: str
    mainstream_judgement: str
    recommendations: str
    evidence_limits: str

    @property
    def comparison_name(self) -> str:
        return f"{self.target_label} vs {self.baseline_label}"


@dataclass(frozen=True)
class ComparisonResult:
    target: PortfolioDataset
    baselines: list[PortfolioDataset]
    combined_baseline: PortfolioDataset | None
    reports: list[ComparisonReport]


@dataclass(frozen=True)
class _LabelStats:
    first_level: str
    second_level: str
    count: int
    year_counts: dict[str, int]
    year_range: str
    peak_year: str
    top_keywords: list[str]
    representative_patents: list[str]
    records: list[TrendPatentRecord]


class ComparisonAnalyzer:
    def __init__(self, client: ComparisonChatClient, max_prompt_labels: int = 16):
        if max_prompt_labels <= 0:
            raise ValueError("max_prompt_labels must be positive")
        self.client = client
        self.max_prompt_labels = max_prompt_labels

    def analyze(self, target: PortfolioDataset, baselines: list[PortfolioDataset]) -> ComparisonResult:
        reports = [self._analyze_pair(target, baseline) for baseline in baselines]
        combined = _combined_baseline(baselines) if len(baselines) > 1 else None
        if combined is not None:
            reports.append(self._analyze_pair(target, combined))
        return ComparisonResult(target=target, baselines=baselines, combined_baseline=combined, reports=reports)

    def _analyze_pair(self, target: PortfolioDataset, baseline: PortfolioDataset) -> ComparisonReport:
        label_comparisons = build_label_comparisons(target, baseline)
        response = self.client.chat(
            COMPARISON_SYSTEM_PROMPT,
            COMPARISON_ANALYSIS_PROMPT.format(
                target_label=target.label,
                baseline_label=baseline.label,
                target_stats=json.dumps(_portfolio_payload(target), ensure_ascii=False, indent=2),
                baseline_stats=json.dumps(_portfolio_payload(baseline), ensure_ascii=False, indent=2),
                label_comparisons=json.dumps(
                    [_label_comparison_payload(item) for item in _prompt_label_subset(label_comparisons, self.max_prompt_labels)],
                    ensure_ascii=False,
                    indent=2,
                ),
                evidence=_evidence_digest(_prompt_label_subset(label_comparisons, self.max_prompt_labels)),
            ),
        )
        payload = parse_comparison_response(response)
        return ComparisonReport(
            target_label=target.label,
            baseline_label=baseline.label,
            baseline_root=baseline.root,
            label_comparisons=label_comparisons,
            overall_conclusion=payload["整体结论"],
            target_layout=payload["A布局概况"],
            advantages=payload["相比外部的优势"],
            weaknesses=payload["相比外部的短板"],
            differentiation=payload["差异化判断"],
            mainstream_judgement=payload["是否主流布局策略"],
            recommendations=payload["建议布局点"],
            evidence_limits=payload["证据局限"],
        )


def load_or_build_portfolio(
    root: Path,
    label: str | None,
    client: ChatClient | None,
    taxonomy: Taxonomy | None,
    recursive: bool,
    refresh_classification: bool,
    classification_filename: str = "patent_classification.xlsx",
) -> PortfolioDataset:
    root = Path(root)
    portfolio_label = label or root.name or str(root)
    classification_path = root / classification_filename
    if classification_path.exists() and not refresh_classification:
        return PortfolioDataset(
            label=portfolio_label,
            root=root,
            classification_path=classification_path,
            classification_source="existing",
            records=load_classification_workbook(classification_path),
        )

    if client is None or taxonomy is None:
        raise ValueError("client and taxonomy are required when classification workbook is missing or refreshed")

    rows = _classify_markdown_summaries(root, client, taxonomy, recursive, refresh_classification)
    write_classification_workbook(rows, classification_path)
    return PortfolioDataset(
        label=portfolio_label,
        root=root,
        classification_path=classification_path,
        classification_source="generated",
        records=load_classification_workbook(classification_path),
    )


def build_label_comparisons(target: PortfolioDataset, baseline: PortfolioDataset) -> list[LabelComparison]:
    target_stats = _label_stats(target.records)
    baseline_stats = _label_stats(baseline.records)
    target_total = sum(stats.count for stats in target_stats.values())
    baseline_total = sum(stats.count for stats in baseline_stats.values())
    keys = sorted(set(target_stats) | set(baseline_stats))

    comparisons: list[LabelComparison] = []
    for first_level, second_level in keys:
        target_item = target_stats.get((first_level, second_level)) or _empty_stats(first_level, second_level)
        baseline_item = baseline_stats.get((first_level, second_level)) or _empty_stats(first_level, second_level)
        target_share = target_item.count / target_total if target_total else 0.0
        baseline_share = baseline_item.count / baseline_total if baseline_total else 0.0
        share_delta = target_share - baseline_share
        category = _comparison_category(target_item.count, baseline_item.count, target_share, baseline_share)
        comparisons.append(
            LabelComparison(
                target_label=target.label,
                baseline_label=baseline.label,
                first_level=first_level,
                second_level=second_level,
                target_count=target_item.count,
                baseline_count=baseline_item.count,
                target_share=round(target_share, 4),
                baseline_share=round(baseline_share, 4),
                share_delta=round(share_delta, 4),
                category=category,
                priority_score=_priority_score(category, target_share, baseline_share, baseline_item.count),
                target_year_counts=target_item.year_counts,
                baseline_year_counts=baseline_item.year_counts,
                target_year_range=target_item.year_range,
                baseline_year_range=baseline_item.year_range,
                target_peak_year=target_item.peak_year,
                baseline_peak_year=baseline_item.peak_year,
                target_keywords=target_item.top_keywords,
                baseline_keywords=baseline_item.top_keywords,
                target_representatives=target_item.representative_patents,
                baseline_representatives=baseline_item.representative_patents,
            )
        )
    return sorted(comparisons, key=lambda item: (-item.priority_score, item.first_level, item.second_level))


def parse_comparison_response(response: str | Mapping[str, Any]) -> dict[str, str]:
    payload = response if isinstance(response, Mapping) else json.loads(_extract_json(response))
    return {field: _stringify(payload.get(field, "")) for field in COMPARISON_REPORT_FIELDS}


def _classify_markdown_summaries(
    root: Path,
    client: ChatClient,
    taxonomy: Taxonomy,
    recursive: bool,
    refresh_classification: bool,
) -> list[ClassificationExportRow]:
    checkpoint = CheckpointStore(root / ".patent_summary_logs" / "markdown_classifications.jsonl")
    successes = {} if refresh_classification else checkpoint.load_successes()
    rows: list[ClassificationExportRow] = []
    for summary_path in _discover_markdown_summaries(root, recursive):
        key = str(summary_path)
        if key in successes:
            rows.append(successes[key])
            continue
        row = _classify_markdown_summary(summary_path, client, taxonomy)
        checkpoint.record_success(row)
        rows.append(row)
    return rows


def _discover_markdown_summaries(root: Path, recursive: bool) -> Iterable[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Input path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {root}")
    paths = root.rglob("*.md") if recursive else root.glob("*.md")
    for path in sorted(paths, key=lambda item: str(item).lower()):
        if ".patent_summary_logs" in path.parts:
            continue
        yield path


def _classify_markdown_summary(summary_path: Path, client: ChatClient, taxonomy: Taxonomy) -> ClassificationExportRow:
    summary = summary_path.read_text(encoding="utf-8", errors="ignore")
    sections = parse_summary_sections(summary)
    metadata = parse_bibliographic_metadata(sections.get("著录信息", ""))
    result = classify_summary(client, summary_path.name, summary, taxonomy)
    return ClassificationExportRow(
        filename=summary_path.name,
        patent_name=sections.get("专利主题") or summary_path.stem,
        source_path=str(summary_path),
        summary_path=str(summary_path),
        primary_first_level=result.primary_first_level,
        primary_second_level=result.primary_second_level,
        primary_confidence=result.primary_confidence,
        candidate_summary=result.candidate_summary,
        review_status=result.review_status,
        reason=result.reason,
        sections=sections,
        metadata=metadata,
    )


def _label_stats(records: Iterable[TrendPatentRecord]) -> dict[tuple[str, str], _LabelStats]:
    grouped: dict[tuple[str, str], list[TrendPatentRecord]] = defaultdict(list)
    for record in records:
        if not record.primary_second_level.strip():
            continue
        grouped[(record.primary_first_level, record.primary_second_level)].append(record)

    return {
        key: _build_stats(key[0], key[1], group_records)
        for key, group_records in grouped.items()
    }


def _build_stats(first_level: str, second_level: str, records: list[TrendPatentRecord]) -> _LabelStats:
    year_counts = Counter(record.inferred_year or "未知" for record in records)
    known_years = sorted(year for year in year_counts if year != "未知")
    representatives = sorted(
        records,
        key=lambda item: (item.primary_confidence is not None, item.primary_confidence or 0.0, item.filename),
        reverse=True,
    )[:5]
    return _LabelStats(
        first_level=first_level,
        second_level=second_level,
        count=len(records),
        year_counts=dict(sorted(year_counts.items())),
        year_range=f"{known_years[0]}-{known_years[-1]}" if known_years else "未知",
        peak_year=_peak_year(year_counts),
        top_keywords=[keyword for keyword, _count in Counter(_iter_keywords(records)).most_common(10)],
        representative_patents=[_representative_name(record) for record in representatives],
        records=records,
    )


def _empty_stats(first_level: str, second_level: str) -> _LabelStats:
    return _LabelStats(
        first_level=first_level,
        second_level=second_level,
        count=0,
        year_counts={},
        year_range="无",
        peak_year="无",
        top_keywords=[],
        representative_patents=[],
        records=[],
    )


def _comparison_category(target_count: int, baseline_count: int, target_share: float, baseline_share: float) -> str:
    if target_count > 0 and baseline_count == 0:
        return "A差异化"
    if target_count == 0 and baseline_count > 0:
        return "A短板"
    if target_count == 0 and baseline_count == 0:
        return "无布局"
    if target_share - baseline_share >= 0.1 or target_share >= baseline_share * 1.5:
        return "A优势"
    if baseline_share - target_share >= 0.1 or baseline_share >= target_share * 1.5:
        return "A短板"
    return "共同布局"


def _priority_score(category: str, target_share: float, baseline_share: float, baseline_count: int) -> float:
    if category == "A短板":
        score = max(0.0, baseline_share - target_share) * 100 + min(baseline_count, 20) + 10
    elif category == "A差异化":
        score = target_share * 60
    elif category == "A优势":
        score = max(0.0, target_share - baseline_share) * 40
    else:
        score = abs(baseline_share - target_share) * 20
    return round(score, 2)


def _combined_baseline(baselines: list[PortfolioDataset]) -> PortfolioDataset:
    records = [record for baseline in baselines for record in baseline.records]
    return PortfolioDataset(
        label="外部总体",
        root=Path("；".join(str(baseline.root) for baseline in baselines)),
        classification_path=Path(""),
        classification_source="combined",
        records=records,
    )


def _portfolio_payload(portfolio: PortfolioDataset) -> dict[str, object]:
    stats = _label_stats(portfolio.records)
    top_labels = sorted(stats.values(), key=lambda item: (-item.count, item.first_level, item.second_level))[:10]
    return {
        "组合": portfolio.label,
        "目录": str(portfolio.root),
        "专利数": len(portfolio.records),
        "有效分类专利数": sum(item.count for item in stats.values()),
        "标签数": len(stats),
        "分类来源": portfolio.classification_source,
        "头部标签": [
            {
                "一级分类": item.first_level,
                "二级分类": item.second_level,
                "专利数": item.count,
                "年份范围": item.year_range,
                "峰值年份": item.peak_year,
                "关键词": item.top_keywords,
            }
            for item in top_labels
        ],
    }


def _label_comparison_payload(item: LabelComparison) -> dict[str, object]:
    return {
        "一级分类": item.first_level,
        "二级分类": item.second_level,
        "A专利数": item.target_count,
        "外部专利数": item.baseline_count,
        "A占比": item.target_share,
        "外部占比": item.baseline_share,
        "占比差": item.share_delta,
        "判断": item.category,
        "建议优先级分": item.priority_score,
        "A关键词": item.target_keywords,
        "外部关键词": item.baseline_keywords,
        "A代表专利": item.target_representatives,
        "外部代表专利": item.baseline_representatives,
    }


def _prompt_label_subset(items: list[LabelComparison], limit: int) -> list[LabelComparison]:
    return sorted(items, key=lambda item: (-item.priority_score, -abs(item.share_delta), item.second_level))[:limit]


def _evidence_digest(items: list[LabelComparison]) -> str:
    sections: list[str] = []
    for item in items:
        sections.append(
            "\n".join(
                [
                    f"标签：{item.first_level} / {item.second_level}",
                    f"判断：{item.category}；优先级分：{item.priority_score}",
                    f"A代表：{'；'.join(item.target_representatives) or '无'}",
                    f"外部代表：{'；'.join(item.baseline_representatives) or '无'}",
                    f"A关键词：{'、'.join(item.target_keywords) or '无'}",
                    f"外部关键词：{'、'.join(item.baseline_keywords) or '无'}",
                ]
            )
        )
    return "\n\n".join(sections)


def _iter_keywords(records: list[TrendPatentRecord]) -> Iterable[str]:
    for record in records:
        text = record.sections.get("潜在分类关键词", "")
        for raw in text.splitlines():
            keyword = re.sub(r"^[\-\*\u2022\d\.\s、]+", "", raw).strip()
            if keyword:
                yield keyword


def _representative_name(record: TrendPatentRecord) -> str:
    if record.patent_name:
        return f"{record.filename}｜{record.patent_name}"
    return record.filename


def _peak_year(year_counts: Counter[str]) -> str:
    known = {year: count for year, count in year_counts.items() if year != "未知"}
    if not known:
        return "未知"
    return max(sorted(known), key=lambda year: (known[year], year))


def _extract_json(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Comparison response is not JSON: {text}")
    return stripped[start : end + 1]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()
