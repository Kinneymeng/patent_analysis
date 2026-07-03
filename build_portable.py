from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_APP = ROOT / "dist" / "PatentSummarizer"
SPEC_FILE = ROOT / "PatentSummarizer.spec"
REPORT_TEMPLATE_NAME = "汽车底盘领域专利布局分析报告模板.docx"
APP_ICON_NAME = "patent-summarizer.ico"
USER_GUIDE_NAME = "使用说明.md"


def main() -> int:
    if DIST_APP.exists():
        print(f"Output already exists: {DIST_APP}")
        print("Move or remove that folder explicitly before rebuilding.")
        return 2

    subprocess.run([sys.executable, "-m", "PyInstaller", str(SPEC_FILE)], cwd=ROOT, check=True)
    copy_runtime_files(DIST_APP)
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


if __name__ == "__main__":
    raise SystemExit(main())
