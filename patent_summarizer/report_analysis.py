from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .comparison import ComparisonResult, PortfolioDataset
from .prompts import REPORT_COMPETITOR_PROMPT, REPORT_EXECUTIVE_PROMPT, REPORT_SYSTEM_PROMPT, REPORT_TARGET_PROMPT
from .trend_analysis import TrendAnalysisResult


FORBIDDEN_REPORT_PHRASES = ("能否实现", "技术可行性判断")
REPORT_VALIDATION_RETRIES = 1
TEXT_LIST_SEPARATOR = re.compile(r"[、，,；;\r\n]+")
COMPANY_SUFFIXES = (
    "集团股份有限公司",
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "有限公司",
    "汽车集团",
    "集团",
    "汽车",
)
ParsedReport = TypeVar("ParsedReport")


class ReportChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str | Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class StrategyItem:
    name: str
    core_feature: str
    companies: list[str]
    technical_categories: list[str]
    statistical_evidence: str
    representative_patents: list[str]
    portfolio_role: str


@dataclass(frozen=True)
class TrendForecast:
    company: str
    direction: str
    time_signal: str
    route_evidence: str
    representative_patents: list[str]
    evidence_limit: str


@dataclass(frozen=True)
class CompetitorAnalysis:
    common_strategies: list[StrategyItem]
    differentiated_strategies: list[StrategyItem]
    forecasts: list[TrendForecast]
    future_focus: str
    evidence_limits: str


@dataclass(frozen=True)
class TargetStrategyItem:
    name: str
    objective: str
    technical_fields: list[str]
    priority: str
    reason: str
    related_points: list[str]


@dataclass(frozen=True)
class LayoutPoint:
    name: str
    first_level: str
    second_level: str
    technical_problem: str
    competitor_layout: str
    target_gap: str
    priority: str
    reason: str
    evidence_patents: list[str]


@dataclass(frozen=True)
class PortfolioPlan:
    layout_point: str
    core_patent: str
    peripheral_branches: list[str]
    technical_links: list[str]
    patent_subjects: list[str]
    series_plan: str
    cross_point_synergy: str
    evidence: str


@dataclass(frozen=True)
class TargetAnalysis:
    strategies: list[TargetStrategyItem]
    layout_points: list[LayoutPoint]
    portfolio_plans: list[PortfolioPlan]
    evidence_limits: str


@dataclass(frozen=True)
class ExecutiveSummary:
    conclusions: list[str]
    evidence_limits: str


@dataclass(frozen=True)
class PatentLayoutReport:
    target: PortfolioDataset
    baselines: list[PortfolioDataset]
    industry: PortfolioDataset
    trends: dict[str, TrendAnalysisResult]
    comparison: ComparisonResult
    competitor_analysis: CompetitorAnalysis
    target_analysis: TargetAnalysis
    executive_summary: ExecutiveSummary
    taxonomy: list[tuple[str, str]]


class ReportCheckpointingClient:
    """Cache chat responses by the exact system/user prompt pair."""

    def __init__(self, delegate: ReportChatClient, path: Path):
        self.delegate = delegate
        self.path = Path(path)
        self._responses = self._load()

    def chat(self, system_prompt: str, user_prompt: str) -> str | Mapping[str, Any]:
        key = _report_cache_key(system_prompt, user_prompt)
        if key in self._responses:
            return self._responses[key]
        response = self.delegate.chat(system_prompt, user_prompt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "response": response}, ensure_ascii=False) + "\n")
        self._responses[key] = response
        return response

    def invalidate(
        self,
        system_prompt: str,
        user_prompt: str,
        reason: str,
        response: str | Mapping[str, Any] | None = None,
    ) -> None:
        """Mark a structurally invalid response so it is not reused after restart."""

        key = _report_cache_key(system_prompt, user_prompt)
        self._responses.pop(key, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"key": key, "invalid": True, "reason": reason}
        if response is not None:
            record["invalid_response"] = response
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def store_validated(
        self,
        system_prompt: str,
        user_prompt: str,
        response: str | Mapping[str, Any],
    ) -> None:
        """Store a repaired response under the original prompt's cache key."""

        key = _report_cache_key(system_prompt, user_prompt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "response": response, "validated": True}, ensure_ascii=False) + "\n")
        self._responses[key] = response

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        responses: dict[str, Any] = {}
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or not payload.get("key"):
                continue
            key = str(payload["key"])
            if payload.get("invalid") is True:
                responses.pop(key, None)
            elif "response" in payload:
                responses[key] = payload["response"]
        return responses


