from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    api_key: str
    model: str
    api_rpm: int = 10
    max_retries: int = 5
    api_timeout_seconds: float = 300.0
    ocr_enabled: bool = True


def load_config(
    env_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    api_rpm: int | None = None,
) -> AppConfig:
    env_path = env_path or Path(".env")
    source = _read_env_file(env_path)
    source.update(dict(os.environ if environ is None else environ))

    base_url = source.get("OPENAI_BASE_URL", "").strip()
    api_key = source.get("OPENAI_API_KEY", "").strip()
    model = source.get("OPENAI_MODEL", "").strip()
    missing = [
        name
        for name, value in [
            ("OPENAI_BASE_URL", base_url),
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_MODEL", model),
        ]
        if not value
    ]
    if missing:
        raise ConfigError("Missing required settings: " + ", ".join(missing))

    return AppConfig(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        api_rpm=_positive_int(api_rpm if api_rpm is not None else source.get("PATENT_API_RPM", "10"), "PATENT_API_RPM"),
        max_retries=_positive_int(source.get("PATENT_MAX_RETRIES", "5"), "PATENT_MAX_RETRIES"),
        api_timeout_seconds=_positive_float(
            source.get("PATENT_API_TIMEOUT_SECONDS", "300"),
            "PATENT_API_TIMEOUT_SECONDS",
        ),
        ocr_enabled=_bool(source.get("PATENT_OCR_ENABLED", "true")),
    )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _positive_int(value: int | str, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return parsed


def _positive_float(value: float | str, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive number")
    return parsed


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
