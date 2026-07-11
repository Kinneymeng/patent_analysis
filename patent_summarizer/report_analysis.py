from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .comparison import ComparisonResult, PortfolioDataset
from .prompts import REPORT_COMPETITOR_PROMPT, REPORT_EXECUTIVE_PROMPT, REPORT_SYSTEM_PROMPT, REPORT_TARGET_PROMPT
from .trend_analysis import TrendAnalysisResult


FORBIDDEN_REPORT_PHRASES = ("能否实现", "技术可行性判断")


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
    domain: str = ""
    domain_context: str = ""


class ReportCheckpointingClient:
    """Cache chat responses by the exact system/user prompt pair."""

    def __init__(self, delegate: ReportChatClient, path: Path):
        self.delegate = delegate
        self.path = Path(path)
        self._responses = self._load()

    def chat(self, system_prompt: str, user_prompt: str) -> str | Mapping[str, Any]:
        key = hashlib.sha256(f"{system_prompt}\0{user_prompt}".encode("utf-8")).hexdigest()
        if key in self._responses:
            return self._responses[key]
        response = self.delegate.chat(system_prompt, user_prompt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "response": response}, ensure_ascii=False) + "\n")
        self._responses[key] = response
        return response

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        responses: dict[str, Any] = {}
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("key") and "response" in payload:
                responses[str(payload["key"])] = payload["response"]
        return responses


class ReportSynthesisAnalyzer:
    def __init__(self, client: ReportChatClient):
        self.client = client

    def analyze(
        self,
        target: PortfolioDataset,
        baselines: list[PortfolioDataset],
        trends: dict[str, TrendAnalysisResult],
        comparison: ComparisonResult,
        domain: str = "未指定领域",
        domain_context: str = "未提供",
        on_stage: Callable[[str, bool], None] | None = None,
    ) -> tuple[CompetitorAnalysis, TargetAnalysis, ExecutiveSummary]:
        evidence = _report_evidence_payload(target, baselines, trends, comparison, domain, domain_context)
        system_prompt = REPORT_SYSTEM_PROMPT.format(
            domain=domain,
            domain_context=domain_context or "未提供",
            target_label=target.label,
        )
        _notify_stage(on_stage, "竞争对手布局策略", started=True)
        competitor = parse_competitor_analysis(
            self.client.chat(
                system_prompt,
                REPORT_COMPETITOR_PROMPT.format(evidence=json.dumps(evidence, ensure_ascii=False, indent=2)),
            )
        )
        _notify_stage(on_stage, "竞争对手布局策略", started=False)
        _notify_stage(on_stage, f"{target.label}布局建议", started=True)
        target_analysis = parse_target_analysis(
            self.client.chat(
                system_prompt,
                REPORT_TARGET_PROMPT.format(evidence=json.dumps(evidence, ensure_ascii=False, indent=2)),
            )
        )
        _notify_stage(on_stage, f"{target.label}布局建议", started=False)
        _notify_stage(on_stage, "管理层结论", started=True)
        executive = parse_executive_summary(
            self.client.chat(
                system_prompt,
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
            )
        )
        _notify_stage(on_stage, "管理层结论", started=False)
        return competitor, target_analysis, executive


def _notify_stage(callback: Callable[[str, bool], None] | None, detail: str, started: bool) -> None:
    if callback is not None:
        callback(detail, started)


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


def parse_competitor_analysis(response: str | Mapping[str, Any]) -> CompetitorAnalysis:
    payload = _mapping_payload(response)
    _require_fields(payload, ["共性布局策略", "企业差异化策略", "竞争对手趋势研判", "未来竞争焦点", "证据局限"])
    _reject_forbidden(payload)
    return CompetitorAnalysis(
        common_strategies=[_parse_strategy(item) for item in _mapping_list(payload["共性布局策略"], "共性布局策略")],
        differentiated_strategies=[_parse_strategy(item) for item in _mapping_list(payload["企业差异化策略"], "企业差异化策略")],
        forecasts=[_parse_forecast(item) for item in _mapping_list(payload["竞争对手趋势研判"], "竞争对手趋势研判")],
        future_focus=_required_text(payload, "未来竞争焦点"),
        evidence_limits=_required_text(payload, "证据局限"),
    )


