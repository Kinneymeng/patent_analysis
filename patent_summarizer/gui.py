from __future__ import annotations

import contextlib
import queue
import sys
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from patent_summarizer.cli import main


MODE_SUMMARIZE = "summarize"
MODE_CLASSIFY = "classify"
MODE_ANALYZE = "analyze"

MODE_LABELS = {
    MODE_SUMMARIZE: "批量总结",
    MODE_CLASSIFY: "分类",
    MODE_ANALYZE: "趋势分析",
}
MODE_OPTIONS = list(MODE_LABELS.values())
MODERN_APPEARANCE_MODE = "light"
MODERN_COLOR_THEME = "green"
GOOGLE_MATERIAL_PALETTE = {
    "primary": "#6750A4",
    "primary_hover": "#5B4596",
    "primary_container": "#EADDFF",
    "secondary": "#625B71",
    "secondary_container": "#E8DEF8",
    "tertiary": "#7D5260",
    "tertiary_hover": "#6F4654",
    "tertiary_container": "#FFD8E4",
    "background": "#FFFBFE",
    "surface": "#FFFFFF",
    "surface_dim": "#F7F2FA",
    "surface_variant": "#E7E0EC",
    "outline": "#79747E",
    "outline_variant": "#CAC4D0",
    "on_primary": "#FFFFFF",
    "on_surface": "#1C1B1F",
    "on_surface_variant": "#49454F",
}


def mode_from_label(label: str) -> str:
    for mode, mode_label in MODE_LABELS.items():
        if mode_label == label:
            return mode
    raise ValueError(f"Unknown mode label: {label}")


