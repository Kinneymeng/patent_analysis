from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_APP = ROOT / "dist" / "PatentSummarizer"
SPEC_FILE = ROOT / "PatentSummarizer.spec"
REPORT_TEMPLATE_NAME = "汽车底盘领域专利布局分析报告模板.docx"
APP_ICON_NAME = "patent-summarizer.ico"
USER_GUIDE_NAME = "使用说明.md"
PLAYWRIGHT_BROWSERS = ROOT / "build" / "playwright-browsers"
MERMAID_RUNTIME = ROOT / "build" / "vendor" / "mermaid.min.js"
MERMAID_URL = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.12.0/mermaid.min.js"
MERMAID_SHA512 = "5TKaYvhenABhlGIKSxAWLFJBZCSQw7HTV7aL1dJcBokM/+3PNtfgJFlv8E6Us/B1VMlQ4u8sPzjudL9TEQ06ww=="


def main() -> int:
    if DIST_APP.exists():
        print(f"Output already exists: {DIST_APP}")
        print("Move or remove that folder explicitly before rebuilding.")
        return 2

    build_env = os.environ.copy()
    build_env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS)
    ensure_mermaid_runtime()
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=ROOT,
        env=build_env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE)],
        cwd=ROOT,
        env=build_env,
        check=True,
    )
    copy_runtime_files(DIST_APP)
    verify_portable_application(DIST_APP, run_self_check=True)
    print(f"Portable build ready: {DIST_APP}")
    return 0


def copy_runtime_files(dist_app: Path) -> None:
    class_file = ROOT / "class.xlsx"
    if not class_file.exists():
        raise FileNotFoundError(f"Missing required class table: {class_file}")
    shutil.copy2(class_file, dist_app / "class.xlsx")

    report_template = ROOT / "output" / "doc" / REPORT_TEMPLATE_NAME
    if not report_template.exists():
        raise FileNotFoundError(f"Missing required report template: {report_template}")
    shutil.copy2(report_template, dist_app / REPORT_TEMPLATE_NAME)

    icon_file = ROOT / "assets" / APP_ICON_NAME
    if not icon_file.exists():
        raise FileNotFoundError(f"Missing required application icon: {icon_file}")
    shutil.copy2(icon_file, dist_app / APP_ICON_NAME)

    user_guide = ROOT / USER_GUIDE_NAME
    if not user_guide.exists():
        raise FileNotFoundError(f"Missing required user guide: {user_guide}")
    shutil.copy2(user_guide, dist_app / USER_GUIDE_NAME)

    env_file = ROOT / ".env"
    if env_file.exists():
        shutil.copy2(env_file, dist_app / ".env")
        return

    env_example = ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, dist_app / ".env")


def ensure_mermaid_runtime() -> Path:
    if MERMAID_RUNTIME.is_file():
        data = MERMAID_RUNTIME.read_bytes()
        _verify_mermaid_runtime(data)
        return MERMAID_RUNTIME
    request = urllib.request.Request(
        MERMAID_URL,
        headers={"User-Agent": "PatentSummarizer portable builder"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    _verify_mermaid_runtime(data)
    MERMAID_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    temporary = MERMAID_RUNTIME.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(MERMAID_RUNTIME)
    return MERMAID_RUNTIME


def _verify_mermaid_runtime(data: bytes) -> None:
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if actual != MERMAID_SHA512:
        raise ValueError("Downloaded Mermaid runtime failed its pinned SHA-512 integrity check")


def verify_portable_application(
    dist_app: Path,
    *,
    run_self_check: bool = False,
) -> None:
    internal = dist_app / "_internal"
    required = (
        dist_app / "PatentSummarizer.exe",
        internal / "playwright-browsers",
        internal / "playwright" / "driver" / "node.exe",
        internal / "mermaid" / "mermaid.min.js",
        internal / "latex2mathml" / "unimathsymbols.txt",
        internal / "patent_summarizer" / "disclosure" / "prompts" / "common.md",
        internal / "patent_summarizer" / "disclosure" / "cnipa_upstream" / "LICENSE.txt",
        internal / "patent_summarizer" / "disclosure" / "MERMAID_LICENSE.txt",
        internal / "patent_summarizer" / "disclosure" / "THIRD_PARTY_NOTICES.md",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Portable build is missing required disclosure resources: "
            + "; ".join(str(path) for path in missing)
        )
    browser_root = internal / "playwright-browsers"
    executables = list(browser_root.rglob("chrome.exe")) + list(
        browser_root.rglob("headless_shell.exe")
    )
    if not executables:
        raise FileNotFoundError(
            f"Portable build does not contain a Chromium executable: {browser_root}"
        )
    if not run_self_check:
        return
    report_path = dist_app / "portable-self-check.json"
    completed = subprocess.run(
        [
            str(dist_app / "PatentSummarizer.exe"),
            "--self-check",
            "--self-check-output",
            str(report_path),
        ],
        cwd=dist_app,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = "self-check did not create a diagnostic report"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                detail = (
                    f"stage={report.get('stage')}, "
                    f"error={report.get('error_type')}: {report.get('error')}"
                )
            except (OSError, ValueError) as exc:
                detail = f"diagnostic report could not be read: {exc}"
        raise RuntimeError(
            "Packaged application self-check failed: Playwright, Mermaid, Pillow, "
            "latex2mathml, DOCX, or Chinese-path output is unavailable; " + detail
        )
    if report_path.exists():
        report_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
