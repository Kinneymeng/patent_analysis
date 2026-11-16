from __future__ import annotations

import io
import json
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile, ZipFile

from docx.opc.oxml import serialize_part_xml
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml import etree

from .disclosure.prompt_loader import polish_system_prompt
from .disclosure.validation import SECTION_TITLES, mapping_payload
from .progress import ProgressTracker


DOCUMENT_PART = "word/document.xml"
MAX_BATCH_CHARS = 3600
MAX_BATCH_PARAGRAPHS = 12
TECHNICAL_TOKENS = re.compile(
    r"(?:[<>≤≥≦≧≈=+＋−-]\s*)?[\d]+(?:[.．,，](?=\d)\d+)*"
    r"|[零〇一二两三四五六七八九十百千万亿]+(?=毫秒|微秒|秒|次|步|倍|个|路|级|组|点|位|项|通道|百分之)"
    r"|(?:图|表|式|步骤|第)\s*[零〇一二两三四五六七八九十百千万亿]+"
    r"|[A-Za-z_][A-Za-z0-9_]*(?:[./-][A-Za-z0-9_]+)*|[\u0370-\u03ff]"
    r"|百分之|万分之|摄氏度|华氏度|毫秒|微秒|纳秒|分钟|毫伏|千伏|毫安|千安"
    r"|微米|纳米|毫米|厘米|千米|千克|千帕|兆帕|千瓦|兆瓦|赫兹|兆赫|千赫|转每分"
    r"|牛顿(?:米|·米)?|[秒米伏安瓦帕欧](?=[/²³，。、；）\s]|$)|[%％‰℃℉°μµΩ·×÷<>≤≥≦≧≈±=]"
)
LITERAL_LINK_OR_CODE = re.compile(r"https?://|ftp://|www\.|`|\]\(|[A-Za-z]:\\|\\\\", re.I)
INVALID_PARAGRAPH_TEXT = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029\ud800-\udfff\ufffe\uffff]")


class PolishClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str: ...


class PolishError(ValueError):
    """A polishing failure with a message suitable for the user interface."""


@dataclass(frozen=True)
class PolishResult:
    output: Path
    changed: int
    rejected: int
    eligible: int


@dataclass(frozen=True)
class EditableParagraph:
    number: int
    node: etree._Element

    @property
    def text(self) -> str:
        return self.node.text or ""


def polish_disclosure_docx(
    source: Path, client: PolishClient, tracker: ProgressTracker
) -> PolishResult:
    source = source.absolute()
    if source.suffix.lower() != ".docx" or not source.is_file():
        raise PolishError("请选择一份存在的技术交底书 DOCX。")
    snapshot = source.read_bytes()
    try:
        archive = ZipFile(io.BytesIO(snapshot))
    except BadZipFile as exc:
        raise PolishError("无法读取技术交底书 DOCX，请检查文件是否完整。") from exc
    with archive:
        try:
            document = parse_xml(archive.read(DOCUMENT_PART))
        except (BadZipFile, etree.XMLSyntaxError, KeyError) as exc:
            raise PolishError("无法读取技术交底书 DOCX，请检查文件是否完整。") from exc
        if document.tag != qn("w:document") or document.body is None:
            raise PolishError("DOCX 缺少有效的 Word 正文。")

        batches = list(_document_batches(document.body.p_lst))
        tracker.start(len(batches) + 1, "正在润色技术交底书")
        changed = rejected = eligible = 0
        try:
            system_prompt = polish_system_prompt() if batches else ""
        except (OSError, ValueError) as exc:
            raise PolishError("无法加载内置润色规则，请检查便携包是否完整。") from exc
        for chapter, paragraphs in batches:
            eligible += len(paragraphs)
            tracker.set_stage("正在润色技术交底书", chapter)
            edits = _request_edits(client, system_prompt, chapter, paragraphs)
            for paragraph in paragraphs:
                candidate = edits[paragraph.number]
                if not _safe_edit(paragraph.text, candidate):
                    rejected += 1
                elif candidate != paragraph.text:
                    paragraph.node.text = candidate
                    changed += 1
            tracker.advance("正在润色技术交底书", chapter)

        tracker.set_stage("正在保存润色版 DOCX")
        output = _save_copy(source, snapshot, archive, document if changed else None)
    return PolishResult(output, changed, rejected, eligible)


