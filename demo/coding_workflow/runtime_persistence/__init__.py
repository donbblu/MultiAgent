"""SQLite persistence primitives for the generic Multi-Agent Runtime kernel.

The persistence package is intentionally separate from ``runtime_domain``:
domain values remain storage-neutral, while this package owns SQLite schema
versioning and transaction boundaries.  The legacy ``runtime_sqlite`` module
continues to store compatibility snapshots and is not an authoritative Store.
"""

from .sqlite import (
    RUNTIME_DB_COMPONENT,
    RUNTIME_DB_SCHEMA_VERSION,
    RuntimeCommitError,
    RuntimeDatabaseBusyError,
    RuntimeDatabaseIntegrityError,
    RuntimePersistenceError,
    RuntimePersistenceFaultPoint,
    RuntimeRollbackError,
    RuntimeSchemaCorruptionError,
    RuntimeSchemaError,
    RuntimeSchemaTooNewError,
    RuntimeSQLiteConfig,
    RuntimeSQLiteConfigurationError,
    RuntimeSQLResult,
    RuntimeTransactionError,
    RuntimeUnitOfWork,
    RuntimeUnitOfWorkState,
    RuntimeUnitOfWorkStateError,
    SQLiteRuntimeDatabase,
)

__all__ = [
    "RUNTIME_DB_COMPONENT",
    "RUNTIME_DB_SCHEMA_VERSION",
    "RuntimePersistenceError",
    "RuntimeSQLiteConfigurationError",
    "RuntimeSchemaError",
    "RuntimeSchemaTooNewError",
    "RuntimeSchemaCorruptionError",
    "RuntimeDatabaseIntegrityError",
    "RuntimeTransactionError",
    "RuntimeDatabaseBusyError",
    "RuntimeUnitOfWorkStateError",
    "RuntimeCommitError",
    "RuntimeRollbackError",
    "RuntimePersistenceFaultPoint",
    "RuntimeUnitOfWorkState",
    "RuntimeSQLiteConfig",
    "RuntimeSQLResult",
    "SQLiteRuntimeDatabase",
    "RuntimeUnitOfWork",
]
