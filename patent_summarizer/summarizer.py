from __future__ import annotations

from .llm import RateLimitedOpenAIClient
from .prompts import FINAL_SUMMARY_PROMPT, PARTIAL_SUMMARY_PROMPT, SYSTEM_PROMPT

# 分块的大小，默认12000字符，约合4000-5000字。
def split_text(text: str, max_chars: int = 65536) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    paragraphs = [para.strip() for para in normalized.split("\n\n") if para.strip()]
    for para in paragraphs:
        para_len = len(para)
        if para_len > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            chunks.extend(_hard_split(para, max_chars))
            continue
        extra = para_len + (2 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks


class PatentSummarizer:
    def __init__(self, client: RateLimitedOpenAIClient, max_chars: int = 12000):
        self.client = client
        self.max_chars = max_chars

    def summarize_document(self, filename: str, text: str) -> str:
        chunks = split_text(text, self.max_chars)
        if not chunks:
            raise ValueError("Cannot summarize empty text")
        if len(chunks) == 1:
            return self.client.chat(
                SYSTEM_PROMPT,
                FINAL_SUMMARY_PROMPT.format(filename=filename, text=chunks[0]),
            )

        partials = []
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            partials.append(
                self.client.chat(
                    SYSTEM_PROMPT,
                    PARTIAL_SUMMARY_PROMPT.format(filename=filename, index=index, total=total, text=chunk),
                )
            )
        merged = "\n\n".join(partials)
        return self.client.chat(
            SYSTEM_PROMPT,
            FINAL_SUMMARY_PROMPT.format(filename=filename, text=merged),
        )


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[start : start + max_chars] for start in range(0, len(text), max_chars)]
