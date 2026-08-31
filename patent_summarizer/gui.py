from __future__ import annotations

import contextlib
import queue
import re
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from patent_summarizer.cli import DISCLOSURE_CANCELLED_EXIT_CODE, REPORT_CANCELLED_EXIT_CODE, main
from patent_summarizer.discovery import SummaryCoverage, inspect_summary_coverage
from patent_summarizer.disclosure import (
    ConflictPolicy,
    DisclosureTitleReviewResult,
    TitleProposal,
    TitleSelection,
    disclosure_output_path,
    selected_work_items,
    validate_title_selections,
)
from patent_summarizer.progress import ProgressUpdate
from patent_summarizer.report_analysis import ChapterSummaries
from patent_summarizer.report_docx import derive_appendix_output


MODE_SUMMARIZE = "summarize"
MODE_CLASSIFY = "classify"
MODE_ANALYZE = "analyze"
MODE_COMPARE = "compare"
MODE_REPORT = "report"
MODE_DISCLOSURE = "disclosure"

MODE_LABELS = {
    MODE_SUMMARIZE: "批量总结",
    MODE_CLASSIFY: "分类",
    MODE_ANALYZE: "趋势分析",
    MODE_COMPARE: "对比",
    MODE_REPORT: "报告",
    MODE_DISCLOSURE: "技术交底书",
}
REPORT_TEMPLATE_NAME = "汽车底盘领域专利布局分析报告模板.docx"
APP_ICON_NAME = "patent-summarizer.ico"
COPYRIGHT_TEXT = "© 2026 平台及模组开发部 · 线控底盘所 版权所有"


@dataclass(frozen=True)
class PortfolioInput:
    label: str
    root: Path


@dataclass
class PortfolioDraft:
    label_var: ctk.StringVar
    root: Path


@dataclass
class ChapterReviewRequest:
    draft: ChapterSummaries
    event: threading.Event
    result: ChapterSummaries | None = None


@dataclass
class DisclosureTitleReviewRequest:
    proposals: list[TitleProposal]
    output_dir: Path
    event: threading.Event
    result: DisclosureTitleReviewResult | None = None


DISCLOSURE_SOURCE_REPORT = "导入分析报告"
DISCLOSURE_SOURCE_IDEA = "直接输入思路"
DISCLOSURE_IDEA_PLACEHOLDER = """示例（开始输入后自动隐藏）：
拟解决的问题：电动助力转向（EPS）在低附着路面或转矩传感器信号异常时，助力输出容易发生波动，影响转向稳定性和驾驶员手感。
预期结构与流程：由 EPS 控制器融合方向盘转矩、转角、车速和电机状态，识别路面及传感器可信度，动态计算基础助力、补偿助力与降级控制量，并向助力电机输出目标转矩。
输入与输出：输入包括方向盘转矩、方向盘转角、车辆速度、电机电流及故障状态；输出包括目标助力转矩、补偿指令和故障降级指令。
使用场景与约束：适用于低速泊车、高速直行、低附着路面及单路传感器异常场景，并需兼顾实时性、控制平顺性和功能安全要求。"""
MODE_OPTIONS = list(MODE_LABELS.values())
MODERN_APPEARANCE_MODE = "light"
MODERN_COLOR_THEME = "green"
DESKTOP_EDITORIAL_PALETTE = {
    "primary": "#CC785C",
    "primary_hover": "#B86950",
    "primary_pressed": "#A9583E",
    "primary_disabled": "#E6DFD8",
    "ink": "#141413",
    "body": "#3D3D3A",
    "body_strong": "#252523",
    "muted": "#6C6A64",
    "muted_soft": "#8E8B82",
    "canvas": "#FAF9F5",
    "surface": "#FFFFFF",
    "surface_soft": "#F5F0E8",
    "surface_card": "#EFE9DE",
    "surface_input": "#FFFEFA",
    "surface_selected": "#E8E0D2",
    "hairline": "#E6DFD8",
    "hairline_soft": "#EBE6DF",
    "dark": "#181715",
    "dark_elevated": "#252320",
    "dark_soft": "#1F1E1B",
    "on_primary": "#FFFFFF",
    "on_dark": "#FAF9F5",
    "on_dark_soft": "#B8B2AA",
    "success": "#4F9D67",
    "success_surface": "#E4F5E9",
    "warning": "#B88918",
    "warning_surface": "#FFF4D6",
    "error": "#C64545",
    "error_surface": "#FDE8E7",
}


def disclosure_idea_value(text: str, showing_placeholder: bool) -> str:
    """Return only user-entered idea text, never the visual example."""
    return "" if showing_placeholder else text.strip()


def mode_from_label(label: str) -> str:
    for mode, mode_label in MODE_LABELS.items():
        if mode_label == label:
            return mode
    raise ValueError(f"Unknown mode label: {label}")


def mode_button_style(selected: bool) -> dict[str, str]:
    if selected:
        return {
            "fg_color": DESKTOP_EDITORIAL_PALETTE["primary"],
            "hover_color": DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            "border_color": DESKTOP_EDITORIAL_PALETTE["primary"],
            "text_color": DESKTOP_EDITORIAL_PALETTE["on_primary"],
        }
    return {
        "fg_color": DESKTOP_EDITORIAL_PALETTE["surface_card"],
        "hover_color": DESKTOP_EDITORIAL_PALETTE["surface_selected"],
        "border_color": DESKTOP_EDITORIAL_PALETTE["hairline"],
        "text_color": DESKTOP_EDITORIAL_PALETTE["ink"],
    }


def progress_display(update: ProgressUpdate) -> tuple[str, float, str]:
    """Return the user-facing detail, fill value, and color for a progress event."""

    if update.state == "preparing":
        return (update.detail or "正在计算任务规模", 0.0, DESKTOP_EDITORIAL_PALETTE["primary"])
    if update.total is None or update.total == 0:
        value = 1.0 if update.state == "success" else 0.0
        count = "100%" if update.state == "success" else ""
    else:
        value = min(1.0, max(0.0, (update.completed or 0) / update.total))
        count = f"{update.completed or 0} / {update.total}（{round(value * 100)}%）"
    detail = " · ".join(part for part in (update.detail, count) if part)
    color = {
        "success": DESKTOP_EDITORIAL_PALETTE["success"],
        "error": DESKTOP_EDITORIAL_PALETTE["error"],
        "cancelled": DESKTOP_EDITORIAL_PALETTE["warning"],
    }.get(update.state, DESKTOP_EDITORIAL_PALETTE["primary"])
    return (detail, value, color)


def split_review_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]


