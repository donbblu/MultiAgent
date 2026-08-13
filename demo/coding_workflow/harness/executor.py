from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Protocol

from ..artifacts import Artifact, ArtifactStore
from ..memory import MemoryKind, MemoryManager, MemoryRecord, RoleMemoryView
from ..models import TaskContext
from ..roles import RoleRegistry
from .lifecycle import LifecycleController, LifecycleState, TaskCancelledError
from .registry import WorkerRegistry
from .scheduler import GraphSnapshot, TaskGraphRuntime
from .task_graph import TaskGraph, TaskSpec


@dataclass(frozen=True)
class TaskRunRequest:
    task: TaskSpec
    parent: TaskContext
    memory: RoleMemoryView
    inputs: Mapping[str, Artifact]
    attempt: int


@dataclass(frozen=True)
class TaskRunResult:
    success: bool
    summary: str
    artifacts: Mapping[str, object] = field(default_factory=dict)
    error: str = ""


class GraphWorker(Protocol):
    def run_task(self, request: TaskRunRequest) -> TaskRunResult: ...


@dataclass(frozen=True)
class GraphExecutionResult:
    snapshot: GraphSnapshot
    attempts: Mapping[str, int]

    @property
    def succeeded(self) -> bool:
        return all(state.value == "succeeded" for state in self.snapshot.states.values())


class TaskGraphExecutor:
    """按 DAG 并发调度 Worker，Harness 独占状态和记忆写入权。"""

    def __init__(
        self,
        graph: TaskGraph,
        workers: WorkerRegistry,
        roles: RoleRegistry,
        memory: MemoryManager,
        *,
        artifacts: ArtifactStore | None = None,
        lifecycle: LifecycleController | None = None,
        max_workers: int = 3,
        finalize_lifecycle: bool = True,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self.runtime = TaskGraphRuntime(graph)
        self.workers = workers
        self.roles = roles
        self.memory = memory
        self.artifacts = artifacts or ArtifactStore()
        self.lifecycle = lifecycle or LifecycleController()
        self.max_workers = max_workers
        self.finalize_lifecycle = finalize_lifecycle
        self._attempts: dict[str, int] = {task_id: 0 for task_id in graph.tasks}
        self._lock = RLock()

    def _request(self, spec: TaskSpec, parent: TaskContext, attempt: int) -> TaskRunRequest:
        role = self.roles.get(spec.role)
        snapshot = self.runtime.snapshot()
        inputs = self.artifacts.resolve({
            name: snapshot.artifacts[name] for name in spec.input_artifacts
        })
        child = TaskContext(
            task_id=parent.task_id, objective=spec.objective,
            acceptance_criteria=list(spec.acceptance_criteria),
            user_request=parent.user_request, project_root=parent.project_root,
            tech_stack=dict(parent.tech_stack), constraints=list(parent.constraints),
            allowed_paths=list(spec.write_scopes or parent.allowed_paths),
            prohibited_actions=list(parent.prohibited_actions),
            assumptions=list(parent.assumptions), attempt=attempt,
        )
        query = " ".join(spec.context_queries)
        view = self.memory.build(child, role, trigger="task_claimed", query=query)
        return TaskRunRequest(spec, parent, view, inputs, attempt)

    def _run_one(self, spec: TaskSpec, parent: TaskContext) -> TaskRunResult:
        worker = self.workers.resolve(spec.role)
        if not hasattr(worker, "run_task"):
            raise TypeError(f"角色 {spec.role} 的 Worker 不支持 run_task")
        last = TaskRunResult(False, "未执行", error="未执行")
        for attempt in range(1, spec.retry_limit + 2):
            with self._lock:
                self._attempts[spec.task_id] = attempt
            self.lifecycle.checkpoint()
            request = self._request(spec, parent, attempt)
            result = worker.run_task(request)
            if not isinstance(result, TaskRunResult):
                raise TypeError("Worker 必须返回 TaskRunResult")
            last = result
            if result.success:
                return result
            self.memory.record(MemoryRecord.create(
                MemoryKind.PERCEPTION, "worker_failure",
                result.error or result.summary,
                task_id=parent.task_id, source=spec.role,
                source_ref=spec.task_id, visibility=(spec.role,),
                content={"attempt": attempt},
            ))
        return last

    def _accept(self, spec: TaskSpec, result: TaskRunResult, parent: TaskContext) -> None:
        expected = set(spec.output_artifacts)
        actual = set(result.artifacts)
        if expected != actual:
            raise ValueError(
                f"任务 {spec.task_id} 输出不匹配，缺少 {sorted(expected-actual)}，多出 {sorted(actual-expected)}"
            )
        references: dict[str, str] = {}
        for name, content in result.artifacts.items():
            references[name] = self.artifacts.put(Artifact.create(name, spec.task_id, content))
        self.runtime.succeed(spec.task_id, references)
        working = self.memory.working_memory(parent.task_id)
        working.node_summaries[spec.task_id] = result.summary
        working.active_artifacts.update(references)
        working.version += 1
        self.memory.record(MemoryRecord.create(
            MemoryKind.WORKING, "node_result", result.summary,
            task_id=parent.task_id, source=spec.role, source_ref=spec.task_id,
            visibility=tuple(self.roles.names()), evidence_refs=tuple(references.values()),
        ))
        self.memory.save_checkpoint(parent.task_id)

    def run(self, parent: TaskContext) -> GraphExecutionResult:
        if self.lifecycle.state is LifecycleState.CREATED:
            self.lifecycle.mark_running()
        elif self.lifecycle.state is LifecycleState.QUEUED:
            self.lifecycle.mark_running()
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="graph-worker") as pool:
                while not self.runtime.finished:
                    self.lifecycle.checkpoint()
                    batch = self.runtime.claim_ready(self.max_workers)
                    if not batch:
                        raise RuntimeError("任务图没有可执行任务且尚未结束")
                    futures: dict[Future[TaskRunResult], TaskSpec] = {
                        pool.submit(self._run_one, spec, parent): spec for spec in batch
                    }
                    for future in as_completed(futures):
                        spec = futures[future]
                        try:
                            result = future.result()
                            if result.success:
                                self._accept(spec, result, parent)
                            else:
                                self.runtime.fail(spec.task_id, result.error or result.summary)
                                self.memory.save_checkpoint(parent.task_id)
                        except TaskCancelledError:
                            self.runtime.cancel(spec.task_id)
                        except BaseException as exc:
                            self.runtime.fail(spec.task_id, str(exc) or type(exc).__name__)
                            self.memory.save_checkpoint(parent.task_id)
            snapshot = self.runtime.snapshot()
            if all(state.value == "succeeded" for state in snapshot.states.values()):
                if self.finalize_lifecycle:
                    self.memory.consolidate(parent.task_id, verified=True)
                    self.lifecycle.mark_completed()
            else:
                if self.finalize_lifecycle:
                    self.lifecycle.mark_failed("一个或多个子任务失败")
            return GraphExecutionResult(snapshot, MappingProxyType(dict(self._attempts)))
        except TaskCancelledError as exc:
            for task_id, state in self.runtime.snapshot().states.items():
                if state.value in {"pending", "ready", "running"}:
                    self.runtime.cancel(task_id)
            self.memory.save_checkpoint(parent.task_id)
            self.lifecycle.mark_cancelled(str(exc))
            return GraphExecutionResult(self.runtime.snapshot(), MappingProxyType(dict(self._attempts)))