def _document_batches(paragraphs: list) -> Iterator[tuple[str, list[EditableParagraph]]]:
    labels = [paragraph.text.strip() for paragraph in paragraphs]
    if labels[:1] == ["研发技术交底书"]:
        chapters = [f"{number}、{title}" for number, title in SECTION_TITLES.items()]
        headings = [(index, label) for index, label in enumerate(labels) if label in chapters]
        if labels[2:3] != ["摘要"] or [label for _, label in headings] != chapters:
            raise PolishError("交底书章节不完整，请选择本工具生成的六章版 DOCX。")
        headings.insert(0, (2, "摘要"))
    elif len(labels) >= 4 and labels[0] and labels[1] == "摘要" and labels[3] == "发明内容":
        headings = [(1, "摘要"), (3, "发明内容")]
    else:
        raise PolishError("无法识别交底书结构，请选择本工具生成的技术交底书 DOCX。")

    ends = [index for index, _ in headings[1:]] + [len(paragraphs)]
    for (heading_index, chapter), end in zip(headings, ends):
        batch = []
        size = 0
        for index in range(heading_index + 1, end):
            node = _editable_text(paragraphs[index])
            if node is None:
                continue
            length = len(node.text)
            if batch and (size + length > MAX_BATCH_CHARS or len(batch) == MAX_BATCH_PARAGRAPHS):
                yield chapter, batch
                batch = []
                size = 0
            batch.append(EditableParagraph(index + 1, node))
            size += length
        if batch:
            yield chapter, batch


def _editable_text(paragraph: etree._Element) -> etree._Element | None:
    # The disclosure exporter justifies prose and gives headings and captions
    # separate formatting. Leave mixed content and formatted lists intact.
    if paragraph.xpath("./w:pPr/w:jc/@w:val") != ["both"]:
        return None
    if paragraph.xpath("./w:pPr/w:outlineLvl | ./w:pPr/w:numPr | ./w:pPr/w:pStyle"):
        return None
    if any(child.tag not in {qn("w:pPr"), qn("w:r")} for child in paragraph):
        return None
    runs = paragraph.findall(qn("w:r"))
    if len(runs) != 1 or any(child.tag not in {qn("w:rPr"), qn("w:t")} for child in runs[0]):
        return None
    nodes = runs[0].findall(qn("w:t"))
    if len(nodes) != 1 or not (nodes[0].text or "").strip():
        return None
    if LITERAL_LINK_OR_CODE.search(nodes[0].text):
        return None
    following = paragraph.getnext()
    if following is not None and following.tag == qn("w:p"):
        if following.xpath("./m:oMath | ./m:oMathPara") or following.text.startswith(
            "原始公式表达（需人工复核）："
        ):
            return None
    return nodes[0]


def _request_edits(
    client: PolishClient, system_prompt: str, chapter: str, paragraphs: list[EditableParagraph]
) -> dict[int, str]:
    request = {
        "chapter": chapter,
        "paragraphs": [{"id": item.number, "text": item.text} for item in paragraphs],
    }
    expected = {item.number for item in paragraphs}
    for attempt in range(2):
        response = client.chat(system_prompt, json.dumps(request, ensure_ascii=False))
        try:
            return _parse_edits(response, expected)
        except ValueError as exc:
            if attempt:
                raise PolishError("模型两次返回的段落编号或 JSON 格式有误，请重新运行润色。") from exc
            request["retry"] = "上次响应的结构或编号有误。请按指定 JSON 格式逐项返回所有原始编号。"
    raise AssertionError("model response retry loop did not return")


def _parse_edits(response: str, expected: set[int]) -> dict[int, str]:
    payload = mapping_payload(response)
    items = payload.get("paragraphs")
    if set(payload) != {"paragraphs"} or not isinstance(items, list):
        raise ValueError("invalid paragraph response")
    edits = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "text"}:
            raise ValueError("invalid paragraph entry")
        number, text = item["id"], item["text"]
        if type(number) is not int or number not in expected or number in edits or not isinstance(text, str):
            raise ValueError("invalid paragraph ID or text type")
        edits[number] = text
    if edits.keys() != expected:
        raise ValueError("missing paragraphs")
    return edits


def _safe_edit(original: str, candidate: str) -> bool:
    if not candidate.strip() or INVALID_PARAGRAPH_TEXT.search(candidate):
        return False
    if candidate != original and candidate != candidate.strip():
        return False
    if LITERAL_LINK_OR_CODE.search(candidate):
        return False
    return TECHNICAL_TOKENS.findall(original) == TECHNICAL_TOKENS.findall(candidate)


def _save_copy(
    source: Path, snapshot: bytes, archive: ZipFile, document: etree._Element | None
) -> Path:
    with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".polish-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            if document is None:
                handle.write(snapshot)
            else:
                with ZipFile(handle, "w") as output:
                    output.comment = archive.comment
                    for member in archive.infolist():
                        data = serialize_part_xml(document) if member.filename == DOCUMENT_PART else archive.read(member)
                        output.writestr(member, data)
            handle.close()
            serial = 1
            while True:
                suffix = f"_{serial}" if serial > 1 else ""
                target = source.with_name(f"{source.stem}_润色版{suffix}.docx")
                try:
                    if os.name == "nt":
                        temporary.rename(target)
                    else:
                        os.link(temporary, target)
                except FileExistsError:
                    serial += 1
                else:
                    return target
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)
