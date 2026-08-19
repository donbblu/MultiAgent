from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from ..artifacts import ArtifactStore
from ..memory import MemoryManager
from ..models import TaskContext
from ..roles import RoleRegistry
from .executor import GraphExecutionResult, TaskGraphExecutor
from .lifecycle import LifecycleController, LifecycleState
from .registry import WorkerRegistry
from .task_graph import TaskGraph


class ConvergenceAction(str, Enum):
    COMPLETE = "complete"
    REWORK = "rework"
    FAIL = "fail"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True)
class ConvergenceDecision:
    action: ConvergenceAction
    summary: str
    gate_artifact_ref: str = ""


@dataclass(frozen=True)
class ScenarioRoundPlan:
    graph: TaskGraph
    workers: WorkerRegistry
    roles: RoleRegistry
    memory: MemoryManager
    initial_artifacts: Mapping[str, str]


@dataclass(frozen=True)
class ScenarioRunState:
    run_id: str
    task_id: str
    project_id: str
    scenario: str
    status: str
    current_round: int
    max_rework_rounds: int
    round_snapshot_ids: tuple[str, ...]
    active_snapshot_id: str
    round_artifacts: tuple[Mapping[str, str], ...] = ()
    active_artifacts: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    gate_artifact_ref: str = ""
    result_artifact_ref: str = ""
    summary: str = ""
    request_fingerprint: str = ""
    workspace_hashes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    version: int = 1


class ScenarioStateStore(Protocol):
    def load(self, run_id: str) -> ScenarioRunState | None: ...
    def save(self, state: ScenarioRunState) -> None: ...


class ScenarioRuntimeStore(Protocol):
    def load(self, snapshot_id: str) -> Any: ...
    def save(self, snapshot: object) -> None: ...
    def validate_workspace(
        self, snapshot: object, current_hashes: Mapping[str, str]
    ) -> None: ...


class ScenarioProfile(Protocol):
    name: str
    max_rework_rounds: int

    def build_round(
        self, state: ScenarioRunState, lifecycle: LifecycleController
    ) -> ScenarioRoundPlan: ...

    def decide(
        self,
        state: ScenarioRunState,
        execution: GraphExecutionResult,
        artifacts: ArtifactStore,
    ) -> ConvergenceDecision: ...

    def finalize(
        self,
        state: ScenarioRunState,
        artifacts: ArtifactStore,
        decision: ConvergenceDecision,
    ) -> tuple[str, object]: ...

    def restore_result(
        self, result_artifact_ref: str, artifacts: ArtifactStore
    ) -> object: ...


