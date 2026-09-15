from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


PROMPT_VERSION = "engineering-disclosure-v2"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    resource = files("patent_summarizer.disclosure").joinpath("prompts", name)
    return resource.read_text(encoding="utf-8")


def system_prompt() -> str:
    return f"{load_prompt('common.md')}\n\n提示词版本：{PROMPT_VERSION}"

