from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Iterable, Mapping

from .memory import MemoryKind, MemoryRecord, TaskWorkingMemory


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
                    scope TEXT NOT NULL, visibility TEXT NOT NULL, task_id TEXT,
                    source_ref TEXT NOT NULL, evidence_refs TEXT NOT NULL,
                    sensitivity TEXT NOT NULL, confidence REAL NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT, version INTEGER NOT NULL,
                    supersedes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_task_kind
                    ON memories(task_id, kind);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL
                );
                """
            )

    def append(self, record: MemoryRecord) -> None:
        values = (
            record.memory_id, record.kind.value, record.subtype, record.summary,
            json.dumps(dict(record.content), ensure_ascii=False), record.source,
            record.scope, json.dumps(sorted(record.visibility), ensure_ascii=False),
            record.task_id, record.source_ref,
            json.dumps(record.evidence_refs, ensure_ascii=False), record.sensitivity,
            record.confidence, record.created_at, record.expires_at, record.version,
            record.supersedes,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            row["memory_id"], MemoryKind(row["kind"]), row["subtype"], row["summary"],
            MappingProxyType(json.loads(row["content"])), row["source"], row["scope"],
            frozenset(json.loads(row["visibility"])), row["task_id"], row["source_ref"],
            tuple(json.loads(row["evidence_refs"])), row["sensitivity"], row["confidence"],
            row["created_at"], row["expires_at"], row["version"], row["supersedes"],
        )

    def query(
        self,
        *,
        task_id: str | None = None,
        kinds: Iterable[MemoryKind] = (),
        role: str = "",
        text: str = "",
    ) -> tuple[MemoryRecord, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if task_id is not None:
            clauses.append("(task_id IS NULL OR task_id = ?)")
            parameters.append(task_id)
        kind_values = [kind.value for kind in kinds]
        if kind_values:
            clauses.append("kind IN (%s)" % ",".join("?" for _ in kind_values))
            parameters.extend(kind_values)
        sql = "SELECT * FROM memories"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY confidence DESC, created_at DESC"
        with self._lock, self._connect() as connection:
            records = tuple(self._record(row) for row in connection.execute(sql, parameters))
        words = {word.lower() for word in text.split() if word}
        return tuple(
            record for record in records
            if (not record.visibility or role in record.visibility)
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
        return TaskWorkingMemory(
            task_id=data["task_id"], plan_summary=data["plan_summary"],
            active_artifacts=dict(data["active_artifacts"]),
            node_summaries=dict(data["node_summaries"]),
            assumptions=list(data["assumptions"]), feedback=list(data["feedback"]),
            memory_refs=list(data["memory_refs"]), version=data["version"],
        )