class ReportSynthesisAnalyzer:
    def __init__(
        self,
        client: ReportChatClient,
        validation_retries: int = REPORT_VALIDATION_RETRIES,
        on_validation_retry: Callable[[str, int, str], None] | None = None,
    ):
        if validation_retries < 0:
            raise ValueError("validation_retries must not be negative")
        self.client = client
        self.validation_retries = validation_retries
        self.on_validation_retry = on_validation_retry

    def analyze(
        self,
        target: PortfolioDataset,
        baselines: list[PortfolioDataset],
        trends: dict[str, TrendAnalysisResult],
        comparison: ComparisonResult,
        on_stage: Callable[[str, bool], None] | None = None,
    ) -> tuple[CompetitorAnalysis, TargetAnalysis, ExecutiveSummary]:
        evidence = _report_evidence_payload(target, baselines, trends, comparison)
        _notify_stage(on_stage, "竞争对手布局策略", started=True)
        competitor_labels = {item.label for item in baselines}
        competitor = self._validated_chat(
            "竞争对手布局策略",
            REPORT_COMPETITOR_PROMPT.format(evidence=json.dumps(evidence, ensure_ascii=False, indent=2)),
            lambda response: parse_competitor_analysis(response, allowed_companies=competitor_labels),
        )
        _notify_stage(on_stage, "竞争对手布局策略", started=False)
        _notify_stage(on_stage, f"{target.label}布局建议", started=True)
        target_analysis = self._validated_chat(
            f"{target.label}布局建议",
            REPORT_TARGET_PROMPT.format(evidence=json.dumps(evidence, ensure_ascii=False, indent=2)),
            parse_target_analysis,
        )
        _notify_stage(on_stage, f"{target.label}布局建议", started=False)
        _notify_stage(on_stage, "管理层结论", started=True)
        executive = self._validated_chat(
            "管理层结论",
            REPORT_EXECUTIVE_PROMPT.format(
                evidence=json.dumps(
                    {
                        "竞争对手分析": asdict(competitor),
                        "目标企业分析": asdict(target_analysis),
                        "基础证据": evidence,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            parse_executive_summary,
        )
        _notify_stage(on_stage, "管理层结论", started=False)
        return competitor, target_analysis, executive

    def _validated_chat(
        self,
        stage: str,
        user_prompt: str,
        parser: Callable[[str | Mapping[str, Any]], ParsedReport],
    ) -> ParsedReport:
        prompt = user_prompt
        for attempt in range(self.validation_retries + 1):
            response = self.client.chat(REPORT_SYSTEM_PROMPT, prompt)
            try:
                result = parser(response)
                if attempt > 0:
                    _store_validated_response(self.client, REPORT_SYSTEM_PROMPT, user_prompt, response)
                return result
            except (TypeError, ValueError) as exc:
                _invalidate_cached_response(self.client, REPORT_SYSTEM_PROMPT, prompt, str(exc), response)
                if attempt >= self.validation_retries:
                    raise ValueError(f"{stage}结构化响应校验失败：{exc}") from exc
                if self.on_validation_retry is not None:
                    self.on_validation_retry(stage, attempt + 1, str(exc))
                prompt = _validation_repair_prompt(user_prompt, str(exc))

        raise AssertionError("unreachable")


def _notify_stage(callback: Callable[[str, bool], None] | None, detail: str, started: bool) -> None:
    if callback is not None:
        callback(detail, started)


def _validation_repair_prompt(original_prompt: str, error: str) -> str:
    return f"""{original_prompt}

上一次输出未通过程序的 JSON 结构校验，错误为：{error}
请重新生成完整 JSON，并严格修正该错误。所有要求为数组的字段都必须使用 JSON 数组；要求非空的数组必须至少包含 1 个非空字符串。不要解释，不要使用 Markdown 代码块，只输出修正后的完整 JSON 对象。
"""


def _report_cache_key(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256(f"{system_prompt}\0{user_prompt}".encode("utf-8")).hexdigest()


def _invalidate_cached_response(
    client: ReportChatClient,
    system_prompt: str,
    user_prompt: str,
    reason: str,
    response: str | Mapping[str, Any],
) -> None:
    invalidate = getattr(client, "invalidate", None)
    if callable(invalidate):
        invalidate(system_prompt, user_prompt, reason, response)


def _store_validated_response(
    client: ReportChatClient,
    system_prompt: str,
    user_prompt: str,
    response: str | Mapping[str, Any],
) -> None:
    store_validated = getattr(client, "store_validated", None)
    if callable(store_validated):
        store_validated(system_prompt, user_prompt, response)


def combine_portfolios(label: str, portfolios: list[PortfolioDataset]) -> PortfolioDataset:
    if not portfolios:
        raise ValueError("At least one portfolio is required")
    return PortfolioDataset(
        label=label,
        root=Path("；".join(str(item.root) for item in portfolios)),
        classification_path=Path(),
        classification_source="combined",
        records=[record for item in portfolios for record in item.records],
    )


def parse_competitor_analysis(
    response: str | Mapping[str, Any],
    allowed_companies: set[str] | None = None,
) -> CompetitorAnalysis:
    payload = _mapping_payload(response)
    _require_fields(payload, ["共性布局策略", "企业差异化策略", "竞争对手趋势研判", "未来竞争焦点", "证据局限"])
    _reject_forbidden(payload)
    common_strategies = [
        _parse_strategy(item, f"共性布局策略[{index}]")
        for index, item in enumerate(_mapping_list(payload["共性布局策略"], "共性布局策略"), start=1)
    ]
    differentiated_strategies = [
        _parse_strategy(item, f"企业差异化策略[{index}]")
        for index, item in enumerate(_mapping_list(payload["企业差异化策略"], "企业差异化策略"), start=1)
    ]
    if allowed_companies is not None:
        common_strategies = _filter_competitor_strategies(common_strategies, allowed_companies)
        differentiated_strategies = _filter_competitor_strategies(differentiated_strategies, allowed_companies)
    return CompetitorAnalysis(
        common_strategies=common_strategies,
        differentiated_strategies=differentiated_strategies,
        forecasts=[
            _parse_forecast(item, f"竞争对手趋势研判[{index}]")
            for index, item in enumerate(_mapping_list(payload["竞争对手趋势研判"], "竞争对手趋势研判"), start=1)
        ],
        future_focus=_required_text(payload, "未来竞争焦点"),
        evidence_limits=_required_text(payload, "证据局限"),
    )


def _filter_competitor_strategies(
    strategies: list[StrategyItem],
    allowed_companies: set[str],
) -> list[StrategyItem]:
    filtered: list[StrategyItem] = []
    for strategy in strategies:
        companies: list[str] = []
        for company in strategy.companies:
            resolved = _resolve_allowed_company(company, allowed_companies)
            if resolved is not None and resolved not in companies:
                companies.append(resolved)
        if companies:
            filtered.append(replace(strategy, companies=companies))
    return filtered


def _resolve_allowed_company(company: str, allowed_companies: set[str]) -> str | None:
    if company in allowed_companies:
        return company
    company_key = _company_key(company)
    if not company_key:
        return None
    matches = [
        allowed
        for allowed in sorted(allowed_companies)
        if _company_keys_match(company_key, _company_key(allowed))
    ]
    return matches[0] if len(matches) == 1 else None


def _company_key(company: str) -> str:
    key = re.sub(r"[^\w]+", "", company, flags=re.UNICODE).replace("_", "").casefold()
    for suffix in COMPANY_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _company_keys_match(first: str, second: str) -> bool:
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return len(shorter) >= 2 and shorter in longer


def parse_target_analysis(response: str | Mapping[str, Any]) -> TargetAnalysis:
    payload = _mapping_payload(response)
    _require_fields(payload, ["长安总体策略", "待布局建议点", "专利组合策略", "证据局限"])
    _reject_forbidden(payload)
    return TargetAnalysis(
        strategies=[
            _parse_target_strategy(item, f"长安总体策略[{index}]")
            for index, item in enumerate(_mapping_list(payload["长安总体策略"], "长安总体策略"), start=1)
        ],
        layout_points=[
            _parse_layout_point(item, f"待布局建议点[{index}]")
            for index, item in enumerate(_mapping_list(payload["待布局建议点"], "待布局建议点"), start=1)
        ],
        portfolio_plans=[
            _parse_portfolio_plan(item, f"专利组合策略[{index}]")
            for index, item in enumerate(_mapping_list(payload["专利组合策略"], "专利组合策略"), start=1)
        ],
        evidence_limits=_required_text(payload, "证据局限"),
    )


def parse_executive_summary(response: str | Mapping[str, Any]) -> ExecutiveSummary:
    payload = _mapping_payload(response)
    _require_fields(payload, ["管理层结论", "总体证据局限"])
    _reject_forbidden(payload)
    conclusions = _text_list(payload["管理层结论"], "管理层结论")
    if not 3 <= len(conclusions) <= 5:
        raise ValueError("管理层结论必须包含 3 到 5 项")
    return ExecutiveSummary(conclusions, _required_text(payload, "总体证据局限"))


def _parse_strategy(payload: Mapping[str, Any], context: str = "策略") -> StrategyItem:
    fields = ["策略名称", "核心特征", "适用企业", "重点技术分类", "统计及连续性证据", "代表性专利", "组合中的作用"]
    _require_fields(payload, fields)
    return StrategyItem(
        _required_text(payload, "策略名称"),
        _required_text(payload, "核心特征"),
        _text_list(payload["适用企业"], f"{context}.适用企业"),
        _text_list(payload["重点技术分类"], f"{context}.重点技术分类"),
        _required_text(payload, "统计及连续性证据"),
        _text_list(payload["代表性专利"], f"{context}.代表性专利"),
        _required_text(payload, "组合中的作用"),
    )


def _parse_forecast(payload: Mapping[str, Any], context: str = "竞争对手趋势研判") -> TrendForecast:
    fields = ["企业", "可能强化方向", "时间序列信号", "关键词及技术路线依据", "代表性专利", "证据限制"]
    _require_fields(payload, fields)
    return TrendForecast(
        _required_text(payload, "企业"),
        _required_text(payload, "可能强化方向"),
        _required_text(payload, "时间序列信号"),
        _required_text(payload, "关键词及技术路线依据"),
        _text_list(payload["代表性专利"], f"{context}.代表性专利"),
        _required_text(payload, "证据限制"),
    )


def _parse_target_strategy(payload: Mapping[str, Any], context: str = "长安总体策略") -> TargetStrategyItem:
    fields = ["策略名称", "目标", "重点技术领域", "优先顺序", "建议理由", "关联建议点"]
    _require_fields(payload, fields)
    return TargetStrategyItem(
        _required_text(payload, "策略名称"),
        _required_text(payload, "目标"),
        _text_list(payload["重点技术领域"], f"{context}.重点技术领域"),
        _required_text(payload, "优先顺序"),
        _required_text(payload, "建议理由"),
        _text_list(payload["关联建议点"], f"{context}.关联建议点"),
    )


def _parse_layout_point(payload: Mapping[str, Any], context: str = "待布局建议点") -> LayoutPoint:
    fields = ["名称", "一级分类", "二级分类", "主要技术问题", "竞争对手布局", "长安现有布局及缺口", "优先级", "建议理由", "证据专利"]
    _require_fields(payload, fields)
    return LayoutPoint(
        _required_text(payload, "名称"),
        _required_text(payload, "一级分类"),
        _required_text(payload, "二级分类"),
        _required_text(payload, "主要技术问题"),
        _required_text(payload, "竞争对手布局"),
        _required_text(payload, "长安现有布局及缺口"),
        _required_text(payload, "优先级"),
        _required_text(payload, "建议理由"),
        _text_list(payload["证据专利"], f"{context}.证据专利"),
    )


def _parse_portfolio_plan(payload: Mapping[str, Any], context: str = "专利组合策略") -> PortfolioPlan:
    fields = ["对应布局点", "核心专利", "外围专利分支", "技术环节覆盖", "专利客体协同", "系列化或补充布局", "跨布局点协同", "证据依据"]
    _require_fields(payload, fields)
    return PortfolioPlan(
        _required_text(payload, "对应布局点"),
        _required_text(payload, "核心专利"),
        _text_list(payload["外围专利分支"], f"{context}.外围专利分支"),
        _text_list(payload["技术环节覆盖"], f"{context}.技术环节覆盖"),
        _text_list(payload["专利客体协同"], f"{context}.专利客体协同"),
        _required_text(payload, "系列化或补充布局"),
        _required_text(payload, "跨布局点协同"),
        _required_text(payload, "证据依据"),
    )


def _mapping_payload(response: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("报告分析响应不是 JSON 对象")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, Mapping):
        raise ValueError("报告分析响应必须是 JSON 对象")
    return payload


def _mapping_list(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是数组")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} 中的每一项必须是对象")
        result.append(item)
    return result


def _text_list(value: Any, field: str) -> list[str]:
    if isinstance(value, str):
        result = [item.strip() for item in TEXT_LIST_SEPARATOR.split(value) if item.strip()]
        if not result:
            raise ValueError(f"{field} 必须包含非空文本")
        return result
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} 必须是非空数组")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} 中的每一项必须是字符串")
    result = [item.strip() for item in value]
    if any(not item for item in result):
        raise ValueError(f"{field} 中的每一项必须是非空文本")
    return result


def _require_fields(payload: Mapping[str, Any], fields: list[str]) -> None:
    for field in fields:
        if field not in payload:
            raise ValueError(f"缺少必填字段：{field}")


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field, "")).strip()
    if not value:
        raise ValueError(f"{field} 必须是非空文本")
    return value


