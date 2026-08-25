from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from typing import Callable, Iterable, Mapping


RUNTIME_DB_COMPONENT = "runtime_kernel"
RUNTIME_DB_SCHEMA_VERSION = 3
_MIGRATION_V1_NAME = "runtime_kernel_base_v1"
_MIGRATION_V2_NAME = "runtime_thread_event_v2"
_MIGRATION_V3_NAME = "runtime_event_outbox_v3"
_OUTBOX_DESTINATION = "core:runtime_events"
_SQLITE_SIGNED_INT64_MAX = (1 << 63) - 1


class RuntimePersistenceError(RuntimeError):
    """Base error for the Runtime kernel persistence boundary."""


class RuntimeSQLiteConfigurationError(RuntimePersistenceError):
    """SQLite cannot satisfy the frozen local persistence configuration."""


class RuntimeOutboxConfigurationError(RuntimeSQLiteConfigurationError):
    """The database has no valid, matching explicit Outbox policy."""


class RuntimeSchemaError(RuntimePersistenceError):
    """The Runtime kernel schema cannot be safely initialized or used."""


class RuntimeSchemaTooNewError(RuntimeSchemaError):
    """The database was created by a newer Runtime kernel implementation."""


class RuntimeSchemaCorruptionError(RuntimeSchemaError):
    """Schema metadata and the migration ledger disagree or are incomplete."""


class RuntimeDatabaseIntegrityError(RuntimePersistenceError):
    """SQLite integrity or foreign-key verification failed."""


class RuntimeTransactionError(RuntimePersistenceError):
    """A RuntimeUnitOfWork transaction failed."""


class RuntimeDatabaseBusyError(RuntimeTransactionError):
    """SQLite could not acquire the write boundary within busy_timeout."""


class RuntimeUnitOfWorkStateError(RuntimeTransactionError):
    """A UnitOfWork operation is invalid in the current lifecycle state."""


class RuntimeCommitError(RuntimeTransactionError):
    """SQLite rejected commit; the transaction was not reported successful."""


