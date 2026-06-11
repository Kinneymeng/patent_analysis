from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .taxonomy import Taxonomy, TaxonomyItem


CLASSIFICATION_SYSTEM_PROMPT = """你是中国专利分类助手。你只能使用用户给定的分类目录，不得创造新分类。"""

CLASSIFICATION_PROMPT = """请将以下专利摘要划分到给定的二级分类中。

文件名：{filename}

分类目录：
{taxonomy}

要求：
- 可以给出多个候选二级分类。
- confidence 使用 0 到 1 的数字。
- 只输出 JSON，不要输出解释性文字。
- JSON 格式：
{{
  "classifications": [
    {{
      "一级分类": "...",
      "二级分类": "...",
      "confidence": 0.0,
      "reason": "..."
    }}
  ]
}}

专利摘要：
{summary}
"""


class ChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class ClassificationCandidate:
    first_level: str
    second_level: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class ClassificationResult:
    candidates: list[ClassificationCandidate]
    primary_first_level: str
    primary_second_level: str
    primary_confidence: float | None
    candidate_summary: str
    review_status: str
    reason: str


def classify_summary(
    client: ChatClient,
    filename: str,
    summary: str,
    taxonomy: Taxonomy,
    max_candidates: int = 3,
) -> ClassificationResult:
    prompt = CLASSIFICATION_PROMPT.format(
        filename=filename,
        taxonomy=format_taxonomy(taxonomy.items),
        summary=summary,
    )
    return parse_classification_response(
        client.chat(CLASSIFICATION_SYSTEM_PROMPT, prompt),
        taxonomy.items,
        max_candidates=max_candidates,
    )


def parse_classification_response(
    response: str | dict[str, Any],
    taxonomy: Iterable[TaxonomyItem],
    max_candidates: int = 3,
    minimum_primary_confidence: float = 0.45,
    review_confidence: float = 0.75,
) -> ClassificationResult:
    payload = response if isinstance(response, dict) else json.loads(_extract_json(response))
    raw_items = payload.get("classifications")
    if not isinstance(raw_items, list):
        raise ValueError("Classification response must contain a classifications list")

    allowed = {(item.first_level, item.second_level) for item in taxonomy}
    candidates: list[ClassificationCandidate] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        first = str(raw.get("一级分类", "")).strip()
        second = str(raw.get("二级分类", "")).strip()
        if (first, second) not in allowed:
            continue
        confidence = _confidence(raw.get("confidence"))
        reason = str(raw.get("reason", "")).strip()
        candidates.append(ClassificationCandidate(first, second, confidence, reason))

    candidates.sort(key=lambda item: item.confidence, reverse=True)
    candidates = candidates[:max_candidates]
    best = candidates[0] if candidates else None
    has_primary = best is not None and best.confidence >= minimum_primary_confidence
    primary_confidence = best.confidence if has_primary else None
    review_status = "通过" if best is not None and best.confidence >= review_confidence else "需人工复核"

    return ClassificationResult(
        candidates=candidates,
        primary_first_level=best.first_level if has_primary and best is not None else "",
        primary_second_level=best.second_level if has_primary and best is not None else "",
        primary_confidence=primary_confidence,
        candidate_summary=_candidate_summary(candidates),
        review_status=review_status,
        reason="；".join(candidate.reason for candidate in candidates if candidate.reason),
    )


def format_taxonomy(items: Iterable[TaxonomyItem]) -> str:
    return "\n".join(f"- {item.first_level} / {item.second_level}" for item in items)


def _extract_json(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Classification response is not JSON: {text}")
    return stripped[start : end + 1]


def _confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, confidence))


def _candidate_summary(candidates: Iterable[ClassificationCandidate]) -> str:
    return "\n".join(
        f"{candidate.first_level} / {candidate.second_level} ({candidate.confidence:.2f})"
        for candidate in candidates
    )
