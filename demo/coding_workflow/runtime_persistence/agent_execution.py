from __future__ import annotations

import json
import sqlite3
from enum import Enum
from typing import Mapping

from ..agent_executor import (
    AgentExecutionEvent,
    AgentExecutionPermission,
    AgentExecutionResult,
    AgentExecutionStateEnvelope,
    AgentExecutionStatus,
    AgentExecutionUsage,
)
from ..runtime_domain import RuntimeProtocolError, ScopedRef, ScopedSnapshotRef
from ..runtime_domain.common import nonempty, thaw_json
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .sqlite import RuntimePersistenceError, RuntimeUnitOfWork, SQLiteRuntimeDatabase


class AgentExecutionStateStoreError(RuntimePersistenceError):
    """Base error for authoritative Agent execution state persistence."""


class AgentExecutionStateStoreValidationError(AgentExecutionStateStoreError):
    """A state/result or locator violates the persistence contract."""


class AgentExecutionStateStoreConflictError(AgentExecutionStateStoreError):
    """An immutable Invocation state or completed result conflicts."""


class AgentExecutionStateRecordResult(str, Enum):
    CREATED = "created"
    ALREADY_RECORDED = "already_recorded"


def _snapshot_to_dict(value: ScopedSnapshotRef) -> dict[str, object]:
    return dict(value.to_dict())


def _state_to_dict(state: AgentExecutionStateEnvelope) -> dict[str, object]:
    return {
        "schema_version": "agent-execution-state/v1",
        "scope_id": state.scope_id,
        "task_ref": dict(state.task_ref.to_dict()),
        "snapshot_ref": _snapshot_to_dict(state.snapshot_ref),
        "permission_snapshot_ref": _snapshot_to_dict(
            state.permission_snapshot_ref
        ),
        "artifact_refs": [
            _snapshot_to_dict(value) for value in state.artifact_refs
        ],
        "permission": state.permission.value,
    }


def _state_from_dict(value: Mapping[str, object]) -> AgentExecutionStateEnvelope:
    required = {
        "schema_version",
        "scope_id",
        "task_ref",
        "snapshot_ref",
        "permission_snapshot_ref",
        "artifact_refs",
        "permission",
    }
    if set(value) != required or value["schema_version"] != "agent-execution-state/v1":
        raise RuntimeProtocolError("AgentExecutionStateEnvelope schema 无效")
    task_ref = value["task_ref"]
    snapshot_ref = value["snapshot_ref"]
    permission_ref = value["permission_snapshot_ref"]
    artifacts = value["artifact_refs"]
    if not isinstance(task_ref, Mapping):
        raise RuntimeProtocolError("task_ref 必须是引用对象")
    if not isinstance(snapshot_ref, Mapping):
        raise RuntimeProtocolError("snapshot_ref 必须是快照引用")
    if not isinstance(permission_ref, Mapping):
        raise RuntimeProtocolError("permission_snapshot_ref 必须是快照引用")
    if not isinstance(artifacts, list) or not all(
        isinstance(item, Mapping) for item in artifacts
    ):
        raise RuntimeProtocolError("artifact_refs 必须是快照引用数组")
    return AgentExecutionStateEnvelope(
        scope_id=value["scope_id"],
        task_ref=ScopedRef.from_dict(task_ref),
        snapshot_ref=ScopedSnapshotRef.from_dict(snapshot_ref),
        permission_snapshot_ref=ScopedSnapshotRef.from_dict(permission_ref),
        artifact_refs=tuple(
            ScopedSnapshotRef.from_dict(item) for item in artifacts
        ),
        permission=AgentExecutionPermission(value["permission"]),
    )


def _result_to_dict(result: AgentExecutionResult) -> dict[str, object]:
    return {
        "schema_version": "agent-execution-result/v1",
        "status": result.status.value,
        "backend": result.backend,
        "cli_version": result.cli_version,
        "session_id": result.session_id,
        "sandbox": result.sandbox,
        "final_message": result.final_message,
        "events": [
            {"kind": event.kind, "data": thaw_json(event.data)}
            for event in result.events
        ],
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "cached_input_tokens": result.usage.cached_input_tokens,
            "output_tokens": result.usage.output_tokens,
            "reasoning_output_tokens": result.usage.reasoning_output_tokens,
        },
        "duration_ms": result.duration_ms,
    }


