import json
from contextlib import contextmanager
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from PIL import Image

from patent_summarizer.cli import build_parser, main
from patent_summarizer.config import AppConfig
from patent_summarizer.disclosure import (
    DisclosureAnalyzer,
    DisclosureCheckpointStore,
    DisclosurePreview,
    DisclosureSourceItem,
    ModuleDefinition,
    ParameterDefinition,
    PriorArtResult,
    PriorArtStatus,
    TechnicalDisclosure,
    TitleProposal,
    TitleSelection,
    disclosure_output_path,
    load_idea_source,
    load_report_sources,
    load_title_selections,
    parse_technical_disclosure,
    parse_invention_candidates,
    parse_title_proposal,
    reconcile_disclosure_with_preview,
    selected_work_items,
    validate_disclosure_title,
    validate_disclosure_against_preview,
    validate_title_selections,
    write_disclosure_bundle,
)
from patent_summarizer.disclosure_docx import (
    BODY_OUTLINE_LEVEL,
    FIRST_LINE_CHARS,
    SECTION_OUTLINE_LEVEL,
    validate_technical_disclosure_docx,
    write_technical_disclosure,
)
from patent_summarizer.disclosure.cnipa_upstream.browser import launch_chromium
from patent_summarizer.disclosure.cnipa_upstream.cnipa_epub_crawler import (
    EpubBatchError, _launch_browser, _search_epub_batches, submit_advanced_search,
)
from patent_summarizer.disclosure.drawio import _style_diagram
from patent_summarizer.disclosure.prior_art import CnipaPriorArtSearcher, PriorArtSearchError
from patent_summarizer.disclosure.validation import SECTION_TITLES, chapter_heading, effect_claim_problem


def _valid_payload(title: str = "一种冗余制动控制方法") -> dict:
    return {
        "标题": title,
        "摘要": "摘" * 180,
        "发明内容": ["技" * 500 for _ in range(6)],
    }


def _valid_disclosure(title: str = "一种冗余制动控制方法") -> TechnicalDisclosure:
    payload = _valid_payload(title)
    return TechnicalDisclosure(payload["标题"], payload["摘要"], payload["发明内容"])


def _valid_preview(title: str = "一种冗余制动控制方法") -> DisclosurePreview:
    return DisclosurePreview(
        source_id="manual",
        title=title,
        technical_problems=("单通道故障时制动力连续性不足",),
        modules=(
            ModuleDefinition("状态判断模块", "判断通道状态", ("状态信号",), ("通道状态",)),
            ModuleDefinition("制动力协调模块", "计算目标制动力", ("通道状态",), ("目标制动力",)),
        ),
        process_steps=("S1 采集状态", "S2 判断有效性", "S3 输出目标制动力"),
        key_points=("跨通道状态协调",),
        evidence_refs=(),
        suggested_parameters=(),
        assumptions=("控制周期稳定",),
        open_questions=("故障阈值需要标定",),
    )


def _structured_reliability_payload(title: str = "车辆姿态融合阻尼控制方法") -> dict:
    section_titles = (
        ("一", "技术领域", "本发明涉及车辆悬架控制，具体涉及按车辆姿态计算悬架阻尼的方法。"),
        ("二", "背景技术", "现有控制方案依赖单一信号，扰动条件下容易产生误判。"),
        ("四", "具体实施方式", "正常用例输入有效车速并检查目标阻尼；异常用例断开传感器并检查保持输出。"),
    )
    sections = [
        {"编号": number, "标题": heading, "段落": [paragraph], "小节": []}
        for number, heading, paragraph in section_titles
    ]
    sections.insert(2, {
        "编号": "三", "标题": "发明内容", "段落": [],
        "小节": [
            {"编号": "3.1", "标题": "发明目的", "段落": ["在传感器短时失效时保持稳定阻尼。"]},
            {"编号": "3.2", "标题": "技术方案", "段落": [
                "姿态融合模块的输入为车速，输出为路面状态，阻尼计算模块输出目标阻尼。",
            ]},
            {"编号": "3.3", "标题": "有益效果", "段落": ["多信号判断可降低阻尼跳变。"]},
        ],
    })
    return {
        "标题": title,
        "摘要": "本方案采集车辆速度和车身垂向加速度，计算信号可信度与路面状态，再根据路面状态计算目标悬架阻尼。系统在信号缺失、超时或越界时进入保持或降级流程，并记录触发原因。工程人员可以按照模块输入输出、计算顺序、参数设置和验证用例完成控制器实现与台架复现。",
        "章节": sections,
        "图示": [
            {"章节": "四", "类型": "系统架构图", "标题": "图1 系统架构", "mermaid": "flowchart TD\nA[车辆信号] --> B[姿态融合模块]\nB --> C[阻尼计算模块]"},
            {"章节": "四", "类型": "流程图", "标题": "图2 控制流程", "mermaid": "flowchart TD\nS1[S1 采集信号] --> S2[S2 检查有效性]\nS2 --> S3[S3 计算目标阻尼]"},
        ],
        "参数表": [],
        "公式": [],
    }


def _add_two_column_table(document, headers, fields):
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = headers[0]
    table.rows[0].cells[1].text = headers[1]
    for key, value in fields:
        cells = table.add_row().cells
        cells[0].text = key
        cells[1].text = value
    return table


def _write_report(path: Path, *, malformed=False, include_layout=True, include_plan=True):
    document = Document()
    document.add_heading("五、目标企业布局现状及优劣势分析", level=1)
    document.add_paragraph("目标企业在冗余制动控制方面存在布局缺口。")
    document.add_heading("六、目标企业专利布局策略及建议", level=1)
    _add_two_column_table(document, ("策略", "内容"), [("重点", "补充冗余控制")])
    document.add_heading("七、待布局的重点建议", level=1)
    if include_layout:
        document.add_heading("7.1 冗余制动协同控制", level=2)
        fields = [
            ("建议布局点名称", "冗余制动协同控制"),
            ("所属技术分类", "制动系统 / 线控制动"),
            ("主要技术问题", "单通道故障时制动力连续性不足"),
            ("竞争对手布局", "竞争样本包含冗余控制路线"),
            ("目标企业现有布局及缺口", "缺少跨通道状态协调"),
            ("建议优先级", "高"),
            ("建议理由", "补齐故障降级链路"),
            ("证据专利", "CN1"),
        ]
        headers = ("错误字段", "分析内容") if malformed else ("固定字段", "分析内容")
        _add_two_column_table(document, headers, fields)
    document.add_heading("八、专利组合构建策略建议", level=1)
    if include_plan:
        document.add_heading("8.1 冗余制动协同控制组合构建", level=2)
        _add_two_column_table(
            document,
            ("组合维度", "建议内容"),
            [
                ("核心专利", "跨通道状态协调方法"),
                ("外围专利分支", "故障检测"),
                ("技术环节覆盖", "感知、决策、执行"),
                ("专利客体协同", "系统、方法、控制器"),
                ("系列化或补充布局", "按故障类型形成系列方案"),
                ("跨布局点协同", "与车辆稳定控制协同"),
                ("证据依据", "第七章建议及样本证据"),
            ],
        )
    document.save(path)


