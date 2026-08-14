from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Iterable, Mapping

from .memory import (
    MemoryKind,
    MemoryRecord,
    MemorySanitizer,
    MemoryStatus,
    FailureObservation,
    QualityGateState,
    TaskWorkingMemory,
    WorkingArtifactState,
    WorkingNodeState,
)


class SQLiteMemoryStore:
    """可恢复的单机记忆存储；每次操作使用短事务。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY, kind TEXT NOT NULL, subtype TEXT NOT NULL,
                    summary TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                    scope TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL, task_id TEXT,
                    source_ref TEXT NOT NULL, evidence_refs TEXT NOT NULL,
                    sensitivity TEXT NOT NULL, confidence REAL NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT, version INTEGER NOT NULL,
                    supersedes TEXT, semantic_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active', invalidated_at TEXT,
                    invalidated_reason TEXT NOT NULL DEFAULT '',
                    last_confirmed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memories_task_kind
                    ON memories(task_id, kind);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(memories)")
            }
            if "project_id" not in columns:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
                )
            additions = {
                "semantic_key": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "invalidated_at": "TEXT",
                "invalidated_reason": "TEXT NOT NULL DEFAULT ''",
                "last_confirmed_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE memories ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                "UPDATE memories SET last_confirmed_at = created_at "
                "WHERE last_confirmed_at = ''"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project_task_kind "
                "ON memories(project_id, task_id, kind)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_semantic_status "
                "ON memories(project_id, semantic_key, status)"
            )

    @staticmethod
    def _values(record: MemoryRecord) -> tuple[object, ...]:
        return (
            record.memory_id, record.kind.value, record.subtype, record.summary,
            json.dumps(dict(record.content), ensure_ascii=False), record.source,
            record.scope, record.project_id,
            json.dumps(sorted(record.visibility), ensure_ascii=False),
            record.task_id, record.source_ref,
            json.dumps(record.evidence_refs, ensure_ascii=False), record.sensitivity,
            record.confidence, record.created_at, record.expires_at, record.version,
            record.supersedes, record.semantic_key, record.status.value,
            record.invalidated_at, record.invalidated_reason, record.last_confirmed_at,
        )

    @classmethod
    def _insert(cls, connection: sqlite3.Connection, record: MemoryRecord) -> None:
        connection.execute(
            """INSERT INTO memories(
                memory_id, kind, subtype, summary, content, source, scope,
                project_id, visibility, task_id, source_ref, evidence_refs,
                sensitivity, confidence, created_at, expires_at, version, supersedes,
                semantic_key, status, invalidated_at, invalidated_reason, last_confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            cls._values(record),
        )

    def append(self, record: MemoryRecord) -> MemoryRecord:
        record = MemorySanitizer.sanitize(record)
        with self._lock, self._connect() as connection:
            if record.kind is MemoryKind.LONG_TERM:
                rows = connection.execute(
                    """SELECT * FROM memories
                       WHERE project_id = ? AND kind = ? AND status = ?""",
                    (record.project_id, MemoryKind.LONG_TERM.value, MemoryStatus.ACTIVE.value),
                )
                active = tuple(self._record(row) for row in rows)
                same_key = next(
                    (item for item in active if item.semantic_key == record.semantic_key),
                    None,
                )
                duplicate = next(
                    (
                        item for item in active
                        if item.payload_fingerprint() == record.payload_fingerprint()
                    ),
                    None,
                )
                existing = same_key or duplicate
                if existing and existing.payload_fingerprint() == record.payload_fingerprint():
                    confirmed = replace(
                        existing,
                        evidence_refs=tuple(dict.fromkeys(
                            (*existing.evidence_refs, *record.evidence_refs)
                        )),
                        last_confirmed_at=record.created_at,
                    )
                    connection.execute(
                        """UPDATE memories
                           SET evidence_refs = ?, last_confirmed_at = ?
                           WHERE memory_id = ?""",
                        (
                            json.dumps(confirmed.evidence_refs, ensure_ascii=False),
                            confirmed.last_confirmed_at,
                            confirmed.memory_id,
                        ),
                    )
                    return confirmed
                if same_key:
                    connection.execute(
                        """UPDATE memories
                           SET status = ?, invalidated_at = ?, invalidated_reason = ?
                           WHERE memory_id = ?""",
                        (
                            MemoryStatus.SUPERSEDED.value,
                            record.created_at,
                            f"由 {record.memory_id} 替代",
                            same_key.memory_id,
                        ),
                    )
                    record = replace(
                        record,
                        version=same_key.version + 1,
                        supersedes=same_key.memory_id,
                    )
            self._insert(connection, record)
            return record

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            row["memory_id"], MemoryKind(row["kind"]), row["subtype"], row["summary"],
            MappingProxyType(json.loads(row["content"])), row["source"], row["scope"],
            row["project_id"], frozenset(json.loads(row["visibility"])),
            row["task_id"], row["source_ref"],
            tuple(json.loads(row["evidence_refs"])), row["sensitivity"], row["confidence"],
            row["created_at"], row["expires_at"], row["version"], row["supersedes"],
            row["semantic_key"], MemoryStatus(row["status"]), row["invalidated_at"],
            row["invalidated_reason"], row["last_confirmed_at"],
        )

    def invalidate(self, memory_id: str, reason: str) -> MemoryRecord:
        if not reason.strip():
            raise ValueError("记忆失效原因不能为空")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"记忆不存在: {memory_id}")
            record = self._record(row)
            if record.status is not MemoryStatus.ACTIVE:
                raise ValueError(f"只有有效记忆可以失效: {memory_id}")
            connection.execute(
                """UPDATE memories SET status = ?, invalidated_at = ?,
                   invalidated_reason = ? WHERE memory_id = ?""",
                (MemoryStatus.INVALIDATED.value, now, reason, memory_id),
            )
            return replace(
                record,
                status=MemoryStatus.INVALIDATED,
                invalidated_at=now,
                invalidated_reason=reason,
            )

    def query(
        self,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
        kinds: Iterable[MemoryKind] = (),
        scopes: Iterable[str] = (),
        role: str = "",
        text: str = "",
        include_inactive: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        now = datetime.now(timezone.utc).isoformat()
        if project_id is not None:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        if task_id is not None:
            clauses.append("(task_id IS NULL OR task_id = ?)")
            parameters.append(task_id)
        kind_values = [kind.value for kind in kinds]
        if kind_values:
            clauses.append("kind IN (%s)" % ",".join("?" for _ in kind_values))
            parameters.extend(kind_values)
        scope_values = list(scopes)
        if scope_values:
            clauses.append("scope IN (%s)" % ",".join("?" for _ in scope_values))
            parameters.extend(scope_values)
        if not include_inactive:
            clauses.append("status = ?")
            parameters.append(MemoryStatus.ACTIVE.value)
            clauses.append("(expires_at IS NULL OR expires_at > ?)")
            parameters.append(now)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY confidence DESC, created_at DESC"
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE memories SET status = ?, invalidated_at = ?,
                   invalidated_reason = ?
                   WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?""",
                (
                    MemoryStatus.EXPIRED.value,
                    now,
                    "已到期",
                    MemoryStatus.ACTIVE.value,
                    now,
                ),
            )
            records = tuple(self._record(row) for row in connection.execute(sql, parameters))
        words = {word.lower() for word in text.split() if word}
        return tuple(
            record for record in records
            if (not role or not record.visibility or role in record.visibility)
            and (not words or any(word in record.summary.lower() for word in words))
        )

    def save_checkpoint(self, working: TaskWorkingMemory) -> None:
        payload = dict(working.checkpoint())
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO checkpoints(task_id, payload, version) VALUES (?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, version=excluded.version""",
                (working.task_id, json.dumps(payload, ensure_ascii=False), working.version),
            )

    def load_checkpoint(self, task_id: str) -> TaskWorkingMemory | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM checkpoints WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload"])
        nodes = {
            key: WorkingNodeState(
                node_id=item["node_id"], role=item["role"], state=item["state"],
                attempt=item.get("attempt", 0), summary=item.get("summary", ""),
                last_error=item.get("last_error", ""),
                input_artifacts=tuple(item.get("input_artifacts", ())),
                output_artifacts=tuple(item.get("output_artifacts", ())),
            )
            for key, item in data.get("nodes", {}).items()
        }
        artifacts = {
            key: WorkingArtifactState(
                reference=item["reference"],
                producer_node_id=item["producer_node_id"], state=item["state"],
                affected_paths=tuple(item.get("affected_paths", ())),
                superseded_by=item.get("superseded_by"),
                verification_refs=tuple(item.get("verification_refs", ())),
            )
            for key, item in data.get("artifacts", {}).items()
        }
        failures = {
            key: FailureObservation(
                failure_id=item["failure_id"], source=item["source"],
                summary=item["summary"], feedback=tuple(item.get("feedback", ())),
                affected_paths=tuple(item.get("affected_paths", ())),
                affected_artifacts=tuple(item.get("affected_artifacts", ())),
                evidence_refs=tuple(item.get("evidence_refs", ())),
                resolved_by=item.get("resolved_by"),
            )
            for key, item in data.get("failures", {}).items()
        }
        gate = data.get("quality_gate", {})
        return TaskWorkingMemory(
            task_id=data["task_id"], plan_summary=data["plan_summary"],
            active_artifacts=dict(data["active_artifacts"]),
            node_summaries=dict(data["node_summaries"]),
            assumptions=list(data["assumptions"]), feedback=list(data["feedback"]),
            memory_refs=list(data["memory_refs"]), nodes=nodes, artifacts=artifacts,
            failures=failures,
            quality_gate=QualityGateState(
                affected_checks_completed=gate.get("affected_checks_completed", False),
                affected_checks_passed=gate.get("affected_checks_passed"),
                full_gate_completed=gate.get("full_gate_completed", False),
                passed=gate.get("passed"), summary=gate.get("summary", ""),
                verification_refs=tuple(gate.get("verification_refs", ())),
            ),
            open_questions=list(data.get("open_questions", ())),
            version=data["version"],
        )