def _result_from_dict(value: Mapping[str, object]) -> AgentExecutionResult:
    required = {
        "schema_version",
        "status",
        "backend",
        "cli_version",
        "session_id",
        "sandbox",
        "final_message",
        "events",
        "usage",
        "duration_ms",
    }
    if set(value) != required or value["schema_version"] != "agent-execution-result/v1":
        raise RuntimeProtocolError("AgentExecutionResult schema 无效")
    raw_events = value["events"]
    raw_usage = value["usage"]
    if not isinstance(raw_events, list) or not isinstance(raw_usage, Mapping):
        raise RuntimeProtocolError("AgentExecutionResult events/usage 无效")
    events: list[AgentExecutionEvent] = []
    for raw_event in raw_events:
        if (
            not isinstance(raw_event, Mapping)
            or set(raw_event) != {"kind", "data"}
            or not isinstance(raw_event["data"], Mapping)
        ):
            raise RuntimeProtocolError("AgentExecutionResult event 无效")
        events.append(AgentExecutionEvent(raw_event["kind"], raw_event["data"]))
    usage_fields = {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
    if set(raw_usage) != usage_fields:
        raise RuntimeProtocolError("AgentExecutionResult usage 无效")
    return AgentExecutionResult(
        status=AgentExecutionStatus(value["status"]),
        backend=nonempty(value["backend"], "backend"),
        cli_version=nonempty(value["cli_version"], "cli_version"),
        session_id=value["session_id"],
        sandbox=nonempty(value["sandbox"], "sandbox"),
        final_message=value["final_message"],
        events=tuple(events),
        usage=AgentExecutionUsage(
            input_tokens=raw_usage["input_tokens"],
            cached_input_tokens=raw_usage["cached_input_tokens"],
            output_tokens=raw_usage["output_tokens"],
            reasoning_output_tokens=raw_usage["reasoning_output_tokens"],
        ),
        duration_ms=value["duration_ms"],
    )


class SQLiteAgentExecutionStateStore:
    """Immutable authority and completed-result replay for Agent Invocations."""

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise AgentExecutionStateStoreValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database

    def record_expected(
        self,
        uow: RuntimeUnitOfWork,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> AgentExecutionStateRecordResult:
        try:
            self._require_uow(uow)
            locator = nonempty(invocation_id, "invocation_id")
            if not isinstance(state, AgentExecutionStateEnvelope):
                raise AgentExecutionStateStoreValidationError(
                    "state 必须是 AgentExecutionStateEnvelope"
                )
            raw = canonical_json(_state_to_dict(state))
            digest = text_digest(raw)
            row = uow._execute_managed(
                """SELECT scope_id, state_json, state_digest
                   FROM runtime_agent_execution_states
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if row is not None:
                existing = self._decode_state_row(row)
                if existing == state:
                    return AgentExecutionStateRecordResult.ALREADY_RECORDED
                raise AgentExecutionStateStoreConflictError(
                    "invocation_id 已绑定不同执行状态"
                )
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_agent_execution_states(
                        invocation_id, scope_id, state_json, state_digest
                    ) VALUES (?, ?, ?, ?)""",
                    (locator, state.scope_id, raw, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Agent execution state 持久约束冲突"
                ) from exc
            return AgentExecutionStateRecordResult.CREATED
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def expected_for(
        self,
        invocation_id: str,
    ) -> AgentExecutionStateEnvelope | None:
        locator = nonempty(invocation_id, "invocation_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT scope_id, state_json, state_digest
                   FROM runtime_agent_execution_states
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            return None if row is None else self._decode_state_row(row)
        finally:
            connection.close()

    def completed_for(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> AgentExecutionResult | None:
        locator = nonempty(invocation_id, "invocation_id")
        expected_raw = canonical_json(_state_to_dict(state))
        expected_digest = text_digest(expected_raw)
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT r.state_digest, r.result_json, r.result_digest
                   FROM runtime_agent_execution_results AS r
                   WHERE r.invocation_id = ?""",
                (locator,),
            ).fetchone()
            if row is None:
                return None
            if row[0] != expected_digest:
                raise AgentExecutionStateStoreConflictError(
                    "completed result 与请求状态不一致"
                )
            return self._decode_result_row(row[1], row[2])
        finally:
            connection.close()

    def record_completed(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult:
        locator = nonempty(invocation_id, "invocation_id")
        if not isinstance(state, AgentExecutionStateEnvelope):
            raise AgentExecutionStateStoreValidationError(
                "state 必须是 AgentExecutionStateEnvelope"
            )
        if not isinstance(result, AgentExecutionResult):
            raise AgentExecutionStateStoreValidationError(
                "result 必须是 AgentExecutionResult"
            )
        state_raw = canonical_json(_state_to_dict(state))
        state_digest = text_digest(state_raw)
        result_raw = canonical_json(_result_to_dict(result))
        result_digest = text_digest(result_raw)
        with self._database.unit_of_work() as uow:
            authority = uow._execute_managed(
                """SELECT scope_id, state_json, state_digest
                   FROM runtime_agent_execution_states
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if authority is None or self._decode_state_row(authority) != state:
                raise AgentExecutionStateStoreConflictError(
                    "completed result 缺少匹配的权威状态"
                )
            existing = uow._execute_managed(
                """SELECT state_digest, result_json, result_digest
                   FROM runtime_agent_execution_results
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if existing is not None:
                persisted = self._decode_result_row(existing[1], existing[2])
                if existing[0] != state_digest or persisted != result:
                    raise AgentExecutionStateStoreConflictError(
                        "Invocation 已有不同完成结果"
                    )
                uow.commit()
                return persisted
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_agent_execution_results(
                        invocation_id, state_digest, result_json, result_digest
                    ) VALUES (?, ?, ?, ?)""",
                    (locator, state_digest, result_raw, result_digest),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Agent execution result 持久约束冲突"
                ) from exc
            uow.commit()
        return result

    def _verify_connection(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute(
            """SELECT scope_id, state_json, state_digest
               FROM runtime_agent_execution_states
               ORDER BY invocation_id"""
        ):
            self._decode_state_row(row)
        for row in connection.execute(
            """SELECT r.result_json, r.result_digest, r.state_digest,
                      s.state_digest
               FROM runtime_agent_execution_results AS r
               JOIN runtime_agent_execution_states AS s
                 ON s.invocation_id = r.invocation_id
               ORDER BY r.invocation_id"""
        ):
            if row[2] != row[3]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_execution_results state digest 漂移"
                )
            self._decode_result_row(row[0], row[1])

    @staticmethod
    def _decode_state_row(row) -> AgentExecutionStateEnvelope:
        try:
            raw = str(row[1])
            if text_digest(raw) != row[2]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_execution_states digest 漂移"
                )
            decoded = json.loads(raw)
            if not isinstance(decoded, Mapping):
                raise RuntimeProtocolError("Agent execution state 必须是对象")
            state = _state_from_dict(decoded)
            if state.scope_id != row[0]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_execution_states projection 漂移"
                )
            return state
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_execution_states 无法重建"
            ) from exc

    @staticmethod
    def _decode_result_row(raw_value: object, digest_value: object) -> AgentExecutionResult:
        try:
            raw = str(raw_value)
            if text_digest(raw) != digest_value:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_execution_results digest 漂移"
                )
            decoded = json.loads(raw)
            if not isinstance(decoded, Mapping):
                raise RuntimeProtocolError("Agent execution result 必须是对象")
            return _result_from_dict(decoded)
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_execution_results 无法重建"
            ) from exc

    def _open_read_connection(self):
        self._database._require_outbox_policy()
        connection = self._database._connect()
        try:
            self._database._assert_wal(connection)
            state = self._database._inspect_schema(connection)
            if state is None:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_kernel schema 尚未初始化"
                )
            self._database._validate_schema(
                state,
                connection=connection,
                require_current=True,
            )
            self._database._assert_outbox_policy_binding(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _require_uow(self, uow: RuntimeUnitOfWork) -> None:
        if not isinstance(uow, RuntimeUnitOfWork):
            raise AgentExecutionStateStoreValidationError(
                "uow 必须是 RuntimeUnitOfWork"
            )
        if uow._database is not self._database:
            raise AgentExecutionStateStoreValidationError(
                "uow 与 SQLiteAgentExecutionStateStore 必须属于同一数据库"
            )


__all__ = [
    "AgentExecutionStateRecordResult",
    "AgentExecutionStateStoreConflictError",
    "AgentExecutionStateStoreError",
    "AgentExecutionStateStoreValidationError",
    "SQLiteAgentExecutionStateStore",
]
