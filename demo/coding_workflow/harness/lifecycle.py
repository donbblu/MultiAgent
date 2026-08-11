from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Condition, RLock


class TaskCancelledError(RuntimeError):
    pass


class LifecycleTransitionError(RuntimeError):
    pass


class LifecycleState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({LifecycleState.QUEUED, LifecycleState.RUNNING, LifecycleState.CANCELLED}),
    LifecycleState.QUEUED: frozenset({LifecycleState.RUNNING, LifecycleState.PAUSED, LifecycleState.CANCELLING, LifecycleState.CANCELLED}),
    LifecycleState.RUNNING: frozenset({LifecycleState.PAUSED, LifecycleState.CANCELLING, LifecycleState.COMPLETED, LifecycleState.FAILED}),
    LifecycleState.PAUSED: frozenset({LifecycleState.RUNNING, LifecycleState.CANCELLING, LifecycleState.CANCELLED}),
    LifecycleState.CANCELLING: frozenset({LifecycleState.CANCELLED}),
    LifecycleState.COMPLETED: frozenset(),
    LifecycleState.FAILED: frozenset(),
    LifecycleState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class LifecycleSnapshot:
    state: LifecycleState
    reason: str
    updated_at: str


@dataclass(frozen=True)
class LifecycleEvent:
    previous: LifecycleState | None
    current: LifecycleState
    reason: str
    timestamp: str


class LifecycleController:
    """线程安全的任务运行控制器；暂停和取消在显式 checkpoint 生效。"""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._state = LifecycleState.CREATED
        self._reason = ""
        self._updated_at = self._now()
        self._history = [
            LifecycleEvent(None, self._state, "生命周期已创建", self._updated_at)
        ]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> LifecycleSnapshot:
        with self._condition:
            return LifecycleSnapshot(self._state, self._reason, self._updated_at)

    def history(self) -> tuple[LifecycleEvent, ...]:
        with self._condition:
            return tuple(self._history)

    @property
    def state(self) -> LifecycleState:
        return self.snapshot().state

    def _transition(self, state: LifecycleState, reason: str = "") -> None:
        if state not in LIFECYCLE_TRANSITIONS[self._state]:
            raise LifecycleTransitionError(
                f"非法生命周期迁移: {self._state.value} -> {state.value}"
            )
        previous = self._state
        self._state = state
        self._reason = reason.strip()
        self._updated_at = self._now()
        self._history.append(
            LifecycleEvent(previous, state, self._reason, self._updated_at)
        )
        self._condition.notify_all()

    def mark_queued(self) -> None:
        with self._condition:
            self._transition(LifecycleState.QUEUED, "任务已提交")

    def mark_running(self) -> None:
        with self._condition:
            self._transition(LifecycleState.RUNNING, "任务开始运行")

    def request_pause(self, reason: str = "用户暂停任务") -> bool:
        with self._condition:
            if self._state not in {LifecycleState.QUEUED, LifecycleState.RUNNING}:
                return False
            self._transition(LifecycleState.PAUSED, reason)
            return True

    def resume(self) -> bool:
        with self._condition:
            if self._state is not LifecycleState.PAUSED:
                return False
            self._transition(LifecycleState.RUNNING, "任务已恢复")
            return True

    def cancel(self, reason: str = "任务被取消") -> bool:
        with self._condition:
            normalized = reason.strip() or "任务被取消"
            if self._state in {LifecycleState.COMPLETED, LifecycleState.FAILED, LifecycleState.CANCELLED}:
                return False
            if self._state is LifecycleState.CREATED:
                self._transition(LifecycleState.CANCELLED, normalized)
            elif self._state is LifecycleState.QUEUED:
                self._transition(LifecycleState.CANCELLING, normalized)
            elif self._state in {LifecycleState.RUNNING, LifecycleState.PAUSED}:
                self._transition(LifecycleState.CANCELLING, normalized)
            self._condition.notify_all()
            return True

    def checkpoint(self) -> None:
        with self._condition:
            while self._state is LifecycleState.PAUSED:
                self._condition.wait()
            if self._state in {LifecycleState.CANCELLING, LifecycleState.CANCELLED}:
                raise TaskCancelledError(self._reason or "任务被取消")

    def mark_completed(self) -> None:
        with self._condition:
            self._transition(LifecycleState.COMPLETED, "任务完成")

    def mark_failed(self, reason: str) -> None:
        with self._condition:
            self._transition(LifecycleState.FAILED, reason)

    def mark_cancelled(self, reason: str = "任务已取消") -> None:
        with self._condition:
            if self._state is LifecycleState.CANCELLED:
                return
            self._transition(LifecycleState.CANCELLED, reason)


class CancellationToken:
    """旧接口兼容层；新代码应使用 LifecycleController。"""

    def __init__(self, controller: LifecycleController | None = None) -> None:
        self.controller = controller or LifecycleController()

    @property
    def cancelled(self) -> bool:
        return self.controller.state in {LifecycleState.CANCELLING, LifecycleState.CANCELLED}

    @property
    def reason(self) -> str:
        return self.controller.snapshot().reason

    def cancel(self, reason: str = "任务被取消") -> None:
        self.controller.cancel(reason)

    def raise_if_cancelled(self) -> None:
        self.controller.checkpoint()
