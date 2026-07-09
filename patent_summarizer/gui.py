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

from patent_summarizer.cli import main
from patent_summarizer.discovery import SummaryCoverage, inspect_summary_coverage


MODE_SUMMARIZE = "summarize"
MODE_CLASSIFY = "classify"
MODE_ANALYZE = "analyze"
MODE_COMPARE = "compare"
MODE_REPORT = "report"

MODE_LABELS = {
    MODE_SUMMARIZE: "批量总结",
    MODE_CLASSIFY: "分类",
    MODE_ANALYZE: "趋势分析",
    MODE_COMPARE: "对比",
    MODE_REPORT: "报告",
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
            self.output_queue.put(text)
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
        self.baselines: list[PortfolioDraft] = []
        self.output_customized = False
        self.mode_var = ctk.StringVar(value=MODE_SUMMARIZE)
        self.status_var = ctk.StringVar(value="请选择专利 DOCX 目录")
        self.mode_buttons: dict[str, ctk.CTkButton] = {}

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

        panel = ctk.CTkFrame(
            self,
            corner_radius=18,
            fg_color=DESKTOP_EDITORIAL_PALETTE["surface"],
            border_width=1,
            border_color=DESKTOP_EDITORIAL_PALETTE["hairline"],
        )
        panel.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 6))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

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

        log_panel = ctk.CTkFrame(panel, corner_radius=14, fg_color=DESKTOP_EDITORIAL_PALETTE["dark"])
        log_panel.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 22))
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
        for child in self.config_frame.winfo_children():
            child.destroy()
        if self._selected_mode() in {MODE_COMPARE, MODE_REPORT}:
            self._render_portfolio_config()
        else:
            self._render_single_directory_config()
        self.after_idle(self._refresh_start_state)

    def _render_single_directory_config(self) -> None:
        row = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        row.grid_columnconfigure(1, weight=1)
        self._section_label(row, "专利目录").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.input_entry = self._entry(row, self.input_dir_var)
        self.input_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self._button(row, "选择", self._browse, width=96).grid(row=0, column=2)

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
        if output.exists():
            if not messagebox.askyesno("确认覆盖", f"输出文件已存在，是否覆盖？\n{output}"):
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
                exit_code = main(argv)
        except BaseException:
            self.output_queue.put(traceback.format_exc())
        finally:
            self.output_queue.put(("done", exit_code))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.output_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "done":
                    self._finish(int(item[1]))
                else:
                    self._append_log(str(item))
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

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
        else:
            self._refresh_start_state()

    def _refresh_start_state(self) -> None:
        if not hasattr(self, "start_button") or self.running:
            return
        if self._selected_mode() in {MODE_COMPARE, MODE_REPORT}:
            error = self._portfolio_validation_error()
            self.start_button.configure(state="disabled" if error else "normal")
            self.status_var.set(error or "所有专利目录已就绪，可以开始运行")
        else:
            self.start_button.configure(state="normal")
            self.status_var.set("请选择专利 DOCX 目录")

    def _finish(self, exit_code: int) -> None:
        self._set_running(False)
        if exit_code == 0:
            self.status_var.set("运行完成")
            self._append_log("\n完成。\n")
        else:
            self.status_var.set(f"运行结束，退出码 {exit_code}")
            self._append_log(f"\n运行结束，退出码 {exit_code}。\n")

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
    for arg in argv:
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
