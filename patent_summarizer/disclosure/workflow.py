from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any, Callable, Mapping, TypeVar

from .checkpoint import DisclosureCheckpointStore

from .models import (
    DisclosureChatClient,
    DisclosurePreview,
    DisclosureSourceItem,
    InventionCandidate,
    PriorArtResult,
    PriorArtRecord,
    PriorArtStatus,
    TechnicalDisclosure,
    TitleProposal,
)
from .prompt_loader import load_prompt, system_prompt
from .source_loader import source_evidence_catalog
from .validation import (
    disclosure_to_payload,
    disclosure_section_to_payload,
    mapping_payload,
    parse_disclosure_artifacts,
    parse_disclosure_preview,
    parse_disclosure_section,
    parse_invention_candidates,
    parse_technical_disclosure,
    reconcile_disclosure_with_preview,
    validate_disclosure_against_preview,
    validate_effect_claims,
    validate_disclosure_title,
)


ParsedValue = TypeVar("ParsedValue")
DISCLOSURE_VALIDATION_RETRIES = 1


class DisclosureAnalyzer:
    def __init__(
        self,
        client: DisclosureChatClient,
        validation_retries: int = DISCLOSURE_VALIDATION_RETRIES,
        on_validation_retry: Callable[[str, int, str], None] | None = None,
        on_validation_warning: Callable[[str, str], None] | None = None,
        checkpoint_store: DisclosureCheckpointStore | None = None,
    ):
        if validation_retries < 0:
            raise ValueError("validation_retries must not be negative")
        self.client = client
        self.validation_retries = validation_retries
        self.on_validation_retry = on_validation_retry
        self.on_validation_warning = on_validation_warning
        self.checkpoint_store = checkpoint_store

    def propose_candidates(self, source: DisclosureSourceItem) -> tuple[InventionCandidate, ...]:
        prompt = _source_prompt(source) + "\n\n" + load_prompt("candidates.md")
        def parse_candidates(response):
            payload = mapping_payload(response)
            raw = payload.get("候选方案")
            recommended_count = (
                sum(bool(item.get("推荐", False)) for item in raw if isinstance(item, Mapping))
                if isinstance(raw, list)
                else 1
            )
            candidates = parse_invention_candidates(payload)
            if recommended_count != 1 and self.on_validation_warning is not None:
                self.on_validation_warning(
                    f"{source.source_id} 候选发明方案",
                    "模型推荐项数量不是一项，已在本地保留第一项推荐或默认推荐第一项",
                )
            return candidates
        return self._validated_chat(
            f"{source.source_id} 候选发明方案",
            system_prompt(),
            prompt,
            parse_candidates,
        )

    def propose_titles(self, source: DisclosureSourceItem) -> TitleProposal:
        candidates = self.propose_candidates(source)
        return TitleProposal(source, [item.title for item in candidates], candidates)

    def plan_search_terms(
        self,
        source: DisclosureSourceItem,
        title: str,
        candidate: InventionCandidate | None = None,
    ) -> tuple[str, ...]:
        validate_disclosure_title(title)
        candidate_text = json.dumps(asdict(candidate), ensure_ascii=False, indent=2) if candidate else "（用户自定义标题；从来源中提取技术手段）"
        prompt = (
            _source_prompt(source)
            + f"\n\n确认标题：{title}\n候选方案：\n{candidate_text}\n\n"
            + load_prompt("search_terms.md")
        )
        return self._validated_chat(
            f"{title} 查新检索词",
            system_prompt(),
            prompt,
            _parse_search_terms,
        )

    def plan_search_terms_for_preview(
        self,
        source: DisclosureSourceItem,
        preview: DisclosurePreview,
    ) -> tuple[str, ...]:
        prompt = (
            _source_prompt(source)
            + "\n\n用户修改后的完整技术方案：\n"
            + json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
            + "\n\n请以用户修改后的具体机制为准重新规划检索词，不沿用已经失效的检索主题。\n\n"
            + load_prompt("search_terms.md")
        )
        return self._validated_chat(
            f"{preview.title} 修改后查新检索词",
            system_prompt(),
            prompt,
            _parse_search_terms,
        )

    def build_preview(
        self,
        source: DisclosureSourceItem,
        title: str,
        *,
        candidate: InventionCandidate | None = None,
        prior_art: PriorArtResult | None = None,
    ) -> DisclosurePreview:
        validate_disclosure_title(title)
        prior_art = prior_art or PriorArtResult.disabled()
        candidate_text = json.dumps(asdict(candidate), ensure_ascii=False, indent=2) if candidate else "（用户编辑或自定义的方向）"
        prompt = (
            _source_prompt(source)
            + f"\n\n确认标题：{title}\n已选方向：\n{candidate_text}"
            + f"\n\n查新上下文：\n{json.dumps(_prior_art_payload(prior_art), ensure_ascii=False, indent=2)}\n\n"
            + load_prompt("preview.md")
        )
        return self._validated_chat(
            f"{title} 完整方案预览",
            system_prompt(),
            prompt,
            lambda response: parse_disclosure_preview(response, title, source.source_id, prior_art),
        )

    def load_confirmed_preview(
        self,
        source: DisclosureSourceItem,
        title: str,
        candidate: InventionCandidate | None,
        *,
        prior_art_enabled: bool,
    ) -> DisclosurePreview | None:
        if self.checkpoint_store is None:
            return None
        key = self._confirmation_key(source, title, candidate, prior_art_enabled)
        payload = self.checkpoint_store.load_payload(key, "confirmed-preview")
        if payload is None:
            return None
        try:
            prior_art = _prior_art_from_payload(payload.get("查新", {}))
            return parse_disclosure_preview(payload, title, source.source_id, prior_art)
        except (TypeError, ValueError):
            return None

    def save_confirmed_preview(
        self,
        source: DisclosureSourceItem,
        preview: DisclosurePreview,
        candidate: InventionCandidate | None,
        *,
        prior_art_enabled: bool,
    ) -> None:
        if self.checkpoint_store is None:
            return
        key = self._confirmation_key(
            source,
            preview.title,
            candidate,
            prior_art_enabled,
        )
        self.checkpoint_store.save_payload(
            key,
            "confirmed-preview",
            _preview_payload(preview),
        )

    def _confirmation_key(
        self,
        source: DisclosureSourceItem,
        title: str,
        candidate: InventionCandidate | None,
        prior_art_enabled: bool,
    ) -> str:
        return self.checkpoint_store.key(
            source.context,
            title,
            {
                "candidate": asdict(candidate) if candidate is not None else None,
                "prior_art_enabled": prior_art_enabled,
            },
        )

    def draft(
        self,
        source: DisclosureSourceItem,
        title: str,
        preview: DisclosurePreview | None = None,
    ) -> TechnicalDisclosure:
        validate_disclosure_title(title)
        confirmed = _preview_payload(preview) if preview else {
            "标题": title,
            "说明": "兼容旧版非交互 CLI：请依据来源形成完整工程方案",
        }
        checkpoint_key = None
        if preview is not None and self.checkpoint_store is not None:
            checkpoint_key = self.checkpoint_store.key(
                source.context,
                title,
                _preview_payload(preview),
            )
            cached = self.checkpoint_store.load_disclosure(
                checkpoint_key,
                "draft",
                title,
            )
            if cached is not None:
                return _confirm_cached_disclosure(cached, preview, source.context)
            result = self._draft_by_sections(
                source,
                title,
                preview,
                checkpoint_key,
            )
            self.checkpoint_store.save_disclosure(checkpoint_key, "draft", result)
            return result
        prompt = (
            _source_prompt(source)
            + f"\n\n用户确认的完整方案：\n{json.dumps(confirmed, ensure_ascii=False, indent=2)}\n\n"
            + load_prompt("builder.md")
        )
        result = self._validated_chat(
            f"{title} 结构化初稿",
            system_prompt(),
            prompt,
            lambda response: _parse_confirmed_disclosure(response, title, preview, source.context),
        )
        if checkpoint_key is not None:
            self.checkpoint_store.save_disclosure(checkpoint_key, "draft", result)
        return result

    def review(
        self,
        source: DisclosureSourceItem,
        draft: TechnicalDisclosure,
        preview: DisclosurePreview | None = None,
    ) -> TechnicalDisclosure:
        checkpoint_key = None
        if preview is not None and self.checkpoint_store is not None:
            checkpoint_key = self.checkpoint_store.key(
                source.context,
                draft.title,
                {**_preview_payload(preview), "draft": disclosure_to_payload(draft)},
            )
            cached = self.checkpoint_store.load_disclosure(
                checkpoint_key,
                "review",
                draft.title,
            )
            if cached is not None:
                return _confirm_cached_disclosure(cached, preview, source.context)
            result = self._review_by_sections(
                source,
                draft,
                preview,
                checkpoint_key,
            )
            self.checkpoint_store.save_disclosure(checkpoint_key, "review", result)
            return result
        prompt = (
            _source_prompt(source)
            + f"\n\n用户确认的完整方案：\n{json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2) if preview else '（旧版兼容流程）'}"
            + f"\n\n待审校初稿：\n{json.dumps(disclosure_to_payload(draft), ensure_ascii=False, indent=2)}\n\n"
            + load_prompt("review.md")
            + "\n\n返回格式必须与待审校初稿完全相同。"
        )
        result = self._validated_chat(
            f"{draft.title} 独立审校",
            system_prompt(),
            prompt,
            lambda response: _parse_confirmed_disclosure(response, draft.title, preview, source.context),
        )
        if checkpoint_key is not None:
            self.checkpoint_store.save_disclosure(checkpoint_key, "review", result)
        return result

    def _draft_by_sections(
        self,
        source: DisclosureSourceItem,
        title: str,
        preview: DisclosurePreview,
        checkpoint_key: str,
    ) -> TechnicalDisclosure:
        confirmed = json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
        sections = []
        section_contexts: list[dict[str, Any]] = []
        for number, section_title in _section_titles().items():
            stage = f"section-{number}"
            cached = self.checkpoint_store.load_payload(checkpoint_key, stage)
            if cached is not None:
                section = parse_disclosure_section(cached, number)
            else:
                prompt = (
                    _source_prompt(source)
                    + f"\n\n用户确认的完整方案：\n{confirmed}"
                    + f"\n\n当前只撰写：{number}、{section_title}\n\n"
                    + load_prompt("section.md")
                )
                section = self._validated_chat(
                    f"{title} 第{number}章",
                    system_prompt(),
                    prompt,
                    lambda response, expected=number: parse_disclosure_section(response, expected),
                )
                self.checkpoint_store.save_payload(
                    checkpoint_key,
                    stage,
                    disclosure_section_to_payload(section),
                )
            sections.append(section)
            section_contexts.append(disclosure_section_to_payload(section)["章节"])

        artifact_stage = "artifacts"
        cached_artifacts = self.checkpoint_store.load_payload(
            checkpoint_key,
            artifact_stage,
        )
        if cached_artifacts is None:
            artifact_prompt = (
                _source_prompt(source)
                + f"\n\n用户确认的完整方案：\n{confirmed}"
                + "\n\n已经完成的六章正文：\n"
                + json.dumps(section_contexts, ensure_ascii=False, indent=2)
                + "\n\n"
                + load_prompt("artifacts.md")
            )
            artifacts = self._validated_chat(
                f"{title} 图文与摘要",
                system_prompt(),
                artifact_prompt,
                parse_disclosure_artifacts,
            )
            abstract, diagrams, tables, formulas = artifacts
            artifact_payload = {
                "摘要": abstract,
                "图示": [
                    {
                        "章节": item.section_number,
                        "类型": item.kind,
                        "标题": item.title,
                        "mermaid": item.mermaid,
                    }
                    for item in diagrams
                ],
                "参数表": [
                    {
                        "章节": item.section_number,
                        "标题": item.title,
                        "表头": list(item.headers),
                        "行": [list(row) for row in item.rows],
                    }
                    for item in tables
                ],
                "公式": [
                    {
                        "章节": item.section_number,
                        "编号": item.label,
                        "LaTeX": item.latex,
                        "说明": item.description,
                        "来源": item.origin,
                        "材料依据": item.source_ref,
                    }
                    for item in formulas
                ],
            }
            self.checkpoint_store.save_payload(
                checkpoint_key,
                artifact_stage,
                artifact_payload,
            )
        else:
            abstract, diagrams, tables, formulas = parse_disclosure_artifacts(
                cached_artifacts
            )

        disclosure = TechnicalDisclosure(
            title=title,
            abstract=abstract,
            content_paragraphs=[
                paragraph for section in sections for paragraph in section.paragraphs
            ],
            sections=tuple(sections),
            diagrams=diagrams,
            tables=tables,
            formulas=formulas,
        )
        return _parse_confirmed_disclosure(
            disclosure_to_payload(disclosure),
            title,
            preview,
            source.context,
        )

    def _review_by_sections(
        self,
        source: DisclosureSourceItem,
        draft: TechnicalDisclosure,
        preview: DisclosurePreview,
        checkpoint_key: str,
    ) -> TechnicalDisclosure:
        confirmed = json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
        reviewed_sections = []
        draft_payload = disclosure_to_payload(draft)
        for section in draft.sections:
            stage = f"review-section-{section.number}"
            cached = self.checkpoint_store.load_payload(checkpoint_key, stage)
            if cached is not None:
                reviewed = parse_disclosure_section(cached, section.number)
            else:
                prompt = (
                    _source_prompt(source)
                    + f"\n\n用户确认的完整方案：\n{confirmed}"
                    + "\n\n全文上下文：\n"
                    + json.dumps(draft_payload, ensure_ascii=False, indent=2)
                    + "\n\n当前待审校章节：\n"
                    + json.dumps(
                        disclosure_section_to_payload(section),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n\n"
                    + load_prompt("section_review.md")
                )
                reviewed = self._validated_chat(
                    f"{draft.title} 第{section.number}章审校",
                    system_prompt(),
                    prompt,
                    lambda response, expected=section.number: parse_disclosure_section(
                        response,
                        expected,
                    ),
                )
                self.checkpoint_store.save_payload(
                    checkpoint_key,
                    stage,
                    disclosure_section_to_payload(reviewed),
                )
            reviewed_sections.append(reviewed)
        reviewed_disclosure = replace(
            draft,
            sections=tuple(reviewed_sections),
            content_paragraphs=[
                paragraph
                for section in reviewed_sections
                for paragraph in section.paragraphs
            ],
        )
        return _parse_confirmed_disclosure(
            disclosure_to_payload(reviewed_disclosure),
            draft.title,
            preview,
            source.context,
        )

    def repair_delivery(
        self,
        source: DisclosureSourceItem,
        disclosure: TechnicalDisclosure,
        preview: DisclosurePreview,
        error: str,
    ) -> TechnicalDisclosure:
        prompt = (
            _source_prompt(source)
            + "\n\n用户确认的完整方案：\n"
            + json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
            + "\n\n未能完成图文交付的文稿：\n"
            + json.dumps(disclosure_to_payload(disclosure), ensure_ascii=False, indent=2)
            + "\n\n本地转换或一致性错误：\n"
            + error
            + "\n\n请修复导致转换失败的 Mermaid、LaTeX、表格或相关正文，"
            + "不得改变用户确认的核心模块、输入输出、流程和参数属性。"
            + "\n\n"
            + load_prompt("review.md")
            + "\n\n只返回与未能交付文稿格式完全相同的完整 JSON。"
        )
        return self._validated_chat(
            f"{disclosure.title} 图文交付修复",
            system_prompt(),
            prompt,
            lambda response: _parse_confirmed_disclosure(
                response,
                disclosure.title,
                preview,
                source.context,
            ),
        )

    def _validated_chat(
        self,
        stage: str,
        system: str,
        user_prompt: str,
        parser: Callable[[str | Mapping[str, Any]], ParsedValue],
    ) -> ParsedValue:
        prompt = user_prompt
        for attempt in range(self.validation_retries + 1):
            response = self.client.chat(system, prompt)
            try:
                parsed = parser(response)
                if attempt > 0 and hasattr(self.client, "store_validated"):
                    self.client.store_validated(system, user_prompt, response)
                return parsed
            except (TypeError, ValueError) as exc:
                if hasattr(self.client, "invalidate"):
                    self.client.invalidate(system, prompt, str(exc), response)
                if attempt >= self.validation_retries:
                    raise ValueError(f"{stage}结构化响应校验失败：{exc}") from exc
                if self.on_validation_retry is not None:
                    self.on_validation_retry(stage, attempt + 1, str(exc))
                prompt = _repair_prompt(user_prompt, str(exc))
        raise AssertionError("unreachable")


def find_candidate(proposal: TitleProposal, title: str) -> InventionCandidate | None:
    return next((item for item in proposal.candidates if item.title == title), None)


def _source_prompt(source: DisclosureSourceItem) -> str:
    return (
        f"来源编号：{source.source_id}\n"
        f"来源名称：{source.label}\n"
        f"来源摘要：{source.summary}\n"
        f"技术上下文：\n{source.context}\n\n"
        f"材料定位目录：\n{source_evidence_catalog(source)}"
    )


def _parse_search_terms(response: str | Mapping[str, Any]) -> tuple[str, ...]:
    payload = mapping_payload(response)
    if set(payload) != {"检索词"}:
        raise ValueError("查新检索词响应必须且只能包含“检索词”字段")
    values = payload["检索词"]
    if not isinstance(values, list) or not 2 <= len(values) <= 8:
        raise ValueError("查新检索词必须包含 2 至 8 项")
    terms: list[str] = []
    for value in values:
        if not isinstance(value, str) or not 2 <= len(value.strip()) <= 24:
            raise ValueError("每个查新检索词必须是 2 至 24 字符的字符串")
        term = value.strip()
        if term in {"系统", "方法", "装置", "技术", "控制"}:
            raise ValueError(f"查新检索词过于宽泛：{term}")
        if term not in terms:
            terms.append(term)
    if len(terms) < 2:
        raise ValueError("查新检索词去重后不足 2 项")
    return tuple(terms)


def _section_titles() -> dict[str, str]:
    return {
        "一": "技术背景与现有方案",
        "二": "需要解决的技术问题",
        "三": "技术方案详细说明",
        "四": "技术效果与适用边界",
        "五": "技术关键点",
        "六": "实施例与验证方法",
    }


def _prior_art_payload(value: PriorArtResult) -> dict[str, Any]:
    return {
        "状态": value.status.value,
        "检索时间": value.searched_at,
        "检索词": list(value.keywords),
        "IPC": list(value.ipc_codes),
        "说明": value.message,
        "记录": [
            {
                "公开号": item.publication_number,
                "标题": item.title,
                "摘要": item.abstract,
                "IPC": list(item.ipc_codes),
                "来源链接": item.url,
                "来源": item.source,
            }
            for item in value.records
        ],
    }


def _prior_art_from_payload(value: Any) -> PriorArtResult:
    if not isinstance(value, Mapping):
        return PriorArtResult.disabled()
    try:
        status = PriorArtStatus(str(value.get("状态", PriorArtStatus.DISABLED.value)))
    except ValueError:
        status = PriorArtStatus.DISABLED
    records = []
    for item in value.get("记录", []):
        if not isinstance(item, Mapping):
            continue
        records.append(
            PriorArtRecord(
                publication_number=str(item.get("公开号", "")),
                title=str(item.get("标题", "")),
                abstract=str(item.get("摘要", "")),
                ipc_codes=tuple(str(code) for code in item.get("IPC", [])),
                url=str(item.get("来源链接", "")),
                source=str(item.get("来源", "国家知识产权局专利公布公告系统")),
            )
        )
    return PriorArtResult(
        status=status,
        searched_at=str(value.get("检索时间", "")),
        keywords=tuple(str(item) for item in value.get("检索词", [])),
        ipc_codes=tuple(str(item) for item in value.get("IPC", [])),
        records=tuple(records),
        message=str(value.get("说明", "")),
    )


def _preview_payload(value: DisclosurePreview | None) -> dict[str, Any]:
    if value is None:
        return {}
    return {
        "标题": value.title,
        "技术问题": list(value.technical_problems),
        "模块": [
            {"名称": item.name, "职责": item.responsibility, "输入": list(item.inputs), "输出": list(item.outputs)}
            for item in value.modules
        ],
        "流程": list(value.process_steps),
        "技术关键点": list(value.key_points),
        "材料依据": list(value.evidence_refs),
        "建议参数": [
            {
                "名称": item.name,
                "符号": item.symbol,
                "含义": item.meaning,
                "值或范围": item.value_or_range,
                "依据": item.basis,
                "示例": item.is_example,
            }
            for item in value.suggested_parameters
        ],
        "假设": list(value.assumptions),
        "待确认问题": list(value.open_questions),
        "查新": _prior_art_payload(value.prior_art),
    }


def _repair_prompt(original_prompt: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一次响应未通过本地结构和工程写作校验。请只返回完整修订 JSON，不要解释。\n"
        f"校验错误：{error}"
    )


def _parse_confirmed_disclosure(
    response: str | Mapping[str, Any],
    title: str,
    preview: DisclosurePreview | None,
    source_context: str,
) -> TechnicalDisclosure:
    disclosure = parse_technical_disclosure(response, title)
    if preview is None:
        return disclosure
    if not disclosure.is_structured:
        raise ValueError("升级后的研发技术交底书必须返回统一六章结构")
    disclosure = replace(disclosure, prior_art_status=preview.prior_art.status)
    disclosure = reconcile_disclosure_with_preview(disclosure, preview)
    validate_disclosure_against_preview(disclosure, preview)
    validate_effect_claims(disclosure, source_context)
    return disclosure


def _confirm_cached_disclosure(
    disclosure: TechnicalDisclosure,
    preview: DisclosurePreview,
    source_context: str,
) -> TechnicalDisclosure:
    disclosure = replace(disclosure, prior_art_status=preview.prior_art.status)
    disclosure = reconcile_disclosure_with_preview(disclosure, preview)
    validate_disclosure_against_preview(disclosure, preview)
    validate_effect_claims(disclosure, source_context)
    return disclosure


__all__ = ["DisclosureAnalyzer", "find_candidate"]
