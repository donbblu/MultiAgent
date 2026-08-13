from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactStore
from .agents import CommandVerificationAgent
from .backends import StructuredCodingBackend
from .graph_workers import PlanningCodingWorker
from .harness import LifecycleController, TaskGraphExecutor, WorkerRegistry
from .integration import IntegrationError, PatchIntegrator
from .memory import MemoryManager
from .memory_sqlite import SQLiteMemoryStore
from .model import ModelClient
from .models import TaskContext, TaskState
from .planning import StructuredTaskPlanner
from .policy import CommandPolicy
from .roles import DEFAULT_ROLES
from .validation import PlanValidator
from .workspace import ProjectWorkspace


@dataclass(frozen=True)
class DagRunResult:
    task: TaskContext
    graph_states: dict[str, str]


def run_dag_task(
    task: TaskContext,
    client: ModelClient,
    workspace: ProjectWorkspace,
    *,
    memory_path: Path,
    lifecycle: LifecycleController | None = None,
    max_workers: int = 3,
    command_policy: CommandPolicy | None = None,
    event_listener: Callable[[dict[str, Any]], None] | None = None,
) -> DagRunResult:
    """真实 DAG 路径：拆分、并发生成 Patch、集中合并。"""
    controller = lifecycle or LifecycleController()
    def emit(event: str, payload: dict[str, object]) -> None:
        if event_listener:
            event_listener({
                "event": event,
                "payload": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    emit("role_assigned", {"name": "planner", "attempt": 0, "objective": "生成并校验任务图"})
    task.transition(TaskState.PLANNING, "Planner 正在生成任务图")
    graph = StructuredTaskPlanner(client).create_graph(task)
    emit("task_graph_created", {"tasks": [
        {"task_id": spec.task_id, "role": spec.role, "dependencies": list(spec.dependencies)}
        for spec in graph.tasks.values()
    ]})
    registry = WorkerRegistry()
    registry.register(
        "implementer",
        PlanningCodingWorker(
            StructuredCodingBackend(client), PlanValidator()
        ),
    )
    artifacts = ArtifactStore()
    memory = MemoryManager(store=SQLiteMemoryStore(memory_path))
    task.transition(TaskState.IMPLEMENTING, "开始并发执行任务图")
    emit("parallel_stage_started", {
        "roles": sorted({spec.role for spec in graph.tasks.values()}),
        "task_ids": list(graph.tasks),
    })
    execution = TaskGraphExecutor(
        graph, registry, DEFAULT_ROLES, memory, artifacts=artifacts,
        lifecycle=controller, max_workers=max_workers, finalize_lifecycle=False,
    ).run(task)
    states = {key: value.value for key, value in execution.snapshot.states.items()}
    emit("task_graph_finished", {"states": states, "attempts": dict(execution.attempts)})
    if not execution.succeeded:
        task.transition(TaskState.FAILED, "任务图执行失败")
        controller.mark_failed("任务图执行失败")
        return DagRunResult(task, states)
    produced = [artifacts.get(ref) for ref in execution.snapshot.artifacts.values()]
    try:
        PatchIntegrator(workspace, task.allowed_paths).integrate(produced)
    except IntegrationError as exc:
        task.feedback.append(str(exc))
        task.transition(TaskState.FAILED, f"Artifact 合并被拒绝: {exc}")
        controller.mark_failed(str(exc))
        emit("artifacts_rejected", {"reason": str(exc)})
        return DagRunResult(task, states)
    emit("artifacts_integrated", {"artifacts": list(execution.snapshot.artifacts)})
    task.transition(TaskState.VERIFYING, "Patch 已安全合并，开始质量验证")
    task.assign_role(DEFAULT_ROLES.get("tester"))
    emit("role_assigned", {"name": "tester", "attempt": 1, "objective": "运行质量门禁"})
    verification = CommandVerificationAgent(
        workspace, command_policy or CommandPolicy()
    ).run(task)
    task.verification = verification
    emit("agent_message", {
        "sender": "tester", "recipient": "coordinator", "message_type": "result",
        "summary": verification.summary, "payload": {"passed": verification.passed},
    })
    if not verification.passed:
        task.feedback.extend(verification.feedback)
        task.transition(TaskState.FAILED, verification.summary)
        controller.mark_failed(verification.summary)
        return DagRunResult(task, states)
    memory.consolidate(task.task_id, verified=True)
    task.transition(TaskState.COMPLETED, verification.summary)
    controller.mark_completed()
    return DagRunResult(task, states)
