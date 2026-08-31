from __future__ import annotations

import json
import sqlite3
from enum import Enum
from typing import Mapping

from ..agent_executor import (
    AgentExecutionContextPart,
    AgentExecutionEvent,
    AgentExecutionPermission,
    AgentExecutionRecoveryBlocked,
    AgentExecutionRecoveryConfirmation,
    AgentExecutionRecoveryConfirmationRejected,
    AgentExecutionRecoveryContext,
    AgentExecutionRecoveryPrompt,
    AgentExecutionRecoveryStopped,
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


def _context_part_from_dict(value: object) -> AgentExecutionContextPart:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"ref", "content"}
        or not isinstance(value["ref"], Mapping)
    ):
        raise RuntimeProtocolError("AgentExecutionContextPart schema 无效")
    return AgentExecutionContextPart(
        ref=ScopedSnapshotRef.from_dict(value["ref"]),
        content=value["content"],
    )


def _recovery_context_from_dict(
    value: Mapping[str, object],
) -> AgentExecutionRecoveryContext:
    required = {
        "schema_version",
        "scope_id",
        "task_ref",
        "task_snapshot",
        "permission_snapshot",
        "messages",
        "artifacts",
    }
    if (
        set(value) != required
        or value["schema_version"]
        != "agent-execution-recovery-context/v1"
        or not isinstance(value["task_ref"], Mapping)
        or not isinstance(value["messages"], list)
        or not isinstance(value["artifacts"], list)
    ):
        raise RuntimeProtocolError("AgentExecutionRecoveryContext schema 无效")
    return AgentExecutionRecoveryContext(
        scope_id=value["scope_id"],
        task_ref=ScopedRef.from_dict(value["task_ref"]),
        task_snapshot=_context_part_from_dict(value["task_snapshot"]),
        permission_snapshot=_context_part_from_dict(
            value["permission_snapshot"]
        ),
        messages=tuple(
            _context_part_from_dict(item) for item in value["messages"]
        ),
        artifacts=tuple(
            _context_part_from_dict(item) for item in value["artifacts"]
        ),
    )


