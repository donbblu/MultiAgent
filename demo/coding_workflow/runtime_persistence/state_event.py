from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Mapping

from ..runtime_domain.common import RuntimeProtocolError, namespaced, nonempty
from ..runtime_domain.events import RuntimeEvent
from ..runtime_domain.interaction import Thread, ThreadState
from .sqlite import (
    _outbox_delivery_key,
    _outbox_intent_digest,
    RuntimeDatabaseIntegrityError,
    RuntimePersistenceError,
    RuntimePersistenceFaultPoint,
    RuntimeUnitOfWork,
    SQLiteRuntimeDatabase,
)


class RuntimeStateEventError(RuntimePersistenceError):
    """A concrete state + RuntimeEvent mutation was rejected."""


class RuntimeStateEventValidationError(RuntimeStateEventError):
    """The requested Thread/Event pair violates the frozen contract."""


class RuntimeStateEventConflictError(RuntimeStateEventError):
    """A durable row conflicts with the requested mutation."""


class RuntimeStateConflictError(RuntimeStateEventConflictError):
    """The authoritative Thread head does not match expected_version."""


class RuntimeEventConflictError(RuntimeStateEventConflictError):
    """An event_id was already committed for different content."""


class RuntimeIdempotencyConflictError(RuntimeStateEventConflictError):
    """An idempotency key was already committed for another mutation."""


class RuntimeEventSequenceConflictError(RuntimeStateEventConflictError):
    """An aggregate sequence number is occupied or non-contiguous."""


class RuntimeStoredDataCorruptionError(RuntimeDatabaseIntegrityError):
    """Stored JSON, digest, projection, or state/event linkage drifted."""


class ThreadEventApplyResult(str, Enum):
    APPLIED = "applied"
    ALREADY_COMMITTED = "already_committed"


@dataclass(frozen=True)
class ThreadEventMutation:
    expected_version: int
    thread: Thread
    event: RuntimeEvent

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expected_version, int)
            or isinstance(self.expected_version, bool)
            or self.expected_version < 0
        ):
            raise RuntimeStateEventValidationError(
                "expected_version 必须是大于等于 0 的整数"
            )
        if not isinstance(self.thread, Thread):
            raise RuntimeStateEventValidationError("thread 必须是 Thread")
        if not isinstance(self.event, RuntimeEvent):
            raise RuntimeStateEventValidationError("event 必须是 RuntimeEvent")


@dataclass(frozen=True)
class _EncodedMutation:
    thread_json: str
    thread_digest: str
    event_json: str
    event_digest: str
    mutation_digest: str


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _encode_mutation(mutation: ThreadEventMutation) -> _EncodedMutation:
    thread_json = _canonical_json(dict(mutation.thread.to_dict()))
    event_json = _canonical_json(dict(mutation.event.to_dict()))
    thread_digest = _text_digest(thread_json)
    event_digest = _text_digest(event_json)
    mutation_json = _canonical_json({
        "expected_version": mutation.expected_version,
        "result_state_digest": thread_digest,
        "event_digest": event_digest,
    })
    return _EncodedMutation(
        thread_json,
        thread_digest,
        event_json,
        event_digest,
        _text_digest(mutation_json),
    )


