from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class GraphValidationError(ValueError):
    """任务图不满足确定性执行约束。"""


class TaskExecutionState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskSpec:
    """Planner 产生、Harness 校验后才可执行的原子任务。"""

    task_id: str
    title: str
    objective: str
    role: str
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    context_queries: tuple[str, ...] = ()
    risk_level: str = "low"
    timeout_seconds: int = 120
    retry_limit: int = 1
    priority: int = 0

    def __post_init__(self) -> None:
        if not all((self.task_id.strip(), self.title.strip(), self.objective.strip(), self.role.strip())):
            raise GraphValidationError("任务 ID、标题、目标和角色不能为空")
        if not self.acceptance_criteria:
            raise GraphValidationError(f"任务 {self.task_id} 缺少验收条件")
        if self.timeout_seconds <= 0 or self.retry_limit < 0:
            raise GraphValidationError(f"任务 {self.task_id} 的超时或重试配置无效")
        if self.risk_level not in {"low", "medium", "high"}:
            raise GraphValidationError(f"任务 {self.task_id} 的风险级别无效")


@dataclass(frozen=True)
class ResourceConflict:
    left_task_id: str
    right_task_id: str
    scope: str
    kind: str


class TaskGraph:
    """不可变任务 DAG；负责依赖、Artifact 和资源冲突校验。"""

    def __init__(self, tasks: Iterable[TaskSpec]) -> None:
        items = tuple(tasks)
        if not items:
            raise GraphValidationError("任务图不能为空")
        by_id = {task.task_id: task for task in items}
        if len(by_id) != len(items):
            raise GraphValidationError("任务 ID 不能重复")
        self._tasks: Mapping[str, TaskSpec] = MappingProxyType(by_id)
        self._validate_dependencies()
        self._validate_acyclic()
        self._validate_artifacts()

    @property
    def tasks(self) -> Mapping[str, TaskSpec]:
        return self._tasks

    def _validate_dependencies(self) -> None:
        ids = self._tasks.keys()
        for task in self._tasks.values():
            missing = set(task.dependencies) - ids
            if missing:
                raise GraphValidationError(
                    f"任务 {task.task_id} 依赖不存在: {sorted(missing)}"
                )

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise GraphValidationError(f"任务图存在环: {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in self._tasks[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in self._tasks:
            visit(task_id)

    def _validate_artifacts(self) -> None:
        producers: dict[str, str] = {}
        for task in self._tasks.values():
            for artifact in task.output_artifacts:
                if artifact in producers:
                    raise GraphValidationError(
                        f"Artifact {artifact} 有多个生产者: {producers[artifact]}, {task.task_id}"
                    )
                producers[artifact] = task.task_id
        for task in self._tasks.values():
            for artifact in task.input_artifacts:
                producer = producers.get(artifact)
                if producer is None:
                    raise GraphValidationError(
                        f"任务 {task.task_id} 的输入 Artifact 不存在: {artifact}"
                    )
                if producer not in self.transitive_dependencies(task.task_id):
                    raise GraphValidationError(
                        f"任务 {task.task_id} 未依赖 Artifact {artifact} 的生产者 {producer}"
                    )

    def transitive_dependencies(self, task_id: str) -> frozenset[str]:
        result: set[str] = set()

        def collect(current: str) -> None:
            for dependency in self._tasks[current].dependencies:
                if dependency not in result:
                    result.add(dependency)
                    collect(dependency)

        collect(task_id)
        return frozenset(result)

    @staticmethod
    def conflicts(left: TaskSpec, right: TaskSpec) -> tuple[ResourceConflict, ...]:
        conflicts: list[ResourceConflict] = []
        for scope in sorted(set(left.write_scopes) & set(right.write_scopes)):
            conflicts.append(ResourceConflict(left.task_id, right.task_id, scope, "write-write"))
        for scope in sorted(set(left.write_scopes) & set(right.read_scopes)):
            conflicts.append(ResourceConflict(left.task_id, right.task_id, scope, "write-read"))
        for scope in sorted(set(left.read_scopes) & set(right.write_scopes)):
            conflicts.append(ResourceConflict(left.task_id, right.task_id, scope, "read-write"))
        return tuple(conflicts)

    def ready_tasks(
        self,
        states: Mapping[str, TaskExecutionState],
        *,
        running_task_ids: Iterable[str] = (),
        available_artifacts: Iterable[str] = (),
        limit: int | None = None,
    ) -> tuple[TaskSpec, ...]:
        """选出依赖满足、Artifact 齐全且与运行任务无资源冲突的任务。"""
        artifacts = set(available_artifacts)
        running = tuple(self._tasks[item] for item in running_task_ids)
        candidates = [
            task
            for task in self._tasks.values()
            if states.get(task.task_id, TaskExecutionState.PENDING)
            in {TaskExecutionState.PENDING, TaskExecutionState.READY}
            and all(states.get(dep) is TaskExecutionState.SUCCEEDED for dep in task.dependencies)
            and set(task.input_artifacts) <= artifacts
            and not any(self.conflicts(task, active) for active in running)
        ]
        candidates.sort(key=lambda task: (-task.priority, task.task_id))

        selected: list[TaskSpec] = []
        for candidate in candidates:
            if any(self.conflicts(candidate, other) for other in selected):
                continue
            selected.append(candidate)
            if limit is not None and len(selected) >= limit:
                break
        return tuple(selected)