def parse_target_analysis(response: str | Mapping[str, Any]) -> TargetAnalysis:
    payload = _mapping_payload(response)
    _require_fields(payload, ["目标总体策略", "待布局建议点", "专利组合策略", "证据局限"])
    _reject_forbidden(payload)
    return TargetAnalysis(
        strategies=[_parse_target_strategy(item) for item in _mapping_list(payload["目标总体策略"], "目标总体策略")],
        layout_points=[_parse_layout_point(item) for item in _mapping_list(payload["待布局建议点"], "待布局建议点")],
        portfolio_plans=[_parse_portfolio_plan(item) for item in _mapping_list(payload["专利组合策略"], "专利组合策略")],
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


def _parse_strategy(payload: Mapping[str, Any]) -> StrategyItem:
    fields = ["策略名称", "核心特征", "适用企业", "重点技术分类", "统计及连续性证据", "代表性专利", "组合中的作用"]
    _require_fields(payload, fields)
    return StrategyItem(
        _required_text(payload, "策略名称"),
        _required_text(payload, "核心特征"),
        _text_list(payload["适用企业"], "适用企业"),
        _text_list(payload["重点技术分类"], "重点技术分类"),
        _required_text(payload, "统计及连续性证据"),
        _text_list(payload["代表性专利"], "代表性专利"),
        _required_text(payload, "组合中的作用"),
    )


def _parse_forecast(payload: Mapping[str, Any]) -> TrendForecast:
    fields = ["企业", "可能强化方向", "时间序列信号", "关键词及技术路线依据", "代表性专利", "证据限制"]
    _require_fields(payload, fields)
    return TrendForecast(
        _required_text(payload, "企业"),
        _required_text(payload, "可能强化方向"),
        _required_text(payload, "时间序列信号"),
        _required_text(payload, "关键词及技术路线依据"),
        _text_list(payload["代表性专利"], "代表性专利"),
        _required_text(payload, "证据限制"),
    )


def _parse_target_strategy(payload: Mapping[str, Any]) -> TargetStrategyItem:
    fields = ["策略名称", "目标", "重点技术领域", "优先顺序", "建议理由", "关联建议点"]
    _require_fields(payload, fields)
    return TargetStrategyItem(
        _required_text(payload, "策略名称"),
        _required_text(payload, "目标"),
        _text_list(payload["重点技术领域"], "重点技术领域"),
        _required_text(payload, "优先顺序"),
        _required_text(payload, "建议理由"),
        _text_list(payload["关联建议点"], "关联建议点"),
    )


def _parse_layout_point(payload: Mapping[str, Any]) -> LayoutPoint:
    fields = ["名称", "一级分类", "二级分类", "主要技术问题", "竞争对手布局", "目标现有布局及缺口", "优先级", "建议理由", "证据专利"]
    _require_fields(payload, fields)
    return LayoutPoint(
        _required_text(payload, "名称"),
        _required_text(payload, "一级分类"),
        _required_text(payload, "二级分类"),
        _required_text(payload, "主要技术问题"),
        _required_text(payload, "竞争对手布局"),
        _required_text(payload, "目标现有布局及缺口"),
        _required_text(payload, "优先级"),
        _required_text(payload, "建议理由"),
        _text_list(payload["证据专利"], "证据专利"),
    )


def _parse_portfolio_plan(payload: Mapping[str, Any]) -> PortfolioPlan:
    fields = ["对应布局点", "核心专利", "外围专利分支", "技术环节覆盖", "专利客体协同", "系列化或补充布局", "跨布局点协同", "证据依据"]
    _require_fields(payload, fields)
    return PortfolioPlan(
        _required_text(payload, "对应布局点"),
        _required_text(payload, "核心专利"),
        _text_list(payload["外围专利分支"], "外围专利分支"),
        _text_list(payload["技术环节覆盖"], "技术环节覆盖"),
        _text_list(payload["专利客体协同"], "专利客体协同"),
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
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} 必须是非空数组")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise ValueError(f"{field} 必须包含非空文本")
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
    domain: str,
    domain_context: str,
) -> dict[str, Any]:
    return {
        "分析领域": domain,
        "领域说明": domain_context or "未提供",
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
