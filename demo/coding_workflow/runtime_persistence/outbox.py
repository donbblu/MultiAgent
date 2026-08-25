from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from ._record_codec import (
    DecodedOutboxRow,
    RuntimeStoredDataCorruptionError,
    canonical_utc_timestamp,
    decode_outbox_row,
    decode_runtime_event_row,
    validate_outbox_aggregate_history,
)
from .sqlite import (
    OutboxPolicy,
    RuntimeDatabaseBusyError,
    RuntimeOutboxConfigurationError,
    RuntimePersistenceError,
    RuntimePersistenceFaultPoint,
    RuntimeSchemaCorruptionError,
    SQLiteRuntimeDatabase,
)


_SQLITE_SIGNED_INT64_MAX = (1 << 63) - 1
_MAX_CLAIM_TTL_MS = 86_400_000
_MAX_BATCH_LIMIT = 1_000
_MAX_RETRY_STEPS = 64
_MAX_RETRY_DELAY_MS = 604_800_000
_CLAIM_TOKEN = re.compile(r"^obc-v1-[0-9a-f]{64}$")
_DELIVERY_KEY = re.compile(r"^obx-v1-[0-9a-f]{64}$")
_TERMINAL_STATES = frozenset({"LEGACY_SUPPRESSED", "PUBLISHED"})


class RuntimeOutboxLifecycleError(RuntimePersistenceError):
    """The local Outbox claim/NACK lifecycle operation was rejected."""


class RuntimeOutboxValidationError(RuntimeOutboxLifecycleError):
    """A lifecycle dependency, policy, or public value is invalid."""


class RuntimeOutboxOwnershipLostError(RuntimeOutboxLifecycleError):
    """The supplied claim is absent, expired, or no longer current."""


class RuntimeOutboxClockError(RuntimeOutboxLifecycleError):
    """The injected wall clock cannot safely drive the lifecycle."""


class RuntimeOutboxTokenFactoryError(RuntimeOutboxLifecycleError):
    """The injected claim-token factory returned no safe fresh token."""


class RuntimeOutboxAttemptExhaustedError(RuntimeOutboxLifecycleError):
    """The persisted attempt identity cannot be incremented in SQLite."""


class OutboxState(str, Enum):
    LEGACY_SUPPRESSED = "LEGACY_SUPPRESSED"
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"


class OutboxNackErrorCode(str, Enum):
    TRANSPORT_ERROR = "outbox:transport_error"
    ACK_MISSING = "outbox:ack_missing"
    ACK_INVALID = "outbox:ack_invalid"


class OutboxClock(Protocol):
    def now(self) -> datetime:
        ...


class OutboxClaimTokenFactory(Protocol):
    def new_token(self) -> str:
        ...