class DisclosureSourceTests(unittest.TestCase):
    def test_loads_layout_points_and_matches_portfolio_plans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.docx"
            _write_report(report)

            sources = load_report_sources(report)

            self.assertEqual(1, len(sources))
            self.assertEqual("7.1", sources[0].source_id)
            self.assertEqual("冗余制动协同控制", sources[0].label)
            self.assertIn("单通道故障", sources[0].context)
            self.assertIn("跨通道状态协调方法", sources[0].context)
            self.assertIn("第五、六章", sources[0].context)

    def test_rejects_report_without_layout_points(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.docx"
            _write_report(report, include_layout=False, include_plan=False)
            with self.assertRaisesRegex(ValueError, "第七章未找到"):
                load_report_sources(report)

    def test_rejects_malformed_layout_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.docx"
            _write_report(report, malformed=True)
            with self.assertRaisesRegex(ValueError, "表头必须"):
                load_report_sources(report)

    def test_rejects_unmatched_portfolio_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.docx"
            _write_report(report, include_plan=False)
            with self.assertRaisesRegex(ValueError, "未匹配到第八章"):
                load_report_sources(report)

    def test_direct_idea_requires_nonempty_text(self):
        self.assertEqual("manual", load_idea_source("冗余制动思路")[0].source_id)
        with self.assertRaisesRegex(ValueError, "不能为空"):
            load_idea_source("  ")


class DisclosureParsingTests(unittest.TestCase):
    def test_title_proposal_requires_three_unique_filename_safe_titles(self):
        titles = parse_title_proposal({"标题": ["标题甲", "标题乙", "标题丙"]})
        self.assertEqual(["标题甲", "标题乙", "标题丙"], titles)
        with self.assertRaisesRegex(ValueError, "严格包含 3 项"):
            parse_title_proposal({"标题": ["标题甲", "标题乙"]})
        with self.assertRaisesRegex(ValueError, "不得重复"):
            parse_title_proposal({"标题": ["标题甲", "标题甲", "标题丙"]})
        with self.assertRaisesRegex(ValueError, "非法字符"):
            parse_title_proposal({"标题": ["标题甲", "标题/乙", "标题丙"]})

    def test_validates_reviewed_content_shape_without_fixed_length_cap_and_claim_language(self):
        parsed = parse_technical_disclosure(_valid_payload(), "一种冗余制动控制方法")
        self.assertEqual(6, len(parsed.content_paragraphs))

        invalid = _valid_payload()
        invalid["摘要"] = "第一段\n第二段"
        with self.assertRaisesRegex(ValueError, "单一自然段"):
            parse_technical_disclosure(invalid, invalid["标题"])

        invalid = _valid_payload()
        invalid["发明内容"] = ["短内容"] * 6
        parsed_short = parse_technical_disclosure(invalid, invalid["标题"])
        self.assertEqual(["短内容"] * 6, parsed_short.content_paragraphs)

        invalid = _valid_payload()
        invalid["发明内容"][0] = "其特征在于" + invalid["发明内容"][0]
        with self.assertRaisesRegex(ValueError, "权利要求式表达"):
            parse_technical_disclosure(invalid, invalid["标题"])

    def test_validation_retry_repairs_invalid_draft(self):
        class SequenceClient:
            def __init__(self):
                self.responses = [
                    {"标题": "错误标题", "摘要": "短", "发明内容": []},
                    _valid_payload(),
                ]
                self.prompts = []

            def chat(self, system_prompt, user_prompt):
                self.prompts.append(user_prompt)
                return self.responses.pop(0)

        source = DisclosureSourceItem("manual", "用户直接思路", "摘要", "上下文")
        client = SequenceClient()
        result = DisclosureAnalyzer(client).draft(source, "一种冗余制动控制方法")
        self.assertEqual("一种冗余制动控制方法", result.title)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("上一次响应未通过", client.prompts[1])

    def test_content_length_does_not_trigger_retry_or_warning(self):
        class SequenceClient:
            def __init__(self, responses):
                self.responses = list(responses)
                self.prompts = []

            def chat(self, system_prompt, user_prompt):
                self.prompts.append(user_prompt)
                return self.responses.pop(0)

        short_payload = _valid_payload()
        short_payload["发明内容"] = ["内容较短但结构完整" for _ in range(6)]

        direct_client = SequenceClient([short_payload])
        direct_warnings = []
        direct_source = DisclosureSourceItem("manual", "用户直接思路", "摘要", "上下文")
        direct_result = DisclosureAnalyzer(
            direct_client,
            on_validation_warning=lambda stage, error: direct_warnings.append((stage, error)),
        ).draft(direct_source, short_payload["标题"])

        report_client = SequenceClient([short_payload])
        report_warnings = []
        report_source = DisclosureSourceItem("7.1", "报告建议", "摘要", "报告上下文")
        valid_draft = parse_technical_disclosure(_valid_payload(), short_payload["标题"])
        report_result = DisclosureAnalyzer(
            report_client,
            on_validation_warning=lambda stage, error: report_warnings.append((stage, error)),
        ).review(report_source, valid_draft)

        self.assertEqual(1, len(direct_client.prompts))
        self.assertEqual(1, len(report_client.prompts))
        self.assertEqual(short_payload["发明内容"], direct_result.content_paragraphs)
        self.assertEqual(short_payload["发明内容"], report_result.content_paragraphs)
        self.assertEqual([], direct_warnings)
        self.assertEqual([], report_warnings)

    def test_non_length_validation_still_fails_after_one_retry(self):
        class SequenceClient:
            def __init__(self, response):
                self.response = response

            def chat(self, system_prompt, user_prompt):
                return self.response

        invalid = _valid_payload()
        invalid["发明内容"][0] = "其特征在于" + invalid["发明内容"][0]
        warnings = []
        analyzer = DisclosureAnalyzer(
            SequenceClient(invalid),
            on_validation_warning=lambda stage, error: warnings.append((stage, error)),
        )
        source = DisclosureSourceItem("manual", "用户直接思路", "摘要", "上下文")

        with self.assertRaisesRegex(ValueError, "结构化响应校验失败"):
            analyzer.draft(source, invalid["标题"])
        self.assertEqual([], warnings)

    def test_title_selection_validates_duplicates_and_filename_mapping(self):
        sources = [DisclosureSourceItem("7.1", "建议", "摘要", "上下文")]
        selections = [TitleSelection("7.1", ["标题甲", "标题乙"])]
        self.assertEqual(2, len(selected_work_items(selections, sources)))
        self.assertEqual(
            Path("out") / "标题甲_技术交底书.docx",
            disclosure_output_path(Path("out"), "标题甲"),
        )
        with self.assertRaisesRegex(ValueError, "批次内标题重复"):
            validate_title_selections(
                [TitleSelection("7.1", ["标题甲", "标题甲"])],
                sources,
            )
        with self.assertRaisesRegex(ValueError, "首尾不能包含空格"):
            validate_disclosure_title(" 标题")

    def test_loads_explicit_title_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "titles.json"
            path.write_text(
                json.dumps([{"source_id": "manual", "titles": ["标题甲"]}], ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(["标题甲"], load_title_selections(path)[0].titles)


class DisclosureDocxTests(unittest.TestCase):
    def test_writes_and_validates_exact_disclosure_format(self):
        disclosure = _valid_disclosure()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / f"{disclosure.title}_技术交底书.docx"
            write_technical_disclosure(disclosure, output)
            validate_technical_disclosure_docx(output, disclosure)
            document = Document(output)

            self.assertEqual(WD_ALIGN_PARAGRAPH.CENTER, document.paragraphs[0].alignment)
            self.assertEqual(WD_ALIGN_PARAGRAPH.LEFT, document.paragraphs[1].alignment)
            self.assertEqual(WD_ALIGN_PARAGRAPH.JUSTIFY, document.paragraphs[2].alignment)
            self.assertEqual(
                BODY_OUTLINE_LEVEL,
                document.paragraphs[0]._p.pPr.outlineLvl.get(qn("w:val")),
            )
            self.assertEqual(
                SECTION_OUTLINE_LEVEL,
                document.paragraphs[1]._p.pPr.outlineLvl.get(qn("w:val")),
            )
            self.assertEqual(
                FIRST_LINE_CHARS,
                document.paragraphs[4]._p.pPr.ind.get(qn("w:firstLineChars")),
            )

    def test_refuses_existing_output_without_overwrite(self):
        disclosure = _valid_disclosure()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / f"{disclosure.title}_技术交底书.docx"
            output.write_bytes(b"existing")
            with self.assertRaisesRegex(FileExistsError, "已经存在"):
                write_technical_disclosure(disclosure, output)
            self.assertEqual(b"existing", output.read_bytes())


class DisclosureCliTests(unittest.TestCase):
    def test_parser_requires_exactly_one_source(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["disclosure", "--output-dir", "out"])
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "disclosure",
                    "--report",
                    "report.docx",
                    "--idea",
                    "idea",
                    "--output-dir",
                    "out",
                ]
            )

    def test_direct_idea_dry_run_does_not_need_api_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = main(
                [
                    "disclosure",
                    "--idea",
                    "一种冗余制动控制思路",
                    "--output-dir",
                    str(Path(temp_dir) / "out"),
                    "--dry-run",
                ]
            )
            self.assertEqual(0, exit_code)

    def test_gui_title_cancel_writes_no_docx(self):
        proposals = ["标题甲", "标题乙", "标题丙"]

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patent_summarizer.cli.load_config",
            return_value=AppConfig("https://example.test", "key", "model"),
        ), patch(
            "patent_summarizer.cli.DisclosureAnalyzer.propose_titles",
            side_effect=lambda source: TitleProposal(source, proposals),
        ):
            output = Path(temp_dir) / "out"
            exit_code = main(
                [
                    "disclosure",
                    "--idea",
                    "一种冗余制动控制思路",
                    "--output-dir",
                    str(output),
                ],
                disclosure_title_review=lambda _proposals, _output: None,
            )
            self.assertEqual(130, exit_code)
            self.assertEqual([], list(output.glob("*.docx")))

    def test_cli_rejects_legacy_unstructured_content_after_upgrade(self):
        class SequenceClient:
            def __init__(self, response):
                self.responses = [response, response, response, response]

            def chat(self, system_prompt, user_prompt):
                return self.responses.pop(0)

        title = "一种冗余制动控制方法"
        short_payload = _valid_payload(title)
        short_payload["发明内容"] = ["内容较短但结构完整" for _ in range(6)]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "out"
            titles = root / "titles.json"
            titles.write_text(
                json.dumps([{"source_id": "manual", "titles": [title]}], ensure_ascii=False),
                encoding="utf-8",
            )
            stdout = StringIO()
            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch(
                "patent_summarizer.cli.RateLimitedOpenAIClient",
                return_value=SequenceClient(short_payload),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.build_preview",
                return_value=_valid_preview(title),
            ), redirect_stdout(stdout):
                exit_code = main(
                    [
                        "disclosure",
                        "--idea",
                        "一种冗余制动控制思路",
                        "--output-dir",
                        str(output),
                        "--titles-file",
                        str(titles),
                    ]
                )

            self.assertEqual(1, exit_code)
            self.assertFalse((output / f"{title}_技术交底书.docx").exists())
            self.assertIn("FAIL\t", stdout.getvalue())
            self.assertNotIn("WARNING\t", stdout.getvalue())

    def test_explicit_titles_generate_skip_and_atomically_overwrite(self):
        title = "一种冗余制动控制方法"
        disclosure = _valid_disclosure(title)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "out"
            titles = root / "titles.json"
            titles.write_text(
                json.dumps(
                    [{"source_id": "manual", "titles": [title]}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            argv = [
                "disclosure",
                "--idea",
                "一种冗余制动控制思路",
                "--output-dir",
                str(output),
                "--titles-file",
                str(titles),
            ]
            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.build_preview",
                return_value=_valid_preview(title),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.draft",
                return_value=disclosure,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.review",
                return_value=disclosure,
            ), patch("patent_summarizer.cli.CnipaPriorArtSearcher.search") as searched:
                self.assertEqual(0, main(argv))
                searched.assert_not_called()
            generated = output / f"{title}_技术交底书.docx"
            self.assertTrue(generated.exists())

            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch("patent_summarizer.cli.DisclosureAnalyzer.draft") as draft:
                self.assertEqual(0, main([*argv, "--skip-existing"]))
                draft.assert_not_called()

            generated.write_bytes(b"stale")
            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.build_preview",
                return_value=_valid_preview(title),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.draft",
                return_value=disclosure,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.review",
                return_value=disclosure,
            ):
                self.assertEqual(0, main([*argv, "--overwrite"]))
            validate_technical_disclosure_docx(generated, disclosure)

    def test_core_preview_edit_triggers_prior_art_requery(self):
        title = "一种冗余制动控制方法"
        initial_preview = _valid_preview(title)
        revised_preview = replace(
            initial_preview,
            modules=(
                ModuleDefinition("信号可信度判断模块", "计算信号可信度", ("状态信号",), ("可信度",)),
                initial_preview.modules[1],
            ),
        )
        review_calls = []

        def review_preview(preview):
            review_calls.append(preview)
            return revised_preview if len(review_calls) == 1 else preview

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "out"
            titles = root / "titles.json"
            titles.write_text(
                json.dumps([{"source_id": "manual", "titles": [title]}], ensure_ascii=False),
                encoding="utf-8",
            )
            bundle = SimpleNamespace(
                docx=output / f"{title}_技术交底书.docx",
                markdown=output / f"{title}_技术交底书.md",
            )
            result = PriorArtResult(PriorArtStatus.SUCCESS, message="查新完成")
            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.plan_search_terms",
                return_value=("冗余制动", "通道协调"),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.plan_search_terms_for_preview",
                return_value=("信号可信度", "制动力协调"),
            ) as replanned, patch(
                "patent_summarizer.cli.CnipaPriorArtSearcher.search",
                side_effect=[result, result],
            ) as searched, patch(
                "patent_summarizer.cli.DisclosureAnalyzer.build_preview",
                return_value=initial_preview,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.draft",
                return_value=_valid_disclosure(title),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.review",
                return_value=_valid_disclosure(title),
            ), patch(
                "patent_summarizer.cli.write_disclosure_bundle",
                return_value=bundle,
            ):
                exit_code = main(
                    [
                        "disclosure",
                        "--idea", "一种冗余制动控制思路",
                        "--output-dir", str(output),
                        "--titles-file", str(titles),
                        "--prior-art",
                    ],
                    disclosure_preview_review=review_preview,
                )

        self.assertEqual(0, exit_code)
        self.assertEqual(2, searched.call_count)
        replanned.assert_called_once()
        self.assertEqual(2, len(review_calls))
        self.assertEqual(PriorArtStatus.SUCCESS, review_calls[1].prior_art.status)

    def test_unhandled_export_failure_does_not_rewrite_the_whole_document(self):
        title = "一种冗余制动控制方法"
        disclosure = _valid_disclosure(title)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "out"
            titles = root / "titles.json"
            titles.write_text(
                json.dumps([{"source_id": "manual", "titles": [title]}], ensure_ascii=False),
                encoding="utf-8",
            )
            bundle = SimpleNamespace(
                docx=output / f"{title}_技术交底书.docx",
                markdown=output / f"{title}_技术交底书.md",
            )
            with patch(
                "patent_summarizer.cli.load_config",
                return_value=AppConfig("https://example.test", "key", "model"),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.build_preview",
                return_value=_valid_preview(title),
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.draft",
                return_value=disclosure,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.review",
                return_value=disclosure,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.repair_delivery",
                return_value=disclosure,
            ) as repair, patch(
                "patent_summarizer.cli.write_disclosure_bundle",
                side_effect=ValueError("DOCX 文件损坏"),
            ) as write:
                exit_code = main([
                    "disclosure",
                    "--idea", "一种冗余制动控制思路",
                    "--output-dir", str(output),
                    "--titles-file", str(titles),
                ])

        self.assertEqual(1, exit_code)
        self.assertEqual(1, write.call_count)
        repair.assert_not_called()


