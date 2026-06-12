from __future__ import annotations

import re
from datetime import date
from typing import Mapping


BIBLIOGRAPHIC_FIELD_NAMES = [
    "申请号",
    "申请日",
    "公开（公告）号",
    "公开（公告）日",
    "申请人",
    "发明人",
    "IPC/CPC",
]


_FIELD_ALIASES = {
    "申请号": "申请号",
    "专利申请号": "申请号",
    "申请日": "申请日",
    "申请日期": "申请日",
    "公开公告号": "公开（公告）号",
    "公开（公告）号": "公开（公告）号",
    "公开号": "公开（公告）号",
    "公告号": "公开（公告）号",
    "公开公告日": "公开（公告）日",
    "公开（公告）日": "公开（公告）日",
    "公开日": "公开（公告）日",
    "公告日": "公开（公告）日",
    "申请人": "申请人",
    "专利权人": "申请人",
    "发明人": "发明人",
    "发明人设计人": "发明人",
    "IPC": "IPC/CPC",
    "CPC": "IPC/CPC",
    "IPC/CPC": "IPC/CPC",
    "IPCCPC": "IPC/CPC",
    "国际专利分类": "IPC/CPC",
}


def parse_bibliographic_metadata(section_text: str) -> dict[str, str]:
    metadata = {name: "" for name in BIBLIOGRAPHIC_FIELD_NAMES}
    if not section_text.strip():
        return metadata

    for raw_line in section_text.splitlines():
        label, value = _split_metadata_line(raw_line)
        if not label:
            continue
        field = _FIELD_ALIASES.get(_normalize_label(label))
        if not field:
            continue
        cleaned = _clean_value(value)
        if cleaned:
            metadata[field] = cleaned
    return metadata


def normalize_patent_date(value: str) -> str:
    text = _clean_value(value)
    if not text or text == "未提及":
        return ""

    year_only = re.fullmatch(r"(19|20)\d{2}", text)
    if year_only:
        return text

    compact = re.fullmatch(r"((?:19|20)\d{2})(\d{2})(\d{2})", text)
    if compact:
        return _valid_date(compact.group(1), compact.group(2), compact.group(3))

    match = re.search(r"((?:19|20)\d{2})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", text)
    if match:
        return _valid_date(match.group(1), match.group(2), match.group(3))

    return ""


def infer_patent_year(metadata: Mapping[str, str], filename: str = "") -> str:
    for field in ("申请日", "公开（公告）日"):
        year = _year_from_date(metadata.get(field, ""))
        if year:
            return year

    for field in ("申请号", "公开（公告）号"):
        year = _year_from_identifier(metadata.get(field, ""))
        if year:
            return year

    year = _year_from_identifier(filename)
    return year or "未知"


def _split_metadata_line(raw_line: str) -> tuple[str, str]:
    line = raw_line.strip()
    if not line:
        return "", ""
    line = re.sub(r"^[\-\*\u2022]\s*", "", line)
    line = re.sub(r"^\d+[\.、]\s*", "", line)
    line = line.replace("**", "").strip()
    if "：" in line:
        label, value = line.split("：", 1)
        return label.strip(), value.strip()
    if ":" in line:
        label, value = line.split(":", 1)
        return label.strip(), value.strip()
    return "", ""


def _normalize_label(label: str) -> str:
    return re.sub(r"[\s:：/（）()、，,]+", "", label).upper().replace("IPCCPC", "IPCCPC")


def _clean_value(value: str) -> str:
    return value.strip().strip("-").strip()


def _valid_date(year: str, month: str, day: str) -> str:
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError:
        return ""
    return parsed.isoformat()


def _year_from_date(value: str) -> str:
    normalized = normalize_patent_date(value)
    if not normalized:
        return ""
    return normalized[:4]


def _year_from_identifier(value: str) -> str:
    text = _clean_value(value)
    if not text:
        return ""
    match = re.search(r"(?:CN)?((?:19|20)\d{2})\d{4,}", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return match.group(1) if match else ""
