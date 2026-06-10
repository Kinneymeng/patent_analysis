from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, input_root: Path):
        self.log_dir = input_root / ".patent_summary_logs"
        self.log_dir.mkdir(exist_ok=True)
        self.run_path = self.log_dir / "run.jsonl"
        self.failures_path = self.log_dir / "failures.csv"
        if not self.failures_path.exists():
            with self.failures_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["time", "source", "stage", "error"])
                writer.writeheader()

    def event(self, **fields: Any) -> None:
        payload = {"time": _now(), **fields}
        with self.run_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def failure(self, source: Path, stage: str, error: str) -> None:
        self.event(source=str(source), status="failed", stage=stage, error=error)
        with self.failures_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["time", "source", "stage", "error"])
            writer.writerow({"time": _now(), "source": str(source), "stage": stage, "error": error})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
