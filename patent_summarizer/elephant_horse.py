from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlparse

from docx import Document

from .comparison import PortfolioDataset
from .disclosure.cnipa_upstream.cnipa_epub_parse import suggest_class_codes
from .disclosure.legacy import load_report_sources
from .disclosure.prior_art import normalize_search_terms
from .extractors import TextExtractor
from .trend_analysis import TrendPatentRecord, load_classification_workbook


SCOPE_SAMPLE = "在本次分析报告样本范围内"
SCOPE_EXPANDED = "在扩展检索覆盖的行业样本中"
FORBIDDEN_CONCLUSIONS = ("全行业穷尽", "必然新颖", "具备创造性", "高价值专利达标", "蓝海")
DOCUMENT_SUFFIXES = {".docx", ".pdf"}


class ElephantHorseChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str | Mapping[str, Any]: ...


class SearchFailureAction(str, Enum):
    RETRY = "retry"
    CONTINUE = "continue"
    CANCEL = "cancel"


SearchFailureCallback = Callable[[str, str], SearchFailureAction]


class ElephantHorseCancelled(RuntimeError):
    pass


class IndustrySearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortfolioSource:
    label: str
    root: Path
    patent_count: int
    classification_source: str
    coverage_count: int
    summary_count: int


@dataclass(frozen=True)
class ElephantHorseInputs:
    report_path: Path
    appendix_path: Path
    target: PortfolioDataset
    baselines: list[PortfolioDataset]
    sources: list[Any]
    portfolio_sources: list[PortfolioSource]


@dataclass(frozen=True)
class LocalPatentEvidence:
    evidence_id: str
    company: str
    patent_name: str
    publication_number: str
    source_path: Path
    summary_path: Path
    summary: str
    full_text: str
    evidence_status: str


@dataclass(frozen=True)
class IndustryPatentEvidence:
    publication_number: str
    title: str
    applicant: str
    abstract: str
    ipc_codes: tuple[str, ...]
    url: str
    relevance_score: int


@dataclass(frozen=True)
class IndustryCoverage:
    enabled: bool
    records: tuple[IndustryPatentEvidence, ...] = ()
    keywords: tuple[str, ...] = ()
    ipc_prefixes: tuple[str, ...] = ()
    failure: str = ""

    @property
    def applicant_count(self) -> int:
        return len({_normalize_applicant(item.applicant) for item in self.records if item.applicant.strip()})

    @property
    def ipc_branch_count(self) -> int:
        return len({prefix for item in self.records for prefix in _ipc_subclasses(item.ipc_codes)})

    @property
    def qualifies(self) -> bool:
        return len(self.records) >= 10 and self.applicant_count >= 3 and self.ipc_branch_count >= 2

    @property
    def scope(self) -> str:
        return SCOPE_EXPANDED if self.qualifies else SCOPE_SAMPLE

    @property
    def threshold_note(self) -> str:
        if not self.enabled:
            return "未启用扩展行业检索。"
        if self.failure:
            return f"扩展检索失败，已按原报告样本继续：{self.failure}"
        if self.qualifies:
            return (
                f"达到主题升级门槛：高相关去重专利 {len(self.records)} 件、"
                f"申请人 {self.applicant_count} 个、IPC 小类分支 {self.ipc_branch_count} 个。"
            )
        missing: list[str] = []
        if len(self.records) < 10:
            missing.append(f"高相关专利不足10件（现有{len(self.records)}件）")
        if self.applicant_count < 3:
            missing.append(f"申请人不足3个（现有{self.applicant_count}个）")
        if self.ipc_branch_count < 2:
            missing.append(f"IPC小类分支不足2个（现有{self.ipc_branch_count}个）")
        return "未达到主题升级门槛：" + "；".join(missing) + "。"


@dataclass(frozen=True)
class ExistingPatentRole:
    evidence_id: str
    role: str
    related_theme: str
    rationale: str
    gap: str


@dataclass(frozen=True)
class LayoutNode:
    node_id: str
    kind: str
    title: str
    category: str
    protection_theme: str
    competitive_basis: str
    implementation_basis: str
    evidence_ids: tuple[str, ...]
    priority: str
    status: str


@dataclass(frozen=True)
class DesignAroundPath:
    path_type: str
    possible_path: str
    response: str
    evidence_state: str


