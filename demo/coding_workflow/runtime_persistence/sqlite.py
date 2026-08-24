from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable


RUNTIME_DB_COMPONENT = "runtime_kernel"
RUNTIME_DB_SCHEMA_VERSION = 1
_MIGRATION_V1_NAME = "runtime_kernel_base_v1"


class RuntimePersistenceError(RuntimeError):
    """Base error for the Runtime kernel persistence boundary."""


class RuntimeSQLiteConfigurationError(RuntimePersistenceError):
    """SQLite cannot satisfy the frozen local persistence configuration."""


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


class RuntimePersistenceFaultPoint(str, Enum):
    MIGRATION_BEFORE_COMMIT = "migration_before_commit"
    UOW_AFTER_BEGIN = "uow_after_begin"
    UOW_BEFORE_COMMIT = "uow_before_commit"


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
        fault_hook: FaultHook | None = None,
    ) -> None:
        if not isinstance(config, RuntimeSQLiteConfig):
            raise RuntimeSQLiteConfigurationError(
                "config 必须是 RuntimeSQLiteConfig"
            )
        self.config = config
        self._fault_hook = fault_hook

    def initialize(self) -> None:
        self.config.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            initial_state = self._inspect_schema(connection)
            if initial_state is not None:
                self._validate_schema(initial_state)
                self._ensure_wal(connection)
                return

            self._ensure_wal(connection)
            connection.execute("BEGIN EXCLUSIVE")
            try:
                locked_state = self._inspect_schema(connection)
                if locked_state is None:
                    self._install_v1(connection)
                else:
                    self._validate_schema(locked_state)
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
        connection = self._connect()
        try:
            state = self._inspect_schema(connection)
            if state is None:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel schema 尚未初始化"
                )
            self._validate_schema(state)
            return state.version
        finally:
            connection.close()

    def verify_integrity(self) -> None:
        connection = self._connect()
        try:
            state = self._inspect_schema(connection)
            if state is None:
                raise RuntimeSchemaCorruptionError(
                    "runtime_kernel schema 尚未初始化"
                )
            self._validate_schema(state)
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
            )
            if foreign_key_rows:
                raise RuntimeDatabaseIntegrityError(
                    f"SQLite foreign_key_check 发现 {len(foreign_key_rows)} 个问题"
                )
        finally:
            connection.close()

    def unit_of_work(self) -> "RuntimeUnitOfWork":
        return RuntimeUnitOfWork(self, fault_hook=self._fault_hook)

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

    def _inspect_schema(self, connection: sqlite3.Connection) -> _SchemaState | None:
        has_metadata = self._table_exists(connection, "runtime_schema_metadata")
        has_migrations = self._table_exists(connection, "runtime_schema_migrations")
        if not has_metadata and not has_migrations:
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
    def _validate_schema(state: _SchemaState) -> None:
        if state.version > RUNTIME_DB_SCHEMA_VERSION:
            raise RuntimeSchemaTooNewError(
                "Runtime SQLite schema 版本过新: "
                f"found={state.version}, supported={RUNTIME_DB_SCHEMA_VERSION}"
            )
        if state.version < 1:
            raise RuntimeSchemaCorruptionError(
                f"Runtime SQLite schema 版本无效: {state.version}"
            )
        expected = ((1, _MIGRATION_V1_NAME, _MIGRATION_V1_CHECKSUM),)
        if state.migrations != expected:
            details = "migration ledger gap/name/checksum mismatch"
            raise RuntimeSchemaCorruptionError(
                f"runtime_kernel {details}: expected={expected!r}, "
                f"actual={state.migrations!r}"
            )
        if state.version != RUNTIME_DB_SCHEMA_VERSION:
            raise RuntimeSchemaCorruptionError(
                "runtime_kernel metadata 与支持版本不一致"
            )

    def _install_v1(self, connection: sqlite3.Connection) -> None:
        for statement in _MIGRATION_V1_DDL:
            connection.execute(statement)
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO runtime_schema_metadata(
                component, schema_version, initialized_at, updated_at
            ) VALUES (?, ?, ?, ?)""",
            (RUNTIME_DB_COMPONENT, RUNTIME_DB_SCHEMA_VERSION, now, now),
        )
        connection.execute(
            """INSERT INTO runtime_schema_migrations(
                component, schema_version, migration_name,
                migration_checksum, applied_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                RUNTIME_DB_COMPONENT,
                RUNTIME_DB_SCHEMA_VERSION,
                _MIGRATION_V1_NAME,
                _MIGRATION_V1_CHECKSUM,
                now,
            ),
        )
        self._emit_fault(RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT)

    @staticmethod
    def _ensure_wal(connection: sqlite3.Connection) -> None:
        mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
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
            self._database._validate_schema(state)
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