class RuntimeRollbackError(RuntimeTransactionError):
    """SQLite rejected rollback; the UnitOfWork is failed and closed."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _strict_nonempty_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RuntimeOutboxConfigurationError(
            f"{field_name} 必须是首尾无空白的非空文本"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeOutboxConfigurationError(
            f"{field_name} 必须是有效 UTF-8 文本"
        ) from exc
    return value


@dataclass(frozen=True)
class OutboxPolicy:
    """Immutable database-bound policy for the v1 Runtime Event Outbox."""

    policy_version: str
    destination: str
    expected_sink_id: str
    claim_ttl_ms: int
    batch_limit: int
    retry_delays_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        _strict_nonempty_text(self.policy_version, "policy_version")
        destination = _strict_nonempty_text(self.destination, "destination")
        _strict_nonempty_text(self.expected_sink_id, "expected_sink_id")
        if destination != _OUTBOX_DESTINATION:
            raise RuntimeOutboxConfigurationError(
                f"destination 必须固定为 {_OUTBOX_DESTINATION}"
            )
        for field_name, value in (
            ("claim_ttl_ms", self.claim_ttl_ms),
            ("batch_limit", self.batch_limit),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > _SQLITE_SIGNED_INT64_MAX
            ):
                raise RuntimeOutboxConfigurationError(
                    f"{field_name} 必须是 SQLite int64 范围内的大于 0 整数"
                )
        if not isinstance(self.retry_delays_ms, tuple) or not self.retry_delays_ms:
            raise RuntimeOutboxConfigurationError(
                "retry_delays_ms 必须是非空整数 tuple"
            )
        for delay in self.retry_delays_ms:
            if (
                not isinstance(delay, int)
                or isinstance(delay, bool)
                or delay < 0
                or delay > _SQLITE_SIGNED_INT64_MAX
            ):
                raise RuntimeOutboxConfigurationError(
                    "retry_delays_ms 每一项必须是 SQLite int64 范围内的非负整数"
                )

    def to_dict(self) -> Mapping[str, object]:
        return {
            "schema": "outbox-policy/v1",
            "policy_version": self.policy_version,
            "destination": self.destination,
            "expected_sink_id": self.expected_sink_id,
            "claim_ttl_ms": self.claim_ttl_ms,
            "batch_limit": self.batch_limit,
            "retry_delays_ms": list(self.retry_delays_ms),
        }

    @property
    def policy_digest(self) -> str:
        return _text_digest(_canonical_json(self.to_dict()))


def _outbox_delivery_key(destination: str, event_id: str) -> str:
    return "obx-v1-" + sha256(
        f"{destination}\0{event_id}".encode("utf-8")
    ).hexdigest()


def _outbox_intent_digest(
    *,
    scope_id: str,
    source_event_id: str,
    event_digest: str,
    destination: str,
    delivery_key: str,
    created_at: str,
    policy: OutboxPolicy,
) -> str:
    return _text_digest(_canonical_json({
        "schema": "outbox-intent/v1",
        "scope_id": scope_id,
        "source_event_id": source_event_id,
        "event_digest": event_digest,
        "destination": destination,
        "delivery_key": delivery_key,
        "created_at": created_at,
        "policy_version": policy.policy_version,
        "policy_digest": policy.policy_digest,
    }))


class RuntimePersistenceFaultPoint(str, Enum):
    MIGRATION_BEFORE_COMMIT = "migration_before_commit"
    UOW_AFTER_BEGIN = "uow_after_begin"
    UOW_BEFORE_COMMIT = "uow_before_commit"
    STATE_EVENT_AFTER_STATE_WRITE = "state_event_after_state_write"
    STATE_EVENT_AFTER_EVENT_APPEND = "state_event_after_event_append"
    STATE_EVENT_AFTER_OUTBOX_ENQUEUE = "state_event_after_outbox_enqueue"


class RuntimeUnitOfWorkState(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


FaultHook = Callable[[RuntimePersistenceFaultPoint], None]


_SCHEMA_DDL_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_REINDEX,
    }
)
_UOW_READ_ONLY_PRAGMAS = frozenset(
    {"busy_timeout", "foreign_keys", "journal_mode", "synchronous"}
)
_MANAGED_DATA_TABLES = frozenset(
    {
        "runtime_threads",
        "runtime_events",
        "runtime_outbox_policy",
        "runtime_outbox",
        "runtime_outbox_receipts",
    }
)


@dataclass(frozen=True)
class RuntimeSQLiteConfig:
    path: Path
    busy_timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        parsed_path = Path(self.path)
        if str(parsed_path) == ":memory:":
            raise RuntimeSQLiteConfigurationError(
                "PROD-01B-1 只支持可重开的文件型 SQLite 数据库"
            )
        if (
            not isinstance(self.busy_timeout_ms, int)
            or isinstance(self.busy_timeout_ms, bool)
            or self.busy_timeout_ms <= 0
        ):
            raise RuntimeSQLiteConfigurationError(
                "busy_timeout_ms 必须是大于 0 的整数"
            )
        object.__setattr__(self, "path", parsed_path)


_MIGRATION_V1_DDL = (
    """CREATE TABLE runtime_schema_metadata (
        component TEXT PRIMARY KEY
            CHECK (component = 'runtime_kernel'),
        schema_version INTEGER NOT NULL
            CHECK (typeof(schema_version) = 'integer' AND schema_version >= 1),
        initialized_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_schema_migrations (
        component TEXT NOT NULL
            CHECK (component = 'runtime_kernel'),
        schema_version INTEGER NOT NULL
            CHECK (typeof(schema_version) = 'integer' AND schema_version >= 1),
        migration_name TEXT NOT NULL,
        migration_checksum TEXT NOT NULL
            CHECK (length(migration_checksum) = 64),
        applied_at TEXT NOT NULL,
        PRIMARY KEY (component, schema_version),
        FOREIGN KEY (component)
            REFERENCES runtime_schema_metadata(component)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )""",
)
_MIGRATION_V1_CHECKSUM = sha256(
    ("\n".join(_MIGRATION_V1_DDL) + "\n" + _MIGRATION_V1_NAME).encode("utf-8")
).hexdigest()

_MIGRATION_V2_DDL = (
    """CREATE TABLE runtime_events (
        event_id TEXT PRIMARY KEY,
        scope_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        aggregate_version INTEGER NOT NULL
            CHECK (typeof(aggregate_version) = 'integer' AND aggregate_version >= 1),
        sequence_no INTEGER NOT NULL
            CHECK (typeof(sequence_no) = 'integer' AND sequence_no >= 1),
        event_version INTEGER NOT NULL
            CHECK (typeof(event_version) = 'integer' AND event_version = 1),
        idempotency_key TEXT NOT NULL UNIQUE,
        trace_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        event_json TEXT NOT NULL CHECK (json_valid(event_json)),
        event_digest TEXT NOT NULL CHECK (length(event_digest) = 64),
        result_state_digest TEXT NOT NULL CHECK (length(result_state_digest) = 64),
        mutation_digest TEXT NOT NULL CHECK (length(mutation_digest) = 64),
        UNIQUE (scope_id, aggregate_type, aggregate_id, sequence_no),
        UNIQUE (
            scope_id, aggregate_type, aggregate_id, aggregate_version,
            sequence_no, event_id, result_state_digest
        )
    ) WITHOUT ROWID""",
    """CREATE TABLE runtime_threads (
        scope_id TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT 'core:thread'
            CHECK (entity_type = 'core:thread'),
        thread_id TEXT NOT NULL,
        version INTEGER NOT NULL
            CHECK (typeof(version) = 'integer' AND version >= 1),
        state TEXT NOT NULL CHECK (state IN ('open', 'paused', 'archived')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT NOT NULL,
        thread_json TEXT NOT NULL CHECK (json_valid(thread_json)),
        thread_digest TEXT NOT NULL CHECK (length(thread_digest) = 64),
        last_sequence_no INTEGER NOT NULL
            CHECK (typeof(last_sequence_no) = 'integer' AND last_sequence_no >= 1),
        last_event_id TEXT NOT NULL,
        PRIMARY KEY (scope_id, thread_id),
        FOREIGN KEY (
            scope_id, entity_type, thread_id, version,
            last_sequence_no, last_event_id, thread_digest
        ) REFERENCES runtime_events(
            scope_id, aggregate_type, aggregate_id, aggregate_version,
            sequence_no, event_id, result_state_digest
        ) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE INDEX runtime_events_recorded_idx
        ON runtime_events(scope_id, recorded_at, event_id)""",
    """CREATE TRIGGER runtime_events_deny_update
        BEFORE UPDATE ON runtime_events
        BEGIN
            SELECT RAISE(ABORT, 'runtime_events is append-only');
        END""",
    """CREATE TRIGGER runtime_events_deny_delete
        BEFORE DELETE ON runtime_events
        BEGIN
            SELECT RAISE(ABORT, 'runtime_events is append-only');
        END""",
    """CREATE TRIGGER runtime_events_deny_replace
        BEFORE INSERT ON runtime_events
        WHEN EXISTS (
            SELECT 1 FROM runtime_events
            WHERE event_id = NEW.event_id
               OR idempotency_key = NEW.idempotency_key
               OR (
                    scope_id = NEW.scope_id
                    AND aggregate_type = NEW.aggregate_type
                    AND aggregate_id = NEW.aggregate_id
                    AND sequence_no = NEW.sequence_no
               )
        )
        BEGIN
            SELECT RAISE(ABORT, 'runtime_events append collision');
        END""",
)
_MIGRATION_V2_CHECKSUM = sha256(
    ("\n".join(_MIGRATION_V2_DDL) + "\n" + _MIGRATION_V2_NAME).encode("utf-8")
).hexdigest()

_MIGRATION_V3_DDL = (
    """CREATE UNIQUE INDEX runtime_events_event_scope_uq
        ON runtime_events(event_id, scope_id)""",
    """CREATE TABLE runtime_outbox_policy (
        component TEXT PRIMARY KEY
            CHECK (component = 'runtime_kernel'),
        policy_version TEXT NOT NULL CHECK (length(policy_version) > 0),
        destination TEXT NOT NULL
            CHECK (destination = 'core:runtime_events'),
        expected_sink_id TEXT NOT NULL CHECK (length(expected_sink_id) > 0),
        claim_ttl_ms INTEGER NOT NULL
            CHECK (typeof(claim_ttl_ms) = 'integer' AND claim_ttl_ms > 0),
        batch_limit INTEGER NOT NULL
            CHECK (typeof(batch_limit) = 'integer' AND batch_limit > 0),
        retry_delays_json TEXT NOT NULL CHECK (json_valid(retry_delays_json)),
        policy_json TEXT NOT NULL CHECK (json_valid(policy_json)),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64)
    ) WITHOUT ROWID""",
    """CREATE TABLE runtime_outbox (
        delivery_key TEXT PRIMARY KEY CHECK (length(delivery_key) = 71),
        source_event_id TEXT NOT NULL UNIQUE,
        scope_id TEXT NOT NULL,
        destination TEXT NOT NULL
            CHECK (destination = 'core:runtime_events'),
        event_digest TEXT NOT NULL CHECK (length(event_digest) = 64),
        created_at TEXT NOT NULL,
        intent_digest TEXT NOT NULL CHECK (length(intent_digest) = 64),
        policy_version TEXT NOT NULL CHECK (length(policy_version) > 0),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        state TEXT NOT NULL
            CHECK (state IN ('LEGACY_SUPPRESSED', 'PENDING', 'CLAIMED', 'PUBLISHED')),
        updated_at TEXT NOT NULL,
        claim_generation INTEGER NOT NULL
            CHECK (typeof(claim_generation) = 'integer' AND claim_generation >= 0),
        attempt_count INTEGER NOT NULL
            CHECK (typeof(attempt_count) = 'integer' AND attempt_count >= 0),
        available_at TEXT,
        claim_token TEXT,
        publisher_id TEXT,
        claim_expires_at TEXT,
        last_error_code TEXT,
        suppress_reason TEXT,
        published_at TEXT,
        receipt_id TEXT UNIQUE,
        FOREIGN KEY (source_event_id, scope_id)
            REFERENCES runtime_events(event_id, scope_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (receipt_id, delivery_key)
            REFERENCES runtime_outbox_receipts(receipt_id, delivery_key)
            DEFERRABLE INITIALLY DEFERRED,
        CHECK (
            (
                state = 'LEGACY_SUPPRESSED'
                AND updated_at = created_at
                AND claim_generation = 0 AND attempt_count = 0
                AND available_at IS NULL AND claim_token IS NULL
                AND publisher_id IS NULL AND claim_expires_at IS NULL
                AND last_error_code IS NULL
                AND suppress_reason = 'pre_outbox_cutover'
                AND published_at IS NULL AND receipt_id IS NULL
            ) OR (
                state = 'PENDING'
                AND available_at IS NOT NULL
                AND claim_token IS NULL AND publisher_id IS NULL
                AND claim_expires_at IS NULL AND suppress_reason IS NULL
                AND published_at IS NULL AND receipt_id IS NULL
                AND (
                    (
                        claim_generation = 0 AND attempt_count = 0
                        AND updated_at = created_at
                        AND available_at = created_at
                        AND last_error_code IS NULL
                    ) OR (
                        claim_generation >= 1
                        AND attempt_count = claim_generation
                        AND last_error_code IS NOT NULL
                    )
                )
            ) OR (
                state = 'CLAIMED'
                AND claim_generation >= 1
                AND attempt_count = claim_generation
                AND available_at IS NULL AND claim_token IS NOT NULL
                AND publisher_id IS NOT NULL AND claim_expires_at IS NOT NULL
                AND last_error_code IS NULL AND suppress_reason IS NULL
                AND published_at IS NULL AND receipt_id IS NULL
            ) OR (
                state = 'PUBLISHED'
                AND claim_generation >= 1
                AND attempt_count = claim_generation
                AND available_at IS NULL AND claim_token IS NULL
                AND publisher_id IS NULL AND claim_expires_at IS NULL
                AND last_error_code IS NULL AND suppress_reason IS NULL
                AND published_at IS NOT NULL AND receipt_id IS NOT NULL
            )
        )
    ) WITHOUT ROWID""",
    """CREATE TABLE runtime_outbox_receipts (
        receipt_id TEXT PRIMARY KEY,
        delivery_key TEXT NOT NULL UNIQUE,
        destination TEXT NOT NULL
            CHECK (destination = 'core:runtime_events'),
        source_event_id TEXT NOT NULL,
        event_digest TEXT NOT NULL CHECK (length(event_digest) = 64),
        claim_generation INTEGER NOT NULL
            CHECK (typeof(claim_generation) = 'integer' AND claim_generation >= 1),
        claim_token TEXT NOT NULL,
        publisher_id TEXT NOT NULL,
        sink_id TEXT NOT NULL,
        ack_id TEXT NOT NULL,
        acked_at TEXT NOT NULL,
        ack_digest TEXT NOT NULL CHECK (length(ack_digest) = 64),
        UNIQUE (receipt_id, delivery_key),
        UNIQUE (sink_id, ack_id),
        FOREIGN KEY (delivery_key)
            REFERENCES runtime_outbox(delivery_key)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    ) WITHOUT ROWID""",
    """CREATE INDEX runtime_outbox_scope_state_idx
        ON runtime_outbox(scope_id, state, available_at, created_at, delivery_key)""",
    """CREATE TRIGGER runtime_outbox_policy_deny_update
        BEFORE UPDATE ON runtime_outbox_policy
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_policy is immutable');
        END""",
    """CREATE TRIGGER runtime_outbox_policy_deny_delete
        BEFORE DELETE ON runtime_outbox_policy
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_policy is immutable');
        END""",
    """CREATE TRIGGER runtime_outbox_policy_deny_replace
        BEFORE INSERT ON runtime_outbox_policy
        WHEN EXISTS (SELECT 1 FROM runtime_outbox_policy)
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_policy already bound');
        END""",
    """CREATE TRIGGER runtime_outbox_deny_identity_update
        BEFORE UPDATE OF delivery_key, source_event_id, scope_id, destination,
            event_digest, created_at, intent_digest, policy_version, policy_digest
        ON runtime_outbox
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox identity is immutable');
        END""",
    """CREATE TRIGGER runtime_outbox_deny_delete
        BEFORE DELETE ON runtime_outbox
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox cannot be deleted');
        END""",
    """CREATE TRIGGER runtime_outbox_deny_replace
        BEFORE INSERT ON runtime_outbox
        WHEN EXISTS (
            SELECT 1 FROM runtime_outbox
            WHERE delivery_key = NEW.delivery_key
               OR source_event_id = NEW.source_event_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox insert collision');
        END""",
    """CREATE TRIGGER runtime_outbox_receipts_deny_update
        BEFORE UPDATE ON runtime_outbox_receipts
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_receipts is append-only');
        END""",
    """CREATE TRIGGER runtime_outbox_receipts_deny_delete
        BEFORE DELETE ON runtime_outbox_receipts
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_receipts is append-only');
        END""",
    """CREATE TRIGGER runtime_outbox_receipts_deny_replace
        BEFORE INSERT ON runtime_outbox_receipts
        WHEN EXISTS (
            SELECT 1 FROM runtime_outbox_receipts
            WHERE receipt_id = NEW.receipt_id
               OR delivery_key = NEW.delivery_key
               OR (sink_id = NEW.sink_id AND ack_id = NEW.ack_id)
        )
        BEGIN
            SELECT RAISE(ABORT, 'runtime_outbox_receipts append collision');
        END""",
)
_MIGRATION_V3_CHECKSUM = sha256(
    ("\n".join(_MIGRATION_V3_DDL) + "\n" + _MIGRATION_V3_NAME).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class _RuntimeMigration:
    version: int
    name: str
    ddl: tuple[str, ...]
    checksum: str


_MIGRATIONS = (
    _RuntimeMigration(1, _MIGRATION_V1_NAME, _MIGRATION_V1_DDL, _MIGRATION_V1_CHECKSUM),
    _RuntimeMigration(2, _MIGRATION_V2_NAME, _MIGRATION_V2_DDL, _MIGRATION_V2_CHECKSUM),
    _RuntimeMigration(3, _MIGRATION_V3_NAME, _MIGRATION_V3_DDL, _MIGRATION_V3_CHECKSUM),
)

_REQUIRED_SCHEMA_OBJECTS = {
    1: (
        ("table", "runtime_schema_metadata", _MIGRATION_V1_DDL[0]),
        ("table", "runtime_schema_migrations", _MIGRATION_V1_DDL[1]),
    ),
    2: (
        ("table", "runtime_events", _MIGRATION_V2_DDL[0]),
        ("table", "runtime_threads", _MIGRATION_V2_DDL[1]),
        ("index", "runtime_events_recorded_idx", _MIGRATION_V2_DDL[2]),
        ("trigger", "runtime_events_deny_update", _MIGRATION_V2_DDL[3]),
        ("trigger", "runtime_events_deny_delete", _MIGRATION_V2_DDL[4]),
        ("trigger", "runtime_events_deny_replace", _MIGRATION_V2_DDL[5]),
    ),
    3: (
        ("index", "runtime_events_event_scope_uq", _MIGRATION_V3_DDL[0]),
        ("table", "runtime_outbox_policy", _MIGRATION_V3_DDL[1]),
        ("table", "runtime_outbox", _MIGRATION_V3_DDL[2]),
        ("table", "runtime_outbox_receipts", _MIGRATION_V3_DDL[3]),
        ("index", "runtime_outbox_scope_state_idx", _MIGRATION_V3_DDL[4]),
        ("trigger", "runtime_outbox_policy_deny_update", _MIGRATION_V3_DDL[5]),
        ("trigger", "runtime_outbox_policy_deny_delete", _MIGRATION_V3_DDL[6]),
        ("trigger", "runtime_outbox_policy_deny_replace", _MIGRATION_V3_DDL[7]),
        ("trigger", "runtime_outbox_deny_identity_update", _MIGRATION_V3_DDL[8]),
        ("trigger", "runtime_outbox_deny_delete", _MIGRATION_V3_DDL[9]),
        ("trigger", "runtime_outbox_deny_replace", _MIGRATION_V3_DDL[10]),
        ("trigger", "runtime_outbox_receipts_deny_update", _MIGRATION_V3_DDL[11]),
        ("trigger", "runtime_outbox_receipts_deny_delete", _MIGRATION_V3_DDL[12]),
        ("trigger", "runtime_outbox_receipts_deny_replace", _MIGRATION_V3_DDL[13]),
    ),
}
_MANAGED_SCHEMA_OBJECT_NAMES = frozenset(
    name
    for objects in _REQUIRED_SCHEMA_OBJECTS.values()
    for _, name, _ in objects
)


@dataclass(frozen=True)
class _SchemaState:
    version: int
    migrations: tuple[tuple[int, str, str], ...]


class RuntimeSQLResult:
    """Restricted cursor result that does not expose its SQLite connection."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    @property
    def description(self):
        return self.__cursor.description

    @property
    def lastrowid(self):
        return self.__cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    def fetchone(self):
        return self.__cursor.fetchone()

    def fetchmany(self, size: int | None = None):
        if size is None:
            return self.__cursor.fetchmany()
        return self.__cursor.fetchmany(size)

    def fetchall(self):
        return self.__cursor.fetchall()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.__cursor)


class SQLiteRuntimeDatabase:
    """Owns the component-scoped SQLite schema and creates explicit UoWs.

    The component metadata deliberately does not use ``PRAGMA user_version``:
    existing deployments may colocate legacy Snapshot, Memory, and Scenario
    tables whose schemas evolve independently.
    """

    def __init__(
        self,
        config: RuntimeSQLiteConfig,
        *,
        outbox_policy: OutboxPolicy | None = None,
        fault_hook: FaultHook | None = None,
    ) -> None:
        if not isinstance(config, RuntimeSQLiteConfig):
            raise RuntimeSQLiteConfigurationError(
                "config 必须是 RuntimeSQLiteConfig"
        )
        if outbox_policy is not None and not isinstance(outbox_policy, OutboxPolicy):
            raise RuntimeOutboxConfigurationError(
                "outbox_policy 必须是 OutboxPolicy"
            )
        self.config = config
        self._outbox_policy = outbox_policy
        self._fault_hook = fault_hook

    @property
    def outbox_policy(self) -> OutboxPolicy:
        return self._require_outbox_policy()

    def initialize(self) -> None:
        self._require_outbox_policy()
        self.config.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            # The preflight read transaction preserves the fail-closed rule for
            # future/corrupt schemas without allowing another initializer to
            # commit between the metadata and ledger reads.
            connection.execute("BEGIN")
            try:
                preflight_state = self._inspect_schema(connection)
                if preflight_state is not None:
                    self._validate_schema(
                        preflight_state,
                        connection=connection,
                        require_current=False,
                    )
                    if preflight_state.version == RUNTIME_DB_SCHEMA_VERSION:
                        self._assert_outbox_policy_binding(connection)
            finally:
                if connection.in_transaction:
                    connection.rollback()

            # journal_mode changes are not reliably covered by SQLite's busy
            # handler.  Retry only transient busy/locked results, bounded by
            # the configured admission deadline.
            self._ensure_wal(connection)
            connection.execute("BEGIN EXCLUSIVE")
            try:
                locked_state = self._inspect_schema(connection)
                if locked_state is None:
                    current_version = 0
                else:
                    self._validate_schema(
                        locked_state,
                        connection=connection,
                        require_current=False,
                    )
                    current_version = locked_state.version
                self._apply_pending_migrations(connection, current_version)
                final_state = self._inspect_schema(connection)
                if final_state is None:
                    raise RuntimeSchemaCorruptionError(
                        "runtime_kernel migration 未生成 schema metadata"
                    )
                self._validate_schema(
                    final_state,
                    connection=connection,
                    require_current=True,
                )
                self._assert_outbox_policy_binding(connection)
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        except (RuntimePersistenceError, KeyboardInterrupt, SystemExit):
            raise
        except sqlite3.DatabaseError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise RuntimeSchemaError("Runtime SQLite schema 初始化失败") from exc
        finally:
            connection.close()

    def schema_version(self) -> int:
        self._require_outbox_policy()
        connection = self._connect()
        try:
            state = self._inspect_schema(connection)
            if state is None:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel schema 尚未初始化"
                )
            self._validate_schema(
                state,
                connection=connection,
                require_current=True,
            )
            self._assert_outbox_policy_binding(connection)
            return state.version
        finally:
            connection.close()

    def verify_integrity(self) -> None:
        self._require_outbox_policy()
        connection = self._connect()
        try:
            state = self._inspect_schema(connection)
            if state is None:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel schema 尚未初始化"
                )
            self._validate_schema(
                state,
                connection=connection,
                require_current=True,
            )
            self._assert_outbox_policy_binding(connection)
            integrity_rows = tuple(
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            )
            if integrity_rows != ("ok",):
                raise RuntimeDatabaseIntegrityError(
                    "SQLite integrity_check 失败: " + "; ".join(integrity_rows)
                )
            foreign_key_rows = tuple(
                connection.execute(
                    "PRAGMA foreign_key_check(runtime_schema_migrations)"
                )
            ) + tuple(
                connection.execute("PRAGMA foreign_key_check(runtime_threads)")
            ) + tuple(
                connection.execute("PRAGMA foreign_key_check(runtime_outbox)")
            ) + tuple(
                connection.execute(
                    "PRAGMA foreign_key_check(runtime_outbox_receipts)"
                )
            )
            if foreign_key_rows:
                raise RuntimeDatabaseIntegrityError(
                    f"SQLite foreign_key_check 发现 {len(foreign_key_rows)} 个问题"
                )
            from .state_event import SQLiteThreadEventStore

            SQLiteThreadEventStore(self)._verify_connection(connection)
        finally:
            connection.close()

    def unit_of_work(self) -> "RuntimeUnitOfWork":
        self._require_outbox_policy()
        return RuntimeUnitOfWork(self, fault_hook=self._fault_hook)

    def _require_outbox_policy(self) -> OutboxPolicy:
        if self._outbox_policy is None:
            raise RuntimeOutboxConfigurationError(
                "SQLiteRuntimeDatabase 必须显式绑定 OutboxPolicy"
            )
        return self._outbox_policy

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.config.path),
            timeout=self.config.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                f"PRAGMA busy_timeout = {self.config.busy_timeout_ms}"
            )
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _connect_for_uow(self) -> sqlite3.Connection:
        return self._connect()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            """SELECT 1 FROM sqlite_schema
               WHERE type = 'table' AND name = ?""",
            (table_name,),
        ).fetchone()
        return row is not None

    def _read_persisted_outbox_policy(
        self,
        connection: sqlite3.Connection,
    ) -> OutboxPolicy:
        try:
            rows = connection.execute(
                """SELECT component, policy_version, destination,
                          expected_sink_id, claim_ttl_ms, batch_limit,
                          retry_delays_json, policy_json, policy_digest
                   FROM runtime_outbox_policy"""
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise RuntimeSchemaCorruptionError(
                "runtime_outbox_policy 无法读取"
            ) from exc
        if len(rows) != 1:
            raise RuntimeSchemaCorruptionError(
                "runtime_outbox_policy 必须恰好包含一行"
            )
        row = rows[0]
        if row[0] != RUNTIME_DB_COMPONENT:
            raise RuntimeSchemaCorruptionError(
                "runtime_outbox_policy component 无效"
            )
        try:
            raw_retry = str(row[6])
            retry_value = json.loads(raw_retry)
            if (
                not isinstance(retry_value, list)
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in retry_value
                )
            ):
                raise ValueError("retry_delays_json shape invalid")
            policy = OutboxPolicy(
                policy_version=row[1],
                destination=row[2],
                expected_sink_id=row[3],
                claim_ttl_ms=row[4],
                batch_limit=row[5],
                retry_delays_ms=tuple(retry_value),
            )
            if raw_retry != _canonical_json(list(policy.retry_delays_ms)):
                raise ValueError("retry_delays_json is not canonical")
            policy_json = str(row[7])
            if policy_json != _canonical_json(policy.to_dict()):
                raise ValueError("policy_json does not match projections")
            if row[8] != policy.policy_digest:
                raise ValueError("policy_digest mismatch")
        except (RuntimeOutboxConfigurationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeSchemaCorruptionError(
                "runtime_outbox_policy 内容损坏"
            ) from exc
        return policy

    def _assert_outbox_policy_binding(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        expected = self._require_outbox_policy()
        actual = self._read_persisted_outbox_policy(connection)
        if actual != expected or actual.policy_digest != expected.policy_digest:
            raise RuntimeOutboxConfigurationError(
                "当前 OutboxPolicy 与数据库持久 Policy 不一致"
            )

    def _persist_outbox_policy(self, connection: sqlite3.Connection) -> None:
        policy = self._require_outbox_policy()
        connection.execute(
            """INSERT INTO runtime_outbox_policy(
                component, policy_version, destination, expected_sink_id,
                claim_ttl_ms, batch_limit, retry_delays_json,
                policy_json, policy_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                RUNTIME_DB_COMPONENT,
                policy.policy_version,
                policy.destination,
                policy.expected_sink_id,
                policy.claim_ttl_ms,
                policy.batch_limit,
                _canonical_json(list(policy.retry_delays_ms)),
                _canonical_json(policy.to_dict()),
                policy.policy_digest,
            ),
        )

    def _backfill_legacy_outbox(self, connection: sqlite3.Connection) -> None:
        policy = self._require_outbox_policy()
        rows = connection.execute(
            """SELECT event_id, scope_id, event_digest, recorded_at
               FROM runtime_events ORDER BY event_id"""
        ).fetchall()
        for event_id, scope_id, event_digest, recorded_at in rows:
            delivery_key = _outbox_delivery_key(policy.destination, event_id)
            intent_digest = _outbox_intent_digest(
                scope_id=scope_id,
                source_event_id=event_id,
                event_digest=event_digest,
                destination=policy.destination,
                delivery_key=delivery_key,
                created_at=recorded_at,
                policy=policy,
            )
            connection.execute(
                """INSERT INTO runtime_outbox(
                    delivery_key, source_event_id, scope_id, destination,
                    event_digest, created_at, intent_digest, policy_version,
                    policy_digest, state, updated_at, claim_generation,
                    attempt_count, available_at, claim_token, publisher_id,
                    claim_expires_at, last_error_code, suppress_reason,
                    published_at, receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'LEGACY_SUPPRESSED', ?,
                          0, 0, NULL, NULL, NULL, NULL, NULL,
                          'pre_outbox_cutover', NULL, NULL)""",
                (
                    delivery_key,
                    event_id,
                    scope_id,
                    policy.destination,
                    event_digest,
                    recorded_at,
                    intent_digest,
                    policy.policy_version,
                    policy.policy_digest,
                    recorded_at,
                ),
            )

    def _inspect_schema(self, connection: sqlite3.Connection) -> _SchemaState | None:
        has_metadata = self._table_exists(connection, "runtime_schema_metadata")
        has_migrations = self._table_exists(connection, "runtime_schema_migrations")
        if not has_metadata and not has_migrations:
            placeholders = ",".join("?" for _ in _MANAGED_SCHEMA_OBJECT_NAMES)
            orphan = connection.execute(
                f"""SELECT type, name, tbl_name FROM sqlite_schema
                    WHERE name IN ({placeholders})
                    ORDER BY type, name LIMIT 1""",
                tuple(sorted(_MANAGED_SCHEMA_OBJECT_NAMES)),
            ).fetchone()
            if orphan is not None:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel metadata/ledger 缺失但受管 schema object "
                    f"已存在: {orphan[0]}:{orphan[1]}"
                )
            return None
        if has_metadata != has_migrations:
            raise RuntimeSchemaCorruptionError(
                "runtime_kernel metadata/ledger 表不完整"
            )
        try:
            metadata_rows = connection.execute(
                """SELECT component, schema_version
                   FROM runtime_schema_metadata"""
            ).fetchall()
            if len(metadata_rows) != 1:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel metadata 必须恰好包含一行"
                )
            component, raw_version = metadata_rows[0]
            if component != RUNTIME_DB_COMPONENT:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel metadata component 无效"
                )
            if not isinstance(raw_version, int) or isinstance(raw_version, bool):
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel schema_version 必须是整数"
                )
            migration_rows = connection.execute(
                """SELECT component, schema_version,
                          migration_name, migration_checksum
                   FROM runtime_schema_migrations
                   ORDER BY component, schema_version"""
            ).fetchall()
        except RuntimePersistenceError:
            raise
        except sqlite3.DatabaseError as exc:
            raise RuntimeSchemaCorruptionError(
                "runtime_kernel metadata/ledger 结构无法读取"
            ) from exc
        parsed_migrations = []
        for component, version, name, checksum in migration_rows:
            if component != RUNTIME_DB_COMPONENT:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel migration component 无效"
                )
            if not isinstance(version, int) or isinstance(version, bool):
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel migration schema_version 必须是整数"
                )
            if not isinstance(name, str) or not isinstance(checksum, str):
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel migration name/checksum 必须是文本"
                )
            parsed_migrations.append((version, name, checksum))
        return _SchemaState(int(raw_version), tuple(parsed_migrations))

    @staticmethod
    def _canonical_sql(statement: str) -> str:
        return " ".join(statement.split()).strip().rstrip(";")

    @classmethod
    def _validate_schema(
        cls,
        state: _SchemaState,
        *,
        connection: sqlite3.Connection,
        require_current: bool,
    ) -> None:
        if state.version > RUNTIME_DB_SCHEMA_VERSION:
            raise RuntimeSchemaTooNewError(
                "Runtime SQLite schema 版本过新: "
                f"found={state.version}, supported={RUNTIME_DB_SCHEMA_VERSION}"
            )
        if state.version < 1:
            raise RuntimeSchemaCorruptionError(
                f"Runtime SQLite schema 版本无效: {state.version}"
            )
        expected = tuple(
            (migration.version, migration.name, migration.checksum)
            for migration in _MIGRATIONS[: state.version]
        )
        if state.migrations != expected:
            details = "migration ledger gap/name/checksum mismatch"
            raise RuntimeSchemaCorruptionError(
                f"runtime_kernel {details}: expected={expected!r}, "
                f"actual={state.migrations!r}"
            )
        for version in range(1, state.version + 1):
            for object_type, name, expected_sql in _REQUIRED_SCHEMA_OBJECTS[version]:
                row = connection.execute(
                    """SELECT sql FROM sqlite_schema
                       WHERE type = ? AND name = ?""",
                    (object_type, name),
                ).fetchone()
                if row is None or row[0] is None:
                    raise RuntimeSchemaCorruptionError(
                        f"runtime_kernel 必需 schema object 缺失: {object_type}:{name}"
                    )
                if cls._canonical_sql(str(row[0])) != cls._canonical_sql(expected_sql):
                    raise RuntimeSchemaCorruptionError(
                        f"runtime_kernel 必需 schema object 漂移: {object_type}:{name}"
                    )
        for version in range(state.version + 1, RUNTIME_DB_SCHEMA_VERSION + 1):
            for _, name, _ in _REQUIRED_SCHEMA_OBJECTS[version]:
                row = connection.execute(
                    """SELECT type, name FROM sqlite_schema
                       WHERE name = ?""",
                    (name,),
                ).fetchone()
                if row is not None:
                    raise RuntimeSchemaCorruptionError(
                        "runtime_kernel 当前版本出现未来保留 schema object: "
                        f"v{version}:{row[0]}:{row[1]}"
                    )
        if require_current and state.version != RUNTIME_DB_SCHEMA_VERSION:
            raise RuntimeSchemaCorruptionError(
                "runtime_kernel schema 尚未迁移到当前版本"
            )

    def _apply_pending_migrations(
        self,
        connection: sqlite3.Connection,
        current_version: int,
    ) -> None:
        pending = _MIGRATIONS[current_version:]
        if not pending:
            return
        for migration in pending:
            if migration.version == 3:
                from .state_event import SQLiteThreadEventStore

                SQLiteThreadEventStore(self)._verify_connection(
                    connection,
                    require_outbox=False,
                )
            for statement in migration.ddl:
                connection.execute(statement)
            if migration.version == 3:
                self._persist_outbox_policy(connection)
                self._backfill_legacy_outbox(connection)
            now = datetime.now(timezone.utc).isoformat()
            if migration.version == 1:
                connection.execute(
                    """INSERT INTO runtime_schema_metadata(
                        component, schema_version, initialized_at, updated_at
                    ) VALUES (?, ?, ?, ?)""",
                    (RUNTIME_DB_COMPONENT, migration.version, now, now),
                )
            else:
                changed = connection.execute(
                    """UPDATE runtime_schema_metadata
                       SET schema_version = ?, updated_at = ?
                       WHERE component = ? AND schema_version = ?""",
                    (
                        migration.version,
                        now,
                        RUNTIME_DB_COMPONENT,
                        migration.version - 1,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeSchemaCorruptionError(
                        "runtime_kernel migration metadata CAS 失败"
                    )
            connection.execute(
                """INSERT INTO runtime_schema_migrations(
                    component, schema_version, migration_name,
                    migration_checksum, applied_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    RUNTIME_DB_COMPONENT,
                    migration.version,
                    migration.name,
                    migration.checksum,
                    now,
                ),
            )
        self._emit_fault(RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT)

    def _ensure_wal(self, connection: sqlite3.Connection) -> None:
        deadline = monotonic() + self.config.busy_timeout_ms / 1_000
        last_busy_error: sqlite3.OperationalError | None = None
        try:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    if last_busy_error is not None:
                        raise last_busy_error
                    raise sqlite3.OperationalError(
                        "WAL bootstrap busy deadline exceeded"
                    )
                attempt_timeout_ms = max(
                    1,
                    min(
                        self.config.busy_timeout_ms,
                        10,
                        int(remaining * 1_000),
                    ),
                )
                connection.execute(
                    f"PRAGMA busy_timeout = {attempt_timeout_ms}"
                )
                try:
                    mode = str(
                        connection.execute(
                            "PRAGMA journal_mode = WAL"
                        ).fetchone()[0]
                    )
                    break
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "locked" not in message and "busy" not in message:
                        raise
                    last_busy_error = exc
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise
                    sleep(min(0.01, remaining))
        finally:
            connection.execute(
                f"PRAGMA busy_timeout = {self.config.busy_timeout_ms}"
            )
        if mode.lower() != "wal":
            raise RuntimeSQLiteConfigurationError(
                f"Runtime SQLite 无法启用 WAL，当前为 {mode}"
            )

    @staticmethod
    def _assert_wal(connection: sqlite3.Connection) -> None:
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        if mode.lower() != "wal":
            raise RuntimeSQLiteConfigurationError(
                f"Runtime SQLite 必须使用 WAL，当前为 {mode}"
            )

    def _emit_fault(self, point: RuntimePersistenceFaultPoint) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)


class RuntimeUnitOfWork:
    """A single explicit SQLite write transaction.

    Clean context exit without ``commit()`` rolls back.  Commit and rollback
    both close the connection so a UnitOfWork can never create an implicit
    second transaction.
    """

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self._database = database
        self._fault_hook = fault_hook
        self._connection: sqlite3.Connection | None = None
        self._owner_thread_id: int | None = None
        self._state = RuntimeUnitOfWorkState.NEW
        self._is_closed = False
        self._allow_internal_transaction_control = False
        self._allow_managed_dml = False
        self._authorization_denial = ""

    @property
    def state(self) -> RuntimeUnitOfWorkState:
        return self._state

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    def __enter__(self) -> "RuntimeUnitOfWork":
        if self._state is not RuntimeUnitOfWorkState.NEW or self._is_closed:
            raise RuntimeUnitOfWorkStateError(
                f"RuntimeUnitOfWork 不能进入，state={self._state.value}"
            )
        connection = self._database._connect_for_uow()
        self._connection = connection
        self._owner_thread_id = threading.get_ident()
        connection.set_authorizer(self._authorize)
        try:
            self._run_transaction_control(
                lambda: connection.execute("BEGIN IMMEDIATE")
            )
        except sqlite3.DatabaseError as exc:
            self._abort_preserving_exception()
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                raise RuntimeDatabaseBusyError(
                    "RuntimeUnitOfWork BEGIN IMMEDIATE 遇到数据库锁"
                ) from exc
            raise RuntimeTransactionError(
                "RuntimeUnitOfWork BEGIN IMMEDIATE 失败"
            ) from exc
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
            self._state = RuntimeUnitOfWorkState.ACTIVE
            self._emit_fault(RuntimePersistenceFaultPoint.UOW_AFTER_BEGIN)
            return self
        except BaseException:
            self._abort_preserving_exception()
            raise

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._state is RuntimeUnitOfWorkState.ACTIVE:
            # A rollback failure is a persistence-boundary failure and must not
            # be hidden.  If the body also failed, Python retains that original
            # exception in the raised rollback error's exception chain.
            self.rollback()
        return False

    def execute(
        self,
        statement: str,
        parameters: Iterable[object] = (),
    ) -> RuntimeSQLResult:
        connection = self._require_active()
        self._authorization_denial = ""
        try:
            cursor = connection.execute(statement, tuple(parameters))
        except sqlite3.DatabaseError as exc:
            self._fail_if_transaction_lost(exc)
            if self._authorization_denial:
                raise RuntimeUnitOfWorkStateError(
                    "RuntimeUnitOfWork 拒绝绕过事务/Schema 边界: "
                    + self._authorization_denial
                ) from exc
            raise
        self._fail_if_transaction_lost()
        return RuntimeSQLResult(cursor)

    def executemany(
        self,
        statement: str,
        parameters: Iterable[Iterable[object]],
    ) -> RuntimeSQLResult:
        connection = self._require_active()
        self._authorization_denial = ""
        try:
            cursor = connection.executemany(
                statement,
                (tuple(item) for item in parameters),
            )
        except sqlite3.DatabaseError as exc:
            self._fail_if_transaction_lost(exc)
            if self._authorization_denial:
                raise RuntimeUnitOfWorkStateError(
                    "RuntimeUnitOfWork 拒绝绕过事务/Schema 边界: "
                    + self._authorization_denial
                ) from exc
            raise
        self._fail_if_transaction_lost()
        return RuntimeSQLResult(cursor)

    def _execute_managed(
        self,
        statement: str,
        parameters: Iterable[object] = (),
    ) -> RuntimeSQLResult:
        """Execute one persistence-owned statement without exposing a connection.

        This is deliberately private.  Public ``execute`` remains unable to
        mutate authoritative Thread/Event tables, while repositories can use
        the same transaction and lifecycle checks.
        """

        connection = self._require_active()
        self._authorization_denial = ""
        self._allow_managed_dml = True
        try:
            cursor = connection.execute(statement, tuple(parameters))
        except sqlite3.DatabaseError as exc:
            self._fail_if_transaction_lost(exc)
            if self._authorization_denial:
                raise RuntimeUnitOfWorkStateError(
                    "RuntimeUnitOfWork 拒绝绕过事务/Schema 边界: "
                    + self._authorization_denial
                ) from exc
            raise
        finally:
            self._allow_managed_dml = False
        self._fail_if_transaction_lost()
        return RuntimeSQLResult(cursor)

    def _abort_managed_operation(self) -> None:
        """Fail closed after a managed state/event mutation was rejected."""

        # A foreign thread is not allowed to touch or close the owner thread's
        # sqlite connection.  Preserve the typed cross-thread rejection and
        # let the owner roll back through the normal UoW lifecycle.
        if self._owner_thread_id != threading.get_ident():
            return
        if self._state is RuntimeUnitOfWorkState.ACTIVE:
            self._rollback_for_abort()

    def commit(self) -> None:
        connection = self._require_active()
        try:
            self._emit_fault(RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT)
        except BaseException:
            self._rollback_for_abort()
            raise
        try:
            self._run_transaction_control(connection.commit)
        except sqlite3.DatabaseError as exc:
            self._rollback_after_commit_failure()
            raise RuntimeCommitError("RuntimeUnitOfWork commit 失败") from exc
        self._state = RuntimeUnitOfWorkState.COMMITTED
        self._close_connection()

    def rollback(self) -> None:
        connection = self._require_active()
        try:
            self._run_transaction_control(connection.rollback)
        except sqlite3.DatabaseError as exc:
            self._state = RuntimeUnitOfWorkState.FAILED
            self._close_connection()
            raise RuntimeRollbackError("RuntimeUnitOfWork rollback 失败") from exc
        self._state = RuntimeUnitOfWorkState.ROLLED_BACK
        self._close_connection()

    def close(self) -> None:
        if self._is_closed:
            return
        if self._state is RuntimeUnitOfWorkState.ACTIVE:
            self.rollback()
            return
        if self._state is RuntimeUnitOfWorkState.NEW:
            self._state = RuntimeUnitOfWorkState.ROLLED_BACK
            self._is_closed = True
            return
        self._close_connection()

    def _require_active(self) -> sqlite3.Connection:
        if (
            self._state is not RuntimeUnitOfWorkState.ACTIVE
            or self._connection is None
            or self._is_closed
        ):
            raise RuntimeUnitOfWorkStateError(
                f"RuntimeUnitOfWork 需要 active，当前 state={self._state.value}"
            )
        if self._owner_thread_id != threading.get_ident():
            raise RuntimeUnitOfWorkStateError(
                "RuntimeUnitOfWork 不能跨线程使用"
            )
        self._fail_if_transaction_lost()
        return self._connection

    def _fail_if_transaction_lost(self, cause: BaseException | None = None) -> None:
        connection = self._connection
        if connection is not None and connection.in_transaction:
            return
        self._state = RuntimeUnitOfWorkState.FAILED
        self._close_connection()
        error = RuntimeTransactionError(
            "RuntimeUnitOfWork 的外层事务已意外终止"
        )
        if cause is None:
            raise error
        raise error from cause

    def _emit_fault(self, point: RuntimePersistenceFaultPoint) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _authorize(self, action, argument1, argument2, database_name, source):
        if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
            if self._allow_internal_transaction_control:
                return sqlite3.SQLITE_OK
            self._authorization_denial = "transaction control SQL"
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            self._authorization_denial = "ATTACH/DETACH"
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = str(argument1 or "").lower()
            if argument2 is not None or pragma_name not in _UOW_READ_ONLY_PRAGMAS:
                self._authorization_denial = f"non-read-only PRAGMA {argument1}"
                return sqlite3.SQLITE_DENY
        if action in _SCHEMA_DDL_ACTIONS:
            self._authorization_denial = "schema DDL"
            return sqlite3.SQLITE_DENY
        if (
            action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
            and argument1 in {"runtime_schema_metadata", "runtime_schema_migrations"}
        ):
            self._authorization_denial = f"managed schema table {argument1}"
            return sqlite3.SQLITE_DENY
        if (
            action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
            and argument1 in _MANAGED_DATA_TABLES
            and not self._allow_managed_dml
        ):
            self._authorization_denial = f"managed data table {argument1}"
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _run_transaction_control(self, operation):
        self._allow_internal_transaction_control = True
        try:
            return operation()
        finally:
            self._allow_internal_transaction_control = False

    def _abort_preserving_exception(self) -> None:
        connection = self._connection
        if connection is not None and connection.in_transaction:
            try:
                self._run_transaction_control(connection.rollback)
                self._state = RuntimeUnitOfWorkState.ROLLED_BACK
            except sqlite3.DatabaseError as exc:
                self._state = RuntimeUnitOfWorkState.FAILED
                self._close_connection()
                raise RuntimeRollbackError(
                    "RuntimeUnitOfWork abort rollback 失败"
                ) from exc
        else:
            self._state = RuntimeUnitOfWorkState.FAILED
        self._close_connection()

    def _rollback_for_abort(self) -> None:
        connection = self._connection
        if connection is None:
            self._state = RuntimeUnitOfWorkState.FAILED
            self._close_connection()
            return
        try:
            self._run_transaction_control(connection.rollback)
            self._state = RuntimeUnitOfWorkState.ROLLED_BACK
        except sqlite3.DatabaseError as exc:
            self._state = RuntimeUnitOfWorkState.FAILED
            self._close_connection()
            raise RuntimeRollbackError(
                "RuntimeUnitOfWork fault rollback 失败"
            ) from exc
        self._close_connection()

    def _rollback_after_commit_failure(self) -> None:
        connection = self._connection
        if connection is not None and connection.in_transaction:
            try:
                self._run_transaction_control(connection.rollback)
            except sqlite3.DatabaseError as exc:
                self._state = RuntimeUnitOfWorkState.FAILED
                self._close_connection()
                raise RuntimeRollbackError(
                    "RuntimeUnitOfWork commit failure rollback 失败"
                ) from exc
        self._state = RuntimeUnitOfWorkState.FAILED
        self._close_connection()

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        self._owner_thread_id = None
        self._is_closed = True
        if connection is not None:
            connection.close()