def mode_button_style(selected: bool) -> dict[str, str]:
    if selected:
        return {
            "fg_color": GOOGLE_MATERIAL_PALETTE["primary"],
            "hover_color": GOOGLE_MATERIAL_PALETTE["primary_hover"],
            "border_color": GOOGLE_MATERIAL_PALETTE["primary"],
            "text_color": GOOGLE_MATERIAL_PALETTE["on_primary"],
        }
    return {
        "fg_color": GOOGLE_MATERIAL_PALETTE["surface_variant"],
        "hover_color": GOOGLE_MATERIAL_PALETTE["secondary_container"],
        "border_color": GOOGLE_MATERIAL_PALETTE["outline_variant"],
        "text_color": GOOGLE_MATERIAL_PALETTE["on_surface"],
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

        self.input_dir_var = ctk.StringVar()
        self.mode_var = ctk.StringVar(value=MODE_SUMMARIZE)
        self.status_var = ctk.StringVar(value="请选择专利 DOCX 目录")
        self.mode_buttons: dict[str, ctk.CTkButton] = {}

        self.title("专利 DOCX 处理工具")
        self.geometry("900x640")
        self.minsize(780, 560)
        self.configure(fg_color=GOOGLE_MATERIAL_PALETTE["surface_dim"])

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
            text_color=GOOGLE_MATERIAL_PALETTE["on_surface"],
        )
        title.grid(row=0, column=0, sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="选择目录和任务后开始运行；EXE 会使用同目录下的 .env 和 class.xlsx。",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=GOOGLE_MATERIAL_PALETTE["on_surface_variant"],
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(6, 0))

        panel = ctk.CTkFrame(
            self,
            corner_radius=18,
            fg_color=GOOGLE_MATERIAL_PALETTE["surface"],
            border_width=1,
            border_color=GOOGLE_MATERIAL_PALETTE["outline_variant"],
        )
        panel.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 18))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 14))
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            controls,
            text="专利目录",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=GOOGLE_MATERIAL_PALETTE["on_surface_variant"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.input_entry = ctk.CTkEntry(
            controls,
            textvariable=self.input_dir_var,
            height=38,
            corner_radius=10,
            border_color=GOOGLE_MATERIAL_PALETTE["outline_variant"],
            fg_color=GOOGLE_MATERIAL_PALETTE["background"],
            text_color=GOOGLE_MATERIAL_PALETTE["on_surface"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
        )
        self.input_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ctk.CTkButton(
            controls,
            text="选择",
            width=96,
            height=38,
            corner_radius=10,
            command=self._browse,
            fg_color=GOOGLE_MATERIAL_PALETTE["secondary"],
            hover_color=GOOGLE_MATERIAL_PALETTE["primary"],
            text_color=GOOGLE_MATERIAL_PALETTE["on_primary"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
        ).grid(row=0, column=2, sticky="e")

        mode_row = ctk.CTkFrame(panel, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 14))
        mode_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            mode_row,
            text="处理任务",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=GOOGLE_MATERIAL_PALETTE["on_surface_variant"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.mode_group = ctk.CTkFrame(
            mode_row,
            fg_color=GOOGLE_MATERIAL_PALETTE["outline_variant"],
            corner_radius=12,
        )
        self.mode_group.grid(row=0, column=1, sticky="w")
        for index, (mode, label) in enumerate(MODE_LABELS.items()):
            button = ctk.CTkButton(
                self.mode_group,
                text=label,
                width=96,
                height=38,
                corner_radius=10,
                border_width=1,
                command=lambda selected_mode=mode: self._select_mode(selected_mode),
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            )
            button.grid(row=0, column=index, sticky="ew", padx=(2 if index else 3, 2), pady=3)
            self.mode_buttons[mode] = button
        self._sync_mode_buttons()

        action_row = ctk.CTkFrame(panel, fg_color=GOOGLE_MATERIAL_PALETTE["surface_dim"], corner_radius=14)
        action_row.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 16))
        action_row.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            action_row,
            textvariable=self.status_var,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color=GOOGLE_MATERIAL_PALETTE["primary"],
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        self.start_button = ctk.CTkButton(
            action_row,
            text="开始运行",
            width=132,
            height=42,
            corner_radius=12,
            command=self._start,
            fg_color=GOOGLE_MATERIAL_PALETTE["tertiary"],
            hover_color=GOOGLE_MATERIAL_PALETTE["tertiary_hover"],
            text_color=GOOGLE_MATERIAL_PALETTE["on_primary"],
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=14, weight="bold"),
        )
        self.start_button.grid(row=0, column=1, sticky="e", padx=12, pady=10)

        log_panel = ctk.CTkFrame(panel, corner_radius=14, fg_color="#211A28")
        log_panel.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 22))
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            log_panel,
            text="运行日志",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold"),
            text_color=GOOGLE_MATERIAL_PALETTE["tertiary_container"],
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        self.log_text = ctk.CTkTextbox(
            log_panel,
            wrap="word",
            corner_radius=10,
            border_width=0,
            fg_color="#1C1B1F",
            text_color="#F5EFF7",
            scrollbar_button_color=GOOGLE_MATERIAL_PALETTE["secondary"],
            scrollbar_button_hover_color=GOOGLE_MATERIAL_PALETTE["tertiary"],
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="选择专利 DOCX 目录")
        if selected:
            self.input_dir_var.set(selected)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
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

    def _selected_mode(self) -> str:
        return self.mode_var.get()

    def _select_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            raise ValueError(f"Unknown mode: {mode}")
        self.mode_var.set(mode)
        self._sync_mode_buttons()

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
        self.start_button.configure(state="disabled" if running else "normal")
        for button in self.mode_buttons.values():
            button.configure(state="disabled" if running else "normal")
        self.status_var.set("正在运行，请等待..." if running else "请选择专利 DOCX 目录")

    def _finish(self, exit_code: int) -> None:
        self._set_running(False)
        if exit_code == 0:
            self.status_var.set("运行完成")
            self._append_log("\n完成。\n")
        else:
            self.status_var.set(f"运行结束，退出码 {exit_code}")
            self._append_log(f"\n运行结束，退出码 {exit_code}。\n")


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