class DisclosureReliabilityTests(unittest.TestCase):
    def test_claim_checks_distinguish_evidence_from_future_work_and_runtime_measurement(self):
        statement = "台架试验表明阻尼波动降低 30%"
        for clause in (
            "材料未给出实测对比数据",
            "不构成已经验证的性能结论",
            "上述范围均为建议值，不能视为已验证的限值",
            "降幅仍需实测确认",
            "若实测阻尼达到阈值，则启动窗口计时",
            "预期缩短 20 ms",
        ):
            with self.subTest(clause=clause):
                self.assertIsNone(effect_claim_problem(clause, "设计方案"))
        self.assertIsNone(effect_claim_problem(statement, f"初次演示记录。{statement}。"))
        for source in (
            "材料只有采集通道的实测设计。",
            "30% 是指目标阈值。",
            f"本项目没有证据证明{statement}。",
            "台架试验表明阻尼波动降低 30‰。",
        ):
            with self.subTest(source=source):
                self.assertIsNotNone(effect_claim_problem(statement, source))

    def test_both_sources_publish_clean_review_drafts_and_resume(self):
        payload = _structured_reliability_payload()
        title = payload["标题"]
        sections = {item["编号"]: {"章节": item} for item in payload["章节"]}
        artifacts = {key: payload[key] for key in ("摘要", "图示", "参数表", "公式")}
        artifacts["公式"] = [{
            "章节": "三", "编号": "式（1）", "LaTeX": "w_2+w_3+q",
            "说明": "w_1至w_4为权重。", "来源": "agent", "材料依据": "",
        }]
        bad_effect = {"章节": {**sections["四"]["章节"], "段落": [
            "多信号判断属于待验证的预期效果。台架实测表明阻尼波动降低 30%。"
            "经查新未发现相似专利。", *sections["四"]["章节"]["段落"],
        ]}}

        class Model:
            broken = True

            def __init__(self):
                self.calls = []

            def chat(self, _system, prompt):
                if "只修复一个公式项目" in prompt:
                    self.calls.append("formula")
                    return {"公式": [dict(artifacts["公式"][0], 说明="w_1至w_4为权重，q为新参数。")]}
                if "图文与摘要生成" in prompt:
                    self.calls.append("artifacts")
                    return artifacts
                write = re.search(r"当前只撰写：([一二三四五六])", prompt)
                if write:
                    number = write[1]
                    self.calls.append(f"write-{number}")
                    if self.broken and number == "四":
                        return bad_effect
                    return sections[number]
                if "当前待审校章节：" in prompt:
                    current = prompt.split("当前待审校章节：\n", 1)[1]
                    item, _ = json.JSONDecoder().raw_decode(current)
                    self.calls.append(f"review-{item['章节']['编号']}")
                    if self.broken and item["章节"]["编号"] == "二":
                        raise ConnectionError("审校连接中断")
                    return item
                raise AssertionError("unexpected request")

        from patent_summarizer.disclosure.diagrams import _render_with_pillow

        for flag, source_id in (("--idea", "manual"), ("--project-dir", "project")):
            with self.subTest(source=source_id), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                project = root / "source"
                project.mkdir()
                (project / "design.txt").write_text("已有姿态输入及阻尼控制流程。", encoding="utf-8")
                title_file = root / "titles.json"
                title_file.write_text(json.dumps([{"source_id": source_id, "titles": [title]}]), encoding="utf-8")
                preview = DisclosurePreview(
                    source_id=source_id, title=title, technical_problems=("保持有效输出",),
                    modules=(
                        ModuleDefinition("姿态融合模块", "判断状态", ("车速",), ("路面状态",)),
                        ModuleDefinition("阻尼计算模块", "计算阻尼", ("路面状态",), ("目标阻尼",)),
                    ),
                    process_steps=("S1 采集信号", "S2 检查有效性", "S3 计算目标阻尼"),
                    key_points=("信号可信度融合",), evidence_refs=(), suggested_parameters=(),
                    assumptions=(), open_questions=(),
                )
                model = Model()
                output = root / "out"
                source_value = str(project) if source_id == "project" else "已有姿态输入及阻尼控制流程。"
                argv = ["disclosure", flag, source_value, "--output-dir", str(output),
                        "--titles-file", str(title_file)]

                def render(code, path, diagram_title):
                    if model.broken and "控制流程" in diagram_title:
                        raise RuntimeError("模拟图示渲染失败")
                    return _render_with_pillow(code, path, diagram_title)

                with patch("patent_summarizer.cli.load_config", return_value=AppConfig(
                    "https://example.test", "key", "model"
                )), patch("patent_summarizer.cli.RateLimitedOpenAIClient", return_value=model), patch(
                    "patent_summarizer.cli.DisclosureAnalyzer.build_preview", return_value=preview
                ) as build_preview, patch(
                    "patent_summarizer.disclosure.diagrams._mermaid_runtime_path", return_value=None
                ), patch(
                    "patent_summarizer.disclosure.diagrams.drawio_runtime_path", return_value=None
                ), patch("patent_summarizer.disclosure.diagrams._render_with_pillow", side_effect=render):
                    log = StringIO()
                    with redirect_stdout(log):
                        self.assertEqual(main(argv), 0)
                    self.assertIn(f"REVIEW_DRAFT\t{source_id}", log.getvalue())
                    first = output / f"{title}_技术交底书_审阅稿"
                    docx = first.with_suffix(".docx")
                    markdown = first.with_suffix(".md")
                    original = docx.read_bytes()
                    document = Document(docx)
                    self.assertEqual([paragraph.text for paragraph in document.paragraphs[:2]], [title, "摘要"])
                    self.assertEqual(
                        [p.text for p in document.paragraphs if p.style.name == "Heading 1"],
                        [chapter_heading(number, name) for number, name in SECTION_TITLES.items()],
                    )
                    self.assertEqual(len(document.inline_shapes), 1)
                    self.assertEqual(len(document.element.body.xpath(".//m:oMath")), 1)
                    for content in (markdown.read_text(encoding="utf-8"),
                                    "\n".join(p.text for p in document.paragraphs)):
                        for marker in ("待确认事项", "独立审校未完成", "【待补】", "【待复核】",
                                       "式（1）存在未定义符号", "【待核验】"):
                            self.assertNotIn(marker, content)
                        self.assertIn("姿态融合模块", content)
                        self.assertIn("降低 30%", content)
                        self.assertNotIn("未发现相似专利", content)
                    self.assertEqual(model.calls.count("write-三"), 1)
                    self.assertNotIn("formula", model.calls)
                    self.assertNotIn("公式局部纠错", log.getvalue())
                    self.assertNotIn("来源材料没有验证记录", log.getvalue())
                    diagnostic = output / ".patent_disclosure_work" / "diagnostics" / f"{first.name}.json"
                    codes = {item["code"] for item in json.loads(diagnostic.read_text(encoding="utf-8"))}
                    self.assertIn("chapter-review-pending", codes)
                    self.assertIn("prior-art-claim-pending", codes)

                    with redirect_stdout(StringIO()):
                        self.assertEqual(main(argv), 0)
                    self.assertTrue(first.with_name(f"{first.name}_2").with_suffix(".docx").is_file())
                    self.assertEqual(docx.read_bytes(), original)

                    model.broken = False
                    model.calls.clear()
                    with patch("patent_summarizer.disclosure.exporter._publish_bundle",
                               side_effect=PermissionError("模拟输出目录不可写")), redirect_stdout(log):
                        self.assertEqual(main(argv), 1)
                    self.assertIn("图文导出与文件核验：模拟输出目录不可写", log.getvalue())
                    self.assertFalse((output / f"{title}_技术交底书.docx").exists())
                    self.assertIn("artifacts", model.calls)
                    self.assertEqual([call for call in model.calls if call.startswith("write-")],
                                     ["write-四"])
                    model.calls.clear()
                    with redirect_stdout(log):
                        self.assertEqual(main(argv), 0)
                    self.assertEqual(model.calls, [])
                    self.assertIn("confirmed-preview", log.getvalue())
                    self.assertTrue((output / f"{title}_技术交底书.docx").is_file())
                    self.assertTrue((output / f"{title}_技术交底书.md").is_file())
                    build_preview.assert_called_once()

    def test_multiple_recommendations_are_normalized_without_model_repair(self):
        payload = {
            "候选方案": [
                {
                    "编号": f"C{index}",
                    "标题": f"车辆阻尼控制方法{index}",
                    "技术问题": "阻尼控制不稳定",
                    "核心创新": "按路面状态调节阻尼",
                    "初步区别": "增加状态判断",
                    "可实施性": "使用现有控制器实现",
                    "材料依据": [],
                    "推荐": True,
                    "推荐理由": "技术闭环完整",
                }
                for index in (1, 2)
            ]
        }

        candidates = parse_invention_candidates(payload)

        self.assertEqual([True, False], [item.recommended for item in candidates])

    def test_missing_confirmed_content_is_restored_as_diagnostics(self):
        payload = _structured_reliability_payload()
        payload["章节"][2]["小节"][1]["段落"] = [
            "控制器接收输入并产生输出，信号异常或超过边界时保持上一周期结果。"
        ]
        payload["章节"][3]["段落"] = ["控制器执行状态判断和阻尼计算。"]
        payload["图示"][0]["mermaid"] = "flowchart TD\nA[输入] --> B[计算]\nB --> C[输出]"
        disclosure = parse_technical_disclosure(payload, payload["标题"])
        preview = DisclosurePreview(
            source_id="manual",
            title=payload["标题"],
            technical_problems=("异常时阻尼跳变",),
            modules=(
                ModuleDefinition("姿态融合模块", "判断路面状态", ("垂向加速度",), ("路面状态",)),
                ModuleDefinition("阻尼计算模块", "计算阻尼", ("路面状态",), ("目标阻尼",)),
            ),
            process_steps=("S1 采集信号", "S2 检查有效性", "S3 计算目标阻尼"),
            key_points=("信号可信度融合后选择阻尼标定区间",),
            evidence_refs=(),
            suggested_parameters=(
                ParameterDefinition("反馈控制器增益", "Kp, Ki, Kd", "PID增益", "示例值：待标定", "台架验证", True),
            ),
            assumptions=(),
            open_questions=(),
        )

        restored = reconcile_disclosure_with_preview(disclosure, preview)
        validate_disclosure_against_preview(restored, preview)

        codes = {item.code for item in restored.diagnostics}
        self.assertIn("confirmed-modules-restored", codes)
        self.assertIn("confirmed-key-points-restored", codes)
        self.assertIn("confirmed-parameters-restored", codes)
        self.assertIn("confirmed-steps-restored", codes)
        self.assertEqual(restored, reconcile_disclosure_with_preview(restored, preview))

    def test_formula_failure_delivers_editable_linear_formula_with_warning(self):
        payload = _structured_reliability_payload()
        payload["公式"] = [{
            "章节": "三",
            "编号": "式（1）",
            "LaTeX": "y=x",
            "说明": "y 表示目标输出，x 表示输入信号。",
            "来源": "agent",
            "材料依据": "",
        }]
        disclosure = parse_technical_disclosure(payload, payload["标题"])
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "patent_summarizer.disclosure.math_to_omml.latex_to_omml",
            side_effect=ValueError("unsupported formula"),
        ):
            paths = write_disclosure_bundle(disclosure, Path(temp_dir))

            self.assertTrue(paths.docx.is_file())
            self.assertTrue(any(item.code == "formula-linear" for item in paths.diagnostics))
            document = Document(paths.docx)
            self.assertGreaterEqual(len(document.element.body.xpath(".//m:oMath")), 1)

    def test_failed_chapter_resumes_without_repeating_completed_chapters(self):
        payload = _structured_reliability_payload()
        sections = {item["编号"]: {"章节": item} for item in payload["章节"]}
        artifacts = {key: payload[key] for key in ("摘要", "图示", "参数表", "公式")}

        class ChapterClient:
            def __init__(self, fail_on=None):
                self.fail_on = fail_on
                self.calls = []

            def chat(self, _system, prompt):
                self.calls.append(prompt)
                for number in SECTION_TITLES:
                    if f"当前只撰写：{number}、" in prompt:
                        if self.fail_on == number:
                            raise ConnectionError("simulated interruption")
                        return sections[number]
                if "图文与摘要生成" in prompt:
                    return artifacts
                raise AssertionError("unexpected prompt")

        preview = DisclosurePreview(
            source_id="manual",
            title=payload["标题"],
            technical_problems=("异常时保持稳定输出",),
            modules=(
                ModuleDefinition("姿态融合模块", "判断状态", ("车速",), ("路面状态",)),
                ModuleDefinition("阻尼计算模块", "计算阻尼", ("路面状态",), ("目标阻尼",)),
            ),
            process_steps=("S1 采集信号", "S2 检查有效性", "S3 计算目标阻尼"),
            key_points=("信号可信度融合",),
            evidence_refs=(),
            suggested_parameters=(),
            assumptions=(),
            open_questions=(),
        )
        source = DisclosureSourceItem("manual", "构思", "摘要", "材料上下文")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DisclosureCheckpointStore(Path(temp_dir), model="model")
            first = ChapterClient(fail_on="三")
            with self.assertRaises(ConnectionError):
                DisclosureAnalyzer(first, checkpoint_store=store).draft(
                    source, payload["标题"], preview
                )
            second = ChapterClient()
            result = DisclosureAnalyzer(second, checkpoint_store=store).draft(
                source, payload["标题"], preview
            )

        self.assertEqual(4, len(result.sections))
        self.assertFalse(any("当前只撰写：一、" in prompt for prompt in second.calls))
        self.assertFalse(any("当前只撰写：二、" in prompt for prompt in second.calls))
        self.assertTrue(any("当前只撰写：三、" in prompt for prompt in second.calls))
        third = next(prompt for prompt in second.calls if "当前只撰写：三、" in prompt)
        previous = json.loads(third.split("已完成的前文章节：\n", 1)[1].split("\n\n当前只撰写", 1)[0])
        self.assertEqual(previous, payload["章节"][:2])


