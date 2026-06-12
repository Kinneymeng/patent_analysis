from __future__ import annotations

import re


SUMMARY_SECTION_NAMES = [
    "著录信息",
    "专利主题",
    "技术领域",
    "背景问题",
    "核心方案",
    "关键创新点",
    "权利要求要点",
    "应用场景",
    "潜在分类关键词",
    "摘要依据与不确定点",
]


def parse_summary_sections(markdown: str) -> dict[str, str]:
    text = markdown.strip()
    sections = {name: "" for name in SUMMARY_SECTION_NAMES}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        if name not in sections:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    sections["摘要全文"] = text
    return sections
