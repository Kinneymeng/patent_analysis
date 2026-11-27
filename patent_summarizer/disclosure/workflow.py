from __future__ import annotations

import json
from dataclasses import asdict, replace
from http.client import IncompleteRead
from typing import Any, Callable, Mapping, TypeVar
from urllib.error import URLError

from ..llm import HttpResponseError
from .checkpoint import DisclosureCheckpointStore

from .models import (
    DisclosureChatClient,
    DisclosureDiagnostic,
    DisclosurePreview,
    DisclosureSection,
    DisclosureSourceItem,
    InventionCandidate,
    PriorArtResult,
    PriorArtRecord,
    PriorArtStatus,
    TechnicalDisclosure,
    TitleProposal,
)
from .prompt_loader import load_prompt, system_prompt
from .recovery import (
    check_section,
    complete_staged_document,
    fallback_section,
    normalize_section,
    prepare_artifacts,
    review_item,
    reviewable_section,
    salvage_section,
)
from .source_loader import source_evidence_catalog
from .validation import (
    SECTION_TITLES,
    disclosure_to_payload,
    disclosure_section_to_payload,
    mapping_payload,
    parse_disclosure_preview,
    parse_disclosure_section,
    parse_invention_candidates,
    parse_technical_disclosure,
    reconcile_disclosure_with_preview,
    validate_disclosure_against_preview,
    validate_disclosure_title,
)


ParsedValue = TypeVar("ParsedValue")
DISCLOSURE_VALIDATION_RETRIES = 1
MODEL_INTERRUPTION = (ConnectionError, TimeoutError, IncompleteRead, URLError, HttpResponseError)


