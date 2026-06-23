# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path.cwd()
pypandoc_datas = collect_data_files("pypandoc")
pypandoc_hiddenimports = collect_submodules("pypandoc")
customtkinter_datas = collect_data_files("customtkinter")
customtkinter_hiddenimports = collect_submodules("customtkinter")

a = Analysis(
    ["patent_summarizer/gui.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=pypandoc_datas + customtkinter_datas,
    hiddenimports=pypandoc_hiddenimports + customtkinter_hiddenimports,
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