@dataclass(frozen=True)
class MatrixCell:
    means: str
    effect: str
    evidence_state: str
    note: str


@dataclass(frozen=True)
class ThemePlan:
    theme_id: str
    name: str
    priority: str
    scope: str
    common_route: str
    target_route: str
    core_difference: str
    technical_effect: str
    confidence: str
    confidence_basis: str
    existing_roles: tuple[ExistingPatentRole, ...]
    elephant: LayoutNode
    horses: tuple[LayoutNode, ...]
    deferred: tuple[LayoutNode, ...]
    design_arounds: tuple[DesignAroundPath, ...]
    matrix: tuple[MatrixCell, ...]
    application_order: str
    combination_relation: str
    questions: tuple[str, ...]
    local_evidence: tuple[LocalPatentEvidence, ...]
    industry_coverage: IndustryCoverage


@dataclass(frozen=True)
class ElephantHorseReport:
    inputs: ElephantHorseInputs
    themes: tuple[ThemePlan, ...]
    management_summary: tuple[str, ...]
    shared_cores: tuple[str, ...]
    duplicate_nodes: tuple[str, ...]
    upstream_downstream_links: tuple[str, ...]
    suggested_batches: tuple[str, ...]
    overall_scope: str
    evidence_index: tuple[dict[str, str], ...]


class ExpandedIndustrySearcher:
    """Two-pass CNIPA search retaining enough evidence for the 10/3/2 threshold."""

    def __init__(self, search_transport: Callable | None = None, limit: int = 30):
        self._transport = search_transport
        self.limit = limit

    def search(self, keywords: Sequence[str]) -> IndustryCoverage:
        terms = normalize_search_terms(keywords)
        if len(terms) < 2:
            raise ValueError("扩展检索需要 2–8 个具体技术词")
        try:
            search = self._transport
            if search is None:
                from .disclosure.cnipa_upstream.cnipa_epub_crawler import search_epub_keywords

                search = search_epub_keywords
            first_batches = search(list(terms), patent_type="invention")
            first_hits = _dedupe_hits(hit for _html, hits in first_batches for hit in hits)
            _kind, prefixes = suggest_class_codes(first_hits, patent_type="invention")
            prefixes = tuple(prefixes[:3])
            pool = list(first_hits)
            if prefixes:
                second_batches = search(
                    list(terms[:3]), patent_type="invention", class_codes=list(prefixes)
                )
                pool.extend(hit for _html, hits in second_batches for hit in hits)
            records = _industry_records(_dedupe_hits(pool), terms, prefixes, self.limit)
            return IndustryCoverage(True, tuple(records), terms, prefixes)
        except Exception as exc:
            raise IndustrySearchError(f"国知局扩展检索失败：{exc}") from exc


