# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python CLI for batch summarizing Chinese patent PDF/DOCX files. Core package code lives in `patent_summarizer/`: `cli.py` parses commands, `config.py` loads `.env` settings, `discovery.py` finds pending documents, `extractors.py` extracts text, `llm.py` handles OpenAI-compatible API calls, and `processor.py` coordinates processing. Tests live in `tests/` and mirror package behavior with files such as `test_config.py` and `test_llm.py`. Root-level patent inputs and generated summaries, for example `CN202411182168.pdf` and `CN202411182168.md`, are sample/runtime artifacts.

## Build, Test, and Development Commands

Use Python 3.10 or newer.

```powershell
python -m unittest discover
```

Runs the full test suite.

```powershell
python -m patent_summarizer run --input "D:\path\to\patents" --recursive --dry-run
```

Lists pending PDF/DOCX files without calling the API.

```powershell
pip install -e .
pip install pdfplumber pypdf pdf2image pytesseract
```

Installs the package locally and optional document/OCR dependencies.

## Coding Style & Naming Conventions

Follow idiomatic Python with 4-space indentation, type hints where useful, and small functions with explicit return values. Use `snake_case` for functions, variables, and modules; use `PascalCase` for classes and exceptions. Keep CLI output concise and tab-separated where existing code already uses that style. Prefer standard-library solutions unless optional dependencies are already part of the document extraction path.

## Testing Guidelines

Tests use the standard `unittest` framework. Add new tests under `tests/` with names matching `test_*.py`, and name test methods `test_<behavior>`. Prefer fake transports, temporary directories, and injected clocks/sleepers over real network calls or slow waits. Run `python -m unittest discover` before submitting changes.

## Commit & Pull Request Guidelines

This directory currently has no Git history, so use clear, imperative commit messages such as `Add dry-run coverage` or `Fix retry handling`. Pull requests should include a short summary, test results, related issue/context, and screenshots or sample CLI output when behavior changes.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local credentials. Never commit real `OPENAI_API_KEY` values or generated log contents from `.patent_summary_logs/`. Keep rate-limit settings conservative when testing against shared API endpoints.

## Agent-Specific Instructions

Do not batch-delete files or directories. Avoid `del /s`, `rd /s`, and `rmdir /s`. If deletion is necessary, remove only one explicit file path at a time, for example `Remove-Item "D:\path\to\file.txt"`.
