from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from ..artifacts import Artifact, ArtifactStore
from ..memory import (
    FailureObservation,
    MemoryKind,
    MemoryManager,
    MemoryRecord,
    RoleMemoryView,
    WorkingArtifactState,
    WorkingNodeState,
)
from ..models import ImplementationPlan, ProjectFile, TaskContext
from ..roles import RoleRegistry
from ..runtime_sqlite import RuntimeSnapshot, SQLiteRuntimeStore
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
        project_files_provider: Callable[
            [TaskSpec, TaskContext], tuple[ProjectFile, ...]
        ] | None = None,
        runtime_snapshot: RuntimeSnapshot | None = None,
        runtime_store: SQLiteRuntimeStore | None = None,
        snapshot_id: str = "",
        workspace_hashes_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self.runtime = TaskGraphRuntime(
            graph, runtime_snapshot.graph_snapshot if runtime_snapshot else None
        )
        self.workers = workers
        self.roles = roles
        self.memory = memory
        self.artifacts = artifacts or ArtifactStore()
        self.lifecycle = lifecycle or LifecycleController()
        self.max_workers = max_workers
        self.finalize_lifecycle = finalize_lifecycle
        self.project_files_provider = project_files_provider
        self._attempts: dict[str, int] = (
            dict(runtime_snapshot.attempts)
            if runtime_snapshot else {task_id: 0 for task_id in graph.tasks}
        )
        self.runtime_store = runtime_store
        self.snapshot_id = snapshot_id
        self.workspace_hashes_provider = workspace_hashes_provider
        self._lock = RLock()

    def _save_runtime(self, parent: TaskContext, phase: str) -> None:
        if not self.runtime_store or not self.snapshot_id:
            return
        hashes = (
            dict(self.workspace_hashes_provider())
            if self.workspace_hashes_provider else {}
        )
        self.runtime_store.save(RuntimeSnapshot(
            self.snapshot_id, parent.task_id, parent.project_id, phase,
            self.runtime.graph, self.runtime.snapshot(),
            MappingProxyType(dict(self._attempts)), self.lifecycle.snapshot(),
            self.artifacts, MappingProxyType(hashes),
        ))

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
            project_id=parent.project_id,
            tech_stack=dict(parent.tech_stack), constraints=list(parent.constraints),
            allowed_paths=list(spec.write_scopes or parent.allowed_paths),
            prohibited_actions=list(parent.prohibited_actions),
            assumptions=list(parent.assumptions), attempt=max(parent.attempt, attempt),
            feedback=list(parent.feedback),
        )
        query = " ".join(spec.context_queries)
        project_files = (
            self.project_files_provider(spec, parent)
            if self.project_files_provider else ()
        )
        view = self.memory.build(
            child, role, project_files, trigger="task_claimed", query=query
        )
        return TaskRunRequest(spec, parent, view, inputs, attempt)

    def _run_one(self, spec: TaskSpec, parent: TaskContext) -> TaskRunResult:
        worker = self.workers.resolve(spec.role)
        if not hasattr(worker, "run_task"):
            raise TypeError(f"角色 {spec.role} 的 Worker 不支持 run_task")
        last = TaskRunResult(False, "未执行", error="未执行")
        for attempt in range(1, spec.retry_limit + 2):
            with self._lock:
                self._attempts[spec.task_id] = attempt
            self.memory.update_node(parent.task_id, WorkingNodeState(
                spec.task_id, spec.role, "running", attempt=attempt,
                input_artifacts=spec.input_artifacts,
                output_artifacts=spec.output_artifacts,
            ))
            self.lifecycle.checkpoint()
            request = self._request(spec, parent, attempt)
            result = worker.run_task(request)
            if not isinstance(result, TaskRunResult):
                raise TypeError("Worker 必须返回 TaskRunResult")
            last = result
            if result.success:
                return result
            failure_id = f"worker:{spec.task_id}:{attempt}"
            self.memory.update_node(parent.task_id, WorkingNodeState(
                spec.task_id, spec.role,
                "retrying" if attempt <= spec.retry_limit else "failed",
                attempt=attempt, summary=result.summary,
                last_error=result.error or result.summary,
                input_artifacts=spec.input_artifacts,
                output_artifacts=spec.output_artifacts,
            ))
            self.memory.observe_failure(parent.task_id, FailureObservation(
                failure_id, spec.role, result.error or result.summary,
                feedback=(result.error or result.summary,),
            ))
            self.memory.record(MemoryRecord.create(
                MemoryKind.PERCEPTION, "worker_failure",
                result.error or result.summary,
                task_id=parent.task_id, source=spec.role,
                project_id=parent.project_id,
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
        self.memory.register_artifact_names(parent.task_id, references)
        self.memory.update_node(parent.task_id, WorkingNodeState(
            spec.task_id, spec.role, "succeeded",
            attempt=self._attempts[spec.task_id], summary=result.summary,
            input_artifacts=spec.input_artifacts,
            output_artifacts=tuple(references.values()),
        ))
        for name, reference in references.items():
            content = result.artifacts[name]
            affected_paths = (
                tuple(change.path for change in content.changes)
                if isinstance(content, ImplementationPlan) else ()
            )
            self.memory.update_artifact(parent.task_id, WorkingArtifactState(
                reference, spec.task_id, "unverified",
                affected_paths=affected_paths,
            ))
        worker_failures = self.memory.failure_ids(
            parent.task_id, prefix=f"worker:{spec.task_id}:"
        )
        if worker_failures:
            self.memory.resolve_failures(
                parent.task_id, spec.task_id, worker_failures
            )
        self.memory.record(MemoryRecord.create(
            MemoryKind.WORKING, "node_result", result.summary,
            task_id=parent.task_id, source=spec.role, source_ref=spec.task_id,
            project_id=parent.project_id,
            visibility=tuple(self.roles.names()), evidence_refs=tuple(references.values()),
        ))
        self.memory.save_checkpoint(parent.task_id)

    def run(self, parent: TaskContext) -> GraphExecutionResult:
        working = self.memory.working_memory(parent.task_id)
        for spec in self.runtime.graph.tasks.values():
            if spec.task_id not in working.nodes:
                self.memory.update_node(parent.task_id, WorkingNodeState(
                    spec.task_id, spec.role, "pending",
                    input_artifacts=spec.input_artifacts,
                    output_artifacts=spec.output_artifacts,
                ))
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
                    self._save_runtime(parent, "executing")
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
                                self.memory.update_node(parent.task_id, WorkingNodeState(
                                    spec.task_id, spec.role, "failed",
                                    attempt=self._attempts[spec.task_id],
                                    summary=result.summary,
                                    last_error=result.error or result.summary,
                                    input_artifacts=spec.input_artifacts,
                                    output_artifacts=spec.output_artifacts,
                                ))
                                self.memory.save_checkpoint(parent.task_id)
                        except TaskCancelledError:
                            self.runtime.cancel(spec.task_id)
                            self.memory.update_node(parent.task_id, WorkingNodeState(
                                spec.task_id, spec.role, "cancelled",
                                attempt=self._attempts[spec.task_id],
                            ))
                        except BaseException as exc:
                            self.runtime.fail(spec.task_id, str(exc) or type(exc).__name__)
                            self.memory.update_node(parent.task_id, WorkingNodeState(
                                spec.task_id, spec.role, "failed",
                                attempt=self._attempts[spec.task_id],
                                last_error=str(exc) or type(exc).__name__,
                            ))
                            self.memory.save_checkpoint(parent.task_id)
                        self._save_runtime(parent, "executing")
            snapshot = self.runtime.snapshot()
            working_nodes = self.memory.node_states(parent.task_id)
            for task_id, state in snapshot.states.items():
                current = working_nodes[task_id]
                if current.state != state.value:
                    self.memory.update_node(parent.task_id, WorkingNodeState(
                        task_id, current.role, state.value,
                        attempt=current.attempt, summary=current.summary,
                        last_error=current.last_error,
                        input_artifacts=current.input_artifacts,
                        output_artifacts=current.output_artifacts,
                    ))
            if all(state.value == "succeeded" for state in snapshot.states.values()):
                if self.finalize_lifecycle:
                    verified_artifacts = tuple(snapshot.artifacts.values())
                    self.artifacts.mark_verified(verified_artifacts, ())
                    for reference in verified_artifacts:
                        current = self.memory.working_memory(
                            parent.task_id
                        ).artifacts[reference]
                        self.memory.update_artifact(
                            parent.task_id,
                            WorkingArtifactState(
                                reference, current.producer_node_id, "verified",
                                affected_paths=current.affected_paths,
                            ),
                        )
                    self.memory.consolidate(
                        parent.task_id,
                        project_id=parent.project_id,
                        verified_artifacts=verified_artifacts,
                    )
                    self.lifecycle.mark_completed()
            else:
                if self.finalize_lifecycle:
                    self.lifecycle.mark_failed("一个或多个子任务失败")
            self._save_runtime(parent, "graph_completed")
            return GraphExecutionResult(snapshot, MappingProxyType(dict(self._attempts)))
        except TaskCancelledError as exc:
            for task_id, state in self.runtime.snapshot().states.items():
                if state.value in {"pending", "ready", "running"}:
                    self.runtime.cancel(task_id)
            self.memory.save_checkpoint(parent.task_id)
            self.lifecycle.mark_cancelled(str(exc))
            self._save_runtime(parent, "cancelled")
            return GraphExecutionResult(self.runtime.snapshot(), MappingProxyType(dict(self._attempts)))