class ElephantHorseAnalyzer:
    def __init__(
        self,
        client: ElephantHorseChatClient,
        extractor: TextExtractor | None = None,
        searcher: ExpandedIndustrySearcher | None = None,
    ):
        self.client = client
        self.extractor = extractor or TextExtractor()
        self.searcher = searcher or ExpandedIndustrySearcher()
        self._full_text_cache: dict[Path, tuple[str, str]] = {}

    def analyze(
        self,
        inputs: ElephantHorseInputs,
        expanded_search: bool = False,
        search_failure: SearchFailureCallback | None = None,
        on_theme: Callable[[str, bool], None] | None = None,
    ) -> ElephantHorseReport:
        themes: list[ThemePlan] = []
        for source in inputs.sources:
            if on_theme:
                on_theme(source.label, True)
            ranked_target_records = _rank_records(source, inputs.target.records, len(inputs.target.records))
            target_records = ranked_target_records[:3]
            baseline_records: list[tuple[str, TrendPatentRecord]] = []
            all_baseline = [
                (portfolio.label, record)
                for portfolio in inputs.baselines
                for record in portfolio.records
            ]
            for company, record in _rank_labeled_records(source, all_baseline, 5):
                baseline_records.append((company, record))
            selected_target_names = {item.filename for item in target_records}
            local = tuple(
                [self._local_evidence(inputs.target.label, inputs.target.root, item) for item in target_records]
                + [
                    self._summary_evidence(inputs.target.label, inputs.target.root, item)
                    for item in ranked_target_records
                    if item.filename not in selected_target_names
                ]
                + [self._local_evidence(company, _portfolio_root(inputs, company), item) for company, item in baseline_records]
            )
            industry = IndustryCoverage(False)
            if expanded_search:
                terms = derive_search_terms(source.label, source.context)
                industry = self._search_with_policy(source.label, terms, search_failure)
            response = self.client.chat(
                _ANALYSIS_SYSTEM_PROMPT,
                _analysis_user_prompt(source, local, industry),
            )
            themes.append(_parse_theme(source, response, local, industry, inputs.target.label))
            if on_theme:
                on_theme(source.label, False)
        return _coordinate(inputs, themes)

    def _search_with_policy(
        self,
        title: str,
        terms: tuple[str, ...],
        callback: SearchFailureCallback | None,
    ) -> IndustryCoverage:
        while True:
            try:
                return self.searcher.search(terms)
            except (IndustrySearchError, ValueError) as exc:
                if callback is None:
                    raise
                action = callback(title, str(exc))
                if action == SearchFailureAction.RETRY:
                    continue
                if action == SearchFailureAction.CONTINUE:
                    return IndustryCoverage(True, keywords=terms, failure=str(exc))
                raise ElephantHorseCancelled("用户取消象马组合生成")

    def _local_evidence(
        self, company: str, root: Path, record: TrendPatentRecord
    ) -> LocalPatentEvidence:
        source_path = _resolve_record_path(root, record.source_path, record.filename)
        summary_path = _resolve_record_path(root, record.summary_path, Path(record.filename).with_suffix(".md").name)
        summary = summary_path.read_text(encoding="utf-8", errors="ignore")
        if source_path not in self._full_text_cache:
            try:
                text = self.extractor.extract(source_path).strip()
                if not text:
                    raise RuntimeError("全文提取结果为空")
                self._full_text_cache[source_path] = (text[:24000], "全文已核验")
            except Exception as exc:
                self._full_text_cache[source_path] = (summary[:12000], f"全文提取失败，降级使用结构化摘要：{exc}")
        full_text, status = self._full_text_cache[source_path]
        publication = (
            record.metadata.get("公开（公告）号", "")
            or record.metadata.get("公开号", "")
            or source_path.stem
        )
        evidence_id = "E-" + hashlib.sha256(
            f"{company}|{source_path.resolve()}".encode("utf-8")
        ).hexdigest()[:10].upper()
        return LocalPatentEvidence(
            evidence_id,
            company,
            record.patent_name or source_path.stem,
            publication,
            source_path,
            summary_path,
            summary[:12000],
            full_text,
            status,
        )

    def _summary_evidence(
        self, company: str, root: Path, record: TrendPatentRecord
    ) -> LocalPatentEvidence:
        source_path = _resolve_record_path(root, record.source_path, record.filename)
        summary_path = _resolve_record_path(
            root, record.summary_path, Path(record.filename).with_suffix(".md").name
        )
        summary = summary_path.read_text(encoding="utf-8", errors="ignore")
        publication = (
            record.metadata.get("公开（公告）号", "")
            or record.metadata.get("公开号", "")
            or source_path.stem
        )
        evidence_id = "E-" + hashlib.sha256(
            f"{company}|{source_path.resolve()}".encode("utf-8")
        ).hexdigest()[:10].upper()
        return LocalPatentEvidence(
            evidence_id,
            company,
            record.patent_name or source_path.stem,
            publication,
            source_path,
            summary_path,
            summary[:12000],
            "",
            "仅结构化摘要筛选；未进入本主题目标专利全文核验前3件",
        )


def derive_appendix_path(report_path: Path) -> Path:
    report_path = Path(report_path)
    return report_path.with_name(f"{report_path.stem}_附录.docx")


def default_elephant_horse_output(report_path: Path, target_label: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*]', "_", target_label.strip()).rstrip(" .") or "目标"
    return Path(report_path).parent / f"{safe}_保护型象马组合布局报告.docx"


