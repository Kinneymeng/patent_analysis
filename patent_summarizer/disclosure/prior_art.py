from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from urllib.parse import urlparse

from .models import PriorArtRecord, PriorArtResult, PriorArtStatus


class PriorArtSearchError(RuntimeError):
    def __init__(self, message: str, partial: PriorArtResult | None = None):
        super().__init__(message)
        self.partial = partial


class CnipaPriorArtSearcher:
    """Two-pass CNIPA invention search adapted from patent-disclosure-skill."""

    def __init__(self, search_transport: Callable | None = None):
        self._transport = search_transport

    def search(self, keywords: Sequence[str]) -> PriorArtResult:
        terms = normalize_search_terms(keywords)
        if not terms:
            raise ValueError("查新至少需要一个具体技术检索词")
        try:
            search, helpers = self._dependencies()
            first_batches = search(list(terms), patent_type="invention")
            first_hits = _dedupe(hit for _html, hits in first_batches for hit in hits)
            _kind, class_codes = helpers[0](first_hits, patent_type="invention")
            class_codes = class_codes[:3]

            second_hits = []
            if class_codes:
                second_batches = search(list(terms[:3]), patent_type="invention", class_codes=list(class_codes))
                second_pool = _dedupe(hit for _html, hits in second_batches for hit in hits)
                second_hits = helpers[1](
                    second_pool,
                    class_prefixes=list(class_codes),
                    core_terms=list(terms[:3]),
                    limit=6,
                )
            selected, reason = helpers[2](
                list(second_hits),
                list(first_hits),
                class_prefixes=list(class_codes),
                core_terms=list(terms),
                min_keep=4,
                limit=6,
            )
        except PriorArtSearchError:
            raise
        except Exception as exc:
            raise PriorArtSearchError(f"国知局查新失败：{exc}") from exc

        records = tuple(_record_from_hit(hit) for hit in selected if _usable_hit(hit))
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if not records:
            return PriorArtResult(
                status=PriorArtStatus.EMPTY,
                searched_at=now,
                keywords=terms,
                ipc_codes=tuple(class_codes),
                records=(),
                message="检索范围内未筛选出可核验的相关中国发明专利记录",
            )
        status = PriorArtStatus.SUCCESS if len(records) >= 4 else PriorArtStatus.PARTIAL
        message = "查新完成" if status == PriorArtStatus.SUCCESS else "检索范围内可核验近邻少于 4 条，已如实保留现有结果"
        if reason == "still_short":
            status = PriorArtStatus.PARTIAL
        return PriorArtResult(
            status=status,
            searched_at=now,
            keywords=terms,
            ipc_codes=tuple(class_codes),
            records=records,
            message=message,
        )

    def _dependencies(self):
        if self._transport is not None:
            from .cnipa_upstream.cnipa_epub_parse import (
                backfill_hits_for_disclosure,
                select_hits_for_disclosure,
                suggest_class_codes,
            )
            return self._transport, (suggest_class_codes, select_hits_for_disclosure, backfill_hits_for_disclosure)
        try:
            from .cnipa_upstream.cnipa_epub_crawler import search_epub_keywords
            from .cnipa_upstream.cnipa_epub_parse import (
                backfill_hits_for_disclosure,
                select_hits_for_disclosure,
                suggest_class_codes,
            )
        except ImportError as exc:
            if "playwright" in str(exc).casefold():
                raise PriorArtSearchError("便携包缺少 Playwright 查新组件") from exc
            raise
        return search_epub_keywords, (suggest_class_codes, select_hits_for_disclosure, backfill_hits_for_disclosure)


def normalize_search_terms(values: Sequence[str]) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        for part in re.split(r"[\s,，;；、]+", value or ""):
            term = part.strip()
            if 2 <= len(term) <= 24 and term not in terms and term not in {"系统", "方法", "装置", "技术", "控制"}:
                terms.append(term)
    return tuple(terms[:8])


def _dedupe(values) -> list:
    result = []
    seen: set[str] = set()
    for item in values:
        key = (getattr(item, "pub_number", None) or getattr(item, "link", None) or getattr(item, "title", None) or "").strip()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _usable_hit(hit) -> bool:
    publication_number = _publication_number(hit)
    return bool(
        (getattr(hit, "title", None) or "").strip()
        and re.fullmatch(r"CN\d{8,}[ABC]\d?", publication_number)
        and _valid_cnipa_url(getattr(hit, "link", None))
    )


def _record_from_hit(hit) -> PriorArtRecord:
    return PriorArtRecord(
        publication_number=_publication_number(hit),
        title=(getattr(hit, "title", None) or "").strip(),
        abstract=(getattr(hit, "abstract", None) or "").strip(),
        ipc_codes=tuple(getattr(hit, "ipc_codes", None) or ()),
        url=(getattr(hit, "link", None) or "").strip(),
    )


def _publication_number(hit) -> str:
    direct = (getattr(hit, "pub_number", None) or "").strip().replace(" ", "")
    if direct:
        return direct.upper()
    link = (getattr(hit, "link", None) or "").strip()
    match = re.search(r"/patent/(CN\d{8,}[A-Z]\d?)", link, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _valid_cnipa_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "epub.cnipa.gov.cn"


__all__ = ["CnipaPriorArtSearcher", "PriorArtSearchError", "normalize_search_terms"]
