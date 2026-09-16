from __future__ import annotations

import os
import uuid
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from .disclosure.math_to_omml import latex_to_omml_with_fallback
from .disclosure.models import TechnicalDisclosure


DISCLOSURE_FONT = "宋体"
DISCLOSURE_FONT_SIZE = Pt(14)
TABLE_FONT_SIZE = Pt(10.5)
LINE_SPACING_VALUE = "360"
BODY_OUTLINE_LEVEL = "9"
SECTION_OUTLINE_LEVEL = "2"
SUBSECTION_OUTLINE_LEVEL = "3"
FIRST_LINE_CHARS = "200"


def write_technical_disclosure(
    disclosure: TechnicalDisclosure,
    output: Path,
    overwrite: bool = False,
) -> Path:
    output = Path(output)
    if output.suffix.lower() != ".docx":
        raise ValueError("技术交底书输出必须使用 .docx 扩展名")
    if output.exists() and not overwrite:
        raise FileExistsError(f"技术交底书已经存在：{output}")
    document = build_technical_disclosure_document(disclosure)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.docx")
    try:
        document.save(temporary)
        validate_technical_disclosure_docx(temporary, disclosure)
        if output.exists() and not overwrite:
            raise FileExistsError(f"技术交底书已经存在：{output}")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def build_technical_disclosure_document(disclosure: TechnicalDisclosure) -> DocumentType:
    document = Document()
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(25.4)
    section.right_margin = Mm(25.4)
    section.bottom_margin = Mm(25.4)
    section.left_margin = Mm(25.4)
    normal = document.styles["Normal"]
    normal.font.name = DISCLOSURE_FONT
    normal.font.size = DISCLOSURE_FONT_SIZE
    _set_rfonts(normal.element.get_or_add_rPr())
    if disclosure.is_structured:
        _add_structured_disclosure(document, disclosure)
    else:
        _add_legacy_disclosure(document, disclosure)
    return document


_build_technical_disclosure = build_technical_disclosure_document


def _add_legacy_disclosure(document: DocumentType, disclosure: TechnicalDisclosure) -> None:
    _add_paragraph(document, disclosure.title, alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True, outline_level=BODY_OUTLINE_LEVEL)
    _add_paragraph(document, "摘要", alignment=WD_ALIGN_PARAGRAPH.LEFT, bold=True, outline_level=SECTION_OUTLINE_LEVEL)
    _add_paragraph(document, disclosure.abstract, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY)
    _add_paragraph(document, "发明内容", alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True, outline_level=SECTION_OUTLINE_LEVEL)
    for text in disclosure.content_paragraphs:
        _add_paragraph(document, text, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_chars=FIRST_LINE_CHARS)


def _add_structured_disclosure(document: DocumentType, disclosure: TechnicalDisclosure) -> None:
    _add_paragraph(document, "研发技术交底书", alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True, outline_level=BODY_OUTLINE_LEVEL, font_size=Pt(18))
    _add_paragraph(document, disclosure.title, alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True, font_size=Pt(16))
    _add_paragraph(document, "摘要", alignment=WD_ALIGN_PARAGRAPH.LEFT, bold=True, outline_level=SECTION_OUTLINE_LEVEL)
    _add_paragraph(document, disclosure.abstract, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_chars=FIRST_LINE_CHARS)
    for section in disclosure.sections:
        _add_paragraph(document, f"{section.number}、{section.title}", alignment=WD_ALIGN_PARAGRAPH.LEFT, bold=True, outline_level=SECTION_OUTLINE_LEVEL, font_size=Pt(16))
        for text in section.paragraphs:
            _add_paragraph(document, text, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_chars=FIRST_LINE_CHARS)
        for subsection in section.subsections:
            _add_paragraph(document, f"{subsection.number} {subsection.title}", alignment=WD_ALIGN_PARAGRAPH.LEFT, bold=True, outline_level=SUBSECTION_OUTLINE_LEVEL)
            for text in subsection.paragraphs:
                _add_paragraph(document, text, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_chars=FIRST_LINE_CHARS)
        for diagram in (item for item in disclosure.diagrams if item.section_number == section.number):
            if diagram.image_path is None or not diagram.image_path.is_file():
                raise ValueError(f"图示尚未成功生成：{diagram.title}")
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.add_run().add_picture(str(diagram.image_path), width=Mm(150))
            _set_paragraph_spacing(paragraph)
            _add_paragraph(document, diagram.title, alignment=WD_ALIGN_PARAGRAPH.CENTER)
        for formula in (item for item in disclosure.formulas if item.section_number == section.number):
            _add_paragraph(document, formula.description, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, first_line_chars=FIRST_LINE_CHARS)
            rendered_formula = latex_to_omml_with_fallback(formula.latex, display=True)
            if rendered_formula.element is not None:
                paragraph = document.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph._element.append(rendered_formula.element)
                _set_paragraph_spacing(paragraph)
            else:
                _add_paragraph(
                    document,
                    f"原始公式表达（需人工复核）：{rendered_formula.text}",
                    alignment=WD_ALIGN_PARAGRAPH.CENTER,
                )
            _add_paragraph(document, formula.label, alignment=WD_ALIGN_PARAGRAPH.CENTER)
        for table in (item for item in disclosure.tables if item.section_number == section.number):
            _add_paragraph(document, table.title, alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True)
            word_table = document.add_table(rows=1, cols=len(table.headers))
            word_table.style = "Table Grid"
            for index, value in enumerate(table.headers):
                _set_cell_text(word_table.rows[0].cells[index], value, bold=True)
            for values in table.rows:
                cells = word_table.add_row().cells
                for index, value in enumerate(values):
                    _set_cell_text(cells[index], value)


