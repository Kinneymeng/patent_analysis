from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

from patent_summarizer.disclosure.drawio import DRAWIO_VERSION


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
DRAWIO_RUNTIME = ROOT / "build" / "vendor" / f"drawio-{DRAWIO_VERSION}"
DRAWIO_URL = f"https://registry.npmjs.org/@next-ai-drawio/mcp-server/-/mcp-server-{DRAWIO_VERSION}.tgz"
DRAWIO_SHA512 = "t0X5MNyNe0598dlh17CQb1vpP4ENGhMmLfzdxJtK9S93dxze7gKw+SZ/tMXgZKGVE6tItavRZDj0f1CuQ6+YMQ=="
DRAWIO_REQUIRED = ("index.html", "LICENSE", "img/LICENSE", "shapes/LICENSE", "stencils/LICENSE", "templates/LICENSE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Windows portable application")
    parser.add_argument("--distpath", type=Path, help="Parent directory for the new portable application")
    parser.add_argument("--workpath", type=Path, help="Directory for PyInstaller build files")
    args = parser.parse_args(argv)
    dist_app = args.distpath.resolve() / DIST_APP.name if args.distpath else DIST_APP
    if dist_app.exists():
        print(f"Output already exists: {dist_app}")
        print("Move or remove that folder explicitly before rebuilding.")
        return 2

    build_env = os.environ.copy()
    build_env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS)
    conda_bin = Path(sys.prefix) / "Library" / "bin"
    if conda_bin.is_dir():
        # Keep Tcl/Tk DLLs and scripts from the same environment when PyInstaller resolves imports.
        build_env["PATH"] = os.pathsep.join((str(conda_bin), build_env.get("PATH", "")))
    ensure_mermaid_runtime()
    build_env["PATENT_DRAWIO_RUNTIME"] = str(ensure_drawio_runtime())
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=ROOT,
        env=build_env,
        check=True,
    )
    command = [sys.executable, "-m", "PyInstaller", "--distpath", str(dist_app.parent)]
    if args.workpath:
        command.extend(["--workpath", str(args.workpath.resolve())])
    subprocess.run(
        [*command, str(SPEC_FILE)],
        cwd=ROOT,
        env=build_env,
        check=True,
    )
    copy_runtime_files(dist_app)
    verify_portable_application(dist_app, run_self_check=True)
    print(f"Portable build ready: {dist_app}")
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

    env_example = ROOT / ".env.example"
    if not env_example.is_file():
        raise FileNotFoundError(f"Missing required configuration template: {env_example}")
    shutil.copy2(env_example, dist_app / env_example.name)


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


def ensure_drawio_runtime() -> Path:
    marker = DRAWIO_RUNTIME / ".integrity"
    if (marker.is_file() and marker.read_text(encoding="ascii") == DRAWIO_SHA512
            and all((DRAWIO_RUNTIME / name).is_file() for name in DRAWIO_REQUIRED)):
        return DRAWIO_RUNTIME

    archive_path = DRAWIO_RUNTIME.with_name(f"{DRAWIO_RUNTIME.name}.tgz")
    if archive_path.is_file():
        data = archive_path.read_bytes()
    else:
        with urllib.request.urlopen(DRAWIO_URL, timeout=120) as response:
            data = response.read()
    if base64.b64encode(hashlib.sha512(data).digest()).decode("ascii") != DRAWIO_SHA512:
        raise ValueError("Draw.io archive failed its pinned SHA-512 integrity check")
    DRAWIO_RUNTIME.mkdir(parents=True, exist_ok=True)
    if not archive_path.is_file():
        temporary = archive_path.with_suffix(".tgz.tmp")
        temporary.write_bytes(data)
        temporary.replace(archive_path)
    prefix = "package/dist/drawio/"
    root = DRAWIO_RUNTIME.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = PurePosixPath(member.name.removeprefix(prefix))
            target = (root / relative).resolve()
            if not target.is_relative_to(root) or "\\" in member.name or ".." in relative.parts:
                raise ValueError(f"Unsafe Draw.io resource path: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    if not all((root / name).is_file() for name in DRAWIO_REQUIRED):
        raise FileNotFoundError("Draw.io archive lacks static editor files or license notices")
    marker.write_text(DRAWIO_SHA512, encoding="ascii")
    return root


def verify_portable_application(
    dist_app: Path,
    *,
    run_self_check: bool = False,
) -> None:
    internal = dist_app / "_internal"
    required = (
        dist_app / "PatentSummarizer.exe",
        dist_app / ".env.example",
        internal / "playwright-browsers",
        internal / "playwright" / "driver" / "node.exe",
        internal / "mermaid" / "mermaid.min.js",
        *(internal / "drawio" / name for name in DRAWIO_REQUIRED),
        internal / "latex2mathml" / "unimathsymbols.txt",
        internal / "patent_summarizer" / "disclosure" / "prompts" / "common.md",
        internal / "patent_summarizer" / "disclosure" / "prompts" / "polish.md",
        internal / "patent_summarizer" / "disclosure" / "prompts" / "humanizer-zh" / "SKILL.md",
        internal / "patent_summarizer" / "disclosure" / "cnipa_upstream" / "LICENSE.txt",
        internal / "patent_summarizer" / "disclosure" / "MERMAID_LICENSE.txt",
        internal / "patent_summarizer" / "disclosure" / "HUMANIZER_ZH_LICENSE.txt",
        internal / "patent_summarizer" / "disclosure" / "THIRD_PARTY_NOTICES.md",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Portable build is missing required disclosure resources: "
            + "; ".join(str(path) for path in missing)
        )
    if any(path.is_file() for path in dist_app.rglob(".env")):
        raise ValueError("Portable build must not contain a .env file")
    if (dist_app / ".env.example").read_bytes() != (ROOT / ".env.example").read_bytes():
        raise ValueError("Packaged .env.example differs from the source template")
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
            "Packaged application self-check failed: " + detail
        )
    if report_path.exists():
        report_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