def load_elephant_horse_inputs(report_path: Path) -> ElephantHorseInputs:
    report_path = Path(report_path).resolve()
    if not report_path.exists() or not report_path.is_file():
        raise FileNotFoundError(f"专利布局分析报告不存在：{report_path}")
    if report_path.suffix.lower() != ".docx":
        raise ValueError("专利布局分析报告必须使用 .docx 扩展名")
    appendix_path = derive_appendix_path(report_path)
    if not appendix_path.exists():
        raise FileNotFoundError(f"未找到与主报告配对的附录：{appendix_path}")
    rows = _appendix_portfolio_rows(appendix_path)
    if len(rows) < 2:
        raise ValueError("报告附录至少需要包含目标企业和一个竞争对手目录")
    sources = load_report_sources(report_path)
    issues: list[str] = []
    datasets: list[PortfolioDataset] = []
    source_details: list[PortfolioSource] = []
    for label, raw_root, appendix_count, classification_source in rows:
        root = Path(raw_root)
        if not root.is_absolute():
            root = (report_path.parent / root).resolve()
        if not root.exists() or not root.is_dir():
            issues.append(f"{label}：目录不可访问（{root}）")
            continue
        classification_path = root / "patent_classification.xlsx"
        if not classification_path.exists():
            issues.append(f"{label}：缺少分类结果 {classification_path}")
            continue
        documents = sorted(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in DOCUMENT_SUFFIXES
        )
        missing = [path.with_suffix(".md") for path in documents if not path.with_suffix(".md").exists()]
        empty = [path.with_suffix(".md") for path in documents if path.with_suffix(".md").exists() and not path.with_suffix(".md").read_text(encoding="utf-8", errors="ignore").strip()]
        if not documents:
            issues.append(f"{label}：目录中没有专利原文（DOCX/PDF）")
        if missing:
            issues.append(f"{label}：缺少 {len(missing)} 篇同名 Markdown 摘要（示例：{missing[0]}）")
        if empty:
            issues.append(f"{label}：存在 {len(empty)} 篇空摘要（示例：{empty[0]}）")
        try:
            records = load_classification_workbook(classification_path)
        except Exception as exc:
            issues.append(f"{label}：分类结果无法读取（{exc}）")
            continue
        if len(records) < len(documents):
            issues.append(f"{label}：分类记录不完整（{len(records)}/{len(documents)}）")
        if appendix_count and appendix_count != len(documents):
            issues.append(f"{label}：附录专利数 {appendix_count} 与目录原文数 {len(documents)} 不一致")
        datasets.append(PortfolioDataset(label, root, classification_path, "existing", records))
        source_details.append(
            PortfolioSource(label, root, appendix_count or len(documents), classification_source, len(documents), len(documents) - len(missing) - len(empty))
        )
    if issues:
        raise ValueError("象马组合输入未就绪：\n- " + "\n- ".join(issues))
    return ElephantHorseInputs(report_path, appendix_path, datasets[0], datasets[1:], sources, source_details)


def derive_search_terms(label: str, context: str) -> tuple[str, ...]:
    candidates = [label]
    for line in context.splitlines():
        if "：" in line:
            candidates.extend(re.split(r"[、，；。/（）()]+", line.split("：", 1)[1]))
    terms = normalize_search_terms(candidates)
    if len(terms) < 2:
        terms = normalize_search_terms([label, *re.findall(r"[\u4e00-\u9fff]{4,12}", context)])
    return terms[:8]


def _appendix_portfolio_rows(path: Path) -> list[tuple[str, str, int, str]]:
    document = Document(path)
    for table in document.tables:
        if not table.rows:
            continue
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        if not {"企业", "目录", "专利数"}.issubset(headers):
            continue
        index = {name: headers.index(name) for name in headers}
        rows: list[tuple[str, str, int, str]] = []
        for row in table.rows[1:]:
            values = [cell.text.strip() for cell in row.cells]
            label, root = values[index["企业"]], values[index["目录"]]
            if not label or not root:
                continue
            try:
                count = int(re.sub(r"\D", "", values[index["专利数"]]) or "0")
            except ValueError:
                count = 0
            source = values[index["分类来源"]] if "分类来源" in index else ""
            rows.append((label, root, count, source))
        if rows:
            return rows
    raise ValueError("报告附录未找到“企业/目录/专利数”样本表")