def _add_paragraph(
    document: DocumentType,
    text: str,
    *,
    alignment: WD_ALIGN_PARAGRAPH,
    bold: bool = False,
    outline_level: str | None = None,
    first_line_chars: str | None = None,
    font_size: Pt | None = None,
):
    paragraph = document.add_paragraph()
    paragraph.alignment = alignment
    _set_paragraph_spacing(paragraph)
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = DISCLOSURE_FONT
    run.font.size = font_size or DISCLOSURE_FONT_SIZE
    run_properties = run._element.get_or_add_rPr()
    _set_rfonts(run_properties)
    size_cs = run_properties.find(qn("w:szCs"))
    if size_cs is None:
        size_cs = OxmlElement("w:szCs")
        run_properties.append(size_cs)
    size_cs.set(qn("w:val"), str(int((font_size or DISCLOSURE_FONT_SIZE).pt * 2)))
    properties = paragraph._element.get_or_add_pPr()
    if outline_level is not None:
        _set_property(properties, "w:outlineLvl", "w:val", outline_level)
    if first_line_chars is not None:
        indentation = properties.find(qn("w:ind"))
        if indentation is None:
            indentation = OxmlElement("w:ind")
            properties.append(indentation)
        indentation.set(qn("w:firstLineChars"), first_line_chars)
    return paragraph


def _set_paragraph_spacing(paragraph) -> None:
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.5


def _set_cell_text(cell, text: str, *, bold: bool = False) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_paragraph_spacing(paragraph)
    paragraph.clear()
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = DISCLOSURE_FONT
    run.font.size = TABLE_FONT_SIZE
    _set_rfonts(run._element.get_or_add_rPr())


def validate_technical_disclosure_docx(path: Path, expected: TechnicalDisclosure) -> None:
    document = Document(path)
    _validate_page(document)
    if not expected.is_structured:
        _validate_legacy_document(document, expected)
        return
    expected_texts = ["研发技术交底书", expected.title, "摘要", expected.abstract]
    for section in expected.sections:
        expected_texts.append(f"{section.number}、{section.title}")
        expected_texts.extend(section.paragraphs)
        for subsection in section.subsections:
            expected_texts.append(f"{subsection.number} {subsection.title}")
            expected_texts.extend(subsection.paragraphs)
        expected_texts.extend(
            item.title for item in expected.diagrams if item.section_number == section.number
        )
        for formula in (item for item in expected.formulas if item.section_number == section.number):
            expected_texts.append(formula.description)
            rendered_formula = latex_to_omml_with_fallback(formula.latex, display=True)
            if rendered_formula.element is None:
                expected_texts.append(
                    f"原始公式表达（需人工复核）：{rendered_formula.text}"
                )
            expected_texts.append(formula.label)
        expected_texts.extend(
            item.title for item in expected.tables if item.section_number == section.number
        )
    actual_texts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    if actual_texts != expected_texts:
        raise ValueError("技术交底书 DOCX 的标题、正文、图题、公式或表题内容不一致")
    if len(document.inline_shapes) != len(expected.diagrams):
        raise ValueError("技术交底书 DOCX 的图示数量不匹配")
    if len(document.tables) != len(expected.tables):
        raise ValueError("技术交底书 DOCX 的参数表数量不匹配")
    for actual, table in zip(document.tables, expected.tables):
        actual_rows = [[cell.text for cell in row.cells] for row in actual.rows]
        expected_rows = [list(table.headers), *[list(row) for row in table.rows]]
        if actual_rows != expected_rows:
            raise ValueError(f"技术交底书 DOCX 参数表内容不匹配：{table.title}")
    math_count = len(document.element.body.xpath(".//m:oMath"))
    raw_formula_count = sum(
        paragraph.text.startswith("原始公式表达（需人工复核）：")
        for paragraph in document.paragraphs
    )
    if math_count + raw_formula_count < len(expected.formulas):
        raise ValueError("技术交底书 DOCX 的公式数量不匹配")


