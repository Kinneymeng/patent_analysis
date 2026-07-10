from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


ProgressState = Literal["preparing", "running", "success", "error"]
ProgressCallback = Callable[["ProgressUpdate"], None]


@dataclass(frozen=True)
class ProgressUpdate:
    """A non-invasive status update for a long-running patent task."""

    mode: str
    state: ProgressState
    stage: str
    detail: str = ""
    completed: int | None = None
    total: int | None = None


class ProgressTracker:
    """Keep a task's observable progress consistent without changing task behavior."""

    def __init__(self, mode: str, callback: ProgressCallback | None = None):
        self.mode = mode
        self.callback = callback
        self.completed = 0
        self.total: int | None = None

    def preparing(self, stage: str, detail: str = "") -> None:
        self._emit("preparing", stage, detail)

    def start(self, total: int, stage: str, detail: str = "") -> None:
        if total < 0:
            raise ValueError("progress total must not be negative")
        self.completed = 0
        self.total = total
        self._emit("running", stage, detail)

    def set_stage(self, stage: str, detail: str = "") -> None:
        self._require_started()
        self._emit("running", stage, detail)

    def advance(self, stage: str, detail: str = "", units: int = 1) -> None:
        self._require_started()
        if units < 0:
            raise ValueError("progress units must not be negative")
        assert self.total is not None
        self.completed = min(self.total, self.completed + units)
        self._emit("running", stage, detail)

    def succeed(self, stage: str = "运行完成", detail: str = "") -> None:
        self._require_started()
        assert self.total is not None
        self.completed = self.total
        self._emit("success", stage, detail)

    def fail(self, stage: str, detail: str = "") -> None:
        self._emit("error", stage, detail)

    def _require_started(self) -> None:
        if self.total is None:
            raise RuntimeError("progress has not started")

    def _emit(self, state: ProgressState, stage: str, detail: str) -> None:
        if self.callback is None:
            return
        self.callback(
            ProgressUpdate(
                mode=self.mode,
                state=state,
                stage=stage,
                detail=detail,
                completed=self.completed if self.total is not None else None,
                total=self.total,
            )
        )