def _reject_forbidden(payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for phrase in FORBIDDEN_REPORT_PHRASES:
        if phrase in text:
            raise ValueError(f"报告内容不得包含：{phrase}")


def _report_evidence_payload(
    target: PortfolioDataset,
    baselines: list[PortfolioDataset],
    trends: dict[str, TrendAnalysisResult],
    comparison: ComparisonResult,
) -> dict[str, Any]:
    return {
        "分析口径": "仅基于用户上传并完成摘要、分类的专利样本",
        "目标企业": _portfolio_digest(target),
        "竞争对手": [_portfolio_digest(item) for item in baselines],
        "趋势分析": {
            label: [
                {
                    "一级分类": item.stats.first_level,
                    "二级分类": item.stats.second_level,
                    "专利数": item.stats.patent_count,
                    "年份分布": item.stats.year_counts,
                    "发展现状": item.status_summary,
                    "发展阶段": item.development_stage,
                    "主要技术路线": item.technical_routes,
                    "热点方向": item.hot_topics,
                    "机会与空白": item.opportunities,
                    "代表专利": item.representative_patents,
                    "证据局限": item.evidence_limits,
                }
                for item in result.reports
            ]
            for label, result in trends.items()
        },
        "目标对比": [
            {
                "对比对象": item.baseline_label,
                "整体结论": item.overall_conclusion,
                "目标布局概况": item.target_layout,
                "优势": item.advantages,
                "短板": item.weaknesses,
                "差异化": item.differentiation,
                "与样本主流关系": item.mainstream_judgement,
                "建议布局点": item.recommendations,
                "证据局限": item.evidence_limits,
                "标签对比": [
                    {
                        "一级分类": label.first_level,
                        "二级分类": label.second_level,
                        "目标专利数": label.target_count,
                        "竞品专利数": label.baseline_count,
                        "目标占比": label.target_share,
                        "竞品占比": label.baseline_share,
                        "判断": label.category,
                        "优先级分": label.priority_score,
                        "目标代表专利": label.target_representatives,
                        "竞品代表专利": label.baseline_representatives,
                    }
                    for label in item.label_comparisons[:30]
                ],
            }
            for item in comparison.reports
        ],
    }


def _portfolio_digest(portfolio: PortfolioDataset) -> dict[str, Any]:
    years = Counter(record.inferred_year or "未知" for record in portfolio.records)
    labels = Counter(
        (record.primary_first_level, record.primary_second_level)
        for record in portfolio.records
        if record.primary_second_level.strip()
    )
    representatives = sorted(
        portfolio.records,
        key=lambda item: (item.primary_confidence or 0.0, item.filename),
        reverse=True,
    )[:20]
    return {
        "企业": portfolio.label,
        "目录": str(portfolio.root),
        "专利数": len(portfolio.records),
        "年份分布": dict(sorted(years.items())),
        "分类分布": [
            {"一级分类": first, "二级分类": second, "专利数": count}
            for (first, second), count in labels.most_common()
        ],
        "代表证据": [
            {
                "文件": item.filename,
                "专利名称": item.patent_name,
                "一级分类": item.primary_first_level,
                "二级分类": item.primary_second_level,
                "年份": item.inferred_year,
                "关键词": item.sections.get("潜在分类关键词", ""),
            }
            for item in representatives
        ],
    }
