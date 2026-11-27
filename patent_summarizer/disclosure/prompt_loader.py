from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


PROMPT_VERSION = "engineering-disclosure-v4"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    resource = files("patent_summarizer.disclosure").joinpath("prompts", name)
    return resource.read_text(encoding="utf-8")


def system_prompt() -> str:
    return f"{load_prompt('common.md')}\n\n提示词版本：{PROMPT_VERSION}"


def polish_system_prompt() -> str:
    upstream = load_prompt("humanizer-zh/SKILL.md")
    # Strip the Agent Skill manifest while retaining the complete rules and examples.
    _, separator, rules = upstream.removeprefix("---\n").partition("\n---\n")
    if not upstream.startswith("---\n") or not separator or not rules.lstrip().startswith("# Humanizer-zh\n"):
        raise ValueError("内置 Humanizer-zh 规则文件格式无效")
    return f"{load_prompt('polish.md').rstrip()}\n\n{rules.lstrip()}"