class ScenarioRuntime:
    """统一控制多轮 DAG、收敛决策、快照与终态。"""

    TERMINAL = frozenset({"completed", "failed", "needs_input"})

    def __init__(
        self,
        *,
        runtime_store: ScenarioRuntimeStore,
        scenario_store: ScenarioStateStore,
        workspace_hashes_provider: Callable[[], Mapping[str, str]],
        max_workers: int = 3,
        checkpoint_hook: Callable[[str, ScenarioRunState], None] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self.runtime_store = runtime_store
        self.scenario_store = scenario_store
        self.workspace_hashes_provider = workspace_hashes_provider
        self.max_workers = max_workers
        self.checkpoint_hook = checkpoint_hook

    def run(
        self,
        *,
        run_id: str,
        task: TaskContext,
        profile: ScenarioProfile,
        artifacts: ArtifactStore,
        lifecycle: LifecycleController | None = None,
    ) -> object:
        controller = lifecycle or LifecycleController()
        state = self.scenario_store.load(run_id)
        restored: Any = None
        if state is None:
            snapshot_id = self._round_snapshot_id(run_id, 0)
            state = ScenarioRunState(
                run_id, task.task_id, task.project_id, profile.name, "running",
                0, profile.max_rework_rounds, (snapshot_id,), snapshot_id,
                workspace_hashes=MappingProxyType(
                    dict(self.workspace_hashes_provider())
                ),
                request_fingerprint=self._request_fingerprint(task),
            )
            self.scenario_store.save(state)
        else:
            self._validate_identity(state, task, profile)
            restored = self.runtime_store.load(state.active_snapshot_id)
            if restored is not None:
                self.runtime_store.validate_workspace(
                    restored, self.workspace_hashes_provider()
                )
                artifacts.replace_with(restored.artifacts)
            elif state.status in self.TERMINAL:
                raise RuntimeError("场景终态缺少 Runtime Snapshot")
            if state.status in self.TERMINAL:
                if not state.result_artifact_ref:
                    raise RuntimeError(state.summary or "场景执行失败")
                return profile.restore_result(state.result_artifact_ref, artifacts)

        while True:
            plan = profile.build_round(state, controller)
            round_snapshot = (
                restored
                if restored is not None
                and restored.snapshot_id == state.active_snapshot_id
                else None
            )
            graph = round_snapshot.graph if round_snapshot else plan.graph
            execution = TaskGraphExecutor(
                graph,
                plan.workers,
                plan.roles,
                plan.memory,
                artifacts=artifacts,
                lifecycle=controller,
                max_workers=self.max_workers,
                finalize_lifecycle=False,
                runtime_snapshot=round_snapshot,
                runtime_store=self.runtime_store,
                snapshot_id=state.active_snapshot_id,
                workspace_hashes_provider=self.workspace_hashes_provider,
                initial_artifacts=(
                    plan.initial_artifacts if round_snapshot is None else None
                ),
                checkpoint_hook=(
                    lambda node_id, snapshot: self._checkpoint(
                        f"node_checkpointed:{node_id}", state
                    )
                ) if self.checkpoint_hook else None,
            ).run(task)
            restored = None
            self._checkpoint("round_graph_completed", state)
            execution_error = ""
            if not execution.succeeded:
                execution_error = "; ".join(
                    execution.snapshot.failures.values()
                ) or "场景 DAG 执行失败"
                decision = ConvergenceDecision(
                    ConvergenceAction.FAIL, execution_error
                )
            else:
                decision = profile.decide(state, execution, artifacts)
            round_artifacts = list(state.round_artifacts)
            current_artifacts = MappingProxyType(
                dict(execution.snapshot.artifacts)
            )
            if len(round_artifacts) == state.current_round:
                round_artifacts.append(current_artifacts)
            elif len(round_artifacts) > state.current_round:
                round_artifacts[state.current_round] = current_artifacts
            else:
                raise RuntimeError("场景轮次 Artifact 历史不连续")
            state = replace(
                state,
                round_artifacts=tuple(round_artifacts),
                active_artifacts=current_artifacts,
                gate_artifact_ref=decision.gate_artifact_ref,
                summary=decision.summary,
                workspace_hashes=MappingProxyType(
                    dict(self.workspace_hashes_provider())
                ),
                version=state.version + 1,
            )
            self._checkpoint("decision_recorded", state)
            if execution_error:
                state = replace(
                    state, status="failed", summary=execution_error,
                    version=state.version + 1,
                )
                if controller.state is LifecycleState.CREATED:
                    controller.mark_running()
                controller.mark_failed(execution_error)
                self.scenario_store.save(state)
                raise RuntimeError(execution_error)

            if (
                decision.action is ConvergenceAction.REWORK
                and state.current_round < state.max_rework_rounds
            ):
                next_round = state.current_round + 1
                next_snapshot_id = self._round_snapshot_id(run_id, next_round)
                state = replace(
                    state,
                    current_round=next_round,
                    round_snapshot_ids=state.round_snapshot_ids + (
                        next_snapshot_id,
                    ),
                    active_snapshot_id=next_snapshot_id,
                    status="running",
                    version=state.version + 1,
                )
                self.scenario_store.save(state)
                self._checkpoint("rework_round_created", state)
                continue
            if decision.action is ConvergenceAction.REWORK:
                decision = ConvergenceDecision(
                    ConvergenceAction.FAIL,
                    "场景修复次数已耗尽",
                    decision.gate_artifact_ref,
                )

            status = {
                ConvergenceAction.COMPLETE: "completed",
                ConvergenceAction.FAIL: "failed",
                ConvergenceAction.NEEDS_INPUT: "needs_input",
            }[decision.action]
            state = replace(
                state, status=status, summary=decision.summary,
                version=state.version + 1,
            )
            result_ref, result = profile.finalize(state, artifacts, decision)
            state = replace(
                state,
                result_artifact_ref=result_ref,
                workspace_hashes=MappingProxyType(
                    dict(self.workspace_hashes_provider())
                ),
                version=state.version + 1,
            )
            if controller.state is LifecycleState.CREATED:
                controller.mark_running()
            if status == "completed":
                controller.mark_completed()
            else:
                controller.mark_failed(decision.summary)
            from ..runtime_sqlite import RuntimeSnapshot

            self.runtime_store.save(RuntimeSnapshot(
                state.active_snapshot_id,
                task.task_id,
                task.project_id,
                f"scenario_{status}",
                graph,
                execution.snapshot,
                execution.attempts,
                controller.snapshot(),
                artifacts,
                self.workspace_hashes_provider(),
                {"scenario_run_id": run_id, "scenario": profile.name},
            ))
            self.scenario_store.save(state)
            self._checkpoint("scenario_finalized", state)
            return result

    def _checkpoint(self, event: str, state: ScenarioRunState) -> None:
        if self.checkpoint_hook:
            self.checkpoint_hook(event, state)

    @staticmethod
    def _round_snapshot_id(run_id: str, round_index: int) -> str:
        return f"{run_id}:round:{round_index}"

    @staticmethod
    def _validate_identity(
        state: ScenarioRunState,
        task: TaskContext,
        profile: ScenarioProfile,
    ) -> None:
        if (
            state.task_id != task.task_id
            or state.project_id != task.project_id
            or state.scenario != profile.name
            or (
                state.request_fingerprint
                and state.request_fingerprint
                != ScenarioRuntime._request_fingerprint(task)
            )
        ):
            raise ValueError("场景快照与当前任务、项目或场景不匹配")

    @staticmethod
    def _request_fingerprint(task: TaskContext) -> str:
        value = repr((
            task.objective,
            task.user_request,
            tuple(task.acceptance_criteria),
            tuple(tuple(item) for item in task.verification_commands),
            tuple(task.allowed_paths),
            tuple(task.prohibited_actions),
        ))
        return sha256(value.encode("utf-8")).hexdigest()
