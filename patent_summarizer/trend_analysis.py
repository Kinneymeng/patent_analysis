from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from .markdown_summary import SUMMARY_SECTION_NAMES
from .metadata import BIBLIOGRAPHIC_FIELD_NAMES, infer_patent_year, parse_bibliographic_metadata
from .prompts import TREND_ANALYSIS_PROMPT, TREND_MERGE_PROMPT, TREND_SYSTEM_PROMPT


TREND_REPORT_FIELDS = [
    "发展现状",
    "发展阶段",
    "主要技术路线",
    "热点方向",
    "机会与空白",
    "风险/不确定性",
    "代表专利",
    "证据局限",
]


class TrendChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class TrendPatentRecord:
    filename: str
    patent_name: str
    source_path: str
    summary_path: str
    primary_first_level: str
    primary_second_level: str
    primary_confidence: float | None
    candidate_summary: str
    review_status: str
    reason: str
    metadata: dict[str, str]
    sections: dict[str, str]
    inferred_year: str


@dataclass(frozen=True)
class TrendGroupStats:
    first_level: str
    second_level: str
    patent_count: int
    year_counts: dict[str, int]
    year_range: str
    peak_year: str
    average_confidence: float | None
    review_count: int
    top_keywords: list[str]
    representative_patents: list[str]


@dataclass(frozen=True)
class TrendReport:
    stats: TrendGroupStats
    status_summary: str
    development_stage: str
    technical_routes: str
    hot_topics: str
    opportunities: str
    risks: str
    representative_patents: str
    evidence_limits: str

    @property
    def key(self) -> str:
        return trend_key(self.stats.first_level, self.stats.second_level)


@dataclass(frozen=True)
class TrendAnalysisResult:
    reports: list[TrendReport]
    records: list[TrendPatentRecord]


class TrendAnalyzer:
    def __init__(self, client: TrendChatClient, batch_size: int = 40):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.client = client
        self.batch_size = batch_size

    def analyze(
        self,
        records: Iterable[TrendPatentRecord],
        checkpoint: TrendCheckpointStore | None = None,
        on_operation: Callable[[str, bool], None] | None = None,
    ) -> TrendAnalysisResult:
        all_records = list(records)
        cached = checkpoint.load_successes() if checkpoint else {}
        reports: list[TrendReport] = []
        for stats, group_records in build_trend_groups(all_records):
            key = trend_key(stats.first_level, stats.second_level)
            if key in cached:
                reports.append(cached[key])
                continue
            report = self._analyze_group(stats, group_records, on_operation)
            if checkpoint:
                checkpoint.record_success(report)
            reports.append(report)
        return TrendAnalysisResult(reports=reports, records=all_records)

    def _analyze_group(
        self,
        stats: TrendGroupStats,
        records: list[TrendPatentRecord],
        on_operation: Callable[[str, bool], None] | None,
    ) -> TrendReport:
        if len(records) <= self.batch_size:
            return self._call_trend_model(stats, records, scope="完整标签", on_operation=on_operation)

        batch_reports: list[TrendReport] = []
        for start in range(0, len(records), self.batch_size):
            chunk = records[start : start + self.batch_size]
            batch_reports.append(
                self._call_trend_model(
                    stats,
                    chunk,
                    scope=f"批次 {start // self.batch_size + 1}",
                    on_operation=on_operation,
                )
            )
        return self._merge_batch_reports(stats, batch_reports, on_operation)

    def _call_trend_model(
        self,
        stats: TrendGroupStats,
        records: list[TrendPatentRecord],
        scope: str,
        on_operation: Callable[[str, bool], None] | None,
    ) -> TrendReport:
        detail = f"{stats.first_level} / {stats.second_level}（{scope}）"
        min_representatives = min(3, len(records))
        max_representatives = min(5, len(records))
        representative_count = str(min_representatives)
        if min_representatives < max_representatives:
            representative_count = f"{min_representatives} 到 {max_representatives}"
        _notify_operation(on_operation, detail, started=True)
        payload = self._request_trend_payload(
            detail,
            TREND_ANALYSIS_PROMPT.format(
                scope=scope,
                first_level=stats.first_level,
                second_level=stats.second_level,
                stats=json.dumps(_stats_payload(stats), ensure_ascii=False, indent=2),
                patents=_patent_digest(records),
                representative_count=representative_count,
            ),
        )
        report = _report_from_payload(stats, payload)
        _notify_operation(on_operation, detail, started=False)
        return report

    def _merge_batch_reports(
        self,
        stats: TrendGroupStats,
        batch_reports: list[TrendReport],
        on_operation: Callable[[str, bool], None] | None,
    ) -> TrendReport:
        detail = f"{stats.first_level} / {stats.second_level}（合并批次）"
        _notify_operation(on_operation, detail, started=True)
        payload = self._request_trend_payload(
            detail,
            TREND_MERGE_PROMPT.format(
                first_level=stats.first_level,
                second_level=stats.second_level,
                stats=json.dumps(_stats_payload(stats), ensure_ascii=False, indent=2),
                batch_reports=json.dumps([_report_payload(report) for report in batch_reports], ensure_ascii=False, indent=2),
            ),
        )
        report = _report_from_payload(stats, payload)
        _notify_operation(on_operation, detail, started=False)
        return report

    def _request_trend_payload(self, detail: str, user_prompt: str) -> dict[str, str]:
        prompt = user_prompt
        for attempt in range(2):
            response = self.client.chat(TREND_SYSTEM_PROMPT, prompt)
            try:
                payload = parse_trend_response(response)
            except ValueError as exc:
                invalidate = getattr(self.client, "invalidate", None)
                if callable(invalidate):
                    invalidate(TREND_SYSTEM_PROMPT, prompt, str(exc), response)
                if attempt > 0:
                    raise ValueError(f"{detail}的趋势 JSON 校验失败（已重试一次）：{exc}") from exc
                prompt = (
                    f"{user_prompt}\n\n上一次输出未通过 JSON 校验。解析错误为 {exc}。\n"
                    "请根据原始证据重新生成一个完整的 JSON 对象。"
                    "确保字符串正确闭合，转义字符串中的换行和制表符。"
                )
                continue
            if attempt > 0:
                store_validated = getattr(self.client, "store_validated", None)
                if callable(store_validated):
                    store_validated(TREND_SYSTEM_PROMPT, user_prompt, response)
            return payload
        raise AssertionError("unreachable")


