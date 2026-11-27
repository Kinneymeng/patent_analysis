# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd()
pypandoc_datas = collect_data_files("pypandoc")
pypandoc_hiddenimports = collect_submodules("pypandoc")
customtkinter_datas = collect_data_files("customtkinter")
customtkinter_hiddenimports = collect_submodules("customtkinter")
disclosure_datas = collect_data_files("patent_summarizer.disclosure")
latex2mathml_datas = collect_data_files("latex2mathml")
playwright_browsers = ROOT / "build" / "playwright-browsers"
mermaid_runtime = ROOT / "build" / "vendor" / "mermaid.min.js"
browser_datas = (
    [(str(playwright_browsers), "playwright-browsers")]
    if playwright_browsers.exists()
    else []
)
mermaid_datas = [(str(mermaid_runtime), "mermaid")] if mermaid_runtime.exists() else []
drawio_runtime = os.environ.get("PATENT_DRAWIO_RUNTIME")
drawio_datas = [(drawio_runtime, "drawio")] if drawio_runtime else []
dynamic_packages = ("playwright", "pptx", "pdfplumber", "pypdf", "PIL", "latex2mathml")
dynamic_hiddenimports = [
    module
    for package in dynamic_packages
    for module in collect_submodules(package)
]

a = Analysis(
    ["patent_summarizer/entrypoint.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=(
        pypandoc_datas
        + customtkinter_datas
        + disclosure_datas
        + latex2mathml_datas
        + browser_datas
        + mermaid_datas
        + drawio_datas
    ),
    hiddenimports=(
        pypandoc_hiddenimports
        + customtkinter_hiddenimports
        + dynamic_hiddenimports
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PatentSummarizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "patent-summarizer.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PatentSummarizer",
)
