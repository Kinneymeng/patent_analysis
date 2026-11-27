from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

from .models import TechnicalDisclosure
from .validation import disclosure_to_payload, parse_technical_disclosure


DISCLOSURE_PROMPT_VERSION = "engineering-disclosure-v4"


class DisclosureCheckpointStore:
    """Semantic workflow checkpoints used only to resume interrupted runs."""

    def __init__(self, root: Path, *, model: str, read_enabled: bool = True):
        self.root = Path(root)
        self.model = model
        self.read_enabled = read_enabled

    def key(self, source_context: str, title: str, preview_payload: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            {
                "source": source_context,
                "title": title,
                "preview": preview_payload,
                "model": self.model,
                "prompt_version": DISCLOSURE_PROMPT_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def load_disclosure(
        self,
        key: str,
        stage: str,
        expected_title: str,
    ) -> TechnicalDisclosure | None:
        path = self._path(key, stage)
        if not self.read_enabled:
            return None
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("key") != key or payload.get("model") != self.model:
                return None
            if payload.get("prompt_version") != DISCLOSURE_PROMPT_VERSION:
                return None
            return parse_technical_disclosure(payload["disclosure"], expected_title)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return None

    def save_disclosure(
        self,
        key: str,
        stage: str,
        disclosure: TechnicalDisclosure,
    ) -> None:
        path = self._path(key, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "stage": stage,
            "model": self.model,
            "prompt_version": DISCLOSURE_PROMPT_VERSION,
            "disclosure": disclosure_to_payload(disclosure),
        }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load_payload(self, key: str, stage: str) -> Mapping[str, Any] | None:
        path = self._path(key, stage)
        if not self.read_enabled:
            return None
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("key") != key or record.get("model") != self.model:
                return None
            if record.get("prompt_version") != DISCLOSURE_PROMPT_VERSION:
                return None
            payload = record.get("payload")
            return payload if isinstance(payload, Mapping) else None
        except (TypeError, ValueError, json.JSONDecodeError, OSError):
            return None

    def save_payload(
        self,
        key: str,
        stage: str,
        payload: Mapping[str, Any],
    ) -> None:
        path = self._path(key, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "stage": stage,
            "model": self.model,
            "prompt_version": DISCLOSURE_PROMPT_VERSION,
            "payload": payload,
        }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _path(self, key: str, stage: str) -> Path:
        safe_stage = "".join(ch for ch in stage if ch.isalnum() or ch in "-_")
        return self.root / key / f"{safe_stage}.json"


__all__ = ["DISCLOSURE_PROMPT_VERSION", "DisclosureCheckpointStore"]
