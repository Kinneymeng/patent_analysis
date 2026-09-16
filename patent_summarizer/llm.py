from __future__ import annotations

import hashlib
import http.client
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


class HttpResponseError(RuntimeError):
    def __init__(self, status_code: int, message: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.retry_after = retry_after


class IncompleteModelResponseError(ConnectionError):
    """Raised when the provider reports a token-truncated response."""


class RateLimiter:
    def __init__(
        self,
        rpm: int,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        safety_seconds: float = 0.5,
    ):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.interval = 60.0 / rpm + safety_seconds # 每个请求之间的最小间隔时间，单位秒
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._last_request: float | None = None

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last_request is not None:
                elapsed = now - self._last_request
                remaining = self.interval - elapsed
                if remaining > 0:
                    self.sleeper(remaining)
                    now = self.clock()
            self._last_request = now


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass
class RateLimitedOpenAIClient:
    base_url: str
    api_key: str
    model: str
    rpm: int = 10
    max_retries: int = 5
    timeout: float = 120
    transport: Transport | None = None
    clock: Callable[[], float] | None = None
    sleeper: Callable[[float], None] | None = None
    jitter: Callable[[int], float] | None = None
    debug_path: Path | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._clock = self.clock or time.monotonic
        self._sleeper = self.sleeper or time.sleep
        self._limiter = RateLimiter(self.rpm, clock=self._clock, sleeper=self._sleeper)
        self._transport = self.transport or _urllib_transport
        self._jitter = self.jitter or (lambda attempt: random.uniform(0, min(1.0, attempt * 0.25)))
        self.debug_path = Path(self.debug_path) if self.debug_path is not None else None
        self._debug_lock = threading.Lock()
        self._debug_run_id = uuid4().hex
        self._debug_sequence = 0

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        request_id = self._record_debug_request(url, payload)

        attempt = 0
        while True:
            self._limiter.wait()
            attempt += 1
            try:
                response = self._transport(url, headers, payload, self.timeout)
                if _finish_reason(response) == "length":
                    raise IncompleteModelResponseError(
                        "模型响应因长度限制被截断，请缩短当前阶段或提高模型输出上限"
                    )
                content = _extract_content(response)
                self._record_debug_event(
                    {
                        "event": "attempt",
                        "request_id": request_id,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "outcome": "success",
                        "response_model": response.get("model"),
                        "system_fingerprint": response.get("system_fingerprint"),
                        "usage": response.get("usage"),
                        "finish_reason": _finish_reason(response),
                        "content_chars": len(content),
                    }
                )
                return content
            except Exception as exc:
                retryable = _is_retryable(exc)
                will_retry = retryable and attempt <= self.max_retries
                retry_delay = _retry_delay(exc, attempt, self._jitter) if will_retry else None
                self._record_debug_event(
                    {
                        "event": "attempt",
                        "request_id": request_id,
                        "attempt": attempt,
                        "outcome": "error",
                        "error_type": type(exc).__name__,
                        "http_status": exc.status_code if isinstance(exc, HttpResponseError) else None,
                        "error": str(exc),
                        "retryable": retryable,
                        "will_retry": will_retry,
                        "retry_delay_seconds": retry_delay,
                    }
                )
                if not will_retry:
                    raise
                self._sleeper(retry_delay)

    def _record_debug_request(self, url: str, payload: dict[str, Any]) -> str | None:
        if self.debug_path is None:
            return None
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self._debug_sequence += 1
        request_id = f"{self._debug_run_id}-{self._debug_sequence}-{payload_hash[:12]}"
        messages = payload.get("messages", [])
        self._record_debug_event(
            {
                "event": "request",
                "request_id": request_id,
                "url": url,
                "timeout_seconds": self.timeout,
                "payload_sha256": payload_hash,
                "payload_chars": len(serialized),
                "payload_utf8_bytes": len(serialized.encode("utf-8")),
                "message_chars": [
                    {
                        "role": message.get("role"),
                        "chars": len(str(message.get("content", ""))),
                        "utf8_bytes": len(str(message.get("content", "")).encode("utf-8")),
                    }
                    for message in messages
                    if isinstance(message, dict)
                ],
                "payload": payload,
            }
        )
        return request_id

    def _record_debug_event(self, event: dict[str, Any]) -> None:
        if self.debug_path is None:
            return
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": self._debug_run_id,
            **event,
        }
        with self._debug_lock:
            self.debug_path.parent.mkdir(parents=True, exist_ok=True)
            with self.debug_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _urllib_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After")
        message = exc.read().decode("utf-8", errors="replace")
        raise HttpResponseError(exc.code, message, _parse_retry_after(retry_after)) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc)) from exc


def _extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected OpenAI-compatible response: {response!r}") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenAI-compatible response did not contain text content")
    return content.strip()


def _finish_reason(response: dict[str, Any]) -> Any:
    try:
        return response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, HttpResponseError):
        return exc.status_code in {408, 409, 429} or 500 <= exc.status_code <= 599
    return isinstance(
        exc,
        (
            TimeoutError,
            socket.timeout,
            ConnectionError,
            ConnectionResetError,
            ConnectionAbortedError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    )


def _retry_delay(exc: Exception, attempt: int, jitter: Callable[[int], float]) -> float:
    if isinstance(exc, HttpResponseError) and exc.status_code == 429 and exc.retry_after is not None:
        return exc.retry_after
    return min(60.0, 2 ** (attempt - 1)) + jitter(attempt)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