def _rank_records(source: Any, records: Sequence[TrendPatentRecord], limit: int) -> list[TrendPatentRecord]:
    query = f"{source.label}\n{source.summary}\n{source.context}".casefold()
    scored = [(_record_score(query, item), item.filename.casefold(), item) for item in records]
    return [item for _score, _name, item in sorted(scored, key=lambda value: (-value[0], value[1]))[:limit]]


def _rank_labeled_records(source: Any, records: Sequence[tuple[str, TrendPatentRecord]], limit: int) -> list[tuple[str, TrendPatentRecord]]:
    query = f"{source.label}\n{source.summary}\n{source.context}".casefold()
    scored = [(_record_score(query, item), company.casefold(), item.filename.casefold(), company, item) for company, item in records]
    return [(company, item) for _score, _company_key, _name, company, item in sorted(scored, key=lambda value: (-value[0], value[1], value[2]))[:limit]]


def _record_score(query: str, record: TrendPatentRecord) -> int:
    text = "\n".join([record.patent_name, record.primary_first_level, record.primary_second_level, record.candidate_summary, *record.sections.values()]).casefold()
    tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,8}", query))
    return sum(2 if token in record.patent_name.casefold() else 1 for token in tokens if token in text)


def _portfolio_root(inputs: ElephantHorseInputs, label: str) -> Path:
    for item in inputs.baselines:
        if item.label == label:
            return item.root
    raise KeyError(label)


def _resolve_record_path(root: Path, raw: str, fallback_name: str) -> Path:
    value = Path(raw) if raw else Path(fallback_name)
    candidates = [value, root / value, root / value.name, root / fallback_name]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    matches = list(root.rglob(value.name or fallback_name))
    if matches:
        return matches[0].resolve()
    raise FileNotFoundError(f"分类记录对应文件不存在：{raw or fallback_name}")


def _analysis_user_prompt(source: Any, local: tuple[LocalPatentEvidence, ...], industry: IndustryCoverage) -> str:
    evidence = [
        {
            "证据ID": item.evidence_id,
            "企业": item.company,
            "专利名称": item.patent_name,
            "公开号": item.publication_number,
            "摘要": item.summary[:4000],
            "全文核验片段": item.full_text[:6000],
            "证据状态": item.evidence_status,
        }
        for item in local
        if item.full_text
    ]
    external = [
        {
            "公开号": item.publication_number,
            "标题": item.title,
            "申请人": item.applicant,
            "摘要": item.abstract,
            "IPC": list(item.ipc_codes),
            "链接": item.url,
        }
        for item in industry.records
    ]
    return (
        f"主题编号：{source.source_id}\n主题名称：{source.label}\n报告上下文：\n{source.context}\n\n"
        f"本主题结论口径：{industry.scope}\n门槛说明：{industry.threshold_note}\n"
        f"本地证据：\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n"
        f"扩展检索证据：\n{json.dumps(external, ensure_ascii=False, indent=2)}\n"
        "请按系统指定 JSON 结构输出。"
    )


