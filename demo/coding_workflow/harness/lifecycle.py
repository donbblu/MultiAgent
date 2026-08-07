from __future__ import annotations

from threading import Event


class TaskCancelledError(RuntimeError):
    pass


class CancellationToken:
    """由调用方控制的协作式中断信号，不把中断权交给 Worker。"""

    def __init__(self) -> None:
        self._event = Event()
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "任务被取消") -> None:
        self._reason = reason.strip() or "任务被取消"
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelledError(self.reason)