class TrendCheckpointStore:
    def __init__(self, path: Path):
        self.path = path

    def load_successes(self) -> dict[str, TrendReport]:
        successes: dict[str, TrendReport] = {}
        if not self.path.exists():
            return successes
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("status") != "success" or not isinstance(payload.get("report"), dict):
                continue
            report = _report_from_dict(payload["report"])
            successes[report.key] = report
        return successes

    def record_success(self, report: TrendReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"status": "success", "key": report.key, "report": asdict(report)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def trend_work_units(
    records: Iterable[TrendPatentRecord],
    batch_size: int,
    checkpoint: TrendCheckpointStore | None = None,
) -> int:
    """Return the exact number of uncached trend-model operations still required."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    cached = checkpoint.load_successes() if checkpoint else {}
    units = 0
    for stats, group_records in build_trend_groups(records):
        if trend_key(stats.first_level, stats.second_level) in cached:
            continue
        batch_count = (len(group_records) + batch_size - 1) // batch_size
        units += batch_count if batch_count == 1 else batch_count + 1
    return units


def _notify_operation(callback: Callable[[str, bool], None] | None, detail: str, started: bool) -> None:
    if callback is not None:
        callback(detail, started)


def load_classification_workbook(path: Path) -> list[TrendPatentRecord]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl>=3.1 is required to read classification workbooks") from exc

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb["分类结果"] if "分类结果" in wb.sheetnames else wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        try:
            headers = [_cell_text(value) for value in next(rows)]
        except StopIteration:
            return []

        records: list[TrendPatentRecord] = []
        for row in rows:
            values = {header: value for header, value in zip(headers, row) if header}
            if not any(value not in (None, "") for value in values.values()):
                continue
            sections = {name: _cell_text(values.get(name)) for name in SUMMARY_SECTION_NAMES}
            parsed_metadata = parse_bibliographic_metadata(sections.get("著录信息", ""))
            metadata = {
                name: _cell_text(values.get(name)) or parsed_metadata.get(name, "")
                for name in BIBLIOGRAPHIC_FIELD_NAMES
            }
            filename = _cell_text(values.get("文件名"))
            records.append(
                TrendPatentRecord(
                    filename=filename,
                    patent_name=_cell_text(values.get("专利名称")),
                    source_path=_cell_text(values.get("文件路径")),
                    summary_path=_cell_text(values.get("摘要路径")),
                    primary_first_level=_cell_text(values.get("主一级分类")),
                    primary_second_level=_cell_text(values.get("主二级分类")),
                    primary_confidence=_optional_float(values.get("主分类置信度")),
                    candidate_summary=_cell_text(values.get("候选分类")),
                    review_status=_cell_text(values.get("复核状态")),
                    reason=_cell_text(values.get("分类理由")),
                    metadata=metadata,
                    sections=sections,
                    inferred_year=infer_patent_year(metadata, filename=filename),
                )
            )
        return records
    finally:
        wb.close()


def build_trend_groups(records: Iterable[TrendPatentRecord]) -> list[tuple[TrendGroupStats, list[TrendPatentRecord]]]:
    grouped: dict[tuple[str, str], list[TrendPatentRecord]] = defaultdict(list)
    for record in records:
        if not record.primary_second_level.strip():
            continue
        grouped[(record.primary_first_level, record.primary_second_level)].append(record)

    result: list[tuple[TrendGroupStats, list[TrendPatentRecord]]] = []
    for (first_level, second_level), group_records in sorted(grouped.items()):
        result.append((_build_stats(first_level, second_level, group_records), group_records))
    return result


def parse_trend_response(response: str | Mapping[str, Any]) -> dict[str, str]:
    payload = response if isinstance(response, Mapping) else _load_trend_json(response)
    return {field: _stringify(payload.get(field, "")) for field in TREND_REPORT_FIELDS}


def trend_key(first_level: str, second_level: str) -> str:
    return f"{first_level}\t{second_level}"


def _build_stats(first_level: str, second_level: str, records: list[TrendPatentRecord]) -> TrendGroupStats:
    year_counts = Counter(record.inferred_year or "未知" for record in records)
    known_years = sorted(year for year in year_counts if year != "未知")
    confidences = [record.primary_confidence for record in records if record.primary_confidence is not None]
    keyword_counts = Counter(_iter_keywords(records))
    representatives = sorted(
        records,
        key=lambda item: (item.primary_confidence is not None, item.primary_confidence or 0.0, item.filename),
        reverse=True,
    )[:5]
    return TrendGroupStats(
        first_level=first_level,
        second_level=second_level,
        patent_count=len(records),
        year_counts=dict(sorted(year_counts.items())),
        year_range=f"{known_years[0]}-{known_years[-1]}" if known_years else "未知",
        peak_year=_peak_year(year_counts),
        average_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        review_count=sum(1 for record in records if record.review_status and record.review_status != "通过"),
        top_keywords=[keyword for keyword, _count in keyword_counts.most_common(10)],
        representative_patents=[_representative_name(record) for record in representatives],
    )


def _iter_keywords(records: list[TrendPatentRecord]) -> Iterable[str]:
    for record in records:
        text = record.sections.get("潜在分类关键词", "")
        for raw in text.splitlines():
            keyword = re.sub(r"^[\-\*\u2022\d\.\s、]+", "", raw).strip()
            if keyword:
                yield keyword


def _peak_year(year_counts: Counter[str]) -> str:
    known = {year: count for year, count in year_counts.items() if year != "未知"}
    if not known:
        return "未知"
    return max(sorted(known), key=lambda year: (known[year], year))


def _representative_name(record: TrendPatentRecord) -> str:
    if record.patent_name:
        return f"{record.filename}｜{record.patent_name}"
    return record.filename


def _stats_payload(stats: TrendGroupStats) -> dict[str, object]:
    return {
        "一级分类": stats.first_level,
        "二级分类": stats.second_level,
        "专利数": stats.patent_count,
        "年度分布": stats.year_counts,
        "年份范围": stats.year_range,
        "峰值年份": stats.peak_year,
        "平均置信度": stats.average_confidence,
        "复核数量": stats.review_count,
        "热点关键词": stats.top_keywords,
        "代表专利候选": stats.representative_patents,
    }


def _patent_digest(records: list[TrendPatentRecord]) -> str:
    lines: list[str] = []
    for index, record in enumerate(records, start=1):
        lines.append(
            "\n".join(
                [
                    f"{index}. 文件名：{record.filename}",
                    f"   专利名称：{record.patent_name}",
                    f"   年份：{record.inferred_year}；置信度：{record.primary_confidence}；复核状态：{record.review_status}",
                    f"   背景问题：{_truncate(record.sections.get('背景问题', ''), 220)}",
                    f"   核心方案：{_truncate(record.sections.get('核心方案', ''), 320)}",
                    f"   关键创新点：{_truncate(record.sections.get('关键创新点', ''), 320)}",
                    f"   应用场景：{_truncate(record.sections.get('应用场景', ''), 180)}",
                    f"   关键词：{_truncate(record.sections.get('潜在分类关键词', ''), 180)}",
                ]
            )
        )
    return "\n\n".join(lines)


def _report_from_payload(stats: TrendGroupStats, payload: Mapping[str, Any]) -> TrendReport:
    parsed = {field: _stringify(payload.get(field, "")) for field in TREND_REPORT_FIELDS}
    return TrendReport(
        stats=stats,
        status_summary=parsed["发展现状"],
        development_stage=parsed["发展阶段"],
        technical_routes=parsed["主要技术路线"],
        hot_topics=parsed["热点方向"],
        opportunities=parsed["机会与空白"],
        risks=parsed["风险/不确定性"],
        representative_patents=parsed["代表专利"],
        evidence_limits=parsed["证据局限"],
    )


def _report_payload(report: TrendReport) -> dict[str, str]:
    return {
        "发展现状": report.status_summary,
        "发展阶段": report.development_stage,
        "主要技术路线": report.technical_routes,
        "热点方向": report.hot_topics,
        "机会与空白": report.opportunities,
        "风险/不确定性": report.risks,
        "代表专利": report.representative_patents,
        "证据局限": report.evidence_limits,
    }


def _report_from_dict(payload: Mapping[str, Any]) -> TrendReport:
    stats_payload = payload.get("stats") if isinstance(payload.get("stats"), Mapping) else {}
    stats = TrendGroupStats(
        first_level=_stringify(stats_payload.get("first_level", "")),
        second_level=_stringify(stats_payload.get("second_level", "")),
        patent_count=int(stats_payload.get("patent_count") or 0),
        year_counts={str(key): int(value) for key, value in dict(stats_payload.get("year_counts") or {}).items()},
        year_range=_stringify(stats_payload.get("year_range", "")),
        peak_year=_stringify(stats_payload.get("peak_year", "")),
        average_confidence=_optional_float(stats_payload.get("average_confidence")),
        review_count=int(stats_payload.get("review_count") or 0),
        top_keywords=[str(item) for item in stats_payload.get("top_keywords") or []],
        representative_patents=[str(item) for item in stats_payload.get("representative_patents") or []],
    )
    return TrendReport(
        stats=stats,
        status_summary=_stringify(payload.get("status_summary", "")),
        development_stage=_stringify(payload.get("development_stage", "")),
        technical_routes=_stringify(payload.get("technical_routes", "")),
        hot_topics=_stringify(payload.get("hot_topics", "")),
        opportunities=_stringify(payload.get("opportunities", "")),
        risks=_stringify(payload.get("risks", "")),
        representative_patents=_stringify(payload.get("representative_patents", "")),
        evidence_limits=_stringify(payload.get("evidence_limits", "")),
    )


def _extract_json(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("趋势响应中未找到完整的 JSON 对象")
    return stripped[start : end + 1]


def _load_trend_json(text: str) -> Mapping[str, Any]:
    candidate = _extract_json(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # A model can restart the object on a new line after a malformed prefix.
        for start in reversed(list(re.finditer(r"\n[ \t]*(?=\{)", candidate))):
            try:
                payload = json.loads(candidate[start.end() :])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and all(field in payload for field in TREND_REPORT_FIELDS):
                return payload
        raise


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "..."
