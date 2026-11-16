from __future__ import annotations

import os
import json
import sys
import tempfile
import tkinter
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace


def _write_report(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_portable_self_check(report_path: Path | None = None) -> int:
    """Exercise packaged disclosure dependencies without network or Office."""

    stage = "initialize"
    root: Path | None = None

    def begin(next_stage: str) -> None:
        nonlocal stage
        stage = next_stage
        _write_report(
            report_path,
            {
                "ok": None,
                "stage": stage,
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
            },
        )

    try:
        begin("import_dependencies")
        import latex2mathml  # noqa: F401
        import playwright  # noqa: F401
        from PIL import Image
        from docx import Document

        from .disclosure.cnipa_upstream.browser import probe_launch
        from .disclosure.diagrams import _render_with_pillow, render_mermaid_flowchart
        from .disclosure.math_to_omml import latex_to_omml_with_fallback
        from .disclosure.models import TechnicalDisclosure
        from .disclosure.prompt_loader import load_prompt
        from .disclosure_docx import build_technical_disclosure_document
        from .disclosure_polish import polish_disclosure_docx
        from .progress import ProgressTracker

        begin("load_polishing_rules")
        upstream_rules = load_prompt("humanizer-zh/SKILL.md").split("\n---\n", 1)[1].lstrip()
        disclosure_constraints = load_prompt("polish.md").rstrip()

        begin("initialize_tcl")
        tkinter.Tcl()

        begin("launch_bundled_browser")
        previous_channel = os.environ.get("PATENT_BROWSER_CHANNEL")
        os.environ["PATENT_BROWSER_CHANNEL"] = "chromium"
        try:
            probe = probe_launch()
        finally:
            if previous_channel is None:
                os.environ.pop("PATENT_BROWSER_CHANNEL", None)
            else:
                os.environ["PATENT_BROWSER_CHANNEL"] = previous_channel
        if not probe.get("ok"):
            raise RuntimeError(f"bundled browser probe failed: {probe.get('error')}")
        if probe.get("channel") != "chromium":
            raise RuntimeError(
                f"bundled browser probe used unexpected channel: {probe.get('channel')}"
            )

        begin("create_chinese_path")
        root = Path(tempfile.gettempdir()) / f"专利交底书自检-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        mermaid_png = root / "mermaid.png"
        pillow_png = root / "pillow.png"
        docx_path = root / "公式自检.docx"
        markdown_path = root / "交底书自检.md"
        try:
            begin("render_mermaid")
            source = "flowchart TD\nA[输入] --> B[控制模块]"
            render_mermaid_flowchart(source, mermaid_png, "Mermaid 自检")

            begin("render_pillow")
            _render_with_pillow(source, pillow_png, "Pillow 自检")

            begin("verify_images")
            for image_path in (mermaid_png, pillow_png):
                with Image.open(image_path) as image:
                    image.verify()

            begin("convert_structured_formula")
            structured = latex_to_omml_with_fallback(r"y = \frac{x}{2}")
            if structured.mode != "structured" or structured.element is None:
                raise RuntimeError(
                    "structured formula conversion failed: "
                    + (structured.error or f"fallback mode={structured.mode}")
                )

            begin("convert_linear_formula")
            linear = latex_to_omml_with_fallback("^")
            if linear.mode != "linear" or linear.element is None:
                raise RuntimeError("linear formula fallback failed")

            begin("write_docx")
            document = Document()
            document.add_paragraph("公式自检")._element.append(structured.element)
            document.add_paragraph("线性公式自检")._element.append(linear.element)
            document.save(docx_path)

            begin("reopen_docx")
            reopened = Document(docx_path)
            if not reopened.paragraphs:
                raise RuntimeError("generated DOCX could not be reopened")

            begin("markdown_round_trip")
            markdown_path.write_text(
                "# 技术交底书自检\n\n```math\ny = \\frac{x}{2}\n```\n",
                encoding="utf-8",
            )
            if "技术交底书自检" not in markdown_path.read_text(encoding="utf-8"):
                raise RuntimeError("Markdown round-trip failed")

            begin("polish_disclosure_docx")
            disclosure = TechnicalDisclosure("控制器自检", "控制器进行状态校验。", ())
            build_technical_disclosure_document(disclosure).save(docx_path)

            def fake_chat(system_prompt: str, user_prompt: str) -> str:
                if system_prompt != f"{disclosure_constraints}\n\n{upstream_rules}":
                    raise RuntimeError("polishing request did not include the complete Humanizer-zh rules")
                paragraphs = json.loads(user_prompt)["paragraphs"]
                paragraphs[0]["text"] = "控制器校验状态。"
                return json.dumps({"paragraphs": paragraphs})

            result = polish_disclosure_docx(docx_path, SimpleNamespace(chat=fake_chat), ProgressTracker("polish"))
            try:
                if result.changed != 1 or Document(result.output).paragraphs[2].text != "控制器校验状态。":
                    raise RuntimeError("polishing round-trip failed")
            finally:
                result.output.unlink()
        finally:
            stage_before_cleanup = stage
            for path in (mermaid_png, pillow_png, docx_path, markdown_path):
                if path.exists():
                    path.unlink()
            if root.exists():
                root.rmdir()
            stage = stage_before_cleanup
        _write_report(
            report_path,
            {
                "ok": True,
                "stage": "complete",
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
                "browser": probe,
            },
        )
        return 0
    except Exception as exc:
        _write_report(
            report_path,
            {
                "ok": False,
                "stage": stage,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
                "temporary_directory": str(root) if root is not None else None,
            },
        )
        return 1


__all__ = ["run_portable_self_check"]
