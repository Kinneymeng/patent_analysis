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
        if self._transport is not None:
            return self._search_rounds(terms, self._transport)
        try:
            from .cnipa_upstream.cnipa_epub_crawler import epub_search_session
        except ImportError as exc:
            if "playwright" in str(exc).casefold():
                raise PriorArtSearchError("便携包缺少 Playwright 查新组件") from exc
            raise

        try:
            with epub_search_session() as search:
                return self._search_rounds(terms, search)
        except PriorArtSearchError:
            raise
        except Exception as exc:
            raise PriorArtSearchError(f"国知局查新浏览器会话失败：{_error_detail(exc)}") from exc

    def _search_rounds(self, terms: tuple[str, ...], search: Callable) -> PriorArtResult:
        from .cnipa_upstream.cnipa_epub_parse import (
            backfill_hits_for_disclosure,
            select_hits_for_disclosure,
            suggest_class_codes,
        )

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            first_hits = _batch_hits(search(list(terms), patent_type="invention"))
            _kind, class_codes = suggest_class_codes(first_hits, patent_type="invention")
            class_codes = class_codes[:3]
        except Exception as exc:
            return _partial_result("国知局关键词检索失败", exc, terms, now)

        second_pool = []
        second_hits = []
        if class_codes:
            try:
                second_pool = _batch_hits(
                    search(list(terms[:3]), patent_type="invention", class_codes=list(class_codes)),
                )
                second_hits = select_hits_for_disclosure(
                    second_pool, class_prefixes=list(class_codes), core_terms=list(terms[:3]), limit=6,
                )
            except Exception as exc:
                return _partial_result("IPC 高级检索未完成", exc, terms, now, class_codes, first_hits)

        selected, reason = backfill_hits_for_disclosure(
            second_hits, first_hits,
            class_prefixes=list(class_codes), core_terms=list(terms), min_keep=4, limit=6,
        )
        records = _verified_records(selected)
        if not records and (first_hits or second_pool):
            records = _verified_records(_dedupe([*first_hits, *second_pool]))
            if not records:
                raise PriorArtSearchError("国知局结果页展示了记录，但未能核验公开号及来源链接")
            reason = "still_short"
        if not records:
            status = PriorArtStatus.EMPTY
            message = "国知局结果页显示本次检索范围内无结果"
        elif len(records) >= 4 and reason != "still_short":
            status = PriorArtStatus.SUCCESS
            message = "查新完成"
        else:
            status = PriorArtStatus.PARTIAL
            message = "已保留本次检索范围内的可核验记录"
        return PriorArtResult(
            status=status, searched_at=now, keywords=terms, ipc_codes=tuple(class_codes),
            records=records, message=message,
        )


def _error_detail(error: Exception) -> str:
    return str(error).partition("\n")[0].strip(": ") or type(error).__name__


def _batch_hits(batches):
    return _dedupe(hit for _html, hits in batches for hit in hits)


def _verified_records(hits) -> tuple[PriorArtRecord, ...]:
    return tuple(_record_from_hit(hit) for hit in hits if _usable_hit(hit))[:6]


def _partial_result(stage, error, terms, now, class_codes=(), first_hits=()) -> PriorArtResult:
    completed = _batch_hits(getattr(error, "completed_batches", ()))
    records = _verified_records(_dedupe([*first_hits, *completed]))
    message = f"{stage}：{_error_detail(error)}"
    if not records:
        raise PriorArtSearchError(message) from error
    return PriorArtResult(
        status=PriorArtStatus.PARTIAL,
        searched_at=now,
        keywords=terms,
        ipc_codes=tuple(class_codes),
        records=records,
        message=f"{message}。已保留已完成检索得到的可核验记录。",
    )


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