class CnipaSearchTests(unittest.TestCase):
    def test_advanced_click_only_dispatches_and_allows_a_response_after_thirty_seconds(self):
        fake_time_ms = 0
        page = Mock()
        button = page.locator.return_value.get_by_role.return_value

        @contextmanager
        def response_wait(*, wait_until, timeout):
            nonlocal fake_time_ms
            self.assertEqual(wait_until, "commit")
            navigation = SimpleNamespace()
            yield navigation
            fake_time_ms += 35_000
            self.assertGreater(timeout, fake_time_ms)
            navigation.value = SimpleNamespace(status=200)

        page.expect_navigation.side_effect = response_wait
        page.wait_for_function.return_value.json_value.return_value = "results"
        with patch("patent_summarizer.disclosure.cnipa_upstream.cnipa_epub_crawler.apply_epub_advanced_type_filter"):
            self.assertEqual("results", submit_advanced_search(page, "转向控制", class_code="B62D5"))
        button.click.assert_called_once_with(timeout=10_000, no_wait_after=True)
        self.assertEqual(fake_time_ms, 35_000)

    def test_advanced_http_error_stops_before_dom_wait(self):
        page = Mock()

        @contextmanager
        def rejected_navigation(**_):
            yield SimpleNamespace(value=SimpleNamespace(status=400))

        page.expect_navigation.side_effect = rejected_navigation
        with patch("patent_summarizer.disclosure.cnipa_upstream.cnipa_epub_crawler.apply_epub_advanced_type_filter"):
            with self.assertRaisesRegex(RuntimeError, "高级查询提交收到 HTTP 400"):
                submit_advanced_search(page, "转向控制", class_code="B62D5")
        page.wait_for_function.assert_not_called()

    def test_browser_returns_empty_only_for_an_explicit_zero_result(self):
        module = "patent_summarizer.disclosure.cnipa_upstream.cnipa_epub_crawler"
        with patch(module + ".wait_for_epub_home_ready"), patch(
            module + "._safe_page_content", return_value="<html/>",
        ), patch(module + ".parse_search_result_html", return_value=[]), patch(
            module + ".submit_index_search", side_effect=["empty", "results"],
        ):
            self.assertEqual([("<html/>", [])], _search_epub_batches(Mock(), ["转向控制"], patent_type="invention"))
            with self.assertRaisesRegex(EpubBatchError, "无法解析专利记录"):
                _search_epub_batches(Mock(), ["转向控制"], patent_type="invention")

    def test_default_search_reuses_session_and_retains_completed_ipc_batches(self):
        def hit(index):
            return SimpleNamespace(pub_number=f"CN12345678{index}A", title=f"转向控制{index}",
                                   abstract="转向控制公开摘要", ipc_codes=["B62D5/04"],
                                   link=f"http://epub.cnipa.gov.cn/patent/CN12345678{index}A")

        advanced_error = EpubBatchError(RuntimeError("高级查询提交收到 HTTP 400"), [("<html>", [hit(2)])])
        search = Mock(side_effect=[[("<html>", [hit(1)])], advanced_error])

        @contextmanager
        def session():
            yield search

        with patch("patent_summarizer.disclosure.cnipa_upstream.cnipa_epub_crawler.epub_search_session",
                   side_effect=session) as start:
            result = CnipaPriorArtSearcher().search(["转向控制"])
        start.assert_called_once_with()
        self.assertEqual(2, search.call_count)
        self.assertEqual(PriorArtStatus.PARTIAL, result.status)
        self.assertIn("HTTP 400", result.message)
        self.assertEqual([hit(1).pub_number, hit(2).pub_number], [r.publication_number for r in result.records])

    def test_bundled_browser_preference_is_local_to_cnipa(self):
        playwright = Mock()
        with patch.dict("os.environ", {}, clear=True):
            _launch_browser(playwright)
            self.assertNotIn("channel", playwright.chromium.launch.call_args.kwargs)
            launch_chromium(playwright)
            self.assertEqual("chrome", playwright.chromium.launch.call_args.kwargs["channel"])
            playwright.chromium.launch.side_effect = [RuntimeError("missing"), Mock()]
            _launch_browser(playwright)
            self.assertEqual("chrome", playwright.chromium.launch.call_args.kwargs["channel"])
        with patch.dict("os.environ", {"PATENT_BROWSER_CHANNEL": "msedge"}):
            playwright.chromium.launch.side_effect = None
            _launch_browser(playwright)
            self.assertEqual("msedge", playwright.chromium.launch.call_args.kwargs["channel"])

    def test_ipc_timeout_preserves_real_first_pass_records_even_above_threshold(self):
        hits = [
            SimpleNamespace(pub_number=f"CN12345678{index}A", title=f"转向控制{index}",
                            abstract="转向控制公开摘要", ipc_codes=["B62D5/04"],
                            link=f"http://epub.cnipa.gov.cn/patent/CN12345678{index}A")
            for index in range(4)
        ]
        transport = Mock(side_effect=[[("<html>", hits)], TimeoutError("模拟 IPC 超时")])
        search = CnipaPriorArtSearcher(transport)
        result = search.search(["电动助力转向"])
        self.assertEqual(result.status, PriorArtStatus.PARTIAL)
        self.assertEqual([record.publication_number for record in result.records], [hit.pub_number for hit in hits])
        self.assertIn("IPC 高级检索未完成", result.message)
        self.assertEqual(transport.call_args_list[1].kwargs["class_codes"], ["B62D5"])

        unverified = SimpleNamespace(**{**vars(hits[0]), "link": "https://example.test/patent"})
        transport.side_effect = [[("<html>", [unverified])], TimeoutError("模拟 IPC 超时")]
        with self.assertRaisesRegex(PriorArtSearchError, "IPC 高级检索未完成"):
            search.search(["电动助力转向"])

    def test_empty_and_blocked_search_are_distinct(self):
        transport = Mock(return_value=[("<html>", [])])
        search = CnipaPriorArtSearcher(transport)
        self.assertEqual(search.search(["电动助力转向"]).status, PriorArtStatus.EMPTY)
        transport.assert_called_once()
        transport.side_effect = RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
        with self.assertRaisesRegex(PriorArtSearchError, "关键词检索失败.*ERR_BLOCKED_BY_CLIENT"):
            search.search(["电动助力转向"])