class DisclosureResponseError(ValueError):
    pass


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
        if preview is not None:
            checkpoint_key = None
            if self.checkpoint_store is not None:
                checkpoint_key = self.checkpoint_store.key(source.context, title, confirmed)
                cached = self.checkpoint_store.load_disclosure(checkpoint_key, "draft", title)
                if cached is not None:
                    result = complete_staged_document(cached, preview)
                    if not result.review_items:
                        return result
            result = self._draft_by_sections(source, title, preview, checkpoint_key)
            if checkpoint_key is not None and not result.review_items:
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
            lambda response: _parse_confirmed_disclosure(response, title, preview),
        )
        return result

    def review(
        self,
        source: DisclosureSourceItem,
        draft: TechnicalDisclosure,
        preview: DisclosurePreview | None = None,
    ) -> TechnicalDisclosure:
        if preview is not None and draft.is_structured:
            checkpoint_key = None
            if self.checkpoint_store is not None:
                checkpoint_key = self.checkpoint_store.key(
                    source.context, draft.title, {**_preview_payload(preview), "draft": disclosure_to_payload(draft)},
                )
                cached = self.checkpoint_store.load_disclosure(checkpoint_key, "review", draft.title)
                if cached is not None and not draft.review_items:
                    result = complete_staged_document(cached, preview)
                    if not result.review_items:
                        return result
            result = self._review_by_sections(source, draft, preview, checkpoint_key)
            if checkpoint_key is not None and not result.review_items:
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
            lambda response: _parse_confirmed_disclosure(response, draft.title, preview),
        )
        return result

    def _draft_by_sections(
        self,
        source: DisclosureSourceItem,
        title: str,
        preview: DisclosurePreview,
        checkpoint_key: str | None,
    ) -> TechnicalDisclosure:
        confirmed = json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
        sections = []
        issues = []
        for number, section_title in SECTION_TITLES.items():
            stage = f"section-{number}"
            prompt = (
                _source_prompt(source)
                + f"\n\n用户确认的完整方案：\n{confirmed}"
                + "\n\n已完成的前文章节：\n" + _sections_context(sections)
                + f"\n\n当前只撰写：{number}、{section_title}\n\n"
                + load_prompt("section.md")
            )
            section, pending = self._staged_section(
                checkpoint_key, stage, number, prompt, preview,
            )
            sections.append(section)
            issues.extend(pending)

        artifact_stage = "artifacts"
        artifact_context = (
            _source_prompt(source)
            + f"\n\n用户确认的完整方案：\n{confirmed}"
            + "\n\n已经完成的四章正文：\n" + _sections_context(sections)
        )
        artifact_key = self._context_key(checkpoint_key, artifact_context)
        cached = self.checkpoint_store.load_payload(artifact_key, artifact_stage) if artifact_key else None
        artifact_response_failed = False

        def parse_artifact_payload(response):
            payload = mapping_payload(response)
            if not {"摘要", "图示", "参数表", "公式"}.intersection(payload):
                raise ValueError("图文响应没有可识别的字段")
            return payload

        if cached is None:
            try:
                raw = self._validated_chat(
                    f"{title} 图文与摘要", system_prompt(),
                    artifact_context + "\n\n" + load_prompt("artifacts.md"), parse_artifact_payload,
                )
            except (DisclosureResponseError, *MODEL_INTERRUPTION) as exc:
                issues.append(review_item(
                    artifact_stage, "artifacts-response-pending", f"图文响应未完成：{exc}",
                    "已使用可核对的摘要和正文，待补图文附件",
                ))
                raw = {}
                artifact_response_failed = True
        else:
            raw = cached

        def repair(field, value, reason, parser):
            if artifact_response_failed:
                return None
            stage = f"{title} {field}局部纠错"
            if self.on_validation_retry is not None:
                self.on_validation_retry(stage, 1, reason)
            prompt = (
                artifact_context
                + f"\n\n只修复一个{field}项目，校验问题：{reason}"
                + f"\n原项目：{json.dumps(value, ensure_ascii=False)}"
                + f"\n只返回含“{field}”键的 JSON，沿用下述规范中该字段的格式。"
                + "\n\n" + load_prompt("artifacts.md")
                + f"\n最终只返回“{field}”字段。保持确认方案及其余项目不变。"
            )
            try:
                return self._validated_chat(
                    stage, system_prompt(), prompt,
                    lambda response: parser(mapping_payload(response)), retries=0,
                )
            except (DisclosureResponseError, *MODEL_INTERRUPTION):
                return None

        document = TechnicalDisclosure(
            title=title, abstract="", sections=tuple(sections),
            content_paragraphs=[paragraph for section in sections for paragraph in section.paragraphs],
            prior_art_status=preview.prior_art.status, diagnostics=tuple(issues),
        )
        document, updated = prepare_artifacts(document, raw, preview, repair)
        if artifact_key and not artifact_response_failed and updated != cached:
            self.checkpoint_store.save_payload(artifact_key, artifact_stage, updated)
        return complete_staged_document(document, preview)

    def _review_by_sections(
        self,
        source: DisclosureSourceItem,
        draft: TechnicalDisclosure,
        preview: DisclosurePreview,
        checkpoint_key: str | None,
    ) -> TechnicalDisclosure:
        confirmed = json.dumps(_preview_payload(preview), ensure_ascii=False, indent=2)
        reviewed_sections = []
        issues = list(draft.diagnostics)
        draft_context = json.dumps(disclosure_to_payload(draft), ensure_ascii=False, indent=2)
        for section in draft.sections:
            stage = f"review-section-{section.number}"
            if any(
                item.stage == f"section-{section.number}" and item.code == "chapter-writing-pending"
                for item in draft.review_items
            ):
                reviewed_sections.append(section)
                continue
            prompt = (
                _source_prompt(source)
                + f"\n\n用户确认的完整方案：\n{confirmed}"
                + "\n\n全文上下文：\n" + draft_context
                + "\n\n已审校的前文章节：\n" + _sections_context(reviewed_sections)
                + "\n\n当前待审校章节：\n"
                + json.dumps(disclosure_section_to_payload(section), ensure_ascii=False, indent=2)
                + "\n\n" + load_prompt("section_review.md")
            )
            reviewed, pending = self._staged_section(
                checkpoint_key, stage, section.number, prompt, preview, original=section,
            )
            reviewed_sections.append(reviewed)
            issues.extend(pending)
        reviewed_disclosure = replace(
            draft, sections=tuple(reviewed_sections), diagnostics=tuple(issues)
        )
        return complete_staged_document(reviewed_disclosure, preview)

    def _context_key(self, checkpoint_key: str | None, context: str) -> str | None:
        if checkpoint_key is None:
            return None
        return self.checkpoint_store.key(context, checkpoint_key, {})

    def _staged_section(
        self,
        checkpoint_key: str | None,
        stage: str,
        number: str,
        prompt: str,
        preview: DisclosurePreview,
        *,
        original: DisclosureSection | None = None,
    ) -> tuple[DisclosureSection, list[DisclosureDiagnostic]]:
        section_key = self._context_key(checkpoint_key, prompt)
        cached = self.checkpoint_store.load_payload(section_key, stage) if section_key else None
        usable = None
        partial = None

        def parse_structure(response):
            nonlocal usable, partial
            try:
                section = normalize_section(parse_disclosure_section(response, number))
            except (TypeError, ValueError):
                partial = salvage_section(response, number) or partial
                raise
            usable = section
            return section

        def parse(response):
            section = parse_structure(response)
            check_section(section, preview)
            return section

        error = None
        if cached is not None:
            try:
                return parse(cached), []
            except (TypeError, ValueError) as exc:
                error = str(exc)
        if error is not None and usable is not None:
            if self.on_validation_retry is not None:
                self.on_validation_retry(stage, 1, error)
            prompt = _repair_prompt(
                prompt + "\n\n只修复本章已保存稿中的错误：\n"
                + json.dumps(disclosure_section_to_payload(usable), ensure_ascii=False),
                error,
            )
            retries = 0
        else:
            retries = self.validation_retries
        try:
            section = self._validated_chat(
                stage, system_prompt(), prompt, parse, retries=retries, preserve_response=True,
            )
        except (DisclosureResponseError, *MODEL_INTERRUPTION) as exc:
            if original is None and isinstance(exc, MODEL_INTERRUPTION):
                raise
            if original is not None:
                return original, [review_item(
                    stage, "chapter-review-pending", f"第{number}章独立审校未完成",
                    "已保留可用初稿，请复核本章",
                )]
            if section_key and usable is not None:
                payload = disclosure_section_to_payload(usable)
                if payload != cached:
                    self.checkpoint_store.save_payload(section_key, stage, payload)
            recovered = usable or partial or fallback_section(number, preview)
            if recovered is None:
                raise
            recovered, issues = reviewable_section(recovered, preview, stage)
            try:
                recovered = parse_disclosure_section(disclosure_section_to_payload(recovered), number)
            except ValueError:
                fallback = fallback_section(number, preview)
                if fallback is None:
                    raise exc
                recovered = fallback
            return recovered, [*issues, review_item(
                stage, "chapter-writing-pending", f"第{number}章撰写未完成",
                "已保留可核对的内容，请复核本章",
            )]
        if section_key:
            self.checkpoint_store.save_payload(section_key, stage, disclosure_section_to_payload(section))
        return section, []

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
            ),
        )

    def _validated_chat(
        self,
        stage: str,
        system: str,
        user_prompt: str,
        parser: Callable[[str | Mapping[str, Any]], ParsedValue],
        *,
        retries: int | None = None,
        preserve_response: bool = False,
    ) -> ParsedValue:
        retries = self.validation_retries if retries is None else retries
        prompt = user_prompt
        for attempt in range(retries + 1):
            response = self.client.chat(system, prompt)
            try:
                parsed = parser(response)
                if attempt > 0 and hasattr(self.client, "store_validated"):
                    self.client.store_validated(system, user_prompt, response)
                return parsed
            except (TypeError, ValueError) as exc:
                if hasattr(self.client, "invalidate"):
                    self.client.invalidate(system, prompt, str(exc), response)
                if attempt >= retries:
                    raise DisclosureResponseError(f"{stage}结构化响应校验失败：{exc}") from exc
                if self.on_validation_retry is not None:
                    self.on_validation_retry(stage, attempt + 1, str(exc))
                repair_base = user_prompt
                if preserve_response:
                    previous = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
                    repair_base += f"\n\n保留上一稿的有效内容，只更正问题部分：\n{previous}"
                prompt = _repair_prompt(repair_base, str(exc))
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


def _sections_context(sections: list[DisclosureSection]) -> str:
    return json.dumps(
        [disclosure_section_to_payload(section)["章节"] for section in sections],
        ensure_ascii=False, indent=2,
    )


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
) -> TechnicalDisclosure:
    disclosure = parse_technical_disclosure(response, title)
    if preview is None:
        return disclosure
    if not disclosure.is_structured:
        raise ValueError("研发技术交底书必须返回统一四章结构")
    disclosure = replace(disclosure, prior_art_status=preview.prior_art.status)
    disclosure = reconcile_disclosure_with_preview(disclosure, preview)
    validate_disclosure_against_preview(disclosure, preview)
    return disclosure


__all__ = ["DisclosureAnalyzer", "find_candidate"]
