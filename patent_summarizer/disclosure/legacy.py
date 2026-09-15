from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import (
    ConflictPolicy,
    DisclosureChatClient,
    DisclosureFailure,
    DisclosureGenerationResult,
    DisclosureSourceItem,
    DisclosureTitleReviewCallback,
    DisclosureTitleReviewResult,
    TechnicalDisclosure,
    TitleProposal,
    TitleSelection,
)


TITLE_COUNT = 3
DISCLOSURE_VALIDATION_RETRIES = 1
MIN_ABSTRACT_CHARS = 150
MAX_ABSTRACT_CHARS = 800
MIN_CONTENT_PARAGRAPHS = 6
MAX_TITLE_CHARS = 120
FORBIDDEN_TITLE_CHARS = re.compile(r'[<>:"/\\|?*\r\n]')
FORBIDDEN_CLAIM_PHRASES = (
    "其特征在于",
    "根据权利要求",
    "独立权利要求",
    "从属权利要求",
)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(1, 10)},
    *{f"LPT{index}" for index in range(1, 10)},
}
ParsedValue = TypeVar("ParsedValue")


TITLE_SYSTEM_PROMPT = """你是中国发明专利技术交底书的技术方案策划助手。你可以使用通用工程知识帮助提出技术实现方向，但不得虚构企业已有技术、专利、实验数据、市场事实或竞争事实。你的任务不是撰写权利要求书，不得使用权利要求式法律表达。"""


TITLE_USER_PROMPT = """请基于以下技术来源生成 3 个彼此技术切入点不同的发明专利技术交底书标题。

来源编号：{source_id}
来源名称：{label}
来源摘要：{summary}
技术上下文：
{context}

要求：
- 只输出 JSON，不要解释，不要使用 Markdown 代码块。
- 必须恰好输出 3 个标题，标题不得重复或只是同义改写。
- 每个标题应对应一份能够独立说明实现方式的技术方案。
- 标题简洁、具体，不含“技术交底书”后缀，不含编号，不含 Windows 文件名非法字符。
- 不得把拟议方案表述为任何企业已经采用的技术。
- JSON 格式：{{"标题": ["...", "...", "..."]}}
"""


DRAFT_USER_PROMPT = """请基于给定来源和已经由用户确认的标题，撰写一份发明专利技术交底书初稿。

确认标题：{title}
来源编号：{source_id}
来源名称：{label}
来源摘要：{summary}
技术上下文：
{context}

写作要求：
- 只输出 JSON，不要解释，不要使用 Markdown 代码块。
- “标题”必须与确认标题逐字一致。
- “摘要”为 300 至 500 个中文字符的单一自然段，不得换行。
- “发明内容”为 JSON 字符串数组，使用足以完整说明输入、处理、判断、输出、异常和边界条件的自然段，不设固定字数上限，且不得在一个数组项中再使用换行拆段。
- 正文采用类似论文的连续技术叙述，只负责把技术实现方式讲清楚，不设置“技术领域”“技术问题”“实施方式”等内部小标题，不使用编号分节。
- 自然说明技术问题、总体思路、系统组成或功能模块、部件关系、工作机理、实现流程、异常或边界处理、可选实施方式以及由技术手段直接带来的效果。
- 不得写保护范围、权利要求拆分建议，不得出现“其特征在于”“根据权利要求”“独立权利要求”“从属权利要求”等表达。
- 可以提出新的实现结构和流程，但不得虚构企业已经采用该技术、已有专利覆盖该技术或未经来源支持的量化性能、实验和测试结果。
- JSON 格式：{{"标题": "{title}", "摘要": "...", "发明内容": ["...", "...", "..."]}}
"""