def _result_to_dict(result: AgentExecutionResult) -> dict[str, object]:
    return {
        "schema_version": "agent-execution-result/v1",
        "status": result.status.value,
        "backend": result.backend_id,
        "cli_version": result.cli_version,
        "session_id": result.backend_session_id,
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
        backend_id=nonempty(value["backend"], "backend"),
        cli_version=nonempty(value["cli_version"], "cli_version"),
        backend_session_id=value["session_id"],
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

    def record_recovery_context(
        self,
        uow: RuntimeUnitOfWork,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
    ) -> AgentExecutionStateRecordResult:
        try:
            self._require_uow(uow)
            locator = nonempty(invocation_id, "invocation_id")
            self._require_context_matches(state, context)
            state_raw = canonical_json(_state_to_dict(state))
            state_digest = text_digest(state_raw)
            context_raw = canonical_json(dict(context.to_dict()))
            context_digest = text_digest(context_raw)
            authority = uow._execute_managed(
                """SELECT scope_id, state_json, state_digest
                   FROM runtime_agent_execution_states
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if authority is None or self._decode_state_row(authority) != state:
                raise AgentExecutionStateStoreConflictError(
                    "recovery context缺少匹配的权威状态"
                )
            existing = uow._execute_managed(
                """SELECT state_digest, context_json, context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if existing is not None:
                persisted = self._decode_recovery_context_row(
                    existing[1], existing[2]
                )
                if existing[0] == state_digest and persisted == context:
                    return AgentExecutionStateRecordResult.ALREADY_RECORDED
                raise AgentExecutionStateStoreConflictError(
                    "Invocation已绑定不同recovery context"
                )
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_agent_execution_recovery_contexts(
                        invocation_id, state_digest, context_json,
                        context_digest
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        locator,
                        state_digest,
                        context_raw,
                        context_digest,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "recovery context持久约束冲突"
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

    def bound_session_for(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> str | None:
        locator = self._binding_locator(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        connection = self._open_read_connection()
        try:
            recovered = connection.execute(
                """SELECT replacement_backend_session_id
                   FROM runtime_backend_session_recoveries
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?
                   ORDER BY recovery_generation DESC LIMIT 1""",
                locator,
            ).fetchone()
            if recovered is not None:
                return str(recovered[0])
            row = connection.execute(
                """SELECT backend_session_id
                   FROM runtime_backend_session_bindings
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?""",
                locator,
            ).fetchone()
            return None if row is None else str(row[0])
        finally:
            connection.close()

    def recovery_context_for(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> AgentExecutionRecoveryContext | None:
        locator = nonempty(invocation_id, "invocation_id")
        if not isinstance(state, AgentExecutionStateEnvelope):
            raise AgentExecutionStateStoreValidationError(
                "state 必须是 AgentExecutionStateEnvelope"
            )
        expected_digest = text_digest(canonical_json(_state_to_dict(state)))
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT state_digest, context_json, context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (locator,),
            ).fetchone()
            if row is None:
                return None
            if row[0] != expected_digest:
                raise AgentExecutionStateStoreConflictError(
                    "recovery context与权威状态不一致"
                )
            context = self._decode_recovery_context_row(row[1], row[2])
            self._require_context_matches(state, context)
            return context
        finally:
            connection.close()

    def record_session_binding(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        backend_session_id: str,
    ) -> str:
        locator = self._binding_locator(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        session_id = nonempty(backend_session_id, "backend_session_id")
        with self._database.unit_of_work() as uow:
            recovered = uow._execute_managed(
                """SELECT replacement_backend_session_id
                   FROM runtime_backend_session_recoveries
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?
                   ORDER BY recovery_generation DESC LIMIT 1""",
                locator,
            ).fetchone()
            if recovered is not None:
                if recovered[0] != session_id:
                    raise AgentExecutionStateStoreConflictError(
                        "Backend Session恢复绑定不可覆盖"
                    )
                uow.commit()
                return session_id
            existing = uow._execute_managed(
                """SELECT backend_session_id
                   FROM runtime_backend_session_bindings
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?""",
                locator,
            ).fetchone()
            if existing is not None:
                if existing[0] != session_id:
                    raise AgentExecutionStateStoreConflictError(
                        "Backend Session绑定不可覆盖"
                    )
                uow.commit()
                return session_id
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_backend_session_bindings(
                        scope_id, thread_id, agent_id, backend_id,
                        backend_session_id
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (*locator, session_id),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Backend Session已绑定其他Agent或Thread"
                ) from exc
            uow.commit()
        return session_id

    def record_session_recovery(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
        replacement_backend_session_id: str,
    ) -> str:
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        invocation = nonempty(invocation_id, "invocation_id")
        stale = nonempty(
            stale_backend_session_id,
            "stale_backend_session_id",
        )
        replacement = nonempty(
            replacement_backend_session_id,
            "replacement_backend_session_id",
        )
        if stale == replacement:
            raise AgentExecutionStateStoreValidationError(
                "replacement Backend Session必须不同于失效Session"
            )
        self._require_context_matches(state, context)
        with self._database.unit_of_work() as uow:
            context_row = uow._execute_managed(
                """SELECT state_digest, context_json, context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if context_row is None:
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复缺少持久recovery context"
                )
            state_digest = text_digest(canonical_json(_state_to_dict(state)))
            persisted_context = self._decode_recovery_context_row(
                context_row[1], context_row[2]
            )
            if (
                context_row[0] != state_digest
                or persisted_context != context
            ):
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复上下文与权威状态不一致"
                )
            base = uow._execute_managed(
                """SELECT backend_session_id
                   FROM runtime_backend_session_bindings
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?""",
                locator,
            ).fetchone()
            latest = uow._execute_managed(
                """SELECT recovery_generation,
                          replacement_backend_session_id
                   FROM runtime_backend_session_recoveries
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?
                   ORDER BY recovery_generation DESC LIMIT 1""",
                locator,
            ).fetchone()
            current = latest[1] if latest is not None else (
                base[0] if base is not None else None
            )
            if current != stale:
                raise AgentExecutionStateStoreConflictError(
                    "失效Session不是当前私有绑定"
                )
            generation = 1 if latest is None else int(latest[0]) + 1
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_backend_session_recoveries(
                        scope_id, thread_id, agent_id, backend_id,
                        recovery_generation, invocation_id,
                        stale_backend_session_id,
                        replacement_backend_session_id, context_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        *locator,
                        generation,
                        invocation,
                        stale,
                        replacement,
                        context.context_digest,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Backend Session恢复持久约束冲突"
                ) from exc
            uow.commit()
        return replacement

    def request_session_recovery_confirmation(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
    ) -> AgentExecutionRecoveryPrompt:
        invocation = nonempty(invocation_id, "invocation_id")
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        stale = nonempty(
            stale_backend_session_id,
            "stale_backend_session_id",
        )
        self._require_context_matches(state, context)
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        confirmation_id = "session-recovery-" + text_digest(canonical_json({
            "schema_version": "agent-execution-recovery-confirmation/v1",
            "invocation_id": invocation,
            "scope_id": state.scope_id,
            "thread_id": locator[1],
            "agent_id": locator[2],
            "backend_id": locator[3],
            "state_digest": state_digest,
            "context_digest": context.context_digest,
        }))
        expected = (
            confirmation_id,
            invocation,
            *locator,
            stale,
            state_digest,
            context.context_digest,
        )
        with self._database.unit_of_work() as uow:
            authority = uow._execute_managed(
                """SELECT scope_id, state_json, state_digest
                   FROM runtime_agent_execution_states
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            context_row = uow._execute_managed(
                """SELECT state_digest, context_json, context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if (
                authority is None
                or self._decode_state_row(authority) != state
                or context_row is None
                or context_row[0] != state_digest
                or context_row[2] != context.context_digest
                or self._decode_recovery_context_row(
                    context_row[1], context_row[2]
                ) != context
            ):
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复确认缺少匹配的权威状态或上下文"
                )
            current = self._current_bound_session(uow, locator)
            if current != stale:
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复确认未绑定当前私有Session"
                )
            existing = uow._execute_managed(
                """SELECT confirmation_id, invocation_id, scope_id,
                          thread_id, agent_id, backend_id,
                          stale_backend_session_id, state_digest,
                          context_digest
                   FROM runtime_backend_session_recovery_requests
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != expected:
                    raise AgentExecutionStateStoreConflictError(
                        "Invocation已绑定不同Session恢复确认请求"
                    )
                uow.commit()
                return AgentExecutionRecoveryPrompt(
                    confirmation_id=confirmation_id,
                    invocation_id=invocation,
                )
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_backend_session_recovery_requests(
                        confirmation_id, invocation_id, scope_id, thread_id,
                        agent_id, backend_id, stale_backend_session_id,
                        state_digest, context_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    expected,
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复确认请求持久约束冲突"
                ) from exc
            uow.commit()
        return AgentExecutionRecoveryPrompt(
            confirmation_id=confirmation_id,
            invocation_id=invocation,
        )

    def record_session_recovery_confirmation(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
    ) -> None:
        if not isinstance(
            confirmation,
            AgentExecutionRecoveryConfirmation,
        ):
            raise AgentExecutionStateStoreValidationError(
                "confirmation必须是AgentExecutionRecoveryConfirmation"
            )
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        stale = nonempty(
            stale_backend_session_id,
            "stale_backend_session_id",
        )
        self._require_context_matches(state, context)
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        with self._database.unit_of_work() as uow:
            row = uow._execute_managed(
                """SELECT invocation_id, scope_id, thread_id, agent_id,
                          backend_id, stale_backend_session_id,
                          state_digest, context_digest
                   FROM runtime_backend_session_recovery_requests
                   WHERE confirmation_id = ?""",
                (confirmation.confirmation_id,),
            ).fetchone()
            expected = (
                confirmation.invocation_id,
                *locator,
                stale,
                state_digest,
                context.context_digest,
            )
            if row is None or tuple(row) != expected:
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_mismatch"
                )
            if self._current_bound_session(uow, locator) != stale:
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_expired"
                )
            existing = uow._execute_managed(
                """SELECT invocation_id, decision, state_digest,
                          context_digest
                   FROM runtime_backend_session_recovery_decisions
                   WHERE confirmation_id = ?""",
                (confirmation.confirmation_id,),
            ).fetchone()
            decision_row = (
                confirmation.invocation_id,
                confirmation.decision.value,
                state_digest,
                context.context_digest,
            )
            if existing is not None:
                if tuple(existing) != decision_row:
                    raise AgentExecutionRecoveryConfirmationRejected(
                        "recovery_confirmation_already_resolved"
                    )
                uow.commit()
                return
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_backend_session_recovery_decisions(
                        confirmation_id, invocation_id, decision,
                        state_digest, context_digest
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (confirmation.confirmation_id, *decision_row),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复确认决定持久约束冲突"
                ) from exc
            uow.commit()

    def pending_session_recovery_confirmation(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryPrompt | None:
        invocation = nonempty(invocation_id, "invocation_id")
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT r.confirmation_id, r.invocation_id, r.scope_id,
                          r.thread_id, r.agent_id, r.backend_id,
                          r.stale_backend_session_id, r.state_digest,
                          r.context_digest, d.decision
                   FROM runtime_backend_session_recovery_requests AS r
                   LEFT JOIN runtime_backend_session_recovery_decisions AS d
                     ON d.confirmation_id = r.confirmation_id
                   WHERE r.invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if row is None or row[9] is not None:
                return None
            if (
                tuple(row[1:6]) != (invocation, *locator)
                or row[7] != state_digest
            ):
                raise AgentExecutionStateStoreConflictError(
                    "待确认Session恢复请求与当前状态不匹配"
                )
            context_row = connection.execute(
                """SELECT context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if context_row is None or context_row[0] != row[8]:
                raise AgentExecutionStateStoreConflictError(
                    "待确认Session恢复请求的Context已漂移"
                )
            latest = connection.execute(
                """SELECT replacement_backend_session_id
                   FROM runtime_backend_session_recoveries
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?
                   ORDER BY recovery_generation DESC LIMIT 1""",
                locator,
            ).fetchone()
            base = connection.execute(
                """SELECT backend_session_id
                   FROM runtime_backend_session_bindings
                   WHERE scope_id = ? AND thread_id = ?
                     AND agent_id = ? AND backend_id = ?""",
                locator,
            ).fetchone()
            current = latest[0] if latest is not None else (
                None if base is None else base[0]
            )
            if current != row[6]:
                raise AgentExecutionStateStoreConflictError(
                    "待确认Session恢复请求已过期"
                )
            return AgentExecutionRecoveryPrompt(
                confirmation_id=str(row[0]),
                invocation_id=invocation,
            )
        finally:
            connection.close()

    def validate_recorded_session_recovery_confirmation(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> None:
        if not isinstance(
            confirmation,
            AgentExecutionRecoveryConfirmation,
        ):
            raise AgentExecutionStateStoreValidationError(
                "confirmation必须是AgentExecutionRecoveryConfirmation"
            )
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        self._require_context_matches(state, context)
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT r.invocation_id, r.scope_id, r.thread_id,
                          r.agent_id, r.backend_id, r.state_digest,
                          r.context_digest, d.decision
                   FROM runtime_backend_session_recovery_requests AS r
                   JOIN runtime_backend_session_recovery_decisions AS d
                     ON d.confirmation_id = r.confirmation_id
                   WHERE r.confirmation_id = ?""",
                (confirmation.confirmation_id,),
            ).fetchone()
            expected = (
                confirmation.invocation_id,
                *locator,
                state_digest,
                context.context_digest,
                confirmation.decision.value,
            )
            if row is None or tuple(row) != expected:
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_mismatch"
                )
        finally:
            connection.close()

    def stopped_session_recovery_for(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryStopped | None:
        invocation = nonempty(invocation_id, "invocation_id")
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT r.invocation_id, r.scope_id, r.thread_id,
                          r.agent_id, r.backend_id, r.state_digest,
                          r.context_digest, d.decision
                   FROM runtime_backend_session_recovery_requests AS r
                   JOIN runtime_backend_session_recovery_decisions AS d
                     ON d.confirmation_id = r.confirmation_id
                   WHERE r.invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if row is None or row[7] != "stop_task":
                return None
            expected = (
                invocation,
                *locator,
                state_digest,
            )
            if tuple(row[:6]) != expected:
                raise AgentExecutionStateStoreConflictError(
                    "已停止Session恢复与当前状态不匹配"
                )
            context_row = connection.execute(
                """SELECT context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if context_row is None or context_row[0] != row[6]:
                raise AgentExecutionStateStoreConflictError(
                    "已停止Session恢复的Context已漂移"
                )
            return AgentExecutionRecoveryStopped(invocation_id=invocation)
        finally:
            connection.close()

    def claim_session_recovery_attempt(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
    ) -> None:
        if (
            not isinstance(confirmation, AgentExecutionRecoveryConfirmation)
            or confirmation.decision.value != "create_new_session"
        ):
            raise AgentExecutionStateStoreValidationError(
                "只有create_new_session确认可以领取恢复尝试"
            )
        self._require_context_matches(state, context)
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        row = (
            confirmation.confirmation_id,
            confirmation.invocation_id,
            state_digest,
            context.context_digest,
        )
        with self._database.unit_of_work() as uow:
            decision = uow._execute_managed(
                """SELECT invocation_id, decision, state_digest,
                          context_digest
                   FROM runtime_backend_session_recovery_decisions
                   WHERE confirmation_id = ?""",
                (confirmation.confirmation_id,),
            ).fetchone()
            if decision is None or tuple(decision) != (
                confirmation.invocation_id,
                confirmation.decision.value,
                state_digest,
                context.context_digest,
            ):
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_mismatch"
                )
            existing = uow._execute_managed(
                """SELECT 1 FROM runtime_backend_session_recovery_attempts
                   WHERE confirmation_id = ?""",
                (confirmation.confirmation_id,),
            ).fetchone()
            if existing is not None:
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_already_used"
                )
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_backend_session_recovery_attempts(
                        confirmation_id, invocation_id, state_digest,
                        context_digest
                    ) VALUES (?, ?, ?, ?)""",
                    row,
                )
            except sqlite3.IntegrityError as exc:
                raise AgentExecutionRecoveryConfirmationRejected(
                    "recovery_confirmation_already_used"
                ) from exc
            uow.commit()

    def unresolved_session_recovery_attempt_for(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryBlocked | None:
        invocation = nonempty(invocation_id, "invocation_id")
        locator = self._binding_locator(
            scope_id=state.scope_id,
            thread_id=thread_id,
            agent_id=agent_id,
            backend_id=backend_id,
        )
        state_digest = text_digest(canonical_json(_state_to_dict(state)))
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT r.invocation_id, r.scope_id, r.thread_id,
                          r.agent_id, r.backend_id, r.state_digest,
                          r.context_digest
                   FROM runtime_backend_session_recovery_requests AS r
                   JOIN runtime_backend_session_recovery_attempts AS a
                     ON a.confirmation_id = r.confirmation_id
                   WHERE r.invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if row is None:
                return None
            if tuple(row[:6]) != (invocation, *locator, state_digest):
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复尝试与当前状态不匹配"
                )
            context_row = connection.execute(
                """SELECT context_digest
                   FROM runtime_agent_execution_recovery_contexts
                   WHERE invocation_id = ?""",
                (invocation,),
            ).fetchone()
            if context_row is None or context_row[0] != row[6]:
                raise AgentExecutionStateStoreConflictError(
                    "Session恢复尝试的Context已漂移"
                )
            recovered = connection.execute(
                """SELECT 1 FROM runtime_backend_session_recoveries
                   WHERE invocation_id = ? AND context_digest = ?""",
                (invocation, row[6]),
            ).fetchone()
            if recovered is not None:
                return None
            return AgentExecutionRecoveryBlocked(invocation_id=invocation)
        finally:
            connection.close()

    @staticmethod
    def _current_bound_session(
        uow: RuntimeUnitOfWork,
        locator: tuple[str, str, str, str],
    ) -> str | None:
        latest = uow._execute_managed(
            """SELECT replacement_backend_session_id
               FROM runtime_backend_session_recoveries
               WHERE scope_id = ? AND thread_id = ?
                 AND agent_id = ? AND backend_id = ?
               ORDER BY recovery_generation DESC LIMIT 1""",
            locator,
        ).fetchone()
        if latest is not None:
            return str(latest[0])
        base = uow._execute_managed(
            """SELECT backend_session_id
               FROM runtime_backend_session_bindings
               WHERE scope_id = ? AND thread_id = ?
                 AND agent_id = ? AND backend_id = ?""",
            locator,
        ).fetchone()
        return None if base is None else str(base[0])

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
        for row in connection.execute(
            """SELECT scope_id, thread_id, agent_id, backend_id,
                      backend_session_id
               FROM runtime_backend_session_bindings
               ORDER BY scope_id, thread_id, agent_id, backend_id"""
        ):
            for index, field_name in enumerate((
                "scope_id",
                "thread_id",
                "agent_id",
                "backend_id",
                "backend_session_id",
            )):
                nonempty(row[index], field_name)
        for row in connection.execute(
            """SELECT c.invocation_id, c.state_digest, c.context_json,
                      c.context_digest, s.scope_id, s.state_json,
                      s.state_digest
               FROM runtime_agent_execution_recovery_contexts AS c
               JOIN runtime_agent_execution_states AS s
                 ON s.invocation_id = c.invocation_id
               ORDER BY c.invocation_id"""
        ):
            if row[1] != row[6]:
                raise RuntimeStoredDataCorruptionError(
                    "recovery context state digest漂移"
                )
            state = self._decode_state_row((row[4], row[5], row[6]))
            context = self._decode_recovery_context_row(row[2], row[3])
            try:
                self._require_context_matches(state, context)
            except AgentExecutionStateStoreError as exc:
                raise RuntimeStoredDataCorruptionError(
                    "recovery context权威引用漂移"
                ) from exc
        chains: dict[tuple[str, str, str, str], tuple[int, str]] = {}
        base_sessions = {
            (row[0], row[1], row[2], row[3]): row[4]
            for row in connection.execute(
                """SELECT scope_id, thread_id, agent_id, backend_id,
                          backend_session_id
                   FROM runtime_backend_session_bindings"""
            )
        }
        for row in connection.execute(
            """SELECT scope_id, thread_id, agent_id, backend_id,
                      recovery_generation, stale_backend_session_id,
                      replacement_backend_session_id
               FROM runtime_backend_session_recoveries
               ORDER BY scope_id, thread_id, agent_id, backend_id,
                        recovery_generation"""
        ):
            key = (row[0], row[1], row[2], row[3])
            previous = chains.get(key, (0, base_sessions.get(key, "")))
            if row[4] != previous[0] + 1 or row[5] != previous[1]:
                raise RuntimeStoredDataCorruptionError(
                    "Backend Session恢复链漂移"
                )
            chains[key] = (row[4], row[6])
        for row in connection.execute(
            """SELECT r.confirmation_id, r.invocation_id, r.scope_id,
                      r.thread_id, r.agent_id, r.backend_id,
                      r.stale_backend_session_id, r.state_digest,
                      r.context_digest, s.state_digest, c.context_digest
               FROM runtime_backend_session_recovery_requests AS r
               JOIN runtime_agent_execution_states AS s
                 ON s.invocation_id = r.invocation_id
               JOIN runtime_agent_execution_recovery_contexts AS c
                 ON c.invocation_id = r.invocation_id
               ORDER BY r.invocation_id"""
        ):
            for index, field_name in enumerate((
                "confirmation_id",
                "invocation_id",
                "scope_id",
                "thread_id",
                "agent_id",
                "backend_id",
                "stale_backend_session_id",
            )):
                nonempty(row[index], field_name)
            if row[7] != row[9] or row[8] != row[10]:
                raise RuntimeStoredDataCorruptionError(
                    "Session恢复确认请求digest漂移"
                )
        for row in connection.execute(
            """SELECT d.confirmation_id, d.invocation_id, d.decision,
                      d.state_digest, d.context_digest,
                      r.invocation_id, r.state_digest, r.context_digest
               FROM runtime_backend_session_recovery_decisions AS d
               JOIN runtime_backend_session_recovery_requests AS r
                 ON r.confirmation_id = d.confirmation_id
               ORDER BY d.confirmation_id"""
        ):
            if (
                row[1] != row[5]
                or row[3] != row[6]
                or row[4] != row[7]
            ):
                raise RuntimeStoredDataCorruptionError(
                    "Session恢复确认决定漂移"
                )
        for row in connection.execute(
            """SELECT a.confirmation_id, a.invocation_id,
                      a.state_digest, a.context_digest, d.decision
               FROM runtime_backend_session_recovery_attempts AS a
               JOIN runtime_backend_session_recovery_decisions AS d
                 ON d.confirmation_id = a.confirmation_id
               ORDER BY a.confirmation_id"""
        ):
            if row[4] != "create_new_session":
                raise RuntimeStoredDataCorruptionError(
                    "停止决定不能拥有Session恢复尝试"
                )

    @staticmethod
    def _binding_locator(
        *,
        scope_id: str,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> tuple[str, str, str, str]:
        return (
            nonempty(scope_id, "scope_id"),
            nonempty(thread_id, "thread_id"),
            nonempty(agent_id, "agent_id"),
            nonempty(backend_id, "backend_id"),
        )

    @staticmethod
    def _require_context_matches(
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
    ) -> None:
        if not isinstance(state, AgentExecutionStateEnvelope):
            raise AgentExecutionStateStoreValidationError(
                "state 必须是 AgentExecutionStateEnvelope"
            )
        if not isinstance(context, AgentExecutionRecoveryContext):
            raise AgentExecutionStateStoreValidationError(
                "context 必须是 AgentExecutionRecoveryContext"
            )
        if (
            context.scope_id != state.scope_id
            or context.task_ref != state.task_ref
            or context.task_snapshot.ref != state.snapshot_ref
            or context.permission_snapshot.ref
            != state.permission_snapshot_ref
            or tuple(item.ref for item in context.artifacts)
            != state.artifact_refs
        ):
            raise AgentExecutionStateStoreConflictError(
                "recovery context与权威状态信封不一致"
            )

    @staticmethod
    def _decode_recovery_context_row(
        raw_value: object,
        digest_value: object,
    ) -> AgentExecutionRecoveryContext:
        try:
            raw = str(raw_value)
            if text_digest(raw) != digest_value:
                raise RuntimeStoredDataCorruptionError(
                    "runtime recovery context digest漂移"
                )
            decoded = json.loads(raw)
            if not isinstance(decoded, Mapping):
                raise RuntimeProtocolError("recovery context必须是对象")
            return _recovery_context_from_dict(decoded)
        except RuntimeStoredDataCorruptionError:
            raise
        except (
            RuntimeProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime recovery context无法重建"
            ) from exc

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