class SQLiteThreadEventStore:
    """First concrete State Table plus an append-only RuntimeEvent Journal.

    This slice intentionally persists only Thread current-state.  It does not
    claim generic aggregate relationships, Event Sourcing, Outbox delivery,
    or producer authorization.  Every newly applied Event does create one
    durable Outbox intent in the same UnitOfWork.
    """

    _CREATE_EVENT_TYPE = "core:thread_created"
    _UPDATE_EVENT_TYPE = "core:thread_updated"
    _PAUSE_EVENT_TYPE = "core:thread_paused"
    _RESUME_EVENT_TYPE = "core:thread_resumed"
    _ARCHIVE_EVENT_TYPE = "core:thread_archived"

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise RuntimeStateEventValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database

    def apply(
        self,
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
    ) -> ThreadEventApplyResult:
        if not isinstance(uow, RuntimeUnitOfWork):
            raise RuntimeStateEventValidationError(
                "uow 必须是 RuntimeUnitOfWork"
            )
        try:
            if uow._database is not self._database:
                raise RuntimeStateEventValidationError(
                    "uow 与 SQLiteThreadEventStore 必须属于同一数据库"
                )
            if not isinstance(mutation, ThreadEventMutation):
                raise RuntimeStateEventValidationError(
                    "mutation 必须是 ThreadEventMutation"
                )
            encoded = _encode_mutation(mutation)
            self._validate_static_binding(mutation)
            duplicate = self._resolve_duplicate(uow, mutation, encoded)
            if duplicate is not None:
                return duplicate
            self._reject_occupied_sequence(uow, mutation)
            current_row = uow.execute(
                """SELECT scope_id, entity_type, thread_id, version, state,
                          created_at, updated_at, archived_at, thread_json,
                          thread_digest, last_sequence_no, last_event_id
                   FROM runtime_threads
                   WHERE scope_id = ? AND thread_id = ?""",
                (mutation.thread.scope_id, mutation.thread.thread_id),
            ).fetchone()
            current = (
                None
                if current_row is None
                else self._decode_thread_row(uow, current_row)
            )
            self._validate_against_current(mutation, current, current_row)
            self._write_thread(uow, mutation, encoded, current_row)
            uow._emit_fault(
                RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_STATE_WRITE
            )
            self._append_event(uow, mutation, encoded)
            uow._emit_fault(
                RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_EVENT_APPEND
            )
            self._enqueue_outbox(uow, mutation, encoded)
            uow._emit_fault(
                RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_OUTBOX_ENQUEUE
            )
            return ThreadEventApplyResult.APPLIED
        except BaseException:
            uow._abort_managed_operation()
            raise

    def get_thread(self, scope_id: str, thread_id: str) -> Thread | None:
        scope_id = nonempty(scope_id, "scope_id")
        thread_id = nonempty(thread_id, "thread_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT scope_id, entity_type, thread_id, version, state,
                          created_at, updated_at, archived_at, thread_json,
                          thread_digest, last_sequence_no, last_event_id
                   FROM runtime_threads
                   WHERE scope_id = ? AND thread_id = ?""",
                (scope_id, thread_id),
            ).fetchone()
            if row is None:
                return None
            return self._decode_thread_row(connection, row)
        finally:
            connection.close()

    def get_event(self, event_id: str) -> RuntimeEvent | None:
        event_id = nonempty(event_id, "event_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT event_id, scope_id, event_type, aggregate_type,
                          aggregate_id, aggregate_version, sequence_no,
                          event_version, idempotency_key, trace_id,
                          correlation_id, occurred_at, recorded_at, event_json,
                          event_digest, result_state_digest, mutation_digest
                   FROM runtime_events WHERE event_id = ?""",
                (event_id,),
            ).fetchone()
            return None if row is None else self._decode_event_row(row)
        finally:
            connection.close()

    def list_events(
        self,
        scope_id: str,
        aggregate_type: str,
        aggregate_id: str,
        *,
        after_sequence_no: int = 0,
        limit: int = 100,
    ) -> tuple[RuntimeEvent, ...]:
        scope_id = nonempty(scope_id, "scope_id")
        aggregate_type = namespaced(aggregate_type, "aggregate_type")
        aggregate_id = nonempty(aggregate_id, "aggregate_id")
        if (
            not isinstance(after_sequence_no, int)
            or isinstance(after_sequence_no, bool)
            or after_sequence_no < 0
        ):
            raise RuntimeStateEventValidationError(
                "after_sequence_no 必须是大于等于 0 的整数"
            )
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 1_000
        ):
            raise RuntimeStateEventValidationError("limit 必须在 1..1000")
        connection = self._open_read_connection()
        try:
            rows = connection.execute(
                """SELECT event_id, scope_id, event_type, aggregate_type,
                          aggregate_id, aggregate_version, sequence_no,
                          event_version, idempotency_key, trace_id,
                          correlation_id, occurred_at, recorded_at, event_json,
                          event_digest, result_state_digest, mutation_digest
                   FROM runtime_events
                   WHERE scope_id = ? AND aggregate_type = ?
                     AND aggregate_id = ? AND sequence_no > ?
                   ORDER BY sequence_no
                   LIMIT ?""",
                (
                    scope_id,
                    aggregate_type,
                    aggregate_id,
                    after_sequence_no,
                    limit,
                ),
            ).fetchall()
            return tuple(self._decode_event_row(row) for row in rows)
        finally:
            connection.close()

    def _verify_connection(
        self,
        connection: sqlite3.Connection,
        *,
        require_outbox: bool = True,
    ) -> None:
        event_rows = connection.execute(
            """SELECT event_id, scope_id, event_type, aggregate_type,
                      aggregate_id, aggregate_version, sequence_no,
                      event_version, idempotency_key, trace_id,
                      correlation_id, occurred_at, recorded_at, event_json,
                      event_digest, result_state_digest, mutation_digest
               FROM runtime_events ORDER BY event_id"""
        ).fetchall()
        decoded_events = []
        for row in event_rows:
            decoded_events.append((self._decode_event_row(row), row[14]))
        orphan = connection.execute(
            """SELECT event.event_id
               FROM runtime_events AS event
               LEFT JOIN runtime_threads AS thread
                 ON thread.scope_id = event.scope_id
                AND thread.thread_id = event.aggregate_id
               WHERE event.aggregate_type = 'core:thread'
                 AND (
                    thread.thread_id IS NULL
                    OR thread.version < event.aggregate_version
                    OR thread.last_sequence_no < event.sequence_no
                 )
               LIMIT 1"""
        ).fetchone()
        if orphan is not None:
            raise RuntimeStoredDataCorruptionError(
                f"RuntimeEvent 缺少可覆盖它的 Thread head: {orphan[0]}"
            )
        thread_rows = connection.execute(
            """SELECT scope_id, entity_type, thread_id, version, state,
                      created_at, updated_at, archived_at, thread_json,
                      thread_digest, last_sequence_no, last_event_id
               FROM runtime_threads ORDER BY scope_id, thread_id"""
        ).fetchall()
        for row in thread_rows:
            self._decode_thread_row(
                connection,
                row,
                require_outbox=require_outbox,
            )
        if require_outbox:
            self._database._assert_outbox_policy_binding(connection)
            for event, event_digest in decoded_events:
                self._verify_event_outbox(connection, event, event_digest)
            extra = connection.execute(
                """SELECT outbox.delivery_key
                   FROM runtime_outbox AS outbox
                   LEFT JOIN runtime_events AS event
                     ON event.event_id = outbox.source_event_id
                    AND event.scope_id = outbox.scope_id
                   WHERE event.event_id IS NULL
                   LIMIT 1"""
            ).fetchone()
            if extra is not None:
                raise RuntimeStoredDataCorruptionError(
                    f"Outbox 缺少 RuntimeEvent: {extra[0]}"
                )

    def _open_read_connection(self) -> sqlite3.Connection:
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

    @staticmethod
    def _validate_static_binding(mutation: ThreadEventMutation) -> None:
        thread = mutation.thread
        event = mutation.event
        if thread.version != mutation.expected_version + 1:
            raise RuntimeStateEventValidationError(
                "post Thread version 必须等于 expected_version + 1"
            )
        if (
            event.scope_id != thread.scope_id
            or event.aggregate_ref != thread.reference
            or event.aggregate_version != thread.version
            or event.thread_ref != thread.reference
        ):
            raise RuntimeStateEventValidationError(
                "RuntimeEvent 必须绑定 post-state Thread 引用"
            )
        state_value = event.payload.get("state")
        if state_value != thread.state.value:
            raise RuntimeStateEventValidationError(
                "RuntimeEvent payload.state 必须等于 post Thread state"
            )
        if mutation.expected_version == 0:
            if thread.version != 1 or thread.state is not ThreadState.OPEN:
                raise RuntimeStateEventValidationError(
                    "Thread create 必须从 version=1/open 开始"
                )
            if event.sequence_no != 1:
                raise RuntimeEventSequenceConflictError(
                    "Thread 首个 RuntimeEvent sequence_no 必须为 1"
                )
            if event.event_type != SQLiteThreadEventStore._CREATE_EVENT_TYPE:
                raise RuntimeStateEventValidationError(
                    "Thread create 必须使用 core:thread_created"
                )
            if "previous_state" in event.payload:
                raise RuntimeStateEventValidationError(
                    "Thread create Event 不能包含 previous_state"
                )
        else:
            if "previous_state" not in event.payload:
                raise RuntimeStateEventValidationError(
                    "Thread update Event 必须包含 previous_state"
                )

    def _resolve_duplicate(
        self,
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
        encoded: _EncodedMutation,
    ) -> ThreadEventApplyResult | None:
        projection = """event_id, scope_id, event_type, aggregate_type,
            aggregate_id, aggregate_version, sequence_no, event_version,
            idempotency_key, trace_id, correlation_id, occurred_at, recorded_at,
            event_json, event_digest, result_state_digest, mutation_digest"""
        by_id = uow.execute(
            f"SELECT {projection} FROM runtime_events WHERE event_id = ?",
            (mutation.event.event_id,),
        ).fetchone()
        by_key = uow.execute(
            f"SELECT {projection} FROM runtime_events WHERE idempotency_key = ?",
            (mutation.event.idempotency_key,),
        ).fetchone()
        if by_id is None and by_key is None:
            return None
        decoded_by_id = None if by_id is None else self._decode_event_row(by_id)
        if by_key is not None and (by_id is None or by_key[0] != by_id[0]):
            self._decode_event_row(by_key)
        if by_id is not None and by_key is not None and by_id[0] == by_key[0]:
            if (
                decoded_by_id == mutation.event
                and by_id[8] == mutation.event.idempotency_key
                and by_id[14] == encoded.event_digest
                and by_id[15] == encoded.thread_digest
                and by_id[16] == encoded.mutation_digest
            ):
                head_row = uow.execute(
                    """SELECT scope_id, entity_type, thread_id, version, state,
                              created_at, updated_at, archived_at, thread_json,
                              thread_digest, last_sequence_no, last_event_id
                       FROM runtime_threads
                       WHERE scope_id = ? AND thread_id = ?""",
                    (
                        mutation.thread.scope_id,
                        mutation.thread.thread_id,
                    ),
                ).fetchone()
                if head_row is None:
                    raise RuntimeStoredDataCorruptionError(
                        "历史成功 Event 缺少当前 Thread head"
                    )
                current = self._decode_thread_row(uow, head_row)
                if (
                    current.version < mutation.event.aggregate_version
                    or int(head_row[10]) < mutation.event.sequence_no
                ):
                    raise RuntimeStoredDataCorruptionError(
                        "当前 Thread head 落后于历史成功 Event"
                    )
                self._verify_event_outbox(
                    uow,
                    mutation.event,
                    encoded.event_digest,
                )
                return ThreadEventApplyResult.ALREADY_COMMITTED
        if by_id is not None:
            raise RuntimeEventConflictError(
                f"event_id 已用于不同 mutation: {mutation.event.event_id}"
            )
        raise RuntimeIdempotencyConflictError(
            "idempotency_key 已用于不同 mutation: "
            f"{mutation.event.idempotency_key}"
        )

    @staticmethod
    def _reject_occupied_sequence(
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
    ) -> None:
        event = mutation.event
        row = uow.execute(
            """SELECT event_id FROM runtime_events
               WHERE scope_id = ? AND aggregate_type = ?
                 AND aggregate_id = ? AND sequence_no = ?""",
            (
                event.scope_id,
                event.aggregate_ref.entity_type,
                event.aggregate_ref.entity_id,
                event.sequence_no,
            ),
        ).fetchone()
        if row is not None:
            raise RuntimeEventSequenceConflictError(
                "aggregate sequence_no 已被其他 Event 占用"
            )

    def _validate_against_current(
        self,
        mutation: ThreadEventMutation,
        current: Thread | None,
        current_row,
    ) -> None:
        post = mutation.thread
        event = mutation.event
        if mutation.expected_version == 0:
            if current is not None:
                raise RuntimeStateConflictError("Thread 已存在，不能按 create 提交")
            return
        if current is None or current_row is None:
            raise RuntimeStateConflictError("Thread 不存在，不能按 update 提交")
        if current.version != mutation.expected_version:
            raise RuntimeStateConflictError(
                "Thread expected_version 与当前版本不一致"
            )
        if post.scope_id != current.scope_id or post.thread_id != current.thread_id:
            raise RuntimeStateEventValidationError("Thread update 不能改变身份")
        if post.created_at != current.created_at:
            raise RuntimeStateEventValidationError(
                "Thread update 不能改变 created_at"
            )
        if datetime.fromisoformat(post.updated_at) <= datetime.fromisoformat(
            current.updated_at
        ):
            raise RuntimeStateEventValidationError(
                "Thread updated_at 必须严格前进"
            )
        if current.state is ThreadState.ARCHIVED:
            raise RuntimeStateEventValidationError("archived Thread 是终态")
        changed = (
            post.title != current.title
            or post.participant_refs != current.participant_refs
            or post.policy_ref != current.policy_ref
            or post.state is not current.state
        )
        if not changed:
            raise RuntimeStateEventValidationError(
                "Thread update 必须包含真实业务字段变化"
            )
        previous_state = event.payload.get("previous_state")
        if previous_state != current.state.value:
            raise RuntimeStateEventValidationError(
                "RuntimeEvent previous_state 与当前 Thread 不一致"
            )
        if post.state is current.state:
            expected_type = self._UPDATE_EVENT_TYPE
        elif current.state is ThreadState.OPEN and post.state is ThreadState.PAUSED:
            expected_type = self._PAUSE_EVENT_TYPE
        elif current.state is ThreadState.PAUSED and post.state is ThreadState.OPEN:
            expected_type = self._RESUME_EVENT_TYPE
        elif post.state is ThreadState.ARCHIVED:
            expected_type = self._ARCHIVE_EVENT_TYPE
        else:
            raise RuntimeStateEventValidationError("Thread state transition 无效")
        if event.event_type != expected_type:
            raise RuntimeStateEventValidationError(
                f"Thread transition 必须使用 {expected_type}"
            )
        expected_sequence = int(current_row[10]) + 1
        if event.sequence_no != expected_sequence:
            raise RuntimeEventSequenceConflictError(
                f"RuntimeEvent sequence_no 必须为 {expected_sequence}"
            )

    @staticmethod
    def _write_thread(
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
        encoded: _EncodedMutation,
        current_row,
    ) -> None:
        thread = mutation.thread
        event = mutation.event
        values = (
            thread.version,
            thread.state.value,
            thread.created_at,
            thread.updated_at,
            thread.archived_at,
            encoded.thread_json,
            encoded.thread_digest,
            event.sequence_no,
            event.event_id,
        )
        if current_row is None:
            uow._execute_managed(
                """INSERT INTO runtime_threads(
                    scope_id, entity_type, thread_id, version, state,
                    created_at, updated_at, archived_at, thread_json,
                    thread_digest, last_sequence_no, last_event_id
                ) VALUES (?, 'core:thread', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (thread.scope_id, thread.thread_id, *values),
            )
            return
        result = uow._execute_managed(
            """UPDATE runtime_threads
               SET version = ?, state = ?, created_at = ?, updated_at = ?,
                   archived_at = ?, thread_json = ?, thread_digest = ?,
                   last_sequence_no = ?, last_event_id = ?
               WHERE scope_id = ? AND thread_id = ?
                 AND version = ? AND last_sequence_no = ?""",
            (
                *values,
                thread.scope_id,
                thread.thread_id,
                mutation.expected_version,
                int(current_row[10]),
            ),
        )
        if result.rowcount != 1:
            raise RuntimeStateConflictError("Thread CAS update 失败")

    @staticmethod
    def _append_event(
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
        encoded: _EncodedMutation,
    ) -> None:
        event = mutation.event
        uow._execute_managed(
            """INSERT INTO runtime_events(
                event_id, scope_id, event_type, aggregate_type, aggregate_id,
                aggregate_version, sequence_no, event_version,
                idempotency_key, trace_id, correlation_id, occurred_at,
                recorded_at, event_json, event_digest, result_state_digest,
                mutation_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.scope_id,
                event.event_type,
                event.aggregate_ref.entity_type,
                event.aggregate_ref.entity_id,
                event.aggregate_version,
                event.sequence_no,
                event.event_version,
                event.idempotency_key,
                event.trace_id,
                event.correlation_id,
                event.occurred_at,
                event.recorded_at,
                encoded.event_json,
                encoded.event_digest,
                encoded.thread_digest,
                encoded.mutation_digest,
            ),
        )

    def _enqueue_outbox(
        self,
        uow: RuntimeUnitOfWork,
        mutation: ThreadEventMutation,
        encoded: _EncodedMutation,
    ) -> None:
        event = mutation.event
        policy = self._database.outbox_policy
        delivery_key = _outbox_delivery_key(
            policy.destination,
            event.event_id,
        )
        intent_digest = _outbox_intent_digest(
            scope_id=event.scope_id,
            source_event_id=event.event_id,
            event_digest=encoded.event_digest,
            destination=policy.destination,
            delivery_key=delivery_key,
            created_at=event.recorded_at,
            policy=policy,
        )
        try:
            uow._execute_managed(
                """INSERT INTO runtime_outbox(
                    delivery_key, source_event_id, scope_id, destination,
                    event_digest, created_at, intent_digest, policy_version,
                    policy_digest, state, updated_at, claim_generation,
                    attempt_count, available_at, claim_token, publisher_id,
                    claim_expires_at, last_error_code, suppress_reason,
                    published_at, receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 0, 0, ?,
                          NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
                (
                    delivery_key,
                    event.event_id,
                    event.scope_id,
                    policy.destination,
                    encoded.event_digest,
                    event.recorded_at,
                    intent_digest,
                    policy.policy_version,
                    policy.policy_digest,
                    event.recorded_at,
                    event.recorded_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox enqueue 与持久 identity 冲突: {event.event_id}"
            ) from exc

    def _verify_event_outbox(
        self,
        reader,
        event: RuntimeEvent,
        event_digest: str,
    ) -> None:
        row = reader.execute(
            """SELECT delivery_key, source_event_id, scope_id, destination,
                      event_digest, created_at, intent_digest, policy_version,
                      policy_digest, state, updated_at, claim_generation,
                      attempt_count, available_at, claim_token, publisher_id,
                      claim_expires_at, last_error_code, suppress_reason,
                      published_at, receipt_id
               FROM runtime_outbox WHERE source_event_id = ?""",
            (event.event_id,),
        ).fetchone()
        if row is None:
            raise RuntimeStoredDataCorruptionError(
                f"RuntimeEvent 缺少 Outbox intent: {event.event_id}"
            )
        policy = self._database.outbox_policy
        delivery_key = _outbox_delivery_key(
            policy.destination,
            event.event_id,
        )
        intent_digest = _outbox_intent_digest(
            scope_id=event.scope_id,
            source_event_id=event.event_id,
            event_digest=event_digest,
            destination=policy.destination,
            delivery_key=delivery_key,
            created_at=event.recorded_at,
            policy=policy,
        )
        expected_identity = (
            delivery_key,
            event.event_id,
            event.scope_id,
            policy.destination,
            event_digest,
            event.recorded_at,
            intent_digest,
            policy.policy_version,
            policy.policy_digest,
        )
        if tuple(row[:9]) != expected_identity:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox identity/digest 与 RuntimeEvent 不一致: {event.event_id}"
            )
        state = row[9]
        if state not in {
            "LEGACY_SUPPRESSED",
            "PENDING",
            "CLAIMED",
            "PUBLISHED",
        }:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox state 无效: {event.event_id}"
            )
        if not isinstance(row[10], str) or not row[10]:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox updated_at 无效: {event.event_id}"
            )
        for index, field_name in ((11, "claim_generation"), (12, "attempt_count")):
            value = row[index]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise RuntimeStoredDataCorruptionError(
                    f"Outbox {field_name} 无效: {event.event_id}"
                )
        if state == "LEGACY_SUPPRESSED":
            expected_lifecycle = (
                event.recorded_at,
                0,
                0,
                None,
                None,
                None,
                None,
                None,
                "pre_outbox_cutover",
                None,
                None,
            )
            if tuple(row[10:]) != expected_lifecycle:
                raise RuntimeStoredDataCorruptionError(
                    f"LEGACY_SUPPRESSED Outbox lifecycle 漂移: {event.event_id}"
                )
        elif state == "PENDING" and row[11] == 0:
            expected_lifecycle = (
                event.recorded_at,
                0,
                0,
                event.recorded_at,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            if tuple(row[10:]) != expected_lifecycle:
                raise RuntimeStoredDataCorruptionError(
                    f"初始 PENDING Outbox lifecycle 漂移: {event.event_id}"
                )

    def _decode_thread_row(
        self,
        reader,
        row,
        *,
        require_outbox: bool = True,
    ) -> Thread:
        try:
            raw = str(row[8])
            decoded = json.loads(raw)
            if _canonical_json(decoded) != raw or _text_digest(raw) != row[9]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_threads canonical JSON/digest 漂移"
                )
            thread = Thread.from_dict(decoded)
            projections = (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
            )
            expected = (
                thread.scope_id,
                Thread.REFERENCE_TYPE,
                thread.thread_id,
                thread.version,
                thread.state.value,
                thread.created_at,
                thread.updated_at,
                thread.archived_at,
            )
            if projections != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_threads projection 与 Thread JSON 不一致"
                )
            event_row = reader.execute(
                """SELECT event_id, scope_id, event_type, aggregate_type,
                          aggregate_id, aggregate_version, sequence_no,
                          event_version, idempotency_key, trace_id,
                          correlation_id, occurred_at, recorded_at, event_json,
                          event_digest, result_state_digest, mutation_digest
                   FROM runtime_events
                   WHERE scope_id = ? AND aggregate_type = ?
                     AND aggregate_id = ? AND aggregate_version = ?
                     AND sequence_no = ? AND event_id = ?
                     AND result_state_digest = ?""",
                (
                    row[0], row[1], row[2], row[3], row[10], row[11], row[9]
                ),
            ).fetchone()
            if event_row is None:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_threads last Event 链接无效"
                )
            event = self._decode_event_row(event_row)
            if require_outbox:
                self._verify_event_outbox(reader, event, event_row[14])
            return thread
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_threads 无法重建 Thread"
            ) from exc

    @staticmethod
    def _decode_event_row(row) -> RuntimeEvent:
        try:
            raw = str(row[13])
            decoded = json.loads(raw)
            if _canonical_json(decoded) != raw or _text_digest(raw) != row[14]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_events canonical JSON/digest 漂移"
                )
            event = RuntimeEvent.from_dict(decoded)
            projections = (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                row[7], row[8], row[9], row[10], row[11], row[12],
            )
            expected = (
                event.event_id,
                event.scope_id,
                event.event_type,
                event.aggregate_ref.entity_type,
                event.aggregate_ref.entity_id,
                event.aggregate_version,
                event.sequence_no,
                event.event_version,
                event.idempotency_key,
                event.trace_id,
                event.correlation_id,
                event.occurred_at,
                event.recorded_at,
            )
            if projections != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_events projection 与 RuntimeEvent JSON 不一致"
                )
            for digest in (row[15], row[16]):
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise RuntimeStoredDataCorruptionError(
                        "runtime_events state/mutation digest 无效"
                    )
            expected_mutation_digest = _text_digest(_canonical_json({
                "expected_version": event.aggregate_version - 1,
                "result_state_digest": row[15],
                "event_digest": row[14],
            }))
            if row[16] != expected_mutation_digest:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_events mutation digest 漂移"
                )
            return event
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_events 无法重建 RuntimeEvent"
            ) from exc