def _validate_legacy_document(document: DocumentType, expected: TechnicalDisclosure) -> None:
    expected_text = [expected.title, "摘要", expected.abstract, "发明内容", *expected.content_paragraphs]
    if [paragraph.text for paragraph in document.paragraphs] != expected_text:
        raise ValueError("技术交底书 DOCX 的段落顺序或内容校验失败")
    if document.tables:
        raise ValueError("技术交底书 DOCX 不应包含表格")
    body_start = 4
    for index, paragraph in enumerate(document.paragraphs):
        if len(paragraph.runs) != 1:
            raise ValueError(f"第 {index + 1} 段必须且只能包含一个文本运行")
        run = paragraph.runs[0]
        if run.font.name != DISCLOSURE_FONT or run.font.size != DISCLOSURE_FONT_SIZE:
            raise ValueError(f"第 {index + 1} 段字体或字号校验失败")
        fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
        for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
            if fonts.get(qn(f"w:{attribute}")) != DISCLOSURE_FONT:
                raise ValueError(f"第 {index + 1} 段 {attribute} 字体校验失败")
        expected_bold = index in {0, 1, 3}
        if bool(run.bold) != expected_bold:
            raise ValueError(f"第 {index + 1} 段加粗属性校验失败")
        _validate_spacing(paragraph, index)
        expected_alignment = {0: WD_ALIGN_PARAGRAPH.CENTER, 1: WD_ALIGN_PARAGRAPH.LEFT, 2: WD_ALIGN_PARAGRAPH.JUSTIFY, 3: WD_ALIGN_PARAGRAPH.CENTER}.get(index, WD_ALIGN_PARAGRAPH.JUSTIFY)
        if paragraph.alignment != expected_alignment:
            raise ValueError(f"第 {index + 1} 段对齐方式校验失败")
        outline = _property_value(paragraph, "w:outlineLvl", "w:val")
        expected_outline = BODY_OUTLINE_LEVEL if index == 0 else SECTION_OUTLINE_LEVEL if index in {1, 3} else None
        if outline != expected_outline:
            raise ValueError(f"第 {index + 1} 段大纲级别校验失败")
        first_line = _property_value(paragraph, "w:ind", "w:firstLineChars")
        expected_first_line = FIRST_LINE_CHARS if index >= body_start else None
        if first_line != expected_first_line:
            raise ValueError(f"第 {index + 1} 段首行缩进校验失败")


def _validate_page(document: DocumentType) -> None:
    section = document.sections[0]
    _require_near(section.page_width.mm, 210.0, "页面宽度")
    _require_near(section.page_height.mm, 297.0, "页面高度")
    for name, value in [("上边距", section.top_margin.mm), ("右边距", section.right_margin.mm), ("下边距", section.bottom_margin.mm), ("左边距", section.left_margin.mm)]:
        _require_near(value, 25.4, name)


def _validate_spacing(paragraph, index: int) -> None:
    properties = paragraph._element.get_or_add_pPr()
    spacing = properties.find(qn("w:spacing"))
    if spacing is None:
        raise ValueError(f"第 {index + 1} 段缺少行距属性")
    if spacing.get(qn("w:line")) != LINE_SPACING_VALUE or spacing.get(qn("w:lineRule")) != "auto":
        raise ValueError(f"第 {index + 1} 段不是 1.5 倍行距")
    if spacing.get(qn("w:before")) != "0" or spacing.get(qn("w:after")) != "0":
        raise ValueError(f"第 {index + 1} 段段前段后间距必须为 0")


def _set_rfonts(run_properties) -> None:
    fonts = run_properties.get_or_add_rFonts()
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{attribute}"), DISCLOSURE_FONT)


def _set_property(parent, tag: str, attribute: str, value: str) -> None:
    element = parent.find(qn(tag))
    if element is None:
        element = OxmlElement(tag)
        parent.append(element)
    element.set(qn(attribute), value)


def _property_value(paragraph, tag: str, attribute: str) -> str | None:
    properties = paragraph._element.get_or_add_pPr()
    element = properties.find(qn(tag))
    return element.get(qn(attribute)) if element is not None else None


def _require_near(actual: float, expected: float, name: str) -> None:
    if abs(actual - expected) > 0.2:
        raise ValueError(f"技术交底书 {name} 校验失败：{actual}")


__all__ = ["build_technical_disclosure_document", "validate_technical_disclosure_docx", "write_technical_disclosure"]
