from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactStore
from .agents import CommandVerificationAgent
from .backends import StructuredCodingBackend
from .graph_workers import PlanningCodingWorker
from .harness import (
    GraphExecutionResult,
    LifecycleController,
    LifecycleState,
    TaskGraph,
    TaskGraphExecutor,
    TaskSpec,
    WorkerRegistry,
)
from .integration import IntegrationError, PatchIntegrator
from .memory import (
    FailureObservation,
    MemoryKind,
    MemoryManager,
    MemoryRecord,
    QualityGateState,
    WorkingArtifactState,
)
from .memory_sqlite import SQLiteMemoryStore
from .model import ModelClient
from .models import (
    ImplementationPlan,
    ProjectFile,
    TaskContext,
    TaskState,
    VerificationResult,
)
from .planning import StructuredTaskPlanner
from .policy import CommandPolicy
from .roles import DEFAULT_ROLES
from .runtime_sqlite import RuntimeSnapshot, SQLiteRuntimeStore
from .truth import workspace_digest
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
    max_rework_attempts: int = 2,
) -> DagRunResult:
    """真实 DAG 路径：拆分、并发生成 Patch、集中合并。"""
    if max_rework_attempts < 0:
        raise ValueError("max_rework_attempts 不能小于 0")
    if not task.project_id:
        task.project_id = sha256(str(workspace.root).encode("utf-8")).hexdigest()
    controller = lifecycle or LifecycleController()
    memory_store = SQLiteMemoryStore(memory_path)
    memory = MemoryManager(store=memory_store)
    runtime_store = SQLiteRuntimeStore(memory_path)
    runtime_snapshot_id = f"{task.task_id}:dag"
    excluded_hash_paths: set[str] = set()
    try:
        memory_relative = str(memory_path.resolve().relative_to(workspace.root))
        excluded_hash_paths.update({
            memory_relative, memory_relative + "-wal", memory_relative + "-shm",
        })
    except ValueError:
        pass

    def workspace_hashes() -> dict[str, str]:
        return workspace.content_hashes(exclude=excluded_hash_paths)

    restored = runtime_store.load(runtime_snapshot_id)
    if restored:
        if restored.task_id != task.task_id or restored.project_id != task.project_id:
            raise ValueError("运行快照与当前任务或项目不匹配")
        runtime_store.validate_workspace(restored, workspace_hashes())

    def emit(event: str, payload: dict[str, object]) -> None:
        if event_listener:
            event_listener({
                "event": event,
                "payload": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    task.transition(
        TaskState.PLANNING,
        "正在恢复任务图" if restored else "Planner 正在生成任务图",
    )
    if restored:
        graph = restored.graph
        artifacts = restored.artifacts
        emit("runtime_resumed", {
            "snapshot_id": runtime_snapshot_id,
            "phase": restored.phase,
            "reset_running_nodes": [
                task_id for task_id, state in restored.graph_snapshot.states.items()
                if state.value in {"running", "ready"}
            ],
        })
    else:
        emit("role_assigned", {
            "name": "planner", "attempt": 0, "objective": "生成并校验任务图"
        })
        graph = StructuredTaskPlanner(client).create_graph(task)
        artifacts = ArtifactStore()
        emit("task_graph_created", {"tasks": [
            {"task_id": spec.task_id, "role": spec.role,
             "dependencies": list(spec.dependencies)}
            for spec in graph.tasks.values()
        ]})
    if restored and (
        restored.phase == "cancelled"
        or restored.lifecycle.state in {
            LifecycleState.CANCELLING, LifecycleState.CANCELLED
        }
    ):
        reason = restored.lifecycle.reason or "任务已取消"
        task.transition(TaskState.CANCELLED, reason)
        controller.cancel(reason)
        return DagRunResult(task, {
            key: value.value
            for key, value in restored.graph_snapshot.states.items()
        })
    registry = WorkerRegistry()
    registry.register(
        "implementer",
        PlanningCodingWorker(
            StructuredCodingBackend(client), PlanValidator()
        ),
    )
    registry.register(
        "fixer",
        PlanningCodingWorker(
            StructuredCodingBackend(client), PlanValidator()
        ),
    )
    task.transition(TaskState.IMPLEMENTING, "开始并发执行任务图")
    emit("parallel_stage_started", {
        "roles": sorted({spec.role for spec in graph.tasks.values()}),
        "task_ids": list(graph.tasks),
    })
    if restored and restored.phase in {"integrated", "completed"}:
        execution = GraphExecutionResult(
            restored.graph_snapshot, restored.attempts
        )
    else:
        execution = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, memory, artifacts=artifacts,
            lifecycle=controller, max_workers=max_workers, finalize_lifecycle=False,
            runtime_snapshot=restored,
            runtime_store=runtime_store, snapshot_id=runtime_snapshot_id,
            workspace_hashes_provider=workspace_hashes,
        ).run(task)
    worker_selections_data = {
        task_id: dict(decision.to_dict())
        for task_id, decision in execution.worker_selections.items()
    }
    if not worker_selections_data and restored:
        restored_selections = restored.runner_data.get("worker_selections", {})
        if isinstance(restored_selections, dict):
            worker_selections_data = dict(restored_selections)
    states = {key: value.value for key, value in execution.snapshot.states.items()}
    emit("task_graph_finished", {
        "states": states, "attempts": dict(execution.attempts)
    })
    if controller.state is LifecycleState.CREATED:
        controller.mark_running()
    if restored and restored.phase == "completed":
        runner_data = restored.runner_data
        summary = str(runner_data.get("verification_summary", "任务已完成"))
        task.attempt = int(runner_data.get("task_attempt", 1))
        task.verification = VerificationResult(
            True, summary,
            evidence=list(runner_data.get("verification_evidence", ())),
        )
        task.transition(TaskState.VERIFYING, "从已完成快照恢复验证结果")
        task.transition(TaskState.COMPLETED, summary)
        controller.mark_completed()
        return DagRunResult(task, dict(runner_data.get("states", states)))
    if not execution.succeeded:
        task.transition(TaskState.FAILED, "任务图执行失败")
        controller.mark_failed("任务图执行失败")
        return DagRunResult(task, states)
    produced = [artifacts.get(ref) for ref in execution.snapshot.artifacts.values()]
    if restored and restored.phase == "integrated":
        runner_data = restored.runner_data
        current_artifact_refs = tuple(runner_data["current_artifact_refs"])
        relevant_paths = list(runner_data["relevant_paths"])
        task.attempt = int(runner_data.get("task_attempt", 1))
        last_fix_id = str(runner_data.get("last_fix_id", ""))
        states = dict(runner_data.get("states", states))
    else:
        current_artifact_refs = tuple(execution.snapshot.artifacts.values())
        try:
            PatchIntegrator(workspace, task.allowed_paths).integrate(produced)
        except IntegrationError as exc:
            task.feedback.append(str(exc))
            task.transition(TaskState.FAILED, f"Artifact 合并被拒绝: {exc}")
            controller.mark_failed(str(exc))
            emit("artifacts_rejected", {"reason": str(exc)})
            return DagRunResult(task, states)
        emit("artifacts_integrated", {"artifacts": list(execution.snapshot.artifacts)})
        relevant_paths = sorted({
            change.path
            for artifact in produced
            if isinstance(artifact.content, ImplementationPlan)
            for change in artifact.content.changes
        })
        task.attempt = 1
        last_fix_id = ""

    def save_runner_snapshot(phase: str, **extra: object) -> None:
        runner_data: dict[str, object] = {
            "current_artifact_refs": list(current_artifact_refs),
            "relevant_paths": list(relevant_paths),
            "task_attempt": task.attempt,
            "last_fix_id": last_fix_id,
            "states": dict(states),
            "worker_selections": worker_selections_data,
        }
        runner_data.update(extra)
        runtime_store.save(RuntimeSnapshot(
            runtime_snapshot_id, task.task_id, task.project_id, phase,
            graph, execution.snapshot, execution.attempts,
            controller.snapshot(), artifacts, workspace_hashes(), runner_data,
        ))

    if not restored or restored.phase != "integrated":
        save_runner_snapshot("integrated")
    verifier = CommandVerificationAgent(workspace, command_policy or CommandPolicy())
    while True:
        if task.state is not TaskState.VERIFYING:
            task.transition(TaskState.VERIFYING, "Patch 已安全合并，开始完整质量验证")
        task.assign_role(DEFAULT_ROLES.get("tester"))
        emit("role_assigned", {
            "name": "tester", "attempt": task.attempt, "objective": "运行质量门禁"
        })
        verification = verifier.run(task)
        task.verification = verification
        verification_refs = tuple(
            "verification://" + sha256(item.encode("utf-8")).hexdigest()
            for item in verification.evidence
        )
        emit("agent_message", {
            "sender": "tester", "recipient": "runtime", "message_type": "result",
            "summary": verification.summary, "payload": {"passed": verification.passed},
        })
        previous_gate = memory.working_memory(task.task_id).quality_gate
        memory.update_quality_gate(task.task_id, QualityGateState(
            affected_checks_completed=previous_gate.affected_checks_completed,
            affected_checks_passed=previous_gate.affected_checks_passed,
            full_gate_completed=True,
            passed=verification.passed,
            summary=verification.summary,
            verification_refs=verification_refs,
        ))
        if verification.passed:
            artifacts.mark_verified(
                current_artifact_refs,
                verification_refs,
                validator_kind="core:test",
                summary=verification.summary,
                workspace_hash=workspace_digest(workspace_hashes()),
            )
            for reference in current_artifact_refs:
                current = memory.working_memory(task.task_id).artifacts[reference]
                memory.update_artifact(task.task_id, WorkingArtifactState(
                    reference, current.producer_node_id, "verified",
                    affected_paths=current.affected_paths,
                    verification_refs=verification_refs,
                ))
            if last_fix_id:
                memory.resolve_failures(task.task_id, last_fix_id)
            memory.save_checkpoint(task.task_id)
            break
        artifacts.mark_failed(
            current_artifact_refs,
            verification_refs,
            validator_kind="core:test",
            summary=verification.summary,
            workspace_hash=workspace_digest(workspace_hashes()),
        )
        for reference in current_artifact_refs:
            current = memory.working_memory(task.task_id).artifacts[reference]
            memory.update_artifact(task.task_id, WorkingArtifactState(
                reference, current.producer_node_id, "failed",
                affected_paths=current.affected_paths,
                verification_refs=verification_refs,
            ))
        task.feedback = list(verification.feedback)
        memory.observe_failure(task.task_id, FailureObservation(
            f"verification:{task.attempt}", "tester", verification.summary,
            feedback=tuple(verification.feedback),
            affected_paths=tuple(relevant_paths),
            affected_artifacts=current_artifact_refs,
            evidence_refs=verification_refs,
        ))
        memory.record(MemoryRecord.create(
            MemoryKind.PERCEPTION, "verification_failure", verification.summary,
            task_id=task.task_id, source="tester", source_ref=f"verification:{task.attempt}",
            project_id=task.project_id,
            visibility=("fixer",), content={"feedback": list(verification.feedback)},
            evidence_refs=verification_refs,
        ))
        memory.save_checkpoint(task.task_id)
        if task.attempt > max_rework_attempts:
            task.transition(TaskState.FAILED, verification.summary)
            controller.mark_failed(verification.summary)
            return DagRunResult(task, states)

        task.transition(TaskState.REWORK, verification.summary)
        task.attempt += 1
        fix_id = f"fix-{task.attempt - 1}"
        last_fix_id = fix_id
        fix_artifact = f"{fix_id}-patch"
        fix_graph = TaskGraph((TaskSpec(
            fix_id, "修复验证失败", "根据验证失败证据修复相关文件", "fixer",
            acceptance_criteria=tuple(task.acceptance_criteria),
            read_scopes=tuple(relevant_paths), write_scopes=tuple(relevant_paths),
            output_artifacts=(fix_artifact,), retry_limit=0,
        ),))

        def relevant_files(spec: TaskSpec, parent: TaskContext) -> tuple[ProjectFile, ...]:
            del spec, parent
            existing = set(workspace.list_files())
            return tuple(
                ProjectFile(path, workspace.read_text(path))
                for path in relevant_paths
                if path in existing
            )

        task.assign_role(DEFAULT_ROLES.get("fixer"))
        task.transition(TaskState.IMPLEMENTING, f"开始第 {task.attempt} 次局部修复")
        emit("fix_task_created", {
            "task_id": fix_id, "write_scopes": relevant_paths,
            "feedback": list(task.feedback),
        })
        fix_execution = TaskGraphExecutor(
            fix_graph, registry, DEFAULT_ROLES, memory, artifacts=artifacts,
            lifecycle=controller, max_workers=1, finalize_lifecycle=False,
            project_files_provider=relevant_files,
        ).run(task)
        worker_selections_data.update({
            task_id: dict(decision.to_dict())
            for task_id, decision in fix_execution.worker_selections.items()
        })
        states.update({
            key: value.value for key, value in fix_execution.snapshot.states.items()
        })
        if not fix_execution.succeeded:
            task.transition(TaskState.FAILED, "局部修复任务失败")
            controller.mark_failed("局部修复任务失败")
            return DagRunResult(task, states)
        fix_refs = fix_execution.snapshot.artifacts
        fix_reference_values = tuple(fix_refs.values())
        fix_outputs = [artifacts.get(ref) for ref in fix_refs.values()]
        try:
            changed = PatchIntegrator(workspace, relevant_paths).integrate(fix_outputs)
        except IntegrationError as exc:
            task.feedback.append(str(exc))
            task.transition(TaskState.FAILED, f"修复 Artifact 合并被拒绝: {exc}")
            controller.mark_failed(str(exc))
            return DagRunResult(task, states)
        if current_artifact_refs and fix_reference_values:
            artifacts.supersede(current_artifact_refs, fix_reference_values[0])
            for reference in current_artifact_refs:
                current = memory.working_memory(task.task_id).artifacts[reference]
                memory.update_artifact(task.task_id, WorkingArtifactState(
                    reference, current.producer_node_id, "superseded",
                    affected_paths=current.affected_paths,
                    superseded_by=fix_reference_values[0],
                    verification_refs=current.verification_refs,
                ))
        current_artifact_refs = fix_reference_values
        relevant_paths = sorted(set(relevant_paths) | set(changed.changed_files))
        emit("fix_artifacts_integrated", {
            "task_id": fix_id, "artifacts": list(fix_refs),
            "changed_files": list(changed.changed_files),
        })
        save_runner_snapshot("integrated")
        suggested_checks = [
            command
            for artifact in fix_outputs
            if isinstance(artifact.content, ImplementationPlan)
            for command in artifact.content.suggested_checks
            if command in task.verification_commands
        ]
        if suggested_checks:
            task.transition(TaskState.VERIFYING, "运行修复 Artifact 声明的受影响测试")
            task.assign_role(DEFAULT_ROLES.get("tester"))
            emit("role_assigned", {
                "name": "tester", "attempt": task.attempt,
                "objective": "运行受影响测试",
            })
            full_commands = task.verification_commands
            try:
                task.verification_commands = suggested_checks
                affected = verifier.run(task)
            finally:
                task.verification_commands = full_commands
            emit("affected_tests_finished", {
                "task_id": fix_id, "passed": affected.passed,
                "summary": affected.summary,
            })
            memory.update_quality_gate(task.task_id, QualityGateState(
                affected_checks_completed=True,
                affected_checks_passed=affected.passed,
                summary=affected.summary,
            ))
            memory.save_checkpoint(task.task_id)
    memory.consolidate(
        task.task_id,
        project_id=task.project_id,
        verified_artifacts=current_artifact_refs,
        verification_refs=verification_refs,
    )
    task.transition(TaskState.COMPLETED, verification.summary)
    controller.mark_completed()
    save_runner_snapshot(
        "completed",
        verification_summary=verification.summary,
        verification_evidence=list(verification.evidence),
    )
    return DagRunResult(task, states)
