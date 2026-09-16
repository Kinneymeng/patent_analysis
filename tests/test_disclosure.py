import json
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

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
        ("一", "技术背景与现有方案", "现有控制方案依赖单一信号，扰动条件下容易产生误判。"),
        ("二", "需要解决的技术问题", "方案需要在传感器短时失效时继续稳定计算目标阻尼。"),
        ("三", "技术方案详细说明", "姿态融合模块的输入为车速，输出为路面状态；阻尼计算模块输出目标阻尼。信号异常或超过边界时进入保持流程。"),
        ("四", "技术效果与适用边界", "多信号判断可降低阻尼跳变，属于待验证的预期效果。"),
        ("五", "技术关键点", "姿态融合模块先计算信号可信度，再选择阻尼标定区间。"),
        ("六", "实施例与验证方法", "正常用例输入有效车速并检查目标阻尼；异常用例断开传感器并检查保持输出。"),
    )
    return {
        "标题": title,
        "摘要": "本方案采集车辆速度和车身垂向加速度，计算信号可信度与路面状态，再根据路面状态计算目标悬架阻尼。系统在信号缺失、超时或越界时进入保持或降级流程，并记录触发原因。工程人员可以按照模块输入输出、计算顺序、参数设置和验证用例完成控制器实现与台架复现。",
        "章节": [
            {"编号": number, "标题": section_title, "段落": [paragraph], "小节": []}
            for number, section_title, paragraph in section_titles
        ],
        "图示": [
            {"章节": "三", "类型": "系统架构图", "标题": "图1 系统架构", "mermaid": "flowchart TD\nA[车辆信号] --> B[姿态融合模块]\nB --> C[阻尼计算模块]"},
            {"章节": "三", "类型": "流程图", "标题": "图2 控制流程", "mermaid": "flowchart TD\nS1[S1 采集信号] --> S2[S2 检查有效性]\nS2 --> S3[S3 计算目标阻尼]"},
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
            ):
                self.assertEqual(0, main(argv))
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
        payload["章节"][2]["段落"] = [
            "控制器接收输入并产生输出，信号异常或超过边界时保持上一周期结果。"
        ]
        payload["章节"][4]["段落"] = ["控制器执行状态判断和阻尼计算。"]
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
                for number in ("一", "二", "三", "四", "五", "六"):
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

        self.assertEqual(6, len(result.sections))
        self.assertFalse(any("当前只撰写：一、" in prompt for prompt in second.calls))
        self.assertFalse(any("当前只撰写：二、" in prompt for prompt in second.calls))
        self.assertTrue(any("当前只撰写：三、" in prompt for prompt in second.calls))


if __name__ == "__main__":
    unittest.main()