REVIEW_USER_PROMPT = """请作为独立技术审校人员，对以下技术交底书初稿进行审校并直接给出修订稿。

确认标题：{title}
来源编号：{source_id}
来源名称：{label}
来源摘要：{summary}
技术上下文：
{context}

待审校初稿：
{draft}

审校要求：
- 只输出最终修订稿 JSON，不输出审校意见、解释或 Markdown 代码块。
- 标题必须与确认标题逐字一致。
- 检查技术问题、组成关系、机理、流程、边界处理、可选实现和技术效果能否形成自洽且可执行的技术说明；有缺失时直接补入正文。
- 保持论文式连续自然段，不增加内部小标题、编号分节、保护范围或权利要求拆分建议。
- 摘要必须是 150 至 800 个非空白字符的单段文字。
- 发明内容至少 6 个自然段，并以工程师能够理解和复现方案为完整性标准，不设固定字数上限。
- 删除或改写“其特征在于”“根据权利要求”“独立权利要求”“从属权利要求”等权利要求式表达。
- 不得虚构企业已有技术、专利事实、实验结果或量化性能；拟议实现应使用技术方案或可选实施方式的语气。
- JSON 格式：{{"标题": "{title}", "摘要": "...", "发明内容": ["...", "...", "..."]}}
"""


def load_report_sources(report_path: Path) -> list[DisclosureSourceItem]:
    report_path = Path(report_path)
    if not report_path.exists():
        raise FileNotFoundError(f"分析报告不存在：{report_path}")
    if report_path.suffix.lower() != ".docx":
        raise ValueError("分析报告必须使用 .docx 扩展名")

    document = Document(report_path)
    chapter = ""
    subheading = ""
    common_context: list[str] = []
    layout_points: list[tuple[str, str, dict[str, str]]] = []
    portfolio_plans: dict[str, tuple[str, dict[str, str]]] = {}

    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            detected = _chapter_marker(text)
            if detected:
                chapter = detected
                subheading = ""
            elif chapter == "七":
                matched = re.match(r"^(7\.\d+)\s+(.+)$", text)
                if matched:
                    subheading = f"{matched.group(1)}\t{matched.group(2).strip()}"
            elif chapter == "八":
                matched = re.match(r"^(8\.\d+)\s+(.+?)组合构建\s*$", text)
                if matched:
                    subheading = f"{matched.group(1)}\t{matched.group(2).strip()}"
            if chapter in {"五", "六"} and text:
                common_context.append(text)
            continue

        if not isinstance(block, Table):
            continue
        if chapter in {"五", "六"}:
            common_context.append(_table_text(block))
            continue
        if chapter == "七":
            source_id, heading_name = _split_subheading(subheading, "第七章建议表缺少 7.x 标题")
            fields = _two_column_table(block, "固定字段", "分析内容", f"{source_id} 建议表")
            name = _required_field(fields, "建议布局点名称", f"{source_id} 建议表")
            if heading_name and heading_name != name:
                raise ValueError(f"{source_id} 标题与建议布局点名称不一致")
            _validate_layout_fields(fields, source_id)
            layout_points.append((source_id, name, fields))
            subheading = ""
        elif chapter == "八":
            source_id, plan_name = _split_subheading(subheading, "第八章组合表缺少 8.x 标题")
            fields = _two_column_table(block, "组合维度", "建议内容", f"{source_id} 组合表")
            _validate_plan_fields(fields, source_id)
            portfolio_plans[_name_key(plan_name)] = (source_id, fields)
            subheading = ""

    if not layout_points:
        raise ValueError("分析报告第七章未找到可用的待布局建议")

    common = "\n".join(item for item in common_context if item).strip()[:12000]
    sources: list[DisclosureSourceItem] = []
    for source_id, name, fields in layout_points:
        plan = portfolio_plans.get(_name_key(name))
        if plan is None:
            raise ValueError(f"{source_id}“{name}”未匹配到第八章组合构建策略")
        plan_id, plan_fields = plan
        gap_field = next(key for key in fields if "现有布局及缺口" in key)
        summary = f"{fields['主要技术问题']}；{fields[gap_field]}"
        context = "\n".join(
            [
                "【第七章待布局建议】",
                _mapping_text(fields),
                f"【第八章组合策略（{plan_id}）】",
                _mapping_text(plan_fields),
                "【第五、六章相关报告上下文】",
                common,
            ]
        ).strip()
        sources.append(
            DisclosureSourceItem(
                source_id=source_id,
                label=name,
                summary=summary[:500],
                context=context,
            )
        )
    return sources


def load_idea_source(idea: str) -> list[DisclosureSourceItem]:
    text = str(idea or "").strip()
    if not text:
        raise ValueError("直接思路不能为空")
    return [
        DisclosureSourceItem(
            source_id="manual",
            label="用户直接思路",
            summary=text[:500],
            context=text,
        )
    ]