def _strict_text(
    value: object,
    field_name: str,
    *,
    maximum: int | None = None,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or (maximum is not None and len(value) > maximum)
    ):
        limit = "" if maximum is None else f"长度不超过 {maximum} 的"
        raise RuntimeOutboxValidationError(
            f"{field_name} 必须是{limit}非空文本"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeOutboxValidationError(
            f"{field_name} 必须是有效 UTF-8 文本"
        ) from exc
    return value


def _strict_nonnegative_int(value: object, field_name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > _SQLITE_SIGNED_INT64_MAX
    ):
        raise RuntimeOutboxValidationError(
            f"{field_name} 必须是非负 SQLite int64"
        )
    return value


def _strict_claim_token(value: object) -> str:
    if not isinstance(value, str) or _CLAIM_TOKEN.fullmatch(value) is None:
        raise RuntimeOutboxValidationError(
            "claim_token 必须是 obc-v1-<64 lowercase hex>"
        )
    return value


@dataclass(frozen=True)
class OutboxSnapshot:
    scope_id: str
    delivery_key: str
    source_event_id: str
    destination: str
    event_digest: str
    intent_digest: str
    policy_version: str
    policy_digest: str
    state: OutboxState
    claim_generation: int
    attempt_count: int
    available_at: str | None
    publisher_id: str | None
    claim_expires_at: str | None
    last_error_code: str | None
    suppress_reason: str | None
    created_at: str
    updated_at: str
    published_at: str | None
    receipt_id: str | None


@dataclass(frozen=True)
class OutboxClaimOwnership:
    scope_id: str
    delivery_key: str
    claim_generation: int
    claim_token: str = field(repr=False)
    publisher_id: str

    def __post_init__(self) -> None:
        _strict_text(self.scope_id, "scope_id")
        if (
            not isinstance(self.delivery_key, str)
            or _DELIVERY_KEY.fullmatch(self.delivery_key) is None
        ):
            raise RuntimeOutboxValidationError("delivery_key 格式无效")
        generation = _strict_nonnegative_int(
            self.claim_generation,
            "claim_generation",
        )
        if generation < 1:
            raise RuntimeOutboxValidationError(
                "claim_generation 必须是正 SQLite int64"
            )
        _strict_claim_token(self.claim_token)
        _strict_text(self.publisher_id, "publisher_id", maximum=256)


@dataclass(frozen=True)
class OutboxClaim:
    ownership: OutboxClaimOwnership
    source_event_id: str
    destination: str
    event_digest: str
    attempt_count: int
    claimed_at: str
    claim_expires_at: str
    policy_version: str
    policy_digest: str


@dataclass(frozen=True)
class OutboxNackResult:
    scope_id: str
    delivery_key: str
    claim_generation: int
    attempt_count: int
    error_code: OutboxNackErrorCode
    failed_at: str
    available_at: str


_EVENT_PROJECTION = """
    e.event_id, e.scope_id, e.event_type, e.aggregate_type,
    e.aggregate_id, e.aggregate_version, e.sequence_no,
    e.event_version, e.idempotency_key, e.trace_id,
    e.correlation_id, e.occurred_at, e.recorded_at, e.event_json,
    e.event_digest, e.result_state_digest, e.mutation_digest
"""
_OUTBOX_PROJECTION = """
    o.delivery_key, o.source_event_id, o.scope_id, o.destination,
    o.event_digest, o.created_at, o.intent_digest, o.policy_version,
    o.policy_digest, o.state, o.updated_at, o.claim_generation,
    o.attempt_count, o.available_at, o.claim_token, o.publisher_id,
    o.claim_expires_at, o.last_error_code, o.suppress_reason,
    o.published_at, o.receipt_id
"""


def _require_lifecycle_policy(policy: OutboxPolicy) -> None:
    if not (1 <= policy.claim_ttl_ms <= _MAX_CLAIM_TTL_MS):
        raise RuntimeOutboxValidationError(
            "3B-1 claim_ttl_ms 必须在 1..86400000"
        )
    if not (1 <= policy.batch_limit <= _MAX_BATCH_LIMIT):
        raise RuntimeOutboxValidationError(
            "3B-1 batch_limit 必须在 1..1000"
        )
    if not (1 <= len(policy.retry_delays_ms) <= _MAX_RETRY_STEPS):
        raise RuntimeOutboxValidationError(
            "3B-1 retry_delays_ms 长度必须在 1..64"
        )
    if any(
        delay < 0 or delay > _MAX_RETRY_DELAY_MS
        for delay in policy.retry_delays_ms
    ):
        raise RuntimeOutboxValidationError(
            "3B-1 retry delay 必须在 0..604800000"
        )


def _snapshot(record: DecodedOutboxRow) -> OutboxSnapshot:
    return OutboxSnapshot(
        scope_id=record.scope_id,
        delivery_key=record.delivery_key,
        source_event_id=record.source_event_id,
        destination=record.destination,
        event_digest=record.event_digest,
        intent_digest=record.intent_digest,
        policy_version=record.policy_version,
        policy_digest=record.policy_digest,
        state=OutboxState(record.state),
        claim_generation=record.claim_generation,
        attempt_count=record.attempt_count,
        available_at=record.available_at,
        publisher_id=record.publisher_id,
        claim_expires_at=record.claim_expires_at,
        last_error_code=record.last_error_code,
        suppress_reason=record.suppress_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        published_at=record.published_at,
        receipt_id=record.receipt_id,
    )


class SQLiteOutboxLifecycleStore:
    """Owns the local v3 Outbox claim/NACK state machine."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        publisher_id: str,
        clock: OutboxClock,
        claim_token_factory: OutboxClaimTokenFactory,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise RuntimeOutboxValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database
        self._publisher_id = _strict_text(
            publisher_id,
            "publisher_id",
            maximum=256,
        )
        if not callable(getattr(clock, "now", None)):
            raise RuntimeOutboxValidationError("clock 必须实现 now()")
        if not callable(getattr(claim_token_factory, "new_token", None)):
            raise RuntimeOutboxValidationError(
                "claim_token_factory 必须实现 new_token()"
            )
        try:
            policy = database.outbox_policy
        except RuntimeOutboxConfigurationError as exc:
            raise RuntimeOutboxValidationError(
                "database 必须显式绑定 OutboxPolicy"
            ) from exc
        _require_lifecycle_policy(policy)
        self._clock = clock
        self._claim_token_factory = claim_token_factory

    def get(
        self,
        scope_id: str,
        delivery_key: str,
    ) -> OutboxSnapshot | None:
        scope_id = _strict_text(scope_id, "scope_id")
        if (
            not isinstance(delivery_key, str)
            or _DELIVERY_KEY.fullmatch(delivery_key) is None
        ):
            raise RuntimeOutboxValidationError("delivery_key 格式无效")
        _require_lifecycle_policy(self._database.outbox_policy)
        connection = None
        try:
            connection = self._open_read_connection()
            record = self._fetch_record(connection, scope_id, delivery_key)
            return None if record is None else _snapshot(record)
        except RuntimePersistenceError:
            raise
        except sqlite3.DatabaseError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                raise RuntimeDatabaseBusyError(
                    "Outbox read 在 busy_timeout 内无法获取读边界"
                ) from exc
            raise RuntimeStoredDataCorruptionError(
                "Outbox read 无法安全完成"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def claim_eligible_batch(
        self,
        scope_id: str,
    ) -> tuple[OutboxClaim, ...]:
        scope_id = _strict_text(scope_id, "scope_id")
        policy = self._database.outbox_policy
        _require_lifecycle_policy(policy)
        with self._database.unit_of_work() as uow:
            try:
                now = self._sample_clock()
                records = self._load_scope(uow, scope_id)
                selected = self._select_candidates(records, now, policy.batch_limit)
                if not selected:
                    uow.commit()
                    return ()
                for record in selected:
                    if (
                        record.claim_generation >= _SQLITE_SIGNED_INT64_MAX
                        or record.attempt_count >= _SQLITE_SIGNED_INT64_MAX
                    ):
                        raise RuntimeOutboxAttemptExhaustedError(
                            f"Outbox attempt 已耗尽: {record.delivery_key}"
                        )
                claimed_at = canonical_utc_timestamp(now)
                expires = self._checked_add(
                    now,
                    policy.claim_ttl_ms,
                    "claim_expires_at",
                )
                expires_at = canonical_utc_timestamp(expires)
                tokens = self._claim_tokens(selected)
                claims = []
                for record, token in zip(selected, tokens):
                    generation = record.claim_generation + 1
                    attempt_count = record.attempt_count + 1
                    result = uow._execute_managed(
                        """UPDATE runtime_outbox
                           SET state = 'CLAIMED', updated_at = ?,
                               claim_generation = ?, attempt_count = ?,
                               available_at = NULL, claim_token = ?,
                               publisher_id = ?, claim_expires_at = ?,
                               last_error_code = NULL
                           WHERE scope_id = ? AND delivery_key = ?
                             AND state IS ? AND updated_at IS ?
                             AND claim_generation IS ? AND attempt_count IS ?
                             AND available_at IS ? AND claim_token IS ?
                             AND publisher_id IS ? AND claim_expires_at IS ?
                             AND last_error_code IS ? AND suppress_reason IS ?
                             AND published_at IS ? AND receipt_id IS ?""",
                        (
                            claimed_at,
                            generation,
                            attempt_count,
                            token,
                            self._publisher_id,
                            expires_at,
                            record.scope_id,
                            record.delivery_key,
                            record.state,
                            record.updated_at,
                            record.claim_generation,
                            record.attempt_count,
                            record.available_at,
                            record.claim_token,
                            record.publisher_id,
                            record.claim_expires_at,
                            record.last_error_code,
                            record.suppress_reason,
                            record.published_at,
                            record.receipt_id,
                        ),
                    )
                    if result.rowcount != 1:
                        raise RuntimeStoredDataCorruptionError(
                            f"Outbox claim CAS 失配: {record.delivery_key}"
                        )
                    uow._emit_fault(
                        RuntimePersistenceFaultPoint.OUTBOX_AFTER_CLAIM_UPDATE
                    )
                    ownership = OutboxClaimOwnership(
                        scope_id=record.scope_id,
                        delivery_key=record.delivery_key,
                        claim_generation=generation,
                        claim_token=token,
                        publisher_id=self._publisher_id,
                    )
                    claims.append(OutboxClaim(
                        ownership=ownership,
                        source_event_id=record.source_event_id,
                        destination=record.destination,
                        event_digest=record.event_digest,
                        attempt_count=attempt_count,
                        claimed_at=claimed_at,
                        claim_expires_at=expires_at,
                        policy_version=record.policy_version,
                        policy_digest=record.policy_digest,
                    ))
                uow.commit()
                return tuple(claims)
            except RuntimePersistenceError:
                uow._abort_managed_operation()
                raise
            except sqlite3.DatabaseError as exc:
                uow._abort_managed_operation()
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    raise RuntimeDatabaseBusyError(
                        "Outbox claim 在 busy_timeout 内无法完成"
                    ) from exc
                raise RuntimeStoredDataCorruptionError(
                    "Outbox claim SQLite 操作失败"
                ) from exc
            except BaseException:
                uow._abort_managed_operation()
                raise

    def nack(
        self,
        ownership: OutboxClaimOwnership,
        error_code: OutboxNackErrorCode,
    ) -> OutboxNackResult:
        if not isinstance(ownership, OutboxClaimOwnership):
            raise RuntimeOutboxValidationError(
                "ownership 必须是 OutboxClaimOwnership"
            )
        if not isinstance(error_code, OutboxNackErrorCode):
            raise RuntimeOutboxValidationError(
                "error_code 必须是 OutboxNackErrorCode"
            )
        if ownership.publisher_id != self._publisher_id:
            raise RuntimeOutboxOwnershipLostError(
                "当前 Store 不持有该 Outbox publisher ownership"
            )
        policy = self._database.outbox_policy
        _require_lifecycle_policy(policy)
        with self._database.unit_of_work() as uow:
            try:
                now = self._sample_clock()
                record = self._fetch_record(
                    uow,
                    ownership.scope_id,
                    ownership.delivery_key,
                )
                if record is None:
                    raise RuntimeOutboxOwnershipLostError(
                        "当前 Outbox ownership 不存在"
                    )
                aggregate = record.event.aggregate_ref
                aggregate_records = self._load_aggregate(
                    uow,
                    record.scope_id,
                    aggregate.entity_type,
                    aggregate.entity_id,
                )
                validate_outbox_aggregate_history(aggregate_records)
                expected_owner = (
                    record.scope_id,
                    record.delivery_key,
                    record.claim_generation,
                    record.claim_token,
                    record.publisher_id,
                )
                supplied_owner = (
                    ownership.scope_id,
                    ownership.delivery_key,
                    ownership.claim_generation,
                    ownership.claim_token,
                    ownership.publisher_id,
                )
                if record.state != "CLAIMED" or expected_owner != supplied_owner:
                    raise RuntimeOutboxOwnershipLostError(
                        "当前 Outbox ownership 已失效"
                    )
                if now < record.updated_instant:
                    raise RuntimeOutboxClockError(
                        f"Outbox Clock 回拨: {record.delivery_key}"
                    )
                if (
                    record.claim_expires_instant is None
                    or now >= record.claim_expires_instant
                ):
                    raise RuntimeOutboxOwnershipLostError(
                        "当前 Outbox ownership 已过期"
                    )
                delay_index = min(
                    record.attempt_count - 1,
                    len(policy.retry_delays_ms) - 1,
                )
                available = self._checked_add(
                    now,
                    policy.retry_delays_ms[delay_index],
                    "available_at",
                )
                failed_at = canonical_utc_timestamp(now)
                available_at = canonical_utc_timestamp(available)
                result = uow._execute_managed(
                    """UPDATE runtime_outbox
                       SET state = 'PENDING', updated_at = ?, available_at = ?,
                           claim_token = NULL, publisher_id = NULL,
                           claim_expires_at = NULL, last_error_code = ?
                       WHERE scope_id = ? AND delivery_key = ?
                         AND state = 'CLAIMED' AND updated_at = ?
                         AND claim_generation = ? AND attempt_count = ?
                         AND available_at IS NULL AND claim_token = ?
                         AND publisher_id = ? AND claim_expires_at = ?
                         AND last_error_code IS NULL AND suppress_reason IS NULL
                         AND published_at IS NULL AND receipt_id IS NULL""",
                    (
                        failed_at,
                        available_at,
                        error_code.value,
                        record.scope_id,
                        record.delivery_key,
                        record.updated_at,
                        record.claim_generation,
                        record.attempt_count,
                        record.claim_token,
                        record.publisher_id,
                        record.claim_expires_at,
                    ),
                )
                if result.rowcount != 1:
                    raise RuntimeStoredDataCorruptionError(
                        f"Outbox NACK CAS 失配: {record.delivery_key}"
                    )
                uow._emit_fault(
                    RuntimePersistenceFaultPoint.OUTBOX_AFTER_NACK_UPDATE
                )
                nack_result = OutboxNackResult(
                    scope_id=record.scope_id,
                    delivery_key=record.delivery_key,
                    claim_generation=record.claim_generation,
                    attempt_count=record.attempt_count,
                    error_code=error_code,
                    failed_at=failed_at,
                    available_at=available_at,
                )
                uow.commit()
                return nack_result
            except RuntimePersistenceError:
                uow._abort_managed_operation()
                raise
            except sqlite3.DatabaseError as exc:
                uow._abort_managed_operation()
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    raise RuntimeDatabaseBusyError(
                        "Outbox NACK 在 busy_timeout 内无法完成"
                    ) from exc
                raise RuntimeStoredDataCorruptionError(
                    "Outbox NACK SQLite 操作失败"
                ) from exc
            except BaseException:
                uow._abort_managed_operation()
                raise

    def _open_read_connection(self) -> sqlite3.Connection:
        connection = self._database._connect()
        try:
            self._database._assert_wal(connection)
            state = self._database._inspect_schema(connection)
            if state is None:
                raise RuntimeSchemaCorruptionError(
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

    def _sample_clock(self) -> datetime:
        try:
            instant = self._clock.now()
            if not isinstance(instant, datetime):
                raise TypeError("Clock did not return datetime")
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("Clock returned naive datetime")
            return instant.astimezone(timezone.utc)
        except RuntimeOutboxClockError:
            raise
        except (Exception,) as exc:
            raise RuntimeOutboxClockError(
                "Outbox Clock 必须返回合法 aware datetime"
            ) from exc

    @staticmethod
    def _checked_add(
        instant: datetime,
        milliseconds: int,
        field_name: str,
    ) -> datetime:
        try:
            return instant + timedelta(milliseconds=milliseconds)
        except (OverflowError, TypeError, ValueError) as exc:
            raise RuntimeOutboxClockError(
                f"Outbox {field_name} 时间计算溢出"
            ) from exc

    def _new_token(self) -> str:
        try:
            token = self._claim_token_factory.new_token()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            raise RuntimeOutboxTokenFactoryError(
                "claim token factory 调用失败"
            ) from exc
        if not isinstance(token, str) or _CLAIM_TOKEN.fullmatch(token) is None:
            raise RuntimeOutboxTokenFactoryError(
                "claim token factory 返回格式无效"
            )
        return token

    def _claim_tokens(
        self,
        records: tuple[DecodedOutboxRow, ...],
    ) -> tuple[str, ...]:
        tokens = []
        seen = set()
        for record in records:
            token = self._new_token()
            if token in seen or (
                record.state == "CLAIMED" and token == record.claim_token
            ):
                raise RuntimeOutboxTokenFactoryError(
                    "claim token 必须在 batch 内唯一且不可复用旧 owner token"
                )
            seen.add(token)
            tokens.append(token)
        return tuple(tokens)

    def _fetch_record(
        self,
        reader,
        scope_id: str,
        delivery_key: str,
    ) -> DecodedOutboxRow | None:
        row = reader.execute(
            f"""SELECT {_EVENT_PROJECTION}, {_OUTBOX_PROJECTION}
                FROM runtime_outbox AS o
                LEFT JOIN runtime_events AS e
                  ON e.event_id = o.source_event_id
                 AND e.scope_id = o.scope_id
                WHERE o.scope_id = ? AND o.delivery_key = ?""",
            (scope_id, delivery_key),
        ).fetchone()
        if row is None:
            return None
        values = tuple(row)
        if values[0] is None:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox 缺少 RuntimeEvent: {delivery_key}"
            )
        event_row = values[:17]
        outbox_row = values[17:]
        event = decode_runtime_event_row(event_row)
        return decode_outbox_row(
            outbox_row,
            event=event,
            event_digest=event_row[14],
            policy=self._database.outbox_policy,
        )

    def _load_scope(
        self,
        reader,
        scope_id: str,
    ) -> tuple[DecodedOutboxRow, ...]:
        rows = reader.execute(
            f"""SELECT {_EVENT_PROJECTION}, {_OUTBOX_PROJECTION}
                FROM runtime_events AS e
                LEFT JOIN runtime_outbox AS o
                  ON o.source_event_id = e.event_id
                 AND o.scope_id = e.scope_id
                WHERE e.scope_id = ?
                ORDER BY e.aggregate_type, e.aggregate_id,
                         e.sequence_no, e.event_id""",
            (scope_id,),
        ).fetchall()
        records = []
        for row in rows:
            values = tuple(row)
            event_row = values[:17]
            outbox_row = values[17:]
            event = decode_runtime_event_row(event_row)
            if not outbox_row or outbox_row[0] is None:
                raise RuntimeStoredDataCorruptionError(
                    f"RuntimeEvent 缺少 Outbox intent: {event.event_id}"
                )
            records.append(decode_outbox_row(
                outbox_row,
                event=event,
                event_digest=event_row[14],
                policy=self._database.outbox_policy,
            ))
        orphan = reader.execute(
            """SELECT o.delivery_key
               FROM runtime_outbox AS o
               LEFT JOIN runtime_events AS e
                 ON e.event_id = o.source_event_id
                AND e.scope_id = o.scope_id
               WHERE o.scope_id = ? AND e.event_id IS NULL
               LIMIT 1""",
            (scope_id,),
        ).fetchone()
        if orphan is not None:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox 缺少 RuntimeEvent: {orphan[0]}"
            )
        return tuple(records)

    def _load_aggregate(
        self,
        reader,
        scope_id: str,
        aggregate_type: str,
        aggregate_id: str,
    ) -> tuple[DecodedOutboxRow, ...]:
        rows = reader.execute(
            f"""SELECT {_EVENT_PROJECTION}, {_OUTBOX_PROJECTION}
                FROM runtime_events AS e
                LEFT JOIN runtime_outbox AS o
                  ON o.source_event_id = e.event_id
                 AND o.scope_id = e.scope_id
                WHERE e.scope_id = ?
                  AND e.aggregate_type = ?
                  AND e.aggregate_id = ?
                ORDER BY e.sequence_no, e.event_id""",
            (scope_id, aggregate_type, aggregate_id),
        ).fetchall()
        records = []
        for row in rows:
            values = tuple(row)
            event_row = values[:17]
            outbox_row = values[17:]
            event = decode_runtime_event_row(event_row)
            if not outbox_row or outbox_row[0] is None:
                raise RuntimeStoredDataCorruptionError(
                    f"RuntimeEvent 缺少 Outbox intent: {event.event_id}"
                )
            records.append(decode_outbox_row(
                outbox_row,
                event=event,
                event_digest=event_row[14],
                policy=self._database.outbox_policy,
            ))
        if not records:
            raise RuntimeStoredDataCorruptionError(
                "Outbox target aggregate 历史缺失"
            )
        return tuple(records)

    @staticmethod
    def _select_candidates(
        records: tuple[DecodedOutboxRow, ...],
        now: datetime,
        batch_limit: int,
    ) -> tuple[DecodedOutboxRow, ...]:
        validate_outbox_aggregate_history(records)
        grouped = {}
        for record in records:
            key = (
                record.event.aggregate_ref.entity_type,
                record.event.aggregate_ref.entity_id,
            )
            grouped.setdefault(key, []).append(record)
        candidates = []
        for group in grouped.values():
            group.sort(key=lambda item: item.event.sequence_no)
            head = None
            saw_unverified_published = False
            for record in group:
                if record.state in _TERMINAL_STATES:
                    if record.state == "PUBLISHED":
                        saw_unverified_published = True
                    continue
                if head is None:
                    if saw_unverified_published:
                        # Receipt projections are not owned by 3B-1, so a
                        # PUBLISHED predecessor cannot be trusted for skipping.
                        raise RuntimeStoredDataCorruptionError(
                            "3B-1 不能验证 PUBLISHED predecessor"
                        )
                    head = record
            if head is None:
                continue
            if head.claim_generation > 0 and now < head.updated_instant:
                # A lifecycle-derived head is the claim target even while its
                # retry/lease boundary still blocks eligibility.  Checking
                # only rows selected below would silently hide clock rollback.
                raise RuntimeOutboxClockError(
                    f"Outbox Clock 回拨: {head.delivery_key}"
                )
            if head.state == "PENDING":
                if head.available_instant is None:
                    raise RuntimeStoredDataCorruptionError(
                        "PENDING available_at 缺失"
                    )
                if now >= head.available_instant:
                    candidates.append((head.available_instant, head))
            elif head.state == "CLAIMED":
                if head.claim_expires_instant is None:
                    raise RuntimeStoredDataCorruptionError(
                        "CLAIMED claim_expires_at 缺失"
                    )
                if now >= head.claim_expires_instant:
                    candidates.append((head.claim_expires_instant, head))
        candidates.sort(key=lambda item: (
            item[0],
            item[1].created_instant,
            item[1].delivery_key,
        ))
        return tuple(item[1] for item in candidates[:batch_limit])