def chapter_summaries_from_review_texts(texts: dict[str, str]) -> ChapterSummaries:
    return ChapterSummaries(
        split_review_paragraphs(texts.get("第四章", "")),
        split_review_paragraphs(texts.get("第五章", "")),
        split_review_paragraphs(texts.get("第六章", "")),
    )


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def build_cli_argv(mode: str, input_dir: Path, app_dir: Path) -> list[str]:
    input_dir = Path(input_dir)
    app_dir = Path(app_dir)
    env_file = app_dir / ".env"
    if mode == MODE_SUMMARIZE:
        return [
            "run",
            "--input",
            str(input_dir),
            "--recursive",
            "--file-type",
            "docx",
            "--env-file",
            str(env_file),
        ]
    if mode == MODE_CLASSIFY:
        return [
            "classify",
            "--input",
            str(input_dir),
            "--classes",
            str(app_dir / "class.xlsx"),
            "--output",
            str(input_dir / "patent_classification.xlsx"),
            "--recursive",
            "--file-type",
            "docx",
            "--env-file",
            str(env_file),
        ]
    if mode == MODE_ANALYZE:
        return [
            "analyze",
            "--classification",
            str(input_dir / "patent_classification.xlsx"),
            "--output",
            str(input_dir / "patent_trends.xlsx"),
            "--env-file",
            str(env_file),
        ]
    raise ValueError(f"Unknown mode: {mode}")


def build_portfolio_cli_argv(
    mode: str,
    target: PortfolioInput,
    baselines: list[PortfolioInput],
    output: Path,
    app_dir: Path,
    overwrite: bool = False,
) -> list[str]:
    if mode not in {MODE_COMPARE, MODE_REPORT}:
        raise ValueError(f"Unknown portfolio mode: {mode}")
    command = "compare" if mode == MODE_COMPARE else "report"
    argv = [command, "--target", str(target.root), "--target-label", target.label]
    for baseline in baselines:
        argv.extend(["--baseline", f"{baseline.label}={baseline.root}"])
    argv.extend(["--classes", str(Path(app_dir) / "class.xlsx")])
    if mode == MODE_REPORT:
        argv.extend(["--template", str(Path(app_dir) / REPORT_TEMPLATE_NAME)])
    argv.extend(["--output", str(Path(output)), "--recursive", "--env-file", str(Path(app_dir) / ".env")])
    if mode == MODE_REPORT and overwrite:
        argv.append("--overwrite")
    return argv


def build_disclosure_cli_argv(
    source_type: str,
    report_path: Path | None,
    idea: str,
    output_dir: Path,
    app_dir: Path,
) -> list[str]:
    argv = ["disclosure"]
    if source_type == DISCLOSURE_SOURCE_REPORT:
        if report_path is None:
            raise ValueError("Report path is required")
        argv.extend(["--report", str(report_path)])
    elif source_type == DISCLOSURE_SOURCE_IDEA:
        argv.extend(["--idea", idea])
    else:
        raise ValueError(f"Unknown disclosure source type: {source_type}")
    argv.extend(
        [
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(Path(app_dir) / ".env"),
        ]
    )
    return argv


def default_output_path(mode: str, target_root: Path, target_label: str) -> Path:
    safe_label = re.sub(r'[<>:"/\\|?*]', "_", target_label.strip()).rstrip(" .") or "目标组合"
    if mode == MODE_COMPARE:
        return Path(target_root) / f"{safe_label}_专利组合对比.xlsx"
    if mode == MODE_REPORT:
        return Path(target_root) / f"{safe_label}_专利布局分析报告.docx"
    raise ValueError(f"Unknown portfolio mode: {mode}")


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[object]):
        self.output_queue = output_queue

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(("log", text))
        return len(text)

    def flush(self) -> None:
        return None


class PatentSummarizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.output_queue: queue.Queue[object] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False

        self.input_dir_var = ctk.StringVar()
        self.target_dir_var = ctk.StringVar()
        self.target_label_var = ctk.StringVar()
        self.output_file_var = ctk.StringVar()
        self.appendix_file_var = ctk.StringVar()
        self.disclosure_source_var = ctk.StringVar(value=DISCLOSURE_SOURCE_REPORT)
        self.disclosure_report_var = ctk.StringVar()
        self.disclosure_output_dir_var = ctk.StringVar()
        self.disclosure_idea = ""
        self.disclosure_idea_editor: ctk.CTkTextbox | None = None
        self.disclosure_idea_showing_placeholder = False
        self.baselines: list[PortfolioDraft] = []
        self.output_customized = False
        self.mode_var = ctk.StringVar(value=MODE_SUMMARIZE)
        self.status_var = ctk.StringVar(value="请选择专利 DOCX 目录")
        self.progress_stage_var = ctk.StringVar(value="等待运行")
        self.progress_detail_var = ctk.StringVar(value="")
        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        self.progress_state = "idle"
        self.last_progress_update: ProgressUpdate | None = None
        self.output_file_var.trace_add("write", self._sync_appendix_path)
        self.disclosure_report_var.trace_add("write", lambda *_args: self.after_idle(self._refresh_start_state))
        self.disclosure_output_dir_var.trace_add("write", lambda *_args: self.after_idle(self._refresh_start_state))

        self.title("专利 DOCX 处理工具")
        with contextlib.suppress(Exception):
            self.iconbitmap(get_app_dir() / APP_ICON_NAME)
        self.geometry("980x720")
        self.minsize(860, 620)
        self.configure(fg_color=DESKTOP_EDITORIAL_PALETTE["canvas"])

        self._build_layout()
        self.after(120, self._drain_queue)

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="专利 DOCX 处理工具",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=24, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
        )
        title.grid(row=0, column=0, sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="选择目录和任务后开始运行；EXE 会使用同目录下的 .env 和 class.xlsx。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=DESKTOP_EDITORIAL_PALETTE["body"],
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(6, 0))

        panel = ctk.CTkScrollableFrame(
            self,
            corner_radius=18,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface"],
            border_width=1,
            border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            scrollbar_button_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
        )
        panel.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 6))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(4, weight=1)
        self.page_scroll = panel

        copyright_label = ctk.CTkLabel(
            self,
            text=COPYRIGHT_TEXT,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
        )
        copyright_label.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 8))

        mode_row = ctk.CTkFrame(panel, fg_color="transparent")
        mode_row.grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 14))
        mode_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            mode_row,
            text="处理任务",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["body_strong"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.mode_group = ctk.CTkFrame(
            mode_row,
            fg_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            corner_radius=12,
        )
        self.mode_group.grid(row=0, column=1, sticky="w")
        for index, (mode, label) in enumerate(MODE_LABELS.items()):
            button = ctk.CTkButton(
                self.mode_group,
                text=label,
                width=104,
                height=38,
                corner_radius=10,
                border_width=1,
                command=lambda selected_mode=mode: self._select_mode(selected_mode),
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            )
            button.grid(row=0, column=index, sticky="ew", padx=(2 if index else 3, 2), pady=3)
            self.mode_buttons[mode] = button
        self._sync_mode_buttons()

        self.config_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.config_frame.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 14))
        self.config_frame.grid_columnconfigure(0, weight=1)

        action_row = ctk.CTkFrame(panel, fg_color=DESKTOP_EDITORIAL_PALETTE["surface_soft"], corner_radius=14)
        action_row.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 16))
        action_row.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            action_row,
            textvariable=self.status_var,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=DESKTOP_EDITORIAL_PALETTE["primary_pressed"],
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        self.start_button = ctk.CTkButton(
            action_row,
            text="开始运行",
            width=132,
            height=42,
            corner_radius=12,
            command=self._start,
            fg_color=DESKTOP_EDITORIAL_PALETTE["primary"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_primary"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=14, weight="bold"),
        )
        self.start_button.grid(row=0, column=1, sticky="e", padx=12, pady=10)

        progress_row = ctk.CTkFrame(panel, fg_color="transparent")
        progress_row.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 12))
        progress_row.grid_columnconfigure(0, weight=1)
        progress_meta = ctk.CTkFrame(progress_row, fg_color="transparent")
        progress_meta.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        progress_meta.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            progress_meta,
            textvariable=self.progress_stage_var,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["body_strong"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            progress_meta,
            textvariable=self.progress_detail_var,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
        ).grid(row=0, column=1, sticky="e")
        self.progress_bar = ctk.CTkProgressBar(
            progress_row,
            height=9,
            corner_radius=5,
            border_width=0,
            fg_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            progress_color=DESKTOP_EDITORIAL_PALETTE["primary"],
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew")
        self.progress_bar.set(0)

        log_panel = ctk.CTkFrame(panel, corner_radius=14, fg_color=DESKTOP_EDITORIAL_PALETTE["dark"])
        log_panel.grid(row=4, column=0, sticky="nsew", padx=22, pady=(0, 22))
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_panel,
            text="运行日志",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["on_dark"],
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        self.log_text = ctk.CTkTextbox(
            log_panel,
            wrap="word",
            corner_radius=10,
            border_width=0,
            fg_color=DESKTOP_EDITORIAL_PALETTE["dark_soft"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_dark"],
            scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["dark_elevated"],
            scrollbar_button_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_pressed"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self._render_mode_config()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="选择专利 DOCX 目录")
        if selected:
            self.input_dir_var.set(selected)
            self.status_var.set(f"已选择：{selected}")

    def _render_mode_config(self) -> None:
        self._capture_disclosure_idea()
        for child in self.config_frame.winfo_children():
            child.destroy()
        self.disclosure_idea_editor = None
        self.disclosure_idea_showing_placeholder = False
        if self._selected_mode() in {MODE_COMPARE, MODE_REPORT}:
            self._render_portfolio_config()
        elif self._selected_mode() == MODE_DISCLOSURE:
            self._render_disclosure_config()
        else:
            self._render_single_directory_config()
        self.start_button.configure(
            text="生成候选标题" if self._selected_mode() == MODE_DISCLOSURE else "开始运行"
        )
        self.after_idle(self._refresh_start_state)

    def _render_single_directory_config(self) -> None:
        row = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        row.grid_columnconfigure(1, weight=1)
        self._section_label(row, "专利目录").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.input_entry = self._entry(row, self.input_dir_var)
        self.input_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self._button(row, "选择", self._browse, width=96).grid(row=0, column=2)

    def _render_disclosure_config(self) -> None:
        source_section = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        source_section.grid(row=0, column=0, sticky="ew")
        source_section.grid_columnconfigure(0, weight=1)
        self._section_label(source_section, "技术来源").grid(row=0, column=0, sticky="w", pady=(0, 7))
        selector = ctk.CTkSegmentedButton(
            source_section,
            values=[DISCLOSURE_SOURCE_REPORT, DISCLOSURE_SOURCE_IDEA],
            variable=self.disclosure_source_var,
            command=self._disclosure_source_changed,
            height=38,
            corner_radius=10,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface_card"],
            selected_color=DESKTOP_EDITORIAL_PALETTE["primary"],
            selected_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            unselected_color=DESKTOP_EDITORIAL_PALETTE["surface_card"],
            unselected_hover_color=DESKTOP_EDITORIAL_PALETTE["surface_selected"],
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        )
        selector.grid(row=1, column=0, sticky="w")

        input_section = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        input_section.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        input_section.grid_columnconfigure(0, weight=1)
        if self.disclosure_source_var.get() == DISCLOSURE_SOURCE_REPORT:
            self._section_label(input_section, "专利布局分析报告").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 7)
            )
            self._entry(input_section, self.disclosure_report_var).grid(
                row=1, column=0, sticky="ew", padx=(0, 10)
            )
            self._button(
                input_section,
                "选择报告",
                self._choose_disclosure_report,
                width=104,
                secondary=True,
            ).grid(row=1, column=1)
            ctk.CTkLabel(
                input_section,
                text="将读取第七章建议及其对应的第八章组合策略，不重新运行分类或趋势分析。",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
                anchor="w",
            ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        else:
            self._section_label(input_section, "发明构想").grid(
                row=0, column=0, sticky="w", pady=(0, 7)
            )
            ctk.CTkLabel(
                input_section,
                text="可说明拟解决的问题、预期结构或流程、输入输出、使用场景和约束条件。",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
                anchor="w",
            ).grid(row=1, column=0, sticky="ew", pady=(0, 6))
            self.disclosure_idea_editor = ctk.CTkTextbox(
                input_section,
                height=146,
                wrap="word",
                corner_radius=10,
                border_width=1,
                border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
                fg_color=DESKTOP_EDITORIAL_PALETTE["surface_input"],
                text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
                scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
                scrollbar_button_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            )
            self.disclosure_idea_editor.grid(row=2, column=0, sticky="ew")
            self.disclosure_idea_editor.bind(
                "<KeyRelease>",
                lambda _event: self._refresh_start_state(),
            )
            self.disclosure_idea_editor.bind(
                "<FocusIn>",
                self._clear_disclosure_idea_placeholder,
            )
            self.disclosure_idea_editor.bind(
                "<FocusOut>",
                self._restore_disclosure_idea_placeholder,
            )
            if self.disclosure_idea:
                self.disclosure_idea_editor.insert("1.0", self.disclosure_idea)
            else:
                self._show_disclosure_idea_placeholder()

        output_section = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        output_section.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        output_section.grid_columnconfigure(0, weight=1)
        self._section_label(output_section, "技术交底书输出目录").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 7)
        )
        self._entry(output_section, self.disclosure_output_dir_var).grid(
            row=1, column=0, sticky="ew", padx=(0, 10)
        )
        self._button(
            output_section,
            "选择目录",
            self._choose_disclosure_output_dir,
            width=104,
            secondary=True,
        ).grid(row=1, column=1)

    def _capture_disclosure_idea(self) -> None:
        editor = getattr(self, "disclosure_idea_editor", None)
        if editor is None:
            return
        with contextlib.suppress(Exception):
            if editor.winfo_exists():
                self.disclosure_idea = disclosure_idea_value(
                    editor.get("1.0", "end"),
                    self.disclosure_idea_showing_placeholder,
                )

    def _show_disclosure_idea_placeholder(self) -> None:
        editor = getattr(self, "disclosure_idea_editor", None)
        if editor is None:
            return
        editor.delete("1.0", "end")
        editor.insert("1.0", DISCLOSURE_IDEA_PLACEHOLDER)
        editor.configure(text_color=DESKTOP_EDITORIAL_PALETTE["muted_soft"])
        self.disclosure_idea_showing_placeholder = True

    def _clear_disclosure_idea_placeholder(self, _event: object | None = None) -> None:
        if not self.disclosure_idea_showing_placeholder:
            return
        editor = getattr(self, "disclosure_idea_editor", None)
        if editor is None:
            return
        editor.delete("1.0", "end")
        editor.configure(text_color=DESKTOP_EDITORIAL_PALETTE["ink"])
        self.disclosure_idea_showing_placeholder = False
        self.disclosure_idea = ""
        self._refresh_start_state()

    def _restore_disclosure_idea_placeholder(self, _event: object | None = None) -> None:
        editor = getattr(self, "disclosure_idea_editor", None)
        if editor is None or self.disclosure_idea_showing_placeholder:
            return
        if editor.get("1.0", "end").strip():
            self._capture_disclosure_idea()
        else:
            self.disclosure_idea = ""
            self._show_disclosure_idea_placeholder()
        self._refresh_start_state()

    def _disclosure_source_changed(self, _value: str | None = None) -> None:
        self._capture_disclosure_idea()
        if (
            self.disclosure_source_var.get() == DISCLOSURE_SOURCE_REPORT
            and self.disclosure_report_var.get().strip()
        ):
            report = Path(self.disclosure_report_var.get().strip())
            self.disclosure_output_dir_var.set(str(report.parent / "技术交底书"))
        self._render_mode_config()

    def _choose_disclosure_report(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择专利布局分析报告",
            filetypes=[("Word 文档", "*.docx")],
        )
        if not selected:
            return
        report = Path(selected)
        self.disclosure_report_var.set(str(report))
        self.disclosure_output_dir_var.set(str(report.parent / "技术交底书"))
        self._refresh_start_state()

    def _choose_disclosure_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择技术交底书输出目录")
        if selected:
            self.disclosure_output_dir_var.set(selected)
            self._refresh_start_state()

    def _render_portfolio_config(self) -> None:
        target_section = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        target_section.grid(row=0, column=0, sticky="ew")
        target_section.grid_columnconfigure(1, weight=1)
        self._section_label(target_section, "目标组合").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 7))
        self._small_label(target_section, "名称").grid(row=1, column=0, sticky="w")
        self._small_label(target_section, "专利目录").grid(row=1, column=1, sticky="w", padx=(10, 0))
        self._small_label(target_section, "摘要 / 分类状态").grid(row=1, column=2, sticky="w", padx=(10, 0))
        target_name = self._entry(target_section, self.target_label_var, width=150)
        target_name.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        target_name.bind("<FocusOut>", lambda _event: self._target_label_changed())
        target_path = self._entry(target_section, self.target_dir_var)
        target_path.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(4, 0))
        target_status = self._status_button(target_section, self._target_root())
        target_status.grid(row=2, column=2, sticky="ew", padx=(10, 0), pady=(4, 0))
        self._button(target_section, "选择", self._choose_target, width=76).grid(row=2, column=3, padx=(10, 0), pady=(4, 0))

        baseline_header = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        baseline_header.grid(row=1, column=0, sticky="ew", pady=(16, 7))
        baseline_header.grid_columnconfigure(0, weight=1)
        baseline_title = "对比组合" if self._selected_mode() == MODE_COMPARE else "竞争对手"
        self._section_label(baseline_header, baseline_title).grid(row=0, column=0, sticky="w")
        self._button(baseline_header, "＋ 添加目录", self._add_baseline, width=118, secondary=True).grid(row=0, column=1)

        baseline_list = ctk.CTkScrollableFrame(
            self.config_frame,
            height=116,
            corner_radius=12,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface_soft"],
            scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
        )
        baseline_list.grid(row=2, column=0, sticky="ew")
        baseline_list.grid_columnconfigure(1, weight=1)
        if not self.baselines:
            self._small_label(baseline_list, "请至少添加一个对比专利目录").grid(row=0, column=0, padx=12, pady=16, sticky="w")
        for index, draft in enumerate(self.baselines):
            entry = self._entry(baseline_list, draft.label_var, width=150)
            entry.grid(row=index, column=0, sticky="ew", padx=(8, 4), pady=5)
            entry.bind("<FocusOut>", lambda _event: self._refresh_start_state())
            path_entry = ctk.CTkEntry(
                baseline_list,
                height=36,
                corner_radius=9,
                fg_color=DESKTOP_EDITORIAL_PALETTE["surface_input"],
                border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
                text_color=DESKTOP_EDITORIAL_PALETTE["body"],
            )
            path_entry.insert(0, str(draft.root))
            path_entry.configure(state="disabled")
            path_entry.grid(row=index, column=1, sticky="ew", padx=4, pady=5)
            self._status_button(baseline_list, draft.root).grid(row=index, column=2, sticky="ew", padx=4, pady=5)
            self._button(
                baseline_list,
                "移除",
                lambda selected=draft: self._remove_baseline(selected),
                width=62,
                secondary=True,
            ).grid(row=index, column=3, padx=(4, 8), pady=5)

        output_section = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        output_section.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        output_section.grid_columnconfigure(0, weight=1)
        title = "输出文件"
        if self._selected_mode() == MODE_REPORT:
            title += f"　·　内置模板：{REPORT_TEMPLATE_NAME}"
        self._section_label(output_section, title).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 7))
        self._entry(output_section, self.output_file_var).grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self._button(output_section, "另存为", self._choose_output, width=96, secondary=True).grid(row=1, column=1)
        if self._selected_mode() == MODE_REPORT:
            ctk.CTkLabel(
                output_section,
                textvariable=self.appendix_file_var,
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
                anchor="w",
            ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _choose_target(self) -> None:
        selected = filedialog.askdirectory(title="选择目标专利目录")
        if not selected:
            return
        root = Path(selected)
        self.target_dir_var.set(str(root))
        self.target_label_var.set(root.name)
        self.output_customized = False
        self._update_default_output()
        self._render_mode_config()
        self._warn_if_incomplete(root)

    def _add_baseline(self) -> None:
        selected = filedialog.askdirectory(title="选择对比专利目录")
        if not selected:
            return
        root = Path(selected)
        if root == self._target_root() or any(item.root == root for item in self.baselines):
            messagebox.showwarning("目录重复", "该专利目录已经添加。")
            return
        self.baselines.append(PortfolioDraft(ctk.StringVar(value=root.name), root))
        self._render_mode_config()
        self._warn_if_incomplete(root)

    def _remove_baseline(self, draft: PortfolioDraft) -> None:
        self.baselines.remove(draft)
        self._render_mode_config()

    def _target_label_changed(self) -> None:
        if not self.output_customized:
            self._update_default_output()
            self._render_mode_config()
        else:
            self._refresh_start_state()

    def _choose_output(self) -> None:
        mode = self._selected_mode()
        extension = ".xlsx" if mode == MODE_COMPARE else ".docx"
        filetypes = [("Excel 工作簿", "*.xlsx")] if mode == MODE_COMPARE else [("Word 文档", "*.docx")]
        selected = filedialog.asksaveasfilename(
            title="选择输出文件",
            defaultextension=extension,
            filetypes=filetypes,
            initialfile=Path(self.output_file_var.get()).name if self.output_file_var.get() else None,
            initialdir=str(self._target_root()) if self._target_root() else None,
        )
        if selected:
            self.output_file_var.set(selected)
            self.output_customized = True
            self._refresh_start_state()

    def _update_default_output(self) -> None:
        root = self._target_root()
        if root:
            self.output_file_var.set(default_output_path(self._selected_mode(), root, self.target_label_var.get()))

    def _sync_appendix_path(self, *_args) -> None:
        value = self.output_file_var.get().strip()
        appendix = derive_appendix_output(Path(value)) if value else None
        self.appendix_file_var.set(f"附录将保存为：{appendix}" if appendix else "")

    def _target_root(self) -> Path | None:
        value = self.target_dir_var.get().strip()
        return Path(value) if value else None

    def _coverage(self, root: Path | None) -> SummaryCoverage | None:
        if root is None:
            return None
        try:
            return inspect_summary_coverage(root, recursive=True)
        except (FileNotFoundError, NotADirectoryError):
            return None

    def _status_button(self, parent, root: Path | None) -> ctk.CTkButton:
        coverage = self._coverage(root)
        classification = bool(root and (root / "patent_classification.xlsx").exists())
        if coverage is None:
            text, color = "请选择目录", DESKTOP_EDITORIAL_PALETTE["surface_card"]
        elif coverage.document_count == 0:
            text, color = "未发现 DOCX", DESKTOP_EDITORIAL_PALETTE["error_surface"]
        elif coverage.missing_summaries:
            text, color = f"缺少 {len(coverage.missing_summaries)} 篇", DESKTOP_EDITORIAL_PALETTE["error_surface"]
        else:
            class_text = "分类已有" if classification else "分类将自动生成"
            text, color = f"{coverage.summary_count}/{coverage.document_count} 已就绪 · {class_text}", DESKTOP_EDITORIAL_PALETTE["success_surface"]
        return ctk.CTkButton(
            parent,
            text=text,
            height=36,
            corner_radius=9,
            fg_color=color,
            hover_color=color,
            text_color=DESKTOP_EDITORIAL_PALETTE["body"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            command=lambda selected=root: self._show_coverage_details(selected),
        )

    def _show_coverage_details(self, root: Path | None) -> None:
        coverage = self._coverage(root)
        if coverage is None:
            messagebox.showinfo("摘要状态", "请先选择一个存在的专利目录。")
        elif coverage.document_count == 0:
            messagebox.showwarning("未发现专利", f"目录中未发现 DOCX 专利：\n{coverage.root}")
        elif coverage.missing_summaries:
            paths = "\n".join(str(path) for path in coverage.missing_summaries[:20])
            remaining = len(coverage.missing_summaries) - 20
            if remaining > 0:
                paths += f"\n……另有 {remaining} 篇"
            messagebox.showwarning("摘要不完整", f"请先对该目录运行“批量总结”：\n\n{paths}")
        else:
            class_text = "已存在" if (coverage.root / "patent_classification.xlsx").exists() else "缺失，运行时将自动生成"
            messagebox.showinfo("目录已就绪", f"摘要：{coverage.summary_count}/{coverage.document_count}\n分类结果：{class_text}")

    def _warn_if_incomplete(self, root: Path) -> None:
        coverage = self._coverage(root)
        if coverage is None or not coverage.is_ready:
            self._show_coverage_details(root)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self._selected_mode() == MODE_DISCLOSURE:
            self._start_disclosure_task()
            return
        if self._selected_mode() in {MODE_COMPARE, MODE_REPORT}:
            self._start_portfolio_task()
            return
        input_dir = Path(self.input_dir_var.get().strip())
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("目录无效", "请选择一个存在的专利目录。")
            return

        app_dir = get_app_dir()
        env_file = app_dir / ".env"
        if not env_file.exists():
            messagebox.showerror("缺少配置", f"未找到配置文件：{env_file}")
            return
        if self._selected_mode() == MODE_CLASSIFY and not (app_dir / "class.xlsx").exists():
            messagebox.showerror("缺少分类表", f"未找到分类表：{app_dir / 'class.xlsx'}")
            return
        if self._selected_mode() == MODE_ANALYZE and not (input_dir / "patent_classification.xlsx").exists():
            messagebox.showerror("缺少分类结果", f"未找到分类结果：{input_dir / 'patent_classification.xlsx'}")
            return

        argv = build_cli_argv(self._selected_mode(), input_dir, app_dir)
        self._set_running(True)
        self._clear_log()
        self._append_log("运行命令：patent_summarizer " + _format_command(argv) + "\n\n")
        self.worker = threading.Thread(target=self._run_cli, args=(argv,), daemon=True)
        self.worker.start()

    def _start_disclosure_task(self) -> None:
        self._capture_disclosure_idea()
        validation_error = self._disclosure_validation_error()
        if validation_error:
            messagebox.showerror("无法运行", validation_error)
            return
        app_dir = get_app_dir()
        env_file = app_dir / ".env"
        if not env_file.exists():
            messagebox.showerror("缺少配置", f"未找到配置文件：{env_file}")
            return
        source_type = self.disclosure_source_var.get()
        report_path = (
            Path(self.disclosure_report_var.get().strip())
            if source_type == DISCLOSURE_SOURCE_REPORT
            else None
        )
        argv = build_disclosure_cli_argv(
            source_type,
            report_path,
            self.disclosure_idea,
            Path(self.disclosure_output_dir_var.get().strip()),
            app_dir,
        )
        self._set_running(True)
        self._clear_log()
        self._append_log("运行命令：patent_summarizer " + _format_command(argv) + "\n\n")
        self.worker = threading.Thread(target=self._run_cli, args=(argv,), daemon=True)
        self.worker.start()

    def _disclosure_validation_error(self) -> str | None:
        source_type = self.disclosure_source_var.get()
        if source_type == DISCLOSURE_SOURCE_REPORT:
            value = self.disclosure_report_var.get().strip()
            if not value:
                return "请选择一份专利布局分析报告。"
            report = Path(value)
            if not report.exists() or not report.is_file():
                return "请选择一份存在的专利布局分析报告。"
            if report.suffix.lower() != ".docx":
                return "专利布局分析报告必须使用 .docx 扩展名。"
        elif source_type == DISCLOSURE_SOURCE_IDEA:
            if not self.disclosure_idea.strip():
                return "请输入发明构想。"
        else:
            return "请选择技术来源。"
        output = self.disclosure_output_dir_var.get().strip()
        if not output:
            return "请选择技术交底书输出目录。"
        output_path = Path(output)
        if output_path.exists() and not output_path.is_dir():
            return "技术交底书输出路径必须是目录。"
        return None

    def _start_portfolio_task(self) -> None:
        validation_error = self._portfolio_validation_error()
        if validation_error:
            messagebox.showerror("无法运行", validation_error)
            return
        app_dir = get_app_dir()
        required = [("配置文件", app_dir / ".env"), ("分类表", app_dir / "class.xlsx")]
        if self._selected_mode() == MODE_REPORT:
            required.append(("内置报告模板", app_dir / REPORT_TEMPLATE_NAME))
        for label, path in required:
            if not path.exists():
                messagebox.showerror(f"缺少{label}", f"未找到{label}：{path}")
                return

        output = Path(self.output_file_var.get().strip())
        overwrite = False
        outputs = [output]
        if self._selected_mode() == MODE_REPORT:
            outputs.append(derive_appendix_output(output))
        existing = [path for path in outputs if path.exists()]
        if existing:
            paths = "\n".join(str(path) for path in existing)
            if not messagebox.askyesno("确认覆盖", f"以下输出文件已存在，是否覆盖？\n{paths}"):
                return
            overwrite = self._selected_mode() == MODE_REPORT
        target = PortfolioInput(self.target_label_var.get().strip(), self._target_root())
        baselines = [PortfolioInput(item.label_var.get().strip(), item.root) for item in self.baselines]
        argv = build_portfolio_cli_argv(
            self._selected_mode(), target, baselines, output, app_dir, overwrite=overwrite
        )
        self._set_running(True)
        self._clear_log()
        self._append_log("运行命令：patent_summarizer " + _format_command(argv) + "\n\n")
        self.worker = threading.Thread(target=self._run_cli, args=(argv,), daemon=True)
        self.worker.start()

    def _portfolio_validation_error(self) -> str | None:
        root = self._target_root()
        if root is None or not root.exists() or not root.is_dir():
            return "请选择一个存在的目标专利目录。"
        if not self.baselines:
            return "请至少添加一个对比专利目录。"
        labels = [self.target_label_var.get().strip(), *[item.label_var.get().strip() for item in self.baselines]]
        if any(not label for label in labels):
            return "目标和对比组合名称不能为空。"
        if len(labels) != len(set(labels)):
            return "目标和对比组合名称不能重复。"
        for item_root in [root, *[item.root for item in self.baselines]]:
            coverage = self._coverage(item_root)
            if coverage is None:
                return f"专利目录无效：{item_root}"
            if coverage.document_count == 0:
                return f"目录中未发现 DOCX 专利：{item_root}"
            if coverage.missing_summaries:
                return f"{item_root} 缺少 {len(coverage.missing_summaries)} 篇摘要，请先运行“批量总结”。"
        if not self.output_file_var.get().strip():
            return "请选择输出文件。"
        expected = ".xlsx" if self._selected_mode() == MODE_COMPARE else ".docx"
        if Path(self.output_file_var.get().strip()).suffix.lower() != expected:
            return f"输出文件必须使用 {expected} 扩展名。"
        return None

    def _selected_mode(self) -> str:
        return self.mode_var.get()

    def _select_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            raise ValueError(f"Unknown mode: {mode}")
        self.mode_var.set(mode)
        self._sync_mode_buttons()
        if mode in {MODE_COMPARE, MODE_REPORT} and self._target_root() and not self.output_customized:
            self._update_default_output()
        self._render_mode_config()

    def _sync_mode_buttons(self) -> None:
        for mode, button in self.mode_buttons.items():
            button.configure(**mode_button_style(mode == self.mode_var.get()))

    def _run_cli(self, argv: list[str]) -> None:
        writer = QueueWriter(self.output_queue)
        exit_code = 1
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                review = self._request_chapter_review if argv and argv[0] == "report" else None
                disclosure_review = (
                    self._request_disclosure_title_review
                    if argv and argv[0] == "disclosure"
                    else None
                )
                exit_code = main(
                    argv,
                    progress=lambda update: self.output_queue.put(("progress", update)),
                    report_review=review,
                    disclosure_title_review=disclosure_review,
                )
        except BaseException:
            self.output_queue.put(("log", traceback.format_exc()))
        finally:
            self.output_queue.put(("done", exit_code))

    def _request_chapter_review(self, draft: ChapterSummaries) -> ChapterSummaries | None:
        request = ChapterReviewRequest(draft=draft, event=threading.Event())
        self.output_queue.put(("review", request))
        request.event.wait()
        return request.result

    def _request_disclosure_title_review(
        self,
        proposals: list[TitleProposal],
        output_dir: Path,
    ) -> DisclosureTitleReviewResult | None:
        request = DisclosureTitleReviewRequest(
            proposals=proposals,
            output_dir=Path(output_dir),
            event=threading.Event(),
        )
        self.output_queue.put(("disclosure_review", request))
        request.event.wait()
        return request.result

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.output_queue.get_nowait()
                if not isinstance(item, tuple):
                    self._append_log(str(item))
                elif item[0] == "log":
                    self._append_log(str(item[1]))
                elif item[0] == "progress":
                    self._apply_progress(item[1])
                elif item[0] == "review":
                    self._show_chapter_review(item[1])
                elif item[0] == "disclosure_review":
                    self._show_disclosure_title_review(item[1])
                elif item[0] == "done":
                    self._finish(int(item[1]))
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _show_chapter_review(self, request: ChapterReviewRequest) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("复核第四、第五、第六章结论摘要")
        dialog.geometry("900x680")
        dialog.minsize(760, 540)
        dialog.configure(fg_color=DESKTOP_EDITORIAL_PALETTE["canvas"])
        dialog.transient(self)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(dialog, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="人工复核章前结论",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=20, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="可直接修改或删除内容；空白行用于分隔段落，确认后的文本将写入报告。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=DESKTOP_EDITORIAL_PALETTE["body"],
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        tabview = ctk.CTkTabview(
            dialog,
            corner_radius=14,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface"],
            border_width=1,
            border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            segmented_button_fg_color=DESKTOP_EDITORIAL_PALETTE["surface_card"],
            segmented_button_selected_color=DESKTOP_EDITORIAL_PALETTE["primary"],
            segmented_button_selected_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            segmented_button_unselected_color=DESKTOP_EDITORIAL_PALETTE["surface_card"],
            segmented_button_unselected_hover_color=DESKTOP_EDITORIAL_PALETTE["surface_selected"],
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
        )
        tabview.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 14))
        drafts = {
            "第四章": "\n\n".join(request.draft.chapter_four),
            "第五章": "\n\n".join(request.draft.chapter_five),
            "第六章": "\n\n".join(request.draft.chapter_six),
        }
        editors: dict[str, ctk.CTkTextbox] = {}
        for chapter, text in drafts.items():
            tab = tabview.add(chapter)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            editor = ctk.CTkTextbox(
                tab,
                wrap="word",
                corner_radius=10,
                border_width=1,
                border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
                fg_color=DESKTOP_EDITORIAL_PALETTE["surface_input"],
                text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
                scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
                scrollbar_button_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            )
            editor.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            editor.insert("1.0", text)
            editors[chapter] = editor

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 22))
        actions.grid_columnconfigure(0, weight=1)

        def complete(result: ChapterSummaries | None) -> None:
            request.result = result
            with contextlib.suppress(Exception):
                dialog.grab_release()
            dialog.destroy()
            request.event.set()

        def confirm() -> None:
            texts = {chapter: editor.get("1.0", "end") for chapter, editor in editors.items()}
            complete(chapter_summaries_from_review_texts(texts))

        def restore() -> None:
            for chapter, editor in editors.items():
                editor.delete("1.0", "end")
                editor.insert("1.0", drafts[chapter])

        ctk.CTkButton(
            actions,
            text="恢复 AI 初稿",
            width=132,
            height=40,
            corner_radius=10,
            command=restore,
            fg_color=DESKTOP_EDITORIAL_PALETTE["dark_elevated"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["dark_soft"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_dark"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            actions,
            text="取消生成",
            width=112,
            height=40,
            corner_radius=10,
            command=lambda: complete(None),
            fg_color=DESKTOP_EDITORIAL_PALETTE["warning_surface"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["surface_selected"],
            text_color=DESKTOP_EDITORIAL_PALETTE["warning"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
        ).grid(row=0, column=2, padx=(0, 10))
        ctk.CTkButton(
            actions,
            text="确认并生成报告",
            width=154,
            height=42,
            corner_radius=12,
            command=confirm,
            fg_color=DESKTOP_EDITORIAL_PALETTE["primary"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_primary"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        ).grid(row=0, column=3)
        dialog.protocol("WM_DELETE_WINDOW", lambda: complete(None))
        with contextlib.suppress(Exception):
            dialog.grab_set()
        dialog.after(60, dialog.focus_force)

    def _show_disclosure_title_review(self, request: DisclosureTitleReviewRequest) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("确认技术交底书标题")
        dialog.geometry("940x720")
        dialog.minsize(780, 560)
        dialog.configure(fg_color=DESKTOP_EDITORIAL_PALETTE["canvas"])
        dialog.transient(self)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(dialog, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="确认技术交底书标题",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=20, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="每个保留标题会生成一份 DOCX；删除一组的全部标题即可跳过该建议。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
            text_color=DESKTOP_EDITORIAL_PALETTE["body"],
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(
            header,
            text=f"输出目录：{request.output_dir}",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", pady=(4, 0))

        content = ctk.CTkScrollableFrame(
            dialog,
            corner_radius=14,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface"],
            border_width=1,
            border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            scrollbar_button_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            scrollbar_button_hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
        )
        content.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 14))
        content.grid_columnconfigure(0, weight=1)
        rows: dict[str, list[tuple[ctk.CTkFrame, ctk.StringVar]]] = {}

        def add_title(rows_frame, source_id: str, value: str = "") -> None:
            row = ctk.CTkFrame(rows_frame, fg_color="transparent")
            row.grid(row=len(rows[source_id]), column=0, sticky="ew", pady=4)
            row.grid_columnconfigure(0, weight=1)
            variable = ctk.StringVar(value=value)
            entry = self._entry(row, variable)
            entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
            record = (row, variable)

            def remove() -> None:
                if record in rows[source_id]:
                    rows[source_id].remove(record)
                row.destroy()
                for row_index, (remaining_row, _variable) in enumerate(rows[source_id]):
                    remaining_row.grid_configure(row=row_index)

            self._button(row, "删除", remove, width=68, secondary=True).grid(row=0, column=1)
            rows[source_id].append(record)

        def render_groups() -> None:
            for child in content.winfo_children():
                child.destroy()
            rows.clear()
            for index, proposal in enumerate(request.proposals):
                source = proposal.source
                card = ctk.CTkFrame(
                    content,
                    corner_radius=12,
                    fg_color=DESKTOP_EDITORIAL_PALETTE["surface_soft"],
                    border_width=1,
                    border_color=DESKTOP_EDITORIAL_PALETTE["hairline_soft"],
                )
                card.grid(row=index, column=0, sticky="ew", padx=8, pady=(8, 4))
                card.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(
                    card,
                    text=f"{source.source_id}　{source.label}",
                    font=ctk.CTkFont(family="Microsoft YaHei UI", size=14, weight="bold"),
                    text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
                    anchor="w",
                ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
                ctk.CTkLabel(
                    card,
                    text=source.summary,
                    font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
                    text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
                    justify="left",
                    anchor="w",
                    wraplength=790,
                ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
                rows_frame = ctk.CTkFrame(card, fg_color="transparent")
                rows_frame.grid(row=2, column=0, sticky="ew", padx=14)
                rows_frame.grid_columnconfigure(0, weight=1)
                rows[source.source_id] = []
                for title in proposal.titles:
                    add_title(rows_frame, source.source_id, title)
                self._button(
                    card,
                    "＋ 增加标题",
                    lambda frame=rows_frame, source_id=source.source_id: add_title(frame, source_id),
                    width=112,
                    secondary=True,
                ).grid(row=3, column=0, sticky="w", padx=14, pady=(6, 12))

        render_groups()

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 22))
        actions.grid_columnconfigure(0, weight=1)

        def complete(result: DisclosureTitleReviewResult | None) -> None:
            request.result = result
            with contextlib.suppress(Exception):
                dialog.grab_release()
            dialog.destroy()
            request.event.set()

        def confirm() -> None:
            selections = [
                TitleSelection(
                    proposal.source.source_id,
                    [variable.get() for _row, variable in rows[proposal.source.source_id]],
                )
                for proposal in request.proposals
            ]
            sources = [proposal.source for proposal in request.proposals]
            try:
                selections = validate_title_selections(selections, sources)
                work_items = selected_work_items(selections, sources)
            except ValueError as exc:
                messagebox.showerror("标题无效", str(exc), parent=dialog)
                return
            if not work_items:
                if not messagebox.askyesno(
                    "未保留标题",
                    "当前没有保留任何标题，是否结束本次任务？",
                    parent=dialog,
                ):
                    return
                complete(DisclosureTitleReviewResult(selections, ConflictPolicy.ERROR))
                return
            conflicts = [
                disclosure_output_path(request.output_dir, title)
                for _source, title in work_items
                if disclosure_output_path(request.output_dir, title).exists()
            ]
            policy = ConflictPolicy.ERROR
            if conflicts:
                displayed = "\n".join(str(path) for path in conflicts[:20])
                if len(conflicts) > 20:
                    displayed += f"\n……另有 {len(conflicts) - 20} 份"
                choice = messagebox.askyesnocancel(
                    "同名文件已存在",
                    f"以下文件已经存在：\n\n{displayed}\n\n"
                    "选择“是”将核验后覆盖；选择“否”将跳过已有文件；选择“取消”返回编辑标题。",
                    parent=dialog,
                )
                if choice is None:
                    return
                policy = ConflictPolicy.OVERWRITE if choice else ConflictPolicy.SKIP
            complete(DisclosureTitleReviewResult(selections, policy))

        ctk.CTkButton(
            actions,
            text="恢复 AI 初稿",
            width=132,
            height=40,
            corner_radius=10,
            command=render_groups,
            fg_color=DESKTOP_EDITORIAL_PALETTE["dark_elevated"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["dark_soft"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_dark"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            actions,
            text="取消生成",
            width=112,
            height=40,
            corner_radius=10,
            command=lambda: complete(None),
            fg_color=DESKTOP_EDITORIAL_PALETTE["warning_surface"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["surface_selected"],
            text_color=DESKTOP_EDITORIAL_PALETTE["warning"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
        ).grid(row=0, column=2, padx=(0, 10))
        ctk.CTkButton(
            actions,
            text="确认并开始生成",
            width=160,
            height=42,
            corner_radius=12,
            command=confirm,
            fg_color=DESKTOP_EDITORIAL_PALETTE["primary"],
            hover_color=DESKTOP_EDITORIAL_PALETTE["primary_hover"],
            text_color=DESKTOP_EDITORIAL_PALETTE["on_primary"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        ).grid(row=0, column=3)
        dialog.protocol("WM_DELETE_WINDOW", lambda: complete(None))
        with contextlib.suppress(Exception):
            dialog.grab_set()
        dialog.after(60, dialog.focus_force)

    def _append_log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def _clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def _set_running(self, running: bool) -> None:
        self.running = running
        for button in self.mode_buttons.values():
            button.configure(state="disabled" if running else "normal")
        if running:
            self.start_button.configure(state="disabled")
            self.status_var.set("正在运行，请等待...")
            self._set_progress_preparing("正在准备任务", "正在计算任务规模")
        else:
            self._refresh_start_state()

    def _refresh_start_state(self) -> None:
        if not hasattr(self, "start_button") or self.running:
            return
        if self._selected_mode() == MODE_DISCLOSURE:
            self._capture_disclosure_idea()
            error = self._disclosure_validation_error()
            self.start_button.configure(state="disabled" if error else "normal")
            self.status_var.set(error or "技术来源已就绪，可以生成候选标题")
        elif self._selected_mode() in {MODE_COMPARE, MODE_REPORT}:
            error = self._portfolio_validation_error()
            self.start_button.configure(state="disabled" if error else "normal")
            self.status_var.set(error or "所有专利目录已就绪，可以开始运行")
        else:
            self.start_button.configure(state="normal")
            self.status_var.set("请选择专利 DOCX 目录")

    def _finish(self, exit_code: int) -> None:
        finished_mode = self._selected_mode()
        self._set_running(False)
        if exit_code in {REPORT_CANCELLED_EXIT_CODE, DISCLOSURE_CANCELLED_EXIT_CODE}:
            previous = self.last_progress_update
            if self.progress_state != "cancelled":
                is_disclosure = finished_mode == MODE_DISCLOSURE
                self._apply_progress(
                    ProgressUpdate(
                        mode=finished_mode,
                        state="cancelled",
                        stage="技术交底书生成已取消" if is_disclosure else "报告生成已取消",
                        detail="未生成正文或 DOCX" if is_disclosure else "未写入正文和附录",
                        completed=previous.completed if previous else None,
                        total=previous.total if previous else None,
                    )
                )
            if finished_mode == MODE_DISCLOSURE:
                self.status_var.set("已取消，未生成技术交底书")
                self._append_log("\n已取消，未生成正文或 DOCX。\n")
            else:
                self.status_var.set("已取消，未生成报告")
                self._append_log("\n已取消，未写入正文和附录。\n")
        elif exit_code == 0:
            if self.progress_state != "success":
                self._apply_progress(
                    ProgressUpdate(
                        mode=self._selected_mode(),
                        state="success",
                        stage="运行完成",
                        detail="任务已完成",
                        completed=1,
                        total=1,
                    )
                )
            self.status_var.set("运行完成")
            self._append_log("\n完成。\n")
            if finished_mode == MODE_REPORT and self.output_file_var.get().strip():
                if messagebox.askyesno(
                    "报告生成完成",
                    "是否继续基于本报告生成技术交底书？\n\n"
                    "选择“是”只会切换并带入报告，不会立即调用 LLM。",
                ):
                    report = Path(self.output_file_var.get().strip())
                    self.disclosure_source_var.set(DISCLOSURE_SOURCE_REPORT)
                    self.disclosure_report_var.set(str(report))
                    self.disclosure_output_dir_var.set(str(report.parent / "技术交底书"))
                    self._select_mode(MODE_DISCLOSURE)
        else:
            previous = self.last_progress_update
            self._apply_progress(
                ProgressUpdate(
                    mode=self._selected_mode(),
                    state="error",
                    stage="运行异常结束",
                    detail=f"退出码 {exit_code}",
                    completed=previous.completed if previous else None,
                    total=previous.total if previous else None,
                )
            )
            self.status_var.set(f"运行结束，退出码 {exit_code}")
            self._append_log(f"\n运行结束，退出码 {exit_code}。\n")

    def _set_progress_preparing(self, stage: str, detail: str) -> None:
        self._apply_progress(
            ProgressUpdate(
                mode=self._selected_mode(),
                state="preparing",
                stage=stage,
                detail=detail,
            )
        )

    def _apply_progress(self, update: ProgressUpdate) -> None:
        self.last_progress_update = update
        self.progress_state = update.state
        self.progress_stage_var.set(update.stage)
        detail, value, color = progress_display(update)
        self.progress_detail_var.set(detail)
        if update.state == "preparing":
            self.progress_bar.stop()
            self.progress_bar.configure(mode="indeterminate", progress_color=color)
            self.progress_bar.set(0)
            self.progress_bar.start()
            return
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", progress_color=color)
        self.progress_bar.set(value)

    def _entry(self, parent, variable: ctk.StringVar, width: int | None = None) -> ctk.CTkEntry:
        return ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=width or 120,
            height=38,
            corner_radius=10,
            border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface_input"],
            text_color=DESKTOP_EDITORIAL_PALETTE["ink"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )

    def _button(self, parent, text: str, command, width: int, secondary: bool = False) -> ctk.CTkButton:
        color = DESKTOP_EDITORIAL_PALETTE["surface_card"] if secondary else DESKTOP_EDITORIAL_PALETTE["dark_elevated"]
        text_color = DESKTOP_EDITORIAL_PALETTE["ink"] if secondary else DESKTOP_EDITORIAL_PALETTE["on_dark"]
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=38,
            corner_radius=10,
            command=command,
            fg_color=color,
            hover_color=DESKTOP_EDITORIAL_PALETTE["surface_selected"] if secondary else DESKTOP_EDITORIAL_PALETTE["dark_soft"],
            text_color=text_color,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12, weight="bold"),
        )

    def _section_label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=DESKTOP_EDITORIAL_PALETTE["body_strong"],
        )

    def _small_label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            text_color=DESKTOP_EDITORIAL_PALETTE["muted"],
        )


def _format_command(argv: list[str]) -> str:
    parts = []
    redact_next = False
    for arg in argv:
        if redact_next:
            parts.append('"<已省略直接思路正文>"')
            redact_next = False
            continue
        if arg == "--idea":
            parts.append(arg)
            redact_next = True
            continue
        if " " in arg:
            parts.append(f'"{arg}"')
        else:
            parts.append(arg)
    return " ".join(parts)


def launch() -> None:
    ctk.set_appearance_mode(MODERN_APPEARANCE_MODE)
    ctk.set_default_color_theme(MODERN_COLOR_THEME)
    app = PatentSummarizerApp()
    app.mainloop()


if __name__ == "__main__":
    launch()