def load_title_selections(path: Path) -> list[TitleSelection]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"标题文件不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"标题文件不是有效 JSON：{exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("标题文件顶层必须是数组")
    selections: list[TitleSelection] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"标题文件第 {index} 项必须是对象")
        if set(item) != {"source_id", "titles"}:
            raise ValueError(f"标题文件第 {index} 项必须且只能包含 source_id 和 titles")
        if not isinstance(item["source_id"], str) or not item["source_id"].strip():
            raise ValueError(f"标题文件第 {index} 项 source_id 必须是非空字符串")
        if not isinstance(item["titles"], list):
            raise ValueError(f"标题文件第 {index} 项 titles 必须是数组")
        selections.append(TitleSelection(item["source_id"].strip(), list(item["titles"])))
    return selections


def selections_from_proposals(proposals: list[TitleProposal]) -> list[TitleSelection]:
    return [TitleSelection(item.source.source_id, list(item.titles)) for item in proposals]


def validate_title_selections(
    selections: list[TitleSelection],
    sources: list[DisclosureSourceItem],
) -> list[TitleSelection]:
    source_ids = {item.source_id for item in sources}
    seen_source_ids: set[str] = set()
    seen_titles: set[str] = set()
    result: list[TitleSelection] = []
    for selection in selections:
        if selection.source_id not in source_ids:
            raise ValueError(f"未知来源编号：{selection.source_id}")
        if selection.source_id in seen_source_ids:
            raise ValueError(f"来源编号重复：{selection.source_id}")
        seen_source_ids.add(selection.source_id)
        titles: list[str] = []
        for raw_title in selection.titles:
            if not isinstance(raw_title, str):
                raise ValueError(f"{selection.source_id} 的标题必须是字符串")
            validate_disclosure_title(raw_title)
            title_key = raw_title.casefold()
            if title_key in seen_titles:
                raise ValueError(f"批次内标题重复：{raw_title}")
            seen_titles.add(title_key)
            titles.append(raw_title)
        result.append(TitleSelection(selection.source_id, titles))
    return result


def validate_disclosure_title(title: str) -> None:
    if not title or title != title.strip():
        raise ValueError("标题不能为空，且首尾不能包含空格")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"标题不得超过 {MAX_TITLE_CHARS} 个字符：{title}")
    if FORBIDDEN_TITLE_CHARS.search(title):
        raise ValueError(f"标题包含 Windows 文件名非法字符：{title}")
    if title.endswith((".", " ")):
        raise ValueError(f"标题不能以句点或空格结尾：{title}")
    if title.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"标题不能使用 Windows 保留名称：{title}")
    if title.endswith("技术交底书"):
        raise ValueError(f"标题不应包含“技术交底书”后缀：{title}")


def disclosure_output_path(output_dir: Path, title: str) -> Path:
    validate_disclosure_title(title)
    return Path(output_dir) / f"{title}_技术交底书.docx"


def selected_work_items(
    selections: list[TitleSelection],
    sources: list[DisclosureSourceItem],
) -> list[tuple[DisclosureSourceItem, str]]:
    validated = validate_title_selections(selections, sources)
    source_map = {item.source_id: item for item in sources}
    return [
        (source_map[selection.source_id], title)
        for selection in validated
        for title in selection.titles
    ]


