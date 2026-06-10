"""Save extracted PDF text and the LLM payloads that would be sent.

Temporary debug helper. It does not call the API.

Usage:
    python debug_pdf_llm_payload.py "D:\\path\\to\\patent.pdf"
    python debug_pdf_llm_payload.py "D:\\path\\to\\patent.pdf" --output "D:\\tmp\\debug_payload"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from patent_summarizer.extractors import TextExtractor
from patent_summarizer.prompts import FINAL_SUMMARY_PROMPT, PARTIAL_SUMMARY_PROMPT, SYSTEM_PROMPT
from patent_summarizer.summarizer import split_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save extracted PDF text and OpenAI-compatible request payloads without calling the API."
    )
    parser.add_argument("pdf", type=Path, help="PDF file to inspect")
    parser.add_argument("--output", type=Path, help="Output debug folder; defaults to <pdf_stem>_llm_debug next to the PDF")
    parser.add_argument("--max-chars", type=int, default=12000, help="Chunk size used by the production summarizer")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "debug-model"), help="Model id to place in saved payload JSON")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback while extracting PDF text")
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"Expected a .pdf file: {pdf_path}")
        return 2

    output_dir = (args.output or pdf_path.with_name(f"{pdf_path.stem}_llm_debug")).resolve()
    chunks_dir = output_dir / "chunks"
    payloads_dir = output_dir / "payloads"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    payloads_dir.mkdir(parents=True, exist_ok=True)

    extractor = TextExtractor(ocr_enabled=not args.no_ocr)
    text = extractor.extract(pdf_path)
    chunks = split_text(text, max_chars=args.max_chars)

    (output_dir / "extracted_text.txt").write_text(text, encoding="utf-8")
    (output_dir / "system_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")

    manifest = {
        "source_pdf": str(pdf_path),
        "output_dir": str(output_dir),
        "text_chars": len(text),
        "chunk_count": len(chunks),
        "max_chars": args.max_chars,
        "ocr_enabled": not args.no_ocr,
        "note": "This script saves request payloads only; it does not call the LLM API.",
    }

    if len(chunks) <= 1:
        user_prompt = FINAL_SUMMARY_PROMPT.format(filename=pdf_path.name, text=chunks[0] if chunks else "")
        _write_request(payloads_dir / "request_001_final.json", args.model, SYSTEM_PROMPT, user_prompt)
        (payloads_dir / "request_001_final_user_prompt.md").write_text(user_prompt, encoding="utf-8")
    else:
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            chunk_name = f"chunk_{index:03d}.txt"
            (chunks_dir / chunk_name).write_text(chunk, encoding="utf-8")
            user_prompt = PARTIAL_SUMMARY_PROMPT.format(
                filename=pdf_path.name,
                index=index,
                total=total,
                text=chunk,
            )
            _write_request(payloads_dir / f"request_{index:03d}_partial.json", args.model, SYSTEM_PROMPT, user_prompt)
            (payloads_dir / f"request_{index:03d}_partial_user_prompt.md").write_text(user_prompt, encoding="utf-8")

        final_template = FINAL_SUMMARY_PROMPT.format(
            filename=pdf_path.name,
            text="这里会填入每个分块请求返回的片段摘要；不调用 LLM 时无法生成真实合并输入。",
        )
        (payloads_dir / "final_merge_request_template_user_prompt.md").write_text(final_template, encoding="utf-8")
        _write_request(payloads_dir / "final_merge_request_template.json", args.model, SYSTEM_PROMPT, final_template)

    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved debug files to: {output_dir}")
    print(f"Extracted text chars: {len(text)}")
    print(f"Chunk count: {len(chunks)}")
    return 0


def _write_request(path: Path, model: str, system_prompt: str, user_prompt: str) -> None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
