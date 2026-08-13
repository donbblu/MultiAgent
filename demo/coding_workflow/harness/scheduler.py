from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Mapping

from .task_graph import TaskExecutionState, TaskGraph, TaskSpec


@dataclass(frozen=True)
class GraphSnapshot:
    states: Mapping[str, TaskExecutionState]
    artifacts: Mapping[str, str]
    failures: Mapping[str, str]


class TaskGraphRuntime:
    """任务图的确定性运行状态；Dispatcher 可并发领取无冲突任务。"""

    def __init__(self, graph: TaskGraph) -> None:
        self.graph = graph
        self._states = {
            task_id: TaskExecutionState.PENDING for task_id in graph.tasks
        }
        self._artifacts: dict[str, str] = {}
        self._failures: dict[str, str] = {}
        self._lock = RLock()

    def claim_ready(self, limit: int) -> tuple[TaskSpec, ...]:
        if limit < 1:
            raise ValueError("领取数量必须大于 0")
        with self._lock:
            running = [
                task_id
                for task_id, state in self._states.items()
                if state is TaskExecutionState.RUNNING
            ]
            ready = self.graph.ready_tasks(
                self._states,
                running_task_ids=running,
                available_artifacts=self._artifacts,
                limit=limit,
            )
            for task in ready:
                self._states[task.task_id] = TaskExecutionState.RUNNING
            return ready

    def succeed(self, task_id: str, artifacts: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._require_running(task_id)
            task = self.graph.tasks[task_id]
            produced = dict(artifacts or {})
            missing = set(task.output_artifacts) - produced.keys()
            extra = produced.keys() - set(task.output_artifacts)
            if missing or extra:
                raise ValueError(
                    f"任务 {task_id} 的 Artifact 不匹配，缺少 {sorted(missing)}，多出 {sorted(extra)}"
                )
            self._artifacts.update(produced)
            self._states[task_id] = TaskExecutionState.SUCCEEDED

    def fail(self, task_id: str, reason: str) -> None:
        with self._lock:
            self._require_running(task_id)
            self._states[task_id] = TaskExecutionState.FAILED
            self._failures[task_id] = reason or "任务执行失败"
            self._block_dependents(task_id)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            state = self._states[task_id]
            if state in {TaskExecutionState.SUCCEEDED, TaskExecutionState.FAILED}:
                raise ValueError(f"终态任务不能取消: {task_id}")
            self._states[task_id] = TaskExecutionState.CANCELLED
            self._block_dependents(task_id)

    def _block_dependents(self, task_id: str) -> None:
        for candidate in self.graph.tasks.values():
            if task_id in self.graph.transitive_dependencies(candidate.task_id):
                if self._states[candidate.task_id] is TaskExecutionState.PENDING:
                    self._states[candidate.task_id] = TaskExecutionState.BLOCKED

    def _require_running(self, task_id: str) -> None:
        if task_id not in self._states:
            raise KeyError(f"任务不存在: {task_id}")
        if self._states[task_id] is not TaskExecutionState.RUNNING:
            raise ValueError(f"任务 {task_id} 当前不可提交结果")

    def snapshot(self) -> GraphSnapshot:
        with self._lock:
            return GraphSnapshot(
                MappingProxyType(dict(self._states)),
                MappingProxyType(dict(self._artifacts)),
                MappingProxyType(dict(self._failures)),
            )

    @property
    def finished(self) -> bool:
        terminal = {
            TaskExecutionState.SUCCEEDED,
            TaskExecutionState.FAILED,
            TaskExecutionState.BLOCKED,
            TaskExecutionState.CANCELLED,
        }
        with self._lock:
            return all(state in terminal for state in self._states.values())
