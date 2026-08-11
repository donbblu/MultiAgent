from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol

from ..models import TaskContext
from .lifecycle import LifecycleController, LifecycleEvent, LifecycleSnapshot, LifecycleState


class HarnessRunner(Protocol):
    def run(self, task: TaskContext) -> TaskContext: ...


HarnessFactory = Callable[[LifecycleController], HarnessRunner]


@dataclass(frozen=True)
class TaskStatus:
    task_id: str
    lifecycle: LifecycleSnapshot
    workflow_state: str
    attempt: int
    version: int


class TaskHandle:
    """提交任务后立即返回的控制句柄。"""

    def __init__(
        self,
        task: TaskContext,
        controller: LifecycleController,
        future: Future[TaskContext],
    ) -> None:
        self._task = task
        self._controller = controller
        self._future = future

    @property
    def task_id(self) -> str:
        return self._task.task_id

    def status(self) -> TaskStatus:
        return TaskStatus(
            self.task_id,
            self._controller.snapshot(),
            self._task.state.value,
            self._task.attempt,
            self._task.version,
        )

    def lifecycle_history(self) -> tuple[LifecycleEvent, ...]:
        return self._controller.history()

    def pause(self, reason: str = "用户暂停任务") -> bool:
        return self._controller.request_pause(reason)

    def resume(self) -> bool:
        return self._controller.resume()

    def cancel(self, reason: str = "用户取消任务") -> bool:
        accepted = self._controller.cancel(reason)
        if accepted and self._future.cancel():
            self._controller.mark_cancelled(reason)
        return accepted

    def result(self, timeout: float | None = None) -> TaskContext:
        return self._future.result(timeout=timeout)

    def done(self) -> bool:
        return self._future.done()


class TaskDispatcher:
    """单机任务投递器；提交、追踪与控制不依赖具体模型供应商。"""

    def __init__(self, harness_factory: HarnessFactory, max_workers: int = 1) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self._factory = harness_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="harness-task"
        )
        self._handles: dict[str, TaskHandle] = {}
        self._lock = RLock()
        self._accepting = True

    @staticmethod
    def _execute(
        factory: HarnessFactory,
        task: TaskContext,
        controller: LifecycleController,
    ) -> TaskContext:
        try:
            return factory(controller).run(task)
        except BaseException as exc:
            if controller.state is LifecycleState.RUNNING:
                controller.mark_failed(str(exc) or type(exc).__name__)
            raise

    def submit(self, task: TaskContext) -> TaskHandle:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("TaskDispatcher 已停止接收任务")
            if task.task_id in self._handles:
                raise ValueError(f"任务 ID 已存在: {task.task_id}")
            controller = LifecycleController()
            controller.mark_queued()
            future = self._executor.submit(self._execute, self._factory, task, controller)
            handle = TaskHandle(task, controller, future)
            self._handles[task.task_id] = handle
            return handle

    def get(self, task_id: str) -> TaskHandle:
        with self._lock:
            try:
                return self._handles[task_id]
            except KeyError as exc:
                raise KeyError(f"任务不存在: {task_id}") from exc

    def statuses(self) -> tuple[TaskStatus, ...]:
        with self._lock:
            return tuple(handle.status() for handle in self._handles.values())

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_running: bool = False,
        reason: str = "Harness 正在优雅退出",
    ) -> None:
        with self._lock:
            self._accepting = False
            handles = tuple(self._handles.values())
        if cancel_running:
            for handle in handles:
                if handle.status().lifecycle.state not in {
                    LifecycleState.COMPLETED,
                    LifecycleState.FAILED,
                    LifecycleState.CANCELLED,
                }:
                    handle.cancel(reason)
        self._executor.shutdown(wait=wait, cancel_futures=cancel_running)

    def __enter__(self) -> "TaskDispatcher":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.shutdown(wait=True, cancel_running=False)
