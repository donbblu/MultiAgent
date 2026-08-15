from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ..artifacts import ArtifactStore
from ..runtime_sqlite import SQLiteRuntimeStore


class VisionForgeRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisionForgeCheckpoint:
    checkpoint_id: str
    task_id: str
    phase: str
    requirement: str
    reference_image_artifact_ref: str
    ui_spec_artifact_ref: str
    current_implementation_artifact_ref: str
    current_integration_artifact_ref: str
    fix_attempts: int
    max_fix_attempts: int
    cycles: tuple[Mapping[str, object], ...]
    model_calls: tuple[Mapping[str, object], ...]
    artifacts: ArtifactStore
    workspace_hashes: Mapping[str, str]
    version: int = 1

    ALLOWED_PHASES = frozenset({"verifying", "needs_fix", "failed"})

    def __post_init__(self) -> None:
        if self.phase not in self.ALLOWED_PHASES:
            raise VisionForgeRecoveryError(f"未知返工 Checkpoint 阶段: {self.phase}")
        if self.fix_attempts < 0 or not 0 <= self.max_fix_attempts <= 10:
            raise VisionForgeRecoveryError("Checkpoint 修复轮数无效")
        if self.fix_attempts > self.max_fix_attempts:
            raise VisionForgeRecoveryError("Checkpoint 修复轮数超过上限")


class VisionForgeCheckpointStore:
    """保存返工阶段、Artifact 快照和 Workspace 哈希的 SQLite Runtime。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS visionforge_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5)

    def save(self, checkpoint: VisionForgeCheckpoint) -> None:
        payload = {
            "requirement": checkpoint.requirement,
            "reference_image_artifact_ref": checkpoint.reference_image_artifact_ref,
            "ui_spec_artifact_ref": checkpoint.ui_spec_artifact_ref,
            "current_implementation_artifact_ref": (
                checkpoint.current_implementation_artifact_ref
            ),
            "current_integration_artifact_ref": (
                checkpoint.current_integration_artifact_ref
            ),
            "fix_attempts": checkpoint.fix_attempts,
            "max_fix_attempts": checkpoint.max_fix_attempts,
            "cycles": [dict(item) for item in checkpoint.cycles],
            "model_calls": [dict(item) for item in checkpoint.model_calls],
            "artifacts": SQLiteRuntimeStore._artifact_items(checkpoint.artifacts),
            "workspace_hashes": dict(checkpoint.workspace_hashes),
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
        except TypeError as exc:
            raise VisionForgeRecoveryError(f"Checkpoint 内容不可序列化: {exc}") from exc
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO visionforge_checkpoints(
                    checkpoint_id, task_id, phase, payload, version
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    phase=excluded.phase,
                    payload=excluded.payload,
                    version=visionforge_checkpoints.version + 1""",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.task_id,
                    checkpoint.phase,
                    encoded,
                    checkpoint.version,
                ),
            )

    def load(self, checkpoint_id: str) -> VisionForgeCheckpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT task_id, phase, payload, version
                   FROM visionforge_checkpoints WHERE checkpoint_id = ?""",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        task_id, phase, raw_payload, version = row
        try:
            data = json.loads(raw_payload)
            artifacts = SQLiteRuntimeStore._restore_artifacts(data["artifacts"])
            return VisionForgeCheckpoint(
                checkpoint_id,
                str(task_id),
                str(phase),
                str(data["requirement"]),
                str(data["reference_image_artifact_ref"]),
                str(data["ui_spec_artifact_ref"]),
                str(data["current_implementation_artifact_ref"]),
                str(data["current_integration_artifact_ref"]),
                int(data["fix_attempts"]),
                int(data["max_fix_attempts"]),
                tuple(MappingProxyType(dict(item)) for item in data["cycles"]),
                tuple(MappingProxyType(dict(item)) for item in data["model_calls"]),
                artifacts,
                MappingProxyType(dict(data["workspace_hashes"])),
                int(version),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, VisionForgeRecoveryError):
                raise
            raise VisionForgeRecoveryError(f"Checkpoint 数据无效: {exc}") from exc

    def delete(self, checkpoint_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM visionforge_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )

    @staticmethod
    def validate_workspace(
        checkpoint: VisionForgeCheckpoint,
        current_hashes: Mapping[str, str],
    ) -> None:
        if dict(checkpoint.workspace_hashes) != dict(current_hashes):
            raise VisionForgeRecoveryError(
                "Workspace 已在 VisionForge Checkpoint 后发生变化，拒绝自动恢复"
            )