_ANALYSIS_SYSTEM_PROMPT = """你是中国专利组合规划分析师。任务是为已有专利布局分析报告形成保护型“1象＋N马”布局草案，而不是技术交底书或申请文件。
必须遵守：
1. 先比较样本常见路线和目标现有路线，再分析替换、省略、等效规避路径，形成手段×功效矩阵，最后给出立项节点。
2. 象是核心原理、通用架构或组合核心。只有证据支持上位概括、替代实现和多个分支时，成熟度才写“具备象布局基础”；否则写“象布局基础待补”。
3. 推荐节点只能有 1 个象、0–3 个马；马类别只能是场景、改进、上游或下游。其余方向放暂缓节点，不为凑数拆件。
4. 先把目标现有专利映射为：象核心支撑、外围马、一般证据、未形成组合关系；然后提出新增布局位。
5. 若材料没有可实施方案，只写保护主题、竞争依据、建议方向，实施依据写“材料未提供”，不得编造必要特征、结构、流程、效果、参数或实施例，并把缺口转成待补问题。
6. 未额外检索的手段×功效格子必须明确写“尚未检索”，不得写成空白或蓝海。
7. 不得输出“全行业穷尽”“必然新颖”“具备创造性”“高价值专利达标”等结论。
8. 所有事实判断引用给定证据 ID；不得虚构公开号、企业或证据。
仅输出 JSON 对象：
{
 "优先级":"高/中/低", "样本常见路线":"...", "目标现有路线":"...", "核心差异":"...", "技术效果":"...",
 "置信等级":"高/中/低", "置信依据":"...",
 "现有专利角色":[{"证据ID":"E-...","角色":"象核心支撑/外围马/一般证据/未形成组合关系","理由":"...","缺口":"..."}],
 "象候选":{"标题":"...","类别":"核心","保护主题":"...","竞争依据":"...","实施依据":"材料已有/材料未提供：...","证据ID":["E-..."],"优先级":"高/中/低","状态":"具备象布局基础/象布局基础待补"},
 "推荐马":[{"标题":"...","类别":"场景/改进/上游/下游","保护主题":"...","竞争依据":"...","实施依据":"...","证据ID":["E-..."],"优先级":"高/中/低","状态":"推荐"}],
 "暂缓节点":[{"标题":"...","类别":"场景/改进/上游/下游","保护主题":"...","竞争依据":"...","实施依据":"...","证据ID":["E-..."],"优先级":"中/低","状态":"暂缓"}],
 "规避路径":[{"类型":"替换/省略/等效","可能路径":"...","应对建议":"...","证据状态":"已由本地证据支持/扩展检索支持/尚未检索"}],
 "手段功效矩阵":[{"手段":"...","功效":"...","证据状态":"已由本地证据支持/扩展检索支持/尚未检索","说明":"..."}],
 "申请顺序":"...", "组合关系":"...", "待补问题":["...结构...","...流程...","...效果...","...替代实施方式..."]
}"""


def _parse_theme(
    source: Any,
    response: str | Mapping[str, Any],
    local: tuple[LocalPatentEvidence, ...],
    industry: IndustryCoverage,
    target_label: str,
) -> ThemePlan:
    payload = _mapping_payload(response)
    allowed_evidence = {item.evidence_id for item in local}
    roles = tuple(
        ExistingPatentRole(
            _text(item, "证据ID"), _choice(item, "角色", {"象核心支撑", "外围马", "一般证据", "未形成组合关系"}), source.label, _text(item, "理由"), _text(item, "缺口", allow_empty=True)
        )
        for item in _object_list(payload.get("现有专利角色", []), "现有专利角色")
    )
    for role in roles:
        if role.evidence_id not in allowed_evidence:
            raise ValueError(f"{source.source_id} 引用了未知本地证据：{role.evidence_id}")
    mapped_ids = {item.evidence_id for item in roles}
    roles = roles + tuple(
        ExistingPatentRole(
            item.evidence_id,
            "未形成组合关系",
            source.label,
            "已使用结构化摘要参与主题筛选，但相关度未进入本主题目标专利全文核验前3件。",
            "如拟纳入组合，需补充其与象核心或马节点的技术关联。",
        )
        for item in local
        if item.company == target_label and item.evidence_id not in mapped_ids
    )
    elephant = _parse_node(source.source_id, "象", 1, _object(payload, "象候选"), allowed_evidence)
    horse_items = _object_list(payload.get("推荐马", []), "推荐马")
    if len(horse_items) > 3:
        raise ValueError(f"{source.source_id} 推荐马超过 3 个")
    horses = tuple(_parse_node(source.source_id, "马", index, item, allowed_evidence) for index, item in enumerate(horse_items, 1))
    deferred = tuple(_parse_node(source.source_id, "暂缓", index, item, allowed_evidence) for index, item in enumerate(_object_list(payload.get("暂缓节点", []), "暂缓节点"), 1))
    design_arounds = tuple(DesignAroundPath(_choice(item, "类型", {"替换", "省略", "等效"}), _text(item, "可能路径"), _text(item, "应对建议"), _text(item, "证据状态")) for item in _object_list(payload.get("规避路径", []), "规避路径"))
    matrix = tuple(MatrixCell(_text(item, "手段"), _text(item, "功效"), _text(item, "证据状态"), _text(item, "说明")) for item in _object_list(payload.get("手段功效矩阵", []), "手段功效矩阵"))
    questions = tuple(_string_list(payload.get("待补问题", []), "待补问题"))
    theme = ThemePlan(
        source.source_id, source.label, _choice(payload, "优先级", {"高", "中", "低"}), industry.scope,
        _text(payload, "样本常见路线"), _text(payload, "目标现有路线"), _text(payload, "核心差异"), _text(payload, "技术效果"),
        _choice(payload, "置信等级", {"高", "中", "低"}), _text(payload, "置信依据"), roles, elephant, horses, deferred,
        design_arounds, matrix, _text(payload, "申请顺序"), _text(payload, "组合关系"), questions, local, industry,
    )
    _validate_no_forbidden(theme)
    return theme


