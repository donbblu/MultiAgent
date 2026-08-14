from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .artifacts import (
    Artifact,
    ArtifactStore,
    ArtifactValidation,
    ArtifactValidationState,
)
from .harness.lifecycle import LifecycleSnapshot, LifecycleState
from .harness.scheduler import GraphSnapshot
from .harness.task_graph import TaskExecutionState, TaskGraph, TaskSpec
from .models import FileChange, ImplementationPlan


class RuntimeRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSnapshot:
    snapshot_id: str
    task_id: str
    project_id: str
    phase: str
    graph: TaskGraph
    graph_snapshot: GraphSnapshot
    attempts: Mapping[str, int]
    lifecycle: LifecycleSnapshot
    artifacts: ArtifactStore
    workspace_hashes: Mapping[str, str]
    runner_data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    version: int = 1


class SQLiteRuntimeStore:
    """持久化可安全重放的运行状态；不保存线程、请求或原始推理。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runtime_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5)

    @staticmethod
    def _task_spec(spec: TaskSpec) -> dict[str, object]:
        return {
            "task_id": spec.task_id, "title": spec.title,
            "objective": spec.objective, "role": spec.role,
            "dependencies": spec.dependencies,
            "acceptance_criteria": spec.acceptance_criteria,
            "read_scopes": spec.read_scopes, "write_scopes": spec.write_scopes,
            "input_artifacts": spec.input_artifacts,
            "output_artifacts": spec.output_artifacts,
            "context_queries": spec.context_queries, "risk_level": spec.risk_level,
            "timeout_seconds": spec.timeout_seconds, "retry_limit": spec.retry_limit,
            "priority": spec.priority,
        }

    @staticmethod
    def _encode_content(content: object) -> dict[str, object]:
        if isinstance(content, ImplementationPlan):
            return {
                "type": "implementation_plan",
                "value": {
                    "summary": content.summary,
                    "changes": [
                        {"path": item.path, "content": item.content, "reason": item.reason}
                        for item in content.changes
                    ],
                    "suggested_checks": content.suggested_checks,
                },
            }
        try:
            json.dumps(content, ensure_ascii=False)
        except TypeError as exc:
            raise RuntimeRecoveryError(
                f"Artifact 内容不可持久化: {type(content).__name__}"
            ) from exc
        return {"type": "json", "value": content}

    @staticmethod
    def _decode_content(data: Mapping[str, object]) -> object:
        if data["type"] == "implementation_plan":
            value = data["value"]
            if not isinstance(value, dict):
                raise RuntimeRecoveryError("ImplementationPlan 快照无效")
            return ImplementationPlan(
                str(value["summary"]),
                [
                    FileChange(str(item["path"]), str(item["content"]), str(item["reason"]))
                    for item in value["changes"]
                ],
                [list(command) for command in value.get("suggested_checks", [])],
            )
        return data["value"]

    @classmethod
    def _artifact_items(cls, store: ArtifactStore) -> list[dict[str, object]]:
        return [
            {
                "artifact": {
                    "artifact_id": artifact.artifact_id, "name": artifact.name,
                    "task_id": artifact.task_id, "kind": artifact.kind,
                    "content": cls._encode_content(artifact.content),
                    "metadata": dict(artifact.metadata), "created_at": artifact.created_at,
                },
                "validation": {
                    "state": validation.state.value,
                    "verification_refs": validation.verification_refs,
                    "superseded_by": validation.superseded_by,
                },
            }
            for artifact, validation in store.snapshot()
        ]

    @classmethod
    def _restore_artifacts(cls, items: list[dict[str, object]]) -> ArtifactStore:
        restored = []
        for item in items:
            artifact_data = item["artifact"]
            validation_data = item["validation"]
            artifact = Artifact(
                str(artifact_data["artifact_id"]), str(artifact_data["name"]),
                str(artifact_data["task_id"]), str(artifact_data["kind"]),
                cls._decode_content(artifact_data["content"]),
                MappingProxyType(dict(artifact_data["metadata"])),
                str(artifact_data["created_at"]),
            )
            validation = ArtifactValidation(
                ArtifactValidationState(str(validation_data["state"])),
                tuple(validation_data.get("verification_refs", ())),
                validation_data.get("superseded_by"),
            )
            restored.append((artifact, validation))
        return ArtifactStore.restore(tuple(restored))

    def save(self, snapshot: RuntimeSnapshot) -> None:
        payload = {
            "graph": [self._task_spec(item) for item in snapshot.graph.tasks.values()],
            "states": {
                key: value.value for key, value in snapshot.graph_snapshot.states.items()
            },
            "graph_artifacts": dict(snapshot.graph_snapshot.artifacts),
            "failures": dict(snapshot.graph_snapshot.failures),
            "attempts": dict(snapshot.attempts),
            "lifecycle": {
                "state": snapshot.lifecycle.state.value,
                "reason": snapshot.lifecycle.reason,
                "updated_at": snapshot.lifecycle.updated_at,
            },
            "artifacts": self._artifact_items(snapshot.artifacts),
            "workspace_hashes": dict(snapshot.workspace_hashes),
            "runner_data": dict(snapshot.runner_data),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO runtime_snapshots(
                    snapshot_id, task_id, project_id, phase, payload, version
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    task_id=excluded.task_id, project_id=excluded.project_id,
                    phase=excluded.phase, payload=excluded.payload,
                    version=runtime_snapshots.version + 1""",
                (
                    snapshot.snapshot_id, snapshot.task_id, snapshot.project_id,
                    snapshot.phase, json.dumps(payload, ensure_ascii=False),
                    snapshot.version,
                ),
            )

    def load(self, snapshot_id: str) -> RuntimeSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT task_id, project_id, phase, payload, version
                   FROM runtime_snapshots WHERE snapshot_id = ?""",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        task_id, project_id, phase, raw_payload, version = row
        data = json.loads(raw_payload)
        graph = TaskGraph(TaskSpec(
            task_id=item["task_id"], title=item["title"], objective=item["objective"],
            role=item["role"], dependencies=tuple(item["dependencies"]),
            acceptance_criteria=tuple(item["acceptance_criteria"]),
            read_scopes=tuple(item["read_scopes"]), write_scopes=tuple(item["write_scopes"]),
            input_artifacts=tuple(item["input_artifacts"]),
            output_artifacts=tuple(item["output_artifacts"]),
            context_queries=tuple(item["context_queries"]), risk_level=item["risk_level"],
            timeout_seconds=int(item["timeout_seconds"]),
            retry_limit=int(item["retry_limit"]), priority=int(item["priority"]),
        ) for item in data["graph"])
        graph_snapshot = GraphSnapshot(
            MappingProxyType({
                key: TaskExecutionState(value) for key, value in data["states"].items()
            }),
            MappingProxyType(dict(data["graph_artifacts"])),
            MappingProxyType(dict(data["failures"])),
        )
        lifecycle = data["lifecycle"]
        return RuntimeSnapshot(
            snapshot_id, str(task_id), str(project_id), str(phase), graph,
            graph_snapshot, MappingProxyType({
                key: int(value) for key, value in data["attempts"].items()
            }),
            LifecycleSnapshot(
                LifecycleState(lifecycle["state"]),
                str(lifecycle["reason"]), str(lifecycle["updated_at"]),
            ),
            self._restore_artifacts(data["artifacts"]),
            MappingProxyType(dict(data["workspace_hashes"])),
            MappingProxyType(dict(data.get("runner_data", {}))), int(version),
        )

    def delete(self, snapshot_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM runtime_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            )

    @staticmethod
    def validate_workspace(
        snapshot: RuntimeSnapshot, current_hashes: Mapping[str, str]
    ) -> None:
        if dict(snapshot.workspace_hashes) != dict(current_hashes):
            raise RuntimeRecoveryError("Workspace 已在快照后发生变化，拒绝自动恢复")
