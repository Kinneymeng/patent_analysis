import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from patent_summarizer.metadata import infer_patent_year, normalize_patent_date, parse_bibliographic_metadata
from patent_summarizer.trend_analysis import TrendAnalyzer, TrendPatentRecord, build_trend_groups, load_classification_workbook, trend_work_units
from patent_summarizer.trend_excel_export import write_trend_workbook


def make_record(
    filename="CN202411111111.pdf",
    patent_name="车辆控制方法",
    first_level="车辆运动控制内核",
    second_level="横向融合控制",
    confidence=0.8,
    review_status="通过",
    year="2024",
    keywords="- EPS\n- 摩擦补偿",
):
    return TrendPatentRecord(
        filename=filename,
        patent_name=patent_name,
        source_path=f"D:/patents/{filename}",
        summary_path=f"D:/patents/{Path(filename).stem}.md",
        primary_first_level=first_level,
        primary_second_level=second_level,
        primary_confidence=confidence,
        candidate_summary="候选",
        review_status=review_status,
        reason="理由",
        metadata={"申请号": filename.removesuffix(".pdf"), "申请日": f"{year}-01-02"},
        sections={
            "背景问题": "标定复杂。",
            "核心方案": "通过分段控制优化转向。",
            "关键创新点": "引入摩擦补偿。",
            "应用场景": "EPS。",
            "潜在分类关键词": keywords,
            "摘要全文": "全文",
        },
        inferred_year=year,
    )


class MetadataTests(unittest.TestCase):
    def test_parses_bibliographic_metadata_and_dates(self):
        section = """- 申请号：CN202411111111
- 申请日：2024年1月2日
- 公开（公告）号：CN118888888A
- 公开（公告）日：2024-03-05
- 申请人：示例公司
- 发明人：张三、李四
- IPC/CPC：B62D 5/04
"""

        metadata = parse_bibliographic_metadata(section)

        self.assertEqual(metadata["申请号"], "CN202411111111")
        self.assertEqual(metadata["申请人"], "示例公司")
        self.assertEqual(metadata["IPC/CPC"], "B62D 5/04")
        self.assertEqual(normalize_patent_date(metadata["申请日"]), "2024-01-02")
        self.assertEqual(normalize_patent_date("20240102"), "2024-01-02")
        self.assertEqual(normalize_patent_date("2024"), "2024")
        self.assertEqual(normalize_patent_date("not a date"), "")

    def test_infers_year_by_priority(self):
        self.assertEqual(
            infer_patent_year({"申请日": "2024年1月2日", "公开（公告）日": "2023-01-01"}, "CN202211111111.pdf"),
            "2024",
        )
        self.assertEqual(
            infer_patent_year({"申请日": "", "公开（公告）日": "2023-01-01", "申请号": "CN202211111111"}),
            "2023",
        )
        self.assertEqual(
            infer_patent_year({"申请日": "", "公开（公告）日": "", "申请号": "CN202211111111"}),
            "2022",
        )
        self.assertEqual(infer_patent_year({}, "CN202011111111.pdf"), "2020")
        self.assertEqual(infer_patent_year({}, "unknown.pdf"), "未知")


class ClassificationWorkbookLoaderTests(unittest.TestCase):
    def test_loads_classification_workbook_by_header_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "classification.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "分类结果"
            ws.append(
                [
                    "主二级分类",
                    "文件名",
                    "著录信息",
                    "主一级分类",
                    "专利名称",
                    "主分类置信度",
                    "复核状态",
                    "潜在分类关键词",
                ]
            )
            ws.append(
                [
                    "横向融合控制",
                    "CN202411111111.pdf",
                    "- 申请号：CN202411111111\n- 申请日：2024年1月2日",
                    "车辆运动控制内核",
                    "车辆控制方法",
                    0.88,
                    "通过",
                    "- EPS",
                ]
            )
            wb.save(path)

            records = load_classification_workbook(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].primary_second_level, "横向融合控制")
        self.assertEqual(records[0].metadata["申请号"], "CN202411111111")
        self.assertEqual(records[0].inferred_year, "2024")


class TrendAggregationTests(unittest.TestCase):
    def test_builds_group_statistics(self):
        records = [
            make_record(filename="CN202411111111.pdf", confidence=0.8, year="2024", keywords="- EPS\n- 摩擦补偿"),
            make_record(
                filename="CN202311111111.pdf",
                patent_name="助力曲线方法",
                confidence=0.6,
                review_status="需人工复核",
                year="2023",
                keywords="- EPS\n- 正弦函数",
            ),
            make_record(filename="unknown.pdf", second_level="", year="未知"),
        ]

        groups = build_trend_groups(records)

        self.assertEqual(len(groups), 1)
        stats, grouped_records = groups[0]
        self.assertEqual(len(grouped_records), 2)
        self.assertEqual(stats.patent_count, 2)
        self.assertEqual(stats.year_counts, {"2023": 1, "2024": 1})
        self.assertEqual(stats.year_range, "2023-2024")
        self.assertEqual(stats.average_confidence, 0.7)
        self.assertEqual(stats.review_count, 1)
        self.assertEqual(stats.top_keywords[0], "EPS")


class FakeTrendClient:
    def __init__(self):
        self.calls = []

    def chat(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return {
            "发展现状": "该标签围绕 EPS 控制持续布局。",
            "发展阶段": "应用深化期",
            "主要技术路线": ["摩擦补偿", "助力曲线优化"],
            "热点方向": "横向控制与驾驶员手感",
            "机会与空白": "可加强多源感知闭环。",
            "风险/不确定性": "样本量有限。",
            "代表专利": "CN202411111111.pdf",
            "证据局限": "部分著录项缺失。",
        }


class TrendAnalyzerTests(unittest.TestCase):
    def test_analyzes_and_writes_trend_workbook(self):
        client = FakeTrendClient()
        analyzer = TrendAnalyzer(client, batch_size=40)
        result = analyzer.analyze([make_record()])

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "trends.xlsx"
            write_trend_workbook(result, output)
            wb = load_workbook(output)
            overview = wb["标签总览"]
            report = wb["趋势报告"]

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(wb.sheetnames, ["标签总览", "年度趋势", "趋势报告", "专利明细"])
        self.assertEqual(overview["A2"].value, "车辆运动控制内核")
        self.assertEqual(overview["I2"].value, "该标签围绕 EPS 控制持续布局。")
        self.assertEqual(report["F2"].value, "摩擦补偿\n助力曲线优化")

    def test_batches_large_groups_before_merging(self):
        client = FakeTrendClient()
        analyzer = TrendAnalyzer(client, batch_size=40)
        records = [make_record(filename=f"CN2024{index:08d}.pdf") for index in range(41)]
        operations = []

        result = analyzer.analyze(records, on_operation=lambda detail, started: operations.append((detail, started)))

        self.assertEqual(len(client.calls), 3)
        self.assertEqual(trend_work_units(records, batch_size=40), 3)
        self.assertEqual([started for _detail, started in operations], [True, False, True, False, True, False])
        self.assertEqual(result.reports[0].stats.patent_count, 41)


if __name__ == "__main__":
    unittest.main()
