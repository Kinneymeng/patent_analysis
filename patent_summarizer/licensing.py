from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence


REPAIR_FLAG = "--repair"
LICENSE_DURATION = timedelta(days=365)
ANNUAL_CODE_HASH = "beba0e1cb254e1f40a21fa09eb053e24ec6ec72ee1ebc79780251c0f45bf1e02"
PERMANENT_CODE_HASH = "d3ad12c5e45f8c33868bcc61f7a0379e235c7ca627d365b2b74af6680299c857"


@dataclass(frozen=True)
class LicenseState:
    mode: str
    expires_at: datetime | None = None


class LicenseStateError(RuntimeError):
    """Raised when a saved license state cannot be trusted."""


def default_state_path(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "PatentSummarizer" / "runtime.dat"


def startup_allows_gui(
    argv: Sequence[str],
    *,
    state_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Process silent repair commands or decide whether the GUI may start."""

    args = list(argv)
    current_time = _utc_time(now)

    if args and args[0] == REPAIR_FLAG:
        if len(args) == 2:
            _repair(args[1], state_path=state_path, now=current_time)
        return False

    try:
        path = Path(state_path) if state_path is not None else default_state_path()
        if not path.exists():
            _write_state(path, LicenseState("annual", current_time + LICENSE_DURATION))
            return True
        state = _read_state(path)
    except (OSError, LicenseStateError, ValueError, TypeError):
        return False

    if state.mode == "permanent":
        return True
    return state.expires_at is not None and current_time < state.expires_at


def _repair(code: str, *, state_path: Path | None, now: datetime) -> None:
    code_kind = _code_kind(code)
    if code_kind is None:
        return

    try:
        path = Path(state_path) if state_path is not None else default_state_path()
        if code_kind == "annual" and path.exists():
            try:
                if _read_state(path).mode == "permanent":
                    return
            except (OSError, LicenseStateError, ValueError, TypeError):
                pass

        if code_kind == "permanent":
            _write_state(path, LicenseState("permanent"))
        else:
            _write_state(path, LicenseState("annual", now + LICENSE_DURATION))
    except (OSError, LicenseStateError, ValueError, TypeError):
        return


def _code_kind(code: str) -> str | None:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if hmac.compare_digest(digest, PERMANENT_CODE_HASH):
        return "permanent"
    if hmac.compare_digest(digest, ANNUAL_CODE_HASH):
        return "annual"
    return None


def _read_state(path: Path) -> LicenseState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LicenseStateError("Invalid license state") from exc

    if not isinstance(payload, dict):
        raise LicenseStateError("Invalid license state")
    if payload.get("mode") == "permanent":
        return LicenseState("permanent")
    if payload.get("mode") != "annual" or not isinstance(payload.get("expires_at"), str):
        raise LicenseStateError("Invalid license state")

    try:
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except ValueError as exc:
        raise LicenseStateError("Invalid license expiry") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise LicenseStateError("License expiry must include a timezone")
    return LicenseState("annual", expires_at.astimezone(timezone.utc))


def _write_state(path: Path, state: LicenseState) -> None:
    if state.mode == "permanent":
        payload = {"mode": "permanent"}
    elif state.mode == "annual" and state.expires_at is not None:
        payload = {
            "mode": "annual",
            "expires_at": state.expires_at.astimezone(timezone.utc).isoformat(),
        }
    else:
        raise LicenseStateError("Invalid license state")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _utc_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Current time must include a timezone")
    return value.astimezone(timezone.utc)