def _parse_node(theme_id: str, kind: str, index: int, item: Mapping[str, Any], allowed_evidence: set[str]) -> LayoutNode:
    evidence_ids = tuple(_string_list(item.get("证据ID", []), "证据ID", allow_empty=True))
    unknown = set(evidence_ids) - allowed_evidence
    if unknown:
        raise ValueError(f"{theme_id} 布局节点引用了未知证据：{'、'.join(sorted(unknown))}")
    seed = f"{theme_id}|{kind}|{_text(item, '标题')}"
    node_id = f"{theme_id.upper()}-{kind}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:6].upper()}"
    return LayoutNode(node_id, kind, _text(item, "标题"), _text(item, "类别"), _text(item, "保护主题"), _text(item, "竞争依据"), _text(item, "实施依据"), evidence_ids, _text(item, "优先级"), _text(item, "状态"))


def _coordinate(inputs: ElephantHorseInputs, themes: list[ThemePlan]) -> ElephantHorseReport:
    all_nodes = [(theme, node) for theme in themes for node in (theme.elephant, *theme.horses, *theme.deferred)]
    title_groups: dict[str, list[tuple[ThemePlan, LayoutNode]]] = {}
    for theme, node in all_nodes:
        key = re.sub(r"[\W_]+", "", node.title).casefold()
        title_groups.setdefault(key, []).append((theme, node))
    duplicates = tuple(
        f"{items[0][1].title}：{'、'.join(item[0].name for item in items)}"
        for items in title_groups.values() if len(items) > 1
    )
    elephant_evidence: dict[tuple[str, ...], list[str]] = {}
    for theme in themes:
        elephant_evidence.setdefault(tuple(sorted(theme.elephant.evidence_ids)), []).append(theme.name)
    shared = tuple(f"共用象核心证据：{'、'.join(names)}" for key, names in elephant_evidence.items() if key and len(names) > 1)
    links = tuple(
        f"{theme.name}：{theme.combination_relation}" for theme in themes if theme.combination_relation.strip()
    )
    ordered = sorted(themes, key=lambda item: ({"高": 0, "中": 1, "低": 2}[item.priority], item.theme_id))
    batches = tuple(f"第{index}批：{theme.name}（{theme.application_order}）" for index, theme in enumerate(ordered, 1))
    high = [item for item in themes if item.priority == "高"]
    overall_scope = SCOPE_EXPANDED if high and all(item.industry_coverage.qualifies for item in high) else SCOPE_SAMPLE
    recommended = sum(1 + len(item.horses) for item in themes)
    summaries = (
        f"建议形成 {len(themes)} 组分主题保护型象马组合，共 {recommended} 个推荐布局节点；每组最多 1 象＋3 马。",
        f"优先主题：{'、'.join(item.name for item in ordered if item.priority == '高') or '暂无高优先级主题'}。",
        f"主要差异均按“{overall_scope}”口径表述；各主题的扩展检索等级在正文单独披露。",
        f"具备象布局基础的主题 {sum(item.elephant.status == '具备象布局基础' for item in themes)} 个，其余主题保留布局位并进入待补问题。",
    )
    evidence_index: list[dict[str, str]] = []
    seen_local: set[tuple[str, str]] = set()
    for theme in themes:
        used_ids = {ref for node in (theme.elephant, *theme.horses, *theme.deferred) for ref in node.evidence_ids}
        for item in theme.local_evidence:
            key = (theme.theme_id, item.evidence_id)
            if key in seen_local:
                continue
            seen_local.add(key)
            evidence_index.append({"主题": theme.name, "企业": item.company, "专利名称": item.patent_name, "公开号/文件名": item.publication_number or item.source_path.name, "来源位置": str(item.source_path), "使用层级": "布局节点证据" if item.evidence_id in used_ids else "主题筛选证据", "证据状态": item.evidence_status, "公开链接": ""})
        for item in theme.industry_coverage.records:
            evidence_index.append({"主题": theme.name, "企业": item.applicant, "专利名称": item.title, "公开号/文件名": item.publication_number, "来源位置": "国知局公开摘要", "使用层级": "扩展行业样本", "证据状态": "公开摘要已核验", "公开链接": item.url})
    return ElephantHorseReport(inputs, tuple(themes), summaries, shared, duplicates, links, batches, overall_scope, tuple(evidence_index))


