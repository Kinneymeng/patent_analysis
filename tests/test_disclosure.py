import json
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from patent_summarizer.cli import build_parser, main
from patent_summarizer.config import AppConfig
from patent_summarizer.disclosure import (
    DisclosureAnalyzer,
    DisclosureSourceItem,
    TechnicalDisclosure,
    TitleProposal,
    TitleSelection,
    disclosure_output_path,
    load_idea_source,
    load_report_sources,
    load_title_selections,
    parse_technical_disclosure,
    parse_title_proposal,
    selected_work_items,
    validate_disclosure_title,
    validate_title_selections,
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

    def test_validates_reviewed_content_shape_length_and_claim_language(self):
        parsed = parse_technical_disclosure(_valid_payload(), "一种冗余制动控制方法")
        self.assertEqual(6, len(parsed.content_paragraphs))

        invalid = _valid_payload()
        invalid["摘要"] = "第一段\n第二段"
        with self.assertRaisesRegex(ValueError, "单一自然段"):
            parse_technical_disclosure(invalid, invalid["标题"])

        invalid = _valid_payload()
        invalid["发明内容"] = ["短内容"] * 6
        with self.assertRaisesRegex(ValueError, "3000 至 5000"):
            parse_technical_disclosure(invalid, invalid["标题"])

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

    def test_content_length_becomes_warning_after_one_retry_for_both_sources(self):
        class SequenceClient:
            def __init__(self, responses):
                self.responses = list(responses)
                self.prompts = []

            def chat(self, system_prompt, user_prompt):
                self.prompts.append(user_prompt)
                return self.responses.pop(0)

        short_payload = _valid_payload()
        short_payload["发明内容"] = ["内容较短但结构完整" for _ in range(6)]

        direct_client = SequenceClient([short_payload, short_payload])
        direct_warnings = []
        direct_source = DisclosureSourceItem("manual", "用户直接思路", "摘要", "上下文")
        direct_result = DisclosureAnalyzer(
            direct_client,
            on_validation_warning=lambda stage, error: direct_warnings.append((stage, error)),
        ).draft(direct_source, short_payload["标题"])

        report_client = SequenceClient([short_payload, short_payload])
        report_warnings = []
        report_source = DisclosureSourceItem("7.1", "报告建议", "摘要", "报告上下文")
        valid_draft = parse_technical_disclosure(_valid_payload(), short_payload["标题"])
        report_result = DisclosureAnalyzer(
            report_client,
            on_validation_warning=lambda stage, error: report_warnings.append((stage, error)),
        ).review(report_source, valid_draft)

        self.assertEqual(2, len(direct_client.prompts))
        self.assertEqual(2, len(report_client.prompts))
        self.assertEqual(short_payload["发明内容"], direct_result.content_paragraphs)
        self.assertEqual(short_payload["发明内容"], report_result.content_paragraphs)
        self.assertIn("初稿", direct_warnings[0][0])
        self.assertIn("独立审校", report_warnings[0][0])
        self.assertIn("实际为", direct_warnings[0][1])

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

    def test_cli_writes_short_content_with_warning_after_repair(self):
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

            self.assertEqual(0, exit_code)
            self.assertTrue((output / f"{title}_技术交底书.docx").exists())
            self.assertIn("WARNING\t", stdout.getvalue())
            self.assertNotIn("FAIL\t", stdout.getvalue())

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
                "patent_summarizer.cli.DisclosureAnalyzer.draft",
                return_value=disclosure,
            ), patch(
                "patent_summarizer.cli.DisclosureAnalyzer.review",
                return_value=disclosure,
            ):
                self.assertEqual(0, main([*argv, "--overwrite"]))
            validate_technical_disclosure_docx(generated, disclosure)


if __name__ == "__main__":
    unittest.main()