class DisclosureAnalyzer:
    def __init__(
        self,
        client: DisclosureChatClient,
        validation_retries: int = DISCLOSURE_VALIDATION_RETRIES,
        on_validation_retry: Callable[[str, int, str], None] | None = None,
        on_validation_warning: Callable[[str, str], None] | None = None,
    ):
        if validation_retries < 0:
            raise ValueError("validation_retries must not be negative")
        self.client = client
        self.validation_retries = validation_retries
        self.on_validation_retry = on_validation_retry
        self.on_validation_warning = on_validation_warning

    def propose_titles(self, source: DisclosureSourceItem) -> TitleProposal:
        prompt = TITLE_USER_PROMPT.format(
            source_id=source.source_id,
            label=source.label,
            summary=source.summary,
            context=source.context,
        )
        titles = self._validated_chat(
            f"{source.source_id} 候选标题",
            TITLE_SYSTEM_PROMPT,
            prompt,
            parse_title_proposal,
        )
        return TitleProposal(source, titles)

    def draft(self, source: DisclosureSourceItem, title: str) -> TechnicalDisclosure:
        validate_disclosure_title(title)
        prompt = DRAFT_USER_PROMPT.format(
            title=title,
            source_id=source.source_id,
            label=source.label,
            summary=source.summary,
            context=source.context,
        )
        return self._validated_chat(
            f"{title} 初稿",
            TITLE_SYSTEM_PROMPT,
            prompt,
            lambda response: parse_technical_disclosure(response, title),
        )

    def review(
        self,
        source: DisclosureSourceItem,
        draft: TechnicalDisclosure,
    ) -> TechnicalDisclosure:
        prompt = REVIEW_USER_PROMPT.format(
            title=draft.title,
            source_id=source.source_id,
            label=source.label,
            summary=source.summary,
            context=source.context,
            draft=json.dumps(
                {
                    "标题": draft.title,
                    "摘要": draft.abstract,
                    "发明内容": draft.content_paragraphs,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return self._validated_chat(
            f"{draft.title} 独立审校",
            TITLE_SYSTEM_PROMPT,
            prompt,
            lambda response: parse_technical_disclosure(response, draft.title),
        )

    def _validated_chat(
        self,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        parser: Callable[[str | Mapping[str, Any]], ParsedValue],
    ) -> ParsedValue:
        prompt = user_prompt
        for attempt in range(self.validation_retries + 1):
            response = self.client.chat(system_prompt, prompt)
            try:
                parsed = parser(response)
                if attempt > 0 and hasattr(self.client, "store_validated"):
                    self.client.store_validated(system_prompt, user_prompt, response)
                return parsed
            except (TypeError, ValueError) as exc:
                if hasattr(self.client, "invalidate"):
                    self.client.invalidate(system_prompt, prompt, str(exc), response)
                if attempt >= self.validation_retries:
                    raise ValueError(f"{stage}结构化响应校验失败：{exc}") from exc
                if self.on_validation_retry is not None:
                    self.on_validation_retry(stage, attempt + 1, str(exc))
                prompt = _repair_prompt(user_prompt, str(exc))
        raise AssertionError("unreachable")


def parse_title_proposal(response: str | Mapping[str, Any]) -> list[str]:
    payload = _mapping_payload(response)
    if set(payload) != {"标题"}:
        raise ValueError("候选标题响应必须且只能包含“标题”字段")
    titles = payload["标题"]
    if not isinstance(titles, list) or len(titles) != TITLE_COUNT:
        raise ValueError(f"候选标题必须严格包含 {TITLE_COUNT} 项")
    if any(not isinstance(item, str) for item in titles):
        raise ValueError("候选标题中的每一项必须是字符串")
    result = list(titles)
    for title in result:
        validate_disclosure_title(title)
    if len({item.casefold() for item in result}) != len(result):
        raise ValueError("候选标题不得重复")
    return result


def parse_technical_disclosure(
    response: str | Mapping[str, Any],
    expected_title: str,
) -> TechnicalDisclosure:
    payload = _mapping_payload(response)
    required = {"标题", "摘要", "发明内容"}
    if set(payload) != required:
        raise ValueError("技术交底书响应必须且只能包含标题、摘要、发明内容")
    title = payload["标题"]
    abstract = payload["摘要"]
    content = payload["发明内容"]
    if not isinstance(title, str) or title != expected_title:
        raise ValueError("模型返回标题必须与用户确认标题逐字一致")
    if not isinstance(abstract, str) or not abstract.strip():
        raise ValueError("摘要必须是非空字符串")
    if abstract != abstract.strip() or "\n" in abstract or "\r" in abstract:
        raise ValueError("摘要必须是没有换行的单一自然段")
    abstract_chars = _non_whitespace_chars(abstract)
    if not MIN_ABSTRACT_CHARS <= abstract_chars <= MAX_ABSTRACT_CHARS:
        raise ValueError(
            f"摘要非空白字符数必须为 {MIN_ABSTRACT_CHARS} 至 {MAX_ABSTRACT_CHARS}，实际为 {abstract_chars}"
        )
    if not isinstance(content, list) or len(content) < MIN_CONTENT_PARAGRAPHS:
        raise ValueError(f"发明内容必须是至少包含 {MIN_CONTENT_PARAGRAPHS} 个自然段的数组")
    if any(not isinstance(item, str) or not item.strip() for item in content):
        raise ValueError("发明内容中的每一段必须是非空字符串")
    paragraphs = list(content)
    if any(item != item.strip() or "\n" in item or "\r" in item for item in paragraphs):
        raise ValueError("发明内容的每个数组项必须对应一个无内嵌换行的自然段")
    full_text = "\n".join([title, abstract, *paragraphs])
    for phrase in FORBIDDEN_CLAIM_PHRASES:
        if phrase in full_text:
            raise ValueError(f"技术交底书不得包含权利要求式表达：{phrase}")
    disclosure = TechnicalDisclosure(title, abstract, paragraphs)
    return disclosure


def _mapping_payload(response: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    if not isinstance(response, str):
        raise ValueError("模型响应必须是 JSON 对象或 JSON 文本")
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型响应不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("模型响应 JSON 顶层必须是对象")
    return payload


def _repair_prompt(original_prompt: str, error: str) -> str:
    return f"""{original_prompt}

上一次响应未通过程序校验，错误为：{error}
请重新完整输出符合原要求的 JSON。不要解释错误，不要使用 Markdown 代码块。
"""


def _chapter_marker(text: str) -> str:
    matched = re.match(r"^([五六七八])、", text)
    return matched.group(1) if matched else ""


def _split_subheading(value: str, error: str) -> tuple[str, str]:
    if "\t" not in value:
        raise ValueError(error)
    source_id, name = value.split("\t", 1)
    return source_id, name


def _two_column_table(
    table: Table,
    first_header: str,
    second_header: str,
    context: str,
) -> dict[str, str]:
    if not table.rows or len(table.rows[0].cells) < 2:
        raise ValueError(f"{context} 必须是两列表格")
    headers = [cell.text.strip() for cell in table.rows[0].cells[:2]]
    if headers != [first_header, second_header]:
        raise ValueError(
            f"{context} 表头必须为“{first_header} / {second_header}”，实际为“{' / '.join(headers)}”"
        )
    result: dict[str, str] = {}
    for row in table.rows[1:]:
        if len(row.cells) < 2:
            continue
        key = row.cells[0].text.strip()
        value = row.cells[1].text.strip()
        if not key or not value:
            raise ValueError(f"{context} 包含空字段或空内容")
        if key in result:
            raise ValueError(f"{context} 字段重复：{key}")
        result[key] = value
    return result


def _required_field(fields: Mapping[str, str], name: str, context: str) -> str:
    value = fields.get(name, "").strip()
    if not value:
        raise ValueError(f"{context} 缺少字段：{name}")
    return value


def _validate_layout_fields(fields: Mapping[str, str], source_id: str) -> None:
    required = {
        "建议布局点名称",
        "所属技术分类",
        "主要技术问题",
        "竞争对手布局",
        "建议优先级",
        "建议理由",
        "证据专利",
    }
    missing = sorted(required - set(fields))
    if missing:
        raise ValueError(f"{source_id} 建议表缺少字段：{'、'.join(missing)}")
    if not any("现有布局及缺口" in key for key in fields):
        raise ValueError(f"{source_id} 建议表缺少目标企业现有布局及缺口字段")


def _validate_plan_fields(fields: Mapping[str, str], source_id: str) -> None:
    required = {
        "核心专利",
        "外围专利分支",
        "技术环节覆盖",
        "专利客体协同",
        "系列化或补充布局",
        "跨布局点协同",
        "证据依据",
    }
    missing = sorted(required - set(fields))
    if missing:
        raise ValueError(f"{source_id} 组合表缺少字段：{'、'.join(missing)}")


def _table_text(table: Table) -> str:
    return "\n".join(
        " | ".join(cell.text.strip() for cell in row.cells)
        for row in table.rows
        if any(cell.text.strip() for cell in row.cells)
    )


def _mapping_text(fields: Mapping[str, str]) -> str:
    return "\n".join(f"{key}：{value}" for key, value in fields.items())


def _name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _non_whitespace_chars(value: str) -> int:
    return len(re.sub(r"\s+", "", value))