def _mapping_payload(response: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    text = response.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("象马组合分析响应不是 JSON 对象")
    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, Mapping):
        raise ValueError("象马组合分析响应必须是 JSON 对象")
    return payload


def _object(payload: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} 必须是对象")
    return value


def _object_list(value: Any, field: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field} 必须是对象数组")
    return list(value)


def _string_list(value: Any, field: str, allow_empty: bool = False) -> list[str]:
    if value in (None, []) and allow_empty:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} 必须是非空字符串数组")
    return [item.strip() for item in value]


def _text(payload: Mapping[str, Any], field: str, allow_empty: bool = False) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{field} 必须是{'可为空的' if allow_empty else '非空'}字符串")
    return value.strip()


def _choice(payload: Mapping[str, Any], field: str, choices: set[str]) -> str:
    value = _text(payload, field)
    if value not in choices:
        raise ValueError(f"{field} 必须是：{'、'.join(sorted(choices))}")
    return value


def _validate_no_forbidden(theme: ThemePlan) -> None:
    text = str(theme)
    for phrase in FORBIDDEN_CONCLUSIONS:
        if phrase in text:
            raise ValueError(f"{theme.theme_id} 包含禁止结论：{phrase}")


def _dedupe_hits(values) -> list:
    result, seen = [], set()
    for item in values:
        key = (_publication_number(item) or getattr(item, "link", None) or "").strip().upper()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _industry_records(hits, terms: tuple[str, ...], prefixes: tuple[str, ...], limit: int) -> list[IndustryPatentEvidence]:
    records: list[IndustryPatentEvidence] = []
    for hit in hits:
        publication = _publication_number(hit)
        title = (getattr(hit, "title", None) or "").strip()
        abstract = (getattr(hit, "abstract", None) or "").strip()
        url = (getattr(hit, "link", None) or "").strip()
        ipc_codes = tuple(getattr(hit, "ipc_codes", None) or ())
        if not publication or not title or not _valid_cnipa_url(url):
            continue
        haystack = f"{title}\n{abstract}".casefold()
        matches = sum(term.casefold() in haystack for term in terms)
        ipc_match = any(any(code.replace(" ", "").upper().startswith(prefix.upper()) for prefix in prefixes) for code in ipc_codes) if prefixes else False
        score = matches + (1 if ipc_match else 0) + (1 if any(term.casefold() in title.casefold() for term in terms) else 0)
        if score < 2:
            continue
        records.append(IndustryPatentEvidence(publication, title, (getattr(hit, "applicant", None) or "").strip(), abstract, ipc_codes, url, score))
    return sorted(records, key=lambda item: (-item.relevance_score, item.publication_number))[:limit]


def _publication_number(hit) -> str:
    direct = (getattr(hit, "pub_number", None) or "").strip().replace(" ", "")
    if direct:
        return direct.upper()
    match = re.search(r"/patent/(CN\d{8,}[A-Z]\d?)", (getattr(hit, "link", None) or ""), re.I)
    return match.group(1).upper() if match else ""


def _valid_cnipa_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "epub.cnipa.gov.cn"


def _normalize_applicant(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _ipc_subclasses(values: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        match = re.search(r"\b([A-HY]\d{2}[A-Z])", value.replace(" ", "").upper())
        if match:
            result.add(match.group(1))
    return result


__all__ = [
    "ElephantHorseAnalyzer", "ElephantHorseCancelled", "ElephantHorseInputs", "ElephantHorseReport",
    "ExpandedIndustrySearcher", "IndustryCoverage", "IndustryPatentEvidence", "SearchFailureAction",
    "SearchFailureCallback", "default_elephant_horse_output", "derive_appendix_path", "derive_search_terms",
    "load_elephant_horse_inputs", "SCOPE_SAMPLE", "SCOPE_EXPANDED",
]