class DrawioDeliveryTests(unittest.TestCase):
    def test_styling_preserves_drawio_decision_shape_and_branch_label(self):
        xml = (
            '<mxGraphModel><root><mxCell id="a" vertex="1" style="rhombus;whiteSpace=wrap;"/>'
            '<mxCell id="b" edge="1" source="a" value="否"/></root></mxGraphModel>'
        )
        model = ElementTree.fromstring(_style_diagram(xml, "流程图"))
        decision, branch = model.findall(".//mxCell")
        self.assertIn("rhombus;", decision.get("style"))
        self.assertIn("fillColor=#fff2cf;", decision.get("style"))
        self.assertEqual("a", branch.get("source"))
        self.assertEqual("否", branch.get("value"))

    def test_normalizes_a_large_decision_and_routes_feedback_orthogonally(self):
        xml = (
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<UserObject id="2" mermaidId="n:S1" label="S1 采集信号">'
            '<mxCell vertex="1" parent="1"><mxGeometry x="190" y="0" width="140" height="54"/></mxCell>'
            '</UserObject><UserObject id="3" mermaidId="n:S2" label="S2 判断多通道传感器输入信号是否可用">'
            '<mxCell vertex="1" parent="1" style="rhombus;">'
            '<mxGeometry x="0" y="115" width="459" height="459"/></mxCell></UserObject>'
            '<mxCell id="4" edge="1" parent="1" source="2" target="3" style="curved=1;"/>'
            '<mxCell id="5" edge="1" parent="1" source="3" target="2" value="否" style="curved=1;"/>'
            '</root></mxGraphModel>'
        )
        result = ElementTree.fromstring(_style_diagram(xml, "流程图"))
        decision = result.find(".//UserObject[@id='3']")
        self.assertLess(float(decision.find("mxCell/mxGeometry").get("height")), 125)
        self.assertIn("<br>", decision.get("label"))
        feedback = result.find(".//mxCell[@id='5']")
        self.assertIn("orthogonalEdgeStyle", feedback.get("style"))
        self.assertIn("curved=0", feedback.get("style"))
        self.assertIn("dashed=1", feedback.get("style"))
        self.assertEqual(2, len(feedback.findall("mxGeometry/Array/mxPoint")))
        self.assertEqual("否", feedback.get("value"))

    def test_model_can_place_and_group_existing_nodes_without_changing_edges(self):
        xml = (
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<UserObject id="2" mermaidId="n:A" label="输入"><mxCell vertex="1" parent="1"/></UserObject>'
            '<UserObject id="3" mermaidId="n:B" label="计算"><mxCell vertex="1" parent="1"/></UserObject>'
            '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
            '</root></mxGraphModel>'
        )
        layout = {"节点": {"A": [180, 90, "input"], "B": [420, 90, "output"]},
                  "分组": [{"名称": "控制模块", "节点": ["A", "B"]}]}
        result = ElementTree.fromstring(_style_diagram(xml, "架构", layout, horizontal=True))
        self.assertEqual("96.0", result.find(".//UserObject[@id='2']/mxCell/mxGeometry").get("x"))
        self.assertIsNotNone(result.find(".//mxCell[@value='控制模块']"))
        self.assertEqual("3", result.find(".//mxCell[@id='4']").get("target"))

    def test_missing_diagram_does_not_trigger_another_model_call(self):
        from patent_summarizer.disclosure.recovery import prepare_artifacts

        payload = _structured_reliability_payload()
        preview = _valid_preview(payload["标题"])
        repair = Mock(side_effect=AssertionError("unexpected diagram regeneration"))
        raw = {key: payload[key] for key in ("摘要", "图示", "参数表", "公式")}
        raw["图示"] = [payload["图示"][0], {"mermaid": "invalid chart"}]
        result, _ = prepare_artifacts(TechnicalDisclosure(payload["标题"], ""), raw, preview, repair)
        self.assertEqual(1, len(result.diagrams))
        self.assertIn("diagram-missing", [item.code for item in result.diagnostics])
        repair.assert_not_called()

    def test_a_drawio_failure_only_rerenders_the_failed_figure(self):
        payload = _structured_reliability_payload("一种姿态融合 (验证) 方法")
        disclosure = parse_technical_disclosure(payload, payload["标题"])

        @contextmanager
        def drawio(_runtime):
            def render(diagram, image, editable):
                editable.write_text("<mxfile/>", encoding="utf-8")
                if diagram.kind == "流程图":
                    raise RuntimeError("模拟 PNG 导出失败")
                Image.new("RGB", (100, 64), "white").save(image)
            yield render

        def mermaid(_source, image, _title, *, diagnostics):
            Image.new("RGB", (64, 100), "white").save(image)
            return image

        module = "patent_summarizer.disclosure.diagrams"
        with tempfile.TemporaryDirectory() as directory, patch(module + ".drawio_runtime_path", return_value=Path(".")), patch(
            module + ".drawio_renderer", side_effect=drawio,
        ), patch(module + ".render_mermaid_flowchart", side_effect=mermaid) as fallback:
            paths = write_disclosure_bundle(disclosure, Path(directory))
            fallback.assert_called_once()
            self.assertEqual(disclosure.diagrams[1].mermaid, fallback.call_args.args[0])
            self.assertEqual(["diagram_01.drawio"], [path.name for path in paths.assets_dir.glob("*.drawio")])
            markdown = paths.markdown.read_text(encoding="utf-8")
            self.assertIn("diagram_01.drawio", markdown)
            self.assertNotIn("diagram_02.drawio", markdown)
            self.assertIn("%28", markdown)
            self.assertEqual(2, len(Document(paths.docx).inline_shapes))
            self.assertIn("drawio-fallback", [item.code for item in paths.diagnostics])
            from patent_summarizer.disclosure import existing_disclosure_bundle_complete
            self.assertTrue(existing_disclosure_bundle_complete(paths))
            (paths.assets_dir / "diagram_01.drawio").unlink()
            self.assertFalse(existing_disclosure_bundle_complete(paths))

    def test_mermaid_failure_uses_pillow_after_drawio_failure(self):
        payload = _structured_reliability_payload()
        disclosure = parse_technical_disclosure(payload, payload["标题"])
        module = "patent_summarizer.disclosure.diagrams"
        with tempfile.TemporaryDirectory() as directory, patch(module + ".drawio_runtime_path", return_value=Path(".")), patch(
            module + ".drawio_renderer", side_effect=RuntimeError("模拟 Draw.io 启动失败"),
        ), patch(module + "._mermaid_runtime_path", return_value=Path("mermaid.js")), patch(
            module + "._render_with_mermaid", side_effect=RuntimeError("模拟 Mermaid 导出失败"),
        ) as mermaid:
            paths = write_disclosure_bundle(disclosure, Path(directory))
            self.assertGreaterEqual(mermaid.call_count, 2)
            self.assertEqual(2, len(Document(paths.docx).inline_shapes))
            codes = [item.code for item in paths.diagnostics]
            self.assertEqual(2, codes.count("drawio-fallback"))
            self.assertEqual(2, codes.count("pillow-fallback"))


if __name__ == "__main__":
    unittest.main()
