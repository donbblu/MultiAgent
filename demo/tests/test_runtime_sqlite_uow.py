from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from hashlib import sha256
from pathlib import Path

from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RUNTIME_DB_COMPONENT,
    RUNTIME_DB_SCHEMA_VERSION,
    RuntimeCommitError,
    RuntimeDatabaseBusyError,
    RuntimePersistenceFaultPoint,
    RuntimeRollbackError,
    RuntimeSchemaCorruptionError,
    RuntimeSchemaTooNewError,
    RuntimeSQLiteConfig,
    RuntimeSQLiteConfigurationError,
    RuntimeTransactionError,
    RuntimeUnitOfWorkState,
    RuntimeUnitOfWorkStateError,
    SQLiteRuntimeDatabase,
)
from coding_workflow.runtime_sqlite import SQLiteRuntimeStore


class InjectedFault(RuntimeError):
    pass


RELEASED_V1_CHECKSUM = "f193d040abb599c178002fe51180e9a7fc59966e8ef4a32cb7338c296154b7d4"
RELEASED_V1_SCHEMA_SQL_SHA256 = (
    "e0bd2f701681d6e51ba5636205f1c44108c0d0260b384862f5c0dd992895e3ce"
)


class RuntimeSQLiteUnitOfWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "nested" / "runtime.sqlite3"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def database(
        self,
        *,
        path: Path | None = None,
        busy_timeout_ms: int = 5_000,
        fault_hook=None,
    ) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path or self.path, busy_timeout_ms=busy_timeout_ms),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/test-v1",
                destination="core:runtime_events",
                expected_sink_id="core:test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000, 5_000, 30_000),
            ),
            fault_hook=fault_hook,
        )

    @staticmethod
    def raw_rows(path: Path, sql: str, parameters=()):
        with sqlite3.connect(str(path)) as connection:
            return connection.execute(sql, parameters).fetchall()

    @staticmethod
    def schema_snapshot(path: Path):
        with sqlite3.connect(str(path)) as connection:
            objects = connection.execute(
                """SELECT type, name, tbl_name, sql
                   FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%'
                   ORDER BY type, name"""
            ).fetchall()
            metadata = connection.execute(
                """SELECT component, schema_version, initialized_at, updated_at
                   FROM runtime_schema_metadata
                   ORDER BY component"""
            ).fetchall()
            migrations = connection.execute(
                """SELECT component, schema_version, migration_name,
                          migration_checksum, applied_at
                   FROM runtime_schema_migrations
                   ORDER BY component, schema_version"""
            ).fetchall()
            canary_exists = connection.execute(
                """SELECT 1 FROM sqlite_schema
                   WHERE type = 'table' AND name = 'probe_parent'"""
            ).fetchone()
            canary = (
                connection.execute(
                    "SELECT id, value FROM probe_parent ORDER BY id"
                ).fetchall()
                if canary_exists
                else []
            )
        return objects, metadata, migrations, canary

    def initialize_probe_schema(self, database: SQLiteRuntimeDatabase) -> None:
        database.initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """CREATE TABLE probe_parent (
                    id TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE probe_child (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    FOREIGN KEY (parent_id) REFERENCES probe_parent(id)
                        DEFERRABLE INITIALLY DEFERRED
                )"""
            )

    def convert_current_fixture_to_released_v1(self) -> None:
        """Build the exact component objects/ledger that existed at v1."""

        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE runtime_product_verifications")
            connection.execute("DROP TABLE runtime_product_artifacts")
            connection.execute("DROP TABLE runtime_product_task_results")
            connection.execute("DROP TABLE runtime_change_applications")
            connection.execute("DROP TABLE runtime_change_application_claims")
            connection.execute("DROP TABLE runtime_change_user_approvals")
            connection.execute("DROP TABLE runtime_change_proposals")
            connection.execute("DROP TABLE runtime_backend_session_recovery_attempts")
            connection.execute("DROP TABLE runtime_backend_session_recovery_decisions")
            connection.execute("DROP TABLE runtime_backend_session_recovery_requests")
            connection.execute("DROP TABLE runtime_backend_session_recoveries")
            connection.execute("DROP TABLE runtime_agent_execution_recovery_contexts")
            connection.execute("DROP TABLE runtime_backend_session_bindings")
            connection.execute("DROP TABLE runtime_agent_execution_results")
            connection.execute("DROP TABLE runtime_agent_execution_states")
            connection.execute("DROP TABLE runtime_role_assignments")
            connection.execute("DROP TABLE runtime_agent_messages")
            connection.execute("DROP TABLE runtime_agent_mailbox_cursors")
            connection.execute("DROP TABLE runtime_agent_private_state")
            connection.execute("DROP TABLE runtime_agent_sessions")
            connection.execute("DROP TABLE runtime_agent_instances")
            connection.execute("DROP TABLE runtime_outbox_receipts")
            connection.execute("DROP TABLE runtime_outbox")
            connection.execute("DROP TABLE runtime_outbox_policy")
            connection.execute("DROP TABLE runtime_threads")
            connection.execute("DROP TABLE runtime_events")
            connection.execute(
                """DELETE FROM runtime_schema_migrations
                   WHERE component = ? AND schema_version >= 2""",
                (RUNTIME_DB_COMPONENT,),
            )
            connection.execute(
                """UPDATE runtime_schema_metadata
                   SET schema_version = 1 WHERE component = ?""",
                (RUNTIME_DB_COMPONENT,),
            )

    def assert_released_v1_fixture(self) -> None:
        rows = self.raw_rows(
            self.path,
            """SELECT type, name, sql FROM sqlite_schema
               WHERE name IN ('runtime_schema_metadata',
                              'runtime_schema_migrations')
               ORDER BY type, name""",
        )
        payload = "\n".join(
            "|".join((object_type, name, " ".join(sql.split())))
            for object_type, name, sql in rows
        )
        self.assertEqual(
            sha256(payload.encode("utf-8")).hexdigest(),
            RELEASED_V1_SCHEMA_SQL_SHA256,
        )
        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT migration_name, migration_checksum
                   FROM runtime_schema_migrations WHERE schema_version = 1""",
            ),
            [("runtime_kernel_base_v1", RELEASED_V1_CHECKSUM)],
        )

    def insert_probe_pair(self, uow, suffix: str = "1") -> None:
        uow.execute(
            "INSERT INTO probe_parent(id, value) VALUES (?, ?)",
            (f"parent-{suffix}", f"parent-value-{suffix}"),
        )
        uow.execute(
            "INSERT INTO probe_child(id, parent_id, value) VALUES (?, ?, ?)",
            (f"child-{suffix}", f"parent-{suffix}", f"child-value-{suffix}"),
        )

    def probe_counts(self):
        with sqlite3.connect(str(self.path)) as connection:
            parent_count = connection.execute(
                "SELECT COUNT(*) FROM probe_parent"
            ).fetchone()[0]
            child_count = connection.execute(
                "SELECT COUNT(*) FROM probe_child"
            ).fetchone()[0]
        return parent_count, child_count

    def test_fresh_database_applies_current_schema_atomically(self) -> None:
        database = self.database()
        database.initialize()

        self.assertEqual(database.schema_version(), RUNTIME_DB_SCHEMA_VERSION)
        self.assertEqual(RUNTIME_DB_SCHEMA_VERSION, 12)
        self.assertEqual(RUNTIME_DB_COMPONENT, "runtime_kernel")
        objects = self.raw_rows(
            self.path,
            """SELECT type, name FROM sqlite_schema
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name""",
        )
        self.assertEqual(
            objects,
            [
                ("index", "runtime_agent_instances_thread_idx"),
                ("index", "runtime_agent_messages_pending_idx"),
                ("index", "runtime_events_event_scope_uq"),
                ("index", "runtime_events_recorded_idx"),
                ("index", "runtime_outbox_scope_state_idx"),
                ("index", "runtime_role_assignments_thread_idx"),
                ("table", "runtime_agent_execution_recovery_contexts"),
                ("table", "runtime_agent_execution_results"),
                ("table", "runtime_agent_execution_states"),
                ("table", "runtime_agent_instances"),
                ("table", "runtime_agent_mailbox_cursors"),
                ("table", "runtime_agent_messages"),
                ("table", "runtime_agent_private_state"),
                ("table", "runtime_agent_sessions"),
                ("table", "runtime_backend_session_bindings"),
                ("table", "runtime_backend_session_recoveries"),
                ("table", "runtime_backend_session_recovery_attempts"),
                ("table", "runtime_backend_session_recovery_decisions"),
                ("table", "runtime_backend_session_recovery_requests"),
                ("table", "runtime_change_application_claims"),
                ("table", "runtime_change_applications"),
                ("table", "runtime_change_proposals"),
                ("table", "runtime_change_user_approvals"),
                ("table", "runtime_events"),
                ("table", "runtime_outbox"),
                ("table", "runtime_outbox_policy"),
                ("table", "runtime_outbox_receipts"),
                ("table", "runtime_product_artifacts"),
                ("table", "runtime_product_task_results"),
                ("table", "runtime_product_verifications"),
                ("table", "runtime_role_assignments"),
                ("table", "runtime_schema_metadata"),
                ("table", "runtime_schema_migrations"),
                ("table", "runtime_threads"),
                ("trigger", "runtime_agent_execution_recovery_contexts_deny_delete"),
                ("trigger", "runtime_agent_execution_recovery_contexts_deny_replace"),
                ("trigger", "runtime_agent_execution_recovery_contexts_deny_update"),
                ("trigger", "runtime_agent_execution_results_deny_delete"),
                ("trigger", "runtime_agent_execution_results_deny_replace"),
                ("trigger", "runtime_agent_execution_results_deny_update"),
                ("trigger", "runtime_agent_execution_states_deny_delete"),
                ("trigger", "runtime_agent_execution_states_deny_replace"),
                ("trigger", "runtime_agent_execution_states_deny_update"),
                ("trigger", "runtime_backend_session_bindings_deny_delete"),
                ("trigger", "runtime_backend_session_bindings_deny_replace"),
                ("trigger", "runtime_backend_session_bindings_deny_update"),
                ("trigger", "runtime_backend_session_recoveries_deny_delete"),
                ("trigger", "runtime_backend_session_recoveries_deny_replace"),
                ("trigger", "runtime_backend_session_recoveries_deny_update"),
                ("trigger", "runtime_backend_session_recovery_attempts_deny_delete"),
                ("trigger", "runtime_backend_session_recovery_attempts_deny_replace"),
                ("trigger", "runtime_backend_session_recovery_attempts_deny_update"),
                ("trigger", "runtime_backend_session_recovery_decisions_deny_delete"),
                ("trigger", "runtime_backend_session_recovery_decisions_deny_replace"),
                ("trigger", "runtime_backend_session_recovery_decisions_deny_update"),
                ("trigger", "runtime_backend_session_recovery_requests_deny_delete"),
                ("trigger", "runtime_backend_session_recovery_requests_deny_replace"),
                ("trigger", "runtime_backend_session_recovery_requests_deny_update"),
                ("trigger", "runtime_change_application_claims_deny_delete"),
                ("trigger", "runtime_change_application_claims_deny_replace"),
                ("trigger", "runtime_change_application_claims_deny_update"),
                ("trigger", "runtime_change_applications_deny_delete"),
                ("trigger", "runtime_change_applications_deny_replace"),
                ("trigger", "runtime_change_applications_deny_update"),
                ("trigger", "runtime_change_proposals_deny_delete"),
                ("trigger", "runtime_change_proposals_deny_replace"),
                ("trigger", "runtime_change_proposals_deny_update"),
                ("trigger", "runtime_change_user_approvals_deny_delete"),
                ("trigger", "runtime_change_user_approvals_deny_replace"),
                ("trigger", "runtime_change_user_approvals_deny_update"),
                ("trigger", "runtime_events_deny_delete"),
                ("trigger", "runtime_events_deny_replace"),
                ("trigger", "runtime_events_deny_update"),
                ("trigger", "runtime_outbox_deny_delete"),
                ("trigger", "runtime_outbox_deny_identity_update"),
                ("trigger", "runtime_outbox_deny_replace"),
                ("trigger", "runtime_outbox_policy_deny_delete"),
                ("trigger", "runtime_outbox_policy_deny_replace"),
                ("trigger", "runtime_outbox_policy_deny_update"),
                ("trigger", "runtime_outbox_receipts_deny_delete"),
                ("trigger", "runtime_outbox_receipts_deny_replace"),
                ("trigger", "runtime_outbox_receipts_deny_update"),
                ("trigger", "runtime_product_artifacts_deny_delete"),
                ("trigger", "runtime_product_artifacts_deny_replace"),
                ("trigger", "runtime_product_artifacts_deny_update"),
                ("trigger", "runtime_product_task_results_deny_delete"),
                ("trigger", "runtime_product_task_results_deny_replace"),
                ("trigger", "runtime_product_task_results_deny_update"),
                ("trigger", "runtime_product_verifications_deny_delete"),
                ("trigger", "runtime_product_verifications_deny_replace"),
                ("trigger", "runtime_product_verifications_deny_update"),
                ("trigger", "runtime_role_assignments_deny_delete"),
                ("trigger", "runtime_role_assignments_deny_replace"),
                ("trigger", "runtime_role_assignments_deny_update"),
            ],
        )
        ledger = self.raw_rows(
            self.path,
            """SELECT component, schema_version, migration_name,
                      length(migration_checksum)
               FROM runtime_schema_migrations""",
        )
        self.assertEqual(
            ledger,
            [
                ("runtime_kernel", 1, "runtime_kernel_base_v1", 64),
                ("runtime_kernel", 2, "runtime_thread_event_v2", 64),
                ("runtime_kernel", 3, "runtime_event_outbox_v3", 64),
                ("runtime_kernel", 4, "runtime_agent_store_v4", 64),
                ("runtime_kernel", 5, "runtime_agent_mailbox_v5", 64),
                ("runtime_kernel", 6, "runtime_role_assignment_v6", 64),
                ("runtime_kernel", 7, "runtime_agent_execution_state_v7", 64),
                ("runtime_kernel", 8, "runtime_backend_session_binding_v8", 64),
                ("runtime_kernel", 9, "runtime_backend_session_recovery_v9", 64),
                ("runtime_kernel", 10, "runtime_backend_session_recovery_confirmation_v10", 64),
                ("runtime_kernel", 11, "runtime_exact_changeset_user_approval_v11", 64),
                ("runtime_kernel", 12, "runtime_product_acceptance_history_v12", 64),
            ],
        )
        database.verify_integrity()

    def test_reinitialize_is_noop_and_preserves_committed_data(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with database.unit_of_work() as uow:
            uow.execute(
                "INSERT INTO probe_parent(id, value) VALUES ('canary', 'stable')"
            )
            uow.commit()
        before = self.schema_snapshot(self.path)

        self.database().initialize()
        self.database().initialize()

        self.assertEqual(self.schema_snapshot(self.path), before)

    def test_backend_session_binding_rejects_raw_replace(self) -> None:
        database = self.database()
        database.initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                """INSERT INTO runtime_backend_session_bindings(
                    scope_id, thread_id, agent_id, backend_id,
                    backend_session_id
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    "scope-a",
                    "thread-a",
                    "reviewer-agent",
                    "codex_cli",
                    "backend-session-original",
                ),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT OR REPLACE INTO runtime_backend_session_bindings(
                        scope_id, thread_id, agent_id, backend_id,
                        backend_session_id
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        "scope-a",
                        "thread-a",
                        "reviewer-agent",
                        "codex_cli",
                        "backend-session-replacement",
                    ),
                )
        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT backend_session_id
                   FROM runtime_backend_session_bindings""",
            ),
            [("backend-session-original",)],
        )

    def test_released_v1_upgrades_to_current_preserving_ledger_and_unmanaged_data(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                "INSERT INTO probe_parent(id, value) VALUES ('v1', 'stable')"
            )
        self.convert_current_fixture_to_released_v1()
        self.assert_released_v1_fixture()

        self.database().initialize()

        self.assertEqual(self.database().schema_version(), 12)
        self.assertEqual(
            self.raw_rows(self.path, "SELECT id, value FROM probe_parent"),
            [("v1", "stable")],
        )
        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT schema_version, migration_name,
                          length(migration_checksum)
                   FROM runtime_schema_migrations ORDER BY schema_version""",
            ),
            [
                (1, "runtime_kernel_base_v1", 64),
                (2, "runtime_thread_event_v2", 64),
                (3, "runtime_event_outbox_v3", 64),
                (4, "runtime_agent_store_v4", 64),
                (5, "runtime_agent_mailbox_v5", 64),
                (6, "runtime_role_assignment_v6", 64),
                (7, "runtime_agent_execution_state_v7", 64),
                (8, "runtime_backend_session_binding_v8", 64),
                (9, "runtime_backend_session_recovery_v9", 64),
                (10, "runtime_backend_session_recovery_confirmation_v10", 64),
                (11, "runtime_exact_changeset_user_approval_v11", 64),
                (12, "runtime_product_acceptance_history_v12", 64),
            ],
        )
        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT migration_checksum FROM runtime_schema_migrations
                   WHERE schema_version = 1""",
            ),
            [(RELEASED_V1_CHECKSUM,)],
        )
        self.database().verify_integrity()

    def test_current_migration_fault_leaves_released_v1_exactly_unchanged(self) -> None:
        self.database().initialize()
        self.convert_current_fixture_to_released_v1()
        self.assert_released_v1_fixture()
        before = self.schema_snapshot(self.path)

        def fail(point):
            if point is RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT:
                raise InjectedFault("v2-migration-stop")

        with self.assertRaisesRegex(InjectedFault, "v2-migration-stop"):
            self.database(fault_hook=fail).initialize()

        self.assertEqual(self.schema_snapshot(self.path), before)
        self.database().initialize()
        self.assertEqual(self.database().schema_version(), 12)

    def test_required_v2_trigger_drift_fails_closed(self) -> None:
        self.database().initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("DROP TRIGGER runtime_events_deny_delete")
        before = self.schema_snapshot(self.path)

        with self.assertRaisesRegex(RuntimeSchemaCorruptionError, "缺失"):
            self.database().initialize()

        self.assertEqual(self.schema_snapshot(self.path), before)

    def test_initialization_coexists_with_unmanaged_legacy_tables(self) -> None:
        SQLiteRuntimeStore(self.path)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                """INSERT INTO runtime_snapshots(
                    snapshot_id, task_id, project_id, phase, payload, version
                ) VALUES ('legacy-1', 'task-1', 'project-1', 'running', '{}', 1)"""
            )
            before_columns = connection.execute(
                "PRAGMA table_info(runtime_snapshots)"
            ).fetchall()

        self.database().initialize()

        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT snapshot_id, task_id, project_id, phase, payload, version
                   FROM runtime_snapshots""",
            ),
            [("legacy-1", "task-1", "project-1", "running", "{}", 1)],
        )
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA table_info(runtime_snapshots)").fetchall(),
                before_columns,
            )
        self.assertEqual(self.database().schema_version(), 12)

    def test_initialization_preserves_database_global_version_pragmas(self) -> None:
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA user_version = 73")
            connection.execute("PRAGMA application_id = 123456")

        self.database().initialize()

        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 73)
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                123456,
            )

    def test_future_schema_fails_closed_without_mutation(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with database.unit_of_work() as uow:
            uow.execute(
                "INSERT INTO probe_parent(id, value) VALUES ('canary', 'stable')"
            )
            uow.commit()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                """UPDATE runtime_schema_metadata
                   SET schema_version = ?
                   WHERE component = ?""",
                (RUNTIME_DB_SCHEMA_VERSION + 1, RUNTIME_DB_COMPONENT),
            )
        before = self.schema_snapshot(self.path)

        with self.assertRaisesRegex(
            RuntimeSchemaTooNewError,
            f"found={RUNTIME_DB_SCHEMA_VERSION + 1}.*supported={RUNTIME_DB_SCHEMA_VERSION}",
        ):
            self.database().initialize()

        self.assertEqual(self.schema_snapshot(self.path), before)

    def test_future_schema_rejection_does_not_change_journal_mode(self) -> None:
        self.database().initialize()
        with sqlite3.connect(str(self.path)) as connection:
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            self.assertEqual(mode.lower(), "delete")
            connection.execute(
                """UPDATE runtime_schema_metadata
                   SET schema_version = ? WHERE component = ?""",
                (RUNTIME_DB_SCHEMA_VERSION + 1, RUNTIME_DB_COMPONENT),
            )
        before = self.schema_snapshot(self.path)

        with self.assertRaises(RuntimeSchemaTooNewError):
            self.database().initialize()

        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "delete",
            )
        self.assertEqual(self.schema_snapshot(self.path), before)

    def test_checksum_drift_fails_closed(self) -> None:
        self.database().initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                """UPDATE runtime_schema_migrations
                   SET migration_checksum = ?
                   WHERE component = ? AND schema_version = 1""",
                ("0" * 64, RUNTIME_DB_COMPONENT),
            )
        before = self.schema_snapshot(self.path)

        with self.assertRaisesRegex(RuntimeSchemaCorruptionError, "checksum"):
            self.database().initialize()

        self.assertEqual(self.schema_snapshot(self.path), before)

    def test_metadata_ledger_gap_fails_closed_without_mutation(self) -> None:
        self.database().initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                "DELETE FROM runtime_schema_migrations WHERE component = ?",
                (RUNTIME_DB_COMPONENT,),
            )
        before = self.raw_rows(
            self.path,
            """SELECT type, name, tbl_name, sql FROM sqlite_schema
               WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name""",
        )

        with self.assertRaisesRegex(RuntimeSchemaCorruptionError, "ledger"):
            self.database().initialize()

        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT type, name, tbl_name, sql FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name""",
            ),
            before,
        )
        self.assertEqual(
            self.raw_rows(
                self.path,
                "SELECT COUNT(*) FROM runtime_schema_migrations",
            ),
            [(0,)],
        )

    def test_non_integer_ledger_version_fails_closed_without_coercion(self) -> None:
        database = self.database()
        database.initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                """UPDATE runtime_schema_migrations
                   SET schema_version = 1.5
                   WHERE component = ? AND schema_version = 1""",
                (RUNTIME_DB_COMPONENT,),
            )
        before = self.raw_rows(
            self.path,
            """SELECT schema_version, typeof(schema_version)
               FROM runtime_schema_migrations""",
        )
        self.assertEqual(
            before,
            [
                (1.5, "real"),
                (2, "integer"),
                (3, "integer"),
                (4, "integer"),
                (5, "integer"),
                (6, "integer"),
                (7, "integer"),
                (8, "integer"),
                (9, "integer"),
                (10, "integer"),
                (11, "integer"),
                (12, "integer"),
            ],
        )

        with self.assertRaisesRegex(RuntimeSchemaCorruptionError, "必须是整数"):
            database.initialize()
        with self.assertRaises(RuntimeSchemaCorruptionError):
            database.schema_version()
        uow = database.unit_of_work()
        with self.assertRaises(RuntimeSchemaCorruptionError):
            uow.__enter__()

        self.assertTrue(uow.is_closed)
        self.assertEqual(uow.state, RuntimeUnitOfWorkState.ROLLED_BACK)
        self.assertEqual(
            self.raw_rows(
                self.path,
                """SELECT schema_version, typeof(schema_version)
                   FROM runtime_schema_migrations""",
            ),
            before,
        )

    def test_migration_fault_before_commit_leaves_no_partial_schema(self) -> None:
        seen = []

        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA user_version = 73")
            connection.execute("PRAGMA application_id = 123456")
            connection.execute(
                "CREATE TABLE legacy_canary(id TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO legacy_canary VALUES ('one', 'stable')")

        def fail(point):
            seen.append(point)
            if point is RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT:
                raise InjectedFault("migration-stop")

        with self.assertRaisesRegex(InjectedFault, "migration-stop"):
            self.database(fault_hook=fail).initialize()
        self.assertEqual(
            seen,
            [RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT],
        )
        tables = self.raw_rows(
            self.path,
            """SELECT name FROM sqlite_schema
               WHERE type = 'table' AND name LIKE 'runtime_schema_%'""",
        )
        self.assertEqual(tables, [])
        self.assertEqual(
            self.raw_rows(self.path, "SELECT id, value FROM legacy_canary"),
            [("one", "stable")],
        )
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 73)
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                123456,
            )

        self.database().initialize()
        self.assertEqual(self.database().schema_version(), 12)
        self.assertEqual(
            self.raw_rows(self.path, "SELECT id, value FROM legacy_canary"),
            [("one", "stable")],
        )

    def test_every_uow_connection_applies_required_pragmas(self) -> None:
        database = self.database(busy_timeout_ms=137)
        database.initialize()

        for _ in range(2):
            with database.unit_of_work() as uow:
                self.assertEqual(uow.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(
                    uow.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                self.assertEqual(uow.execute("PRAGMA busy_timeout").fetchone()[0], 137)
                self.assertEqual(uow.execute("PRAGMA synchronous").fetchone()[0], 2)
                uow.rollback()

    def test_uow_rechecks_wal_inside_its_write_transaction(self) -> None:
        database = self.database()
        database.initialize()
        connect_after_precheck = database._connect_for_uow

        def switch_mode_after_precheck():
            connection = connect_after_precheck()
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            self.assertEqual(mode.lower(), "delete")
            return connection

        database._connect_for_uow = switch_mode_after_precheck
        uow = database.unit_of_work()

        with self.assertRaises(RuntimeSQLiteConfigurationError):
            uow.__enter__()

        self.assertTrue(uow.is_closed)
        self.assertEqual(uow.state, RuntimeUnitOfWorkState.ROLLED_BACK)

        retry_database = self.database()
        retry_database.initialize()
        with retry_database.unit_of_work() as retry:
            retry.rollback()

    def test_uow_rejects_schema_ddl_and_mutable_pragmas(self) -> None:
        database = self.database()
        database.initialize()

        statements = (
            "ALTER TABLE runtime_schema_metadata ADD COLUMN rogue TEXT",
            "CREATE INDEX rogue_idx ON runtime_schema_metadata(schema_version)",
            "CREATE TABLE rogue_table(id TEXT)",
            "PRAGMA foreign_keys = OFF",
            "PRAGMA user_version = 99",
        )
        with database.unit_of_work() as uow:
            for statement in statements:
                with self.subTest(statement=statement):
                    with self.assertRaises(RuntimeUnitOfWorkStateError):
                        uow.execute(statement)
            uow.rollback()

        objects = self.raw_rows(
            self.path,
            """SELECT name FROM sqlite_schema
               WHERE name IN ('rogue_idx', 'rogue_table') ORDER BY name""",
        )
        self.assertEqual(objects, [])
        with sqlite3.connect(str(self.path)) as connection:
            columns = connection.execute(
                "PRAGMA table_info(runtime_schema_metadata)"
            ).fetchall()
            self.assertEqual([row[1] for row in columns], [
                "component",
                "schema_version",
                "initialized_at",
                "updated_at",
            ])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_busy_begin_has_typed_error_and_leaves_no_open_uow(self) -> None:
        database = self.database(busy_timeout_ms=1)
        database.initialize()
        blocker = sqlite3.connect(str(self.path), timeout=0, isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            uow = database.unit_of_work()

            with self.assertRaises(RuntimeDatabaseBusyError):
                uow.__enter__()

            self.assertTrue(uow.is_closed)
            self.assertEqual(uow.state, RuntimeUnitOfWorkState.FAILED)
        finally:
            blocker.rollback()
            blocker.close()

        with database.unit_of_work() as retry:
            retry.rollback()

    def test_integrity_check_owns_only_runtime_component_foreign_keys(self) -> None:
        database = self.database()
        database.initialize()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute(
                "CREATE TABLE legacy_parent(id TEXT PRIMARY KEY)"
            )
            connection.execute(
                """CREATE TABLE legacy_child(
                    id TEXT PRIMARY KEY,
                    parent_id TEXT REFERENCES legacy_parent(id)
                )"""
            )
            connection.execute("INSERT INTO legacy_child VALUES ('one', 'missing')")

        database.verify_integrity()

    def test_explicit_commit_persists_all_rows_after_reopen(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with database.unit_of_work() as uow:
            self.insert_probe_pair(uow)
            uow.commit()

        self.assertEqual(self.probe_counts(), (1, 1))

    def test_explicit_rollback_discards_all_rows_after_reopen(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with database.unit_of_work() as uow:
            self.insert_probe_pair(uow)
            uow.rollback()

        self.assertEqual(self.probe_counts(), (0, 0))

    def test_clean_context_exit_without_commit_rolls_back(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        with database.unit_of_work() as uow:
            self.insert_probe_pair(uow)

        self.assertEqual(self.probe_counts(), (0, 0))

    def test_exception_exit_rolls_back_and_propagates_original_error(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)

        with self.assertRaisesRegex(InjectedFault, "unique-sentinel"):
            with database.unit_of_work() as uow:
                self.insert_probe_pair(uow)
                raise InjectedFault("unique-sentinel")

        self.assertEqual(self.probe_counts(), (0, 0))
        with database.unit_of_work() as uow:
            self.insert_probe_pair(uow)
            uow.commit()
        self.assertEqual(self.probe_counts(), (1, 1))

    def test_body_and_rollback_failures_are_both_observable(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()

        with self.assertRaises(RuntimeRollbackError) as raised:
            with uow:
                self.insert_probe_pair(uow)
                original_control = uow._run_transaction_control

                def fail_rollback(operation):
                    if getattr(operation, "__name__", "") == "rollback":
                        raise sqlite3.OperationalError("rollback-sentinel")
                    return original_control(operation)

                uow._run_transaction_control = fail_rollback
                raise InjectedFault("body-sentinel")

        observed = []
        pending = [raised.exception]
        seen = set()
        while pending:
            error = pending.pop()
            if id(error) in seen:
                continue
            seen.add(id(error))
            observed.append(str(error))
            if error.__cause__ is not None:
                pending.append(error.__cause__)
            if error.__context__ is not None:
                pending.append(error.__context__)
        self.assertTrue(any("rollback-sentinel" in item for item in observed), observed)
        self.assertTrue(any("body-sentinel" in item for item in observed), observed)
        self.assertEqual(uow.state, RuntimeUnitOfWorkState.FAILED)
        self.assertTrue(uow.is_closed)
        self.assertEqual(self.probe_counts(), (0, 0))

    def test_deferred_foreign_key_commit_failure_rolls_back_all_rows(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()

        with self.assertRaisesRegex(RuntimeCommitError, "commit"):
            with uow:
                uow.execute(
                    "INSERT INTO probe_parent(id, value) VALUES ('parent-1', 'value')"
                )
                uow.execute(
                    """INSERT INTO probe_child(id, parent_id, value)
                       VALUES ('child-1', 'missing-parent', 'value')"""
                )
                uow.commit()

        self.assertEqual(uow.state, RuntimeUnitOfWorkState.FAILED)
        self.assertTrue(uow.is_closed)
        self.assertEqual(self.probe_counts(), (0, 0))
        with database.unit_of_work() as retry:
            self.insert_probe_pair(retry)
            retry.commit()
        self.assertEqual(self.probe_counts(), (1, 1))

    def test_fault_before_commit_is_deterministic_and_atomic(self) -> None:
        seen = []

        def fail(point):
            seen.append(point)
            if point is RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT:
                raise InjectedFault("before-commit")

        database = self.database()
        self.initialize_probe_schema(database)
        faulting_database = self.database(fault_hook=fail)

        with self.assertRaisesRegex(InjectedFault, "before-commit"):
            with faulting_database.unit_of_work() as uow:
                self.insert_probe_pair(uow)
                uow.commit()

        self.assertEqual(
            seen,
            [
                RuntimePersistenceFaultPoint.UOW_AFTER_BEGIN,
                RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT,
            ],
        )
        self.assertEqual(self.probe_counts(), (0, 0))

    def test_fault_after_begin_releases_lock_for_next_uow(self) -> None:
        def fail(point):
            if point is RuntimePersistenceFaultPoint.UOW_AFTER_BEGIN:
                raise InjectedFault("after-begin")

        database = self.database()
        self.initialize_probe_schema(database)
        with self.assertRaisesRegex(InjectedFault, "after-begin"):
            with self.database(fault_hook=fail).unit_of_work():
                self.fail("fault hook should prevent entering the body")

        with database.unit_of_work() as uow:
            self.insert_probe_pair(uow)
            uow.commit()
        self.assertEqual(self.probe_counts(), (1, 1))

    def test_uow_rejects_use_before_enter_reentry_and_reuse(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()

        for operation in (
            lambda: uow.execute("SELECT 1"),
            uow.commit,
            uow.rollback,
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(RuntimeUnitOfWorkStateError):
                    operation()

        with uow:
            with self.assertRaises(RuntimeUnitOfWorkStateError):
                uow.__enter__()
            self.insert_probe_pair(uow)
            uow.commit()
            self.assertEqual(uow.state, RuntimeUnitOfWorkState.COMMITTED)
            self.assertTrue(uow.is_closed)
            for operation in (
                lambda: uow.execute("SELECT 1"),
                uow.commit,
                uow.rollback,
            ):
                with self.assertRaises(RuntimeUnitOfWorkStateError):
                    operation()

        with self.assertRaises(RuntimeUnitOfWorkStateError):
            uow.__enter__()
        self.assertEqual(self.probe_counts(), (1, 1))

    def test_close_active_uow_rolls_back_and_is_idempotent(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()
        uow.__enter__()
        self.insert_probe_pair(uow)

        uow.close()
        uow.close()

        self.assertEqual(uow.state, RuntimeUnitOfWorkState.ROLLED_BACK)
        self.assertTrue(uow.is_closed)
        self.assertEqual(self.probe_counts(), (0, 0))

    def test_sql_cannot_bypass_the_explicit_uow_commit_boundary(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)

        with self.assertRaises(RuntimeUnitOfWorkStateError):
            with database.unit_of_work() as uow:
                self.insert_probe_pair(uow)
                uow.execute("COMMIT")

        self.assertEqual(self.probe_counts(), (0, 0))

    def test_conflict_rollback_cannot_escape_the_uow_state_machine(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()

        with self.assertRaises(RuntimeTransactionError):
            with uow:
                uow.execute(
                    "INSERT INTO probe_parent(id, value) VALUES ('same', 'first')"
                )
                uow.execute(
                    """INSERT OR ROLLBACK INTO probe_parent(id, value)
                       VALUES ('same', 'second')"""
                )

        self.assertEqual(uow.state, RuntimeUnitOfWorkState.FAILED)
        self.assertTrue(uow.is_closed)
        self.assertEqual(self.probe_counts(), (0, 0))
        with database.unit_of_work() as retry:
            retry.execute(
                "INSERT INTO probe_parent(id, value) VALUES ('retry', 'stable')"
            )
            retry.commit()
        self.assertEqual(self.probe_counts(), (1, 0))

    def test_uow_and_sql_results_do_not_expose_a_raw_connection(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)

        with database.unit_of_work() as uow:
            self.assertFalse(hasattr(uow, "connection"))
            result = uow.execute("SELECT 1")
            self.assertFalse(hasattr(result, "connection"))
            result_iterator = iter(result)
            self.assertIs(result_iterator, result)
            self.assertFalse(hasattr(result_iterator, "connection"))
            self.assertEqual(next(result_iterator)[0], 1)
            uow.rollback()

    def test_uow_rejects_cross_thread_use_and_owner_can_still_rollback(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)
        uow = database.unit_of_work()
        uow.__enter__()
        failures = []

        def use_from_other_thread():
            try:
                uow.execute("SELECT 1")
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=use_from_other_thread)
        worker.start()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeUnitOfWorkStateError)
        uow.rollback()
        self.assertTrue(uow.is_closed)

    def test_fault_point_enum_has_no_after_commit_hook(self) -> None:
        self.assertEqual(
            {point.value for point in RuntimePersistenceFaultPoint},
            {
                "migration_before_commit",
                "uow_after_begin",
                "uow_before_commit",
                "state_event_after_state_write",
                "state_event_after_event_append",
                "state_event_after_outbox_enqueue",
                "outbox_after_claim_update",
                "outbox_after_nack_update",
            },
        )

    def test_process_exit_before_commit_is_none_after_reopen(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)

        result = self.run_crash_process("before_commit")

        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertEqual(self.probe_counts(), (0, 0))
        database.verify_integrity()

    def test_process_exit_after_commit_is_all_after_reopen(self) -> None:
        database = self.database()
        self.initialize_probe_schema(database)

        result = self.run_crash_process("after_commit")

        self.assertEqual(result.returncode, 92, result.stderr)
        self.assertEqual(self.probe_counts(), (1, 1))
        database.verify_integrity()

    def run_crash_process(self, mode: str):
        script = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path
            from coding_workflow.runtime_persistence import (
                OutboxPolicy,
                RuntimeSQLiteConfig,
                SQLiteRuntimeDatabase,
            )

            database = SQLiteRuntimeDatabase(
                RuntimeSQLiteConfig(Path(sys.argv[1])),
                outbox_policy=OutboxPolicy(
                    policy_version='outbox-policy/test-v1',
                    destination='core:runtime_events',
                    expected_sink_id='core:test-sink',
                    claim_ttl_ms=60000,
                    batch_limit=10,
                    retry_delays_ms=(1000, 5000, 30000),
                ),
            )
            with database.unit_of_work() as uow:
                uow.execute(
                    "INSERT INTO probe_parent(id, value) VALUES ('parent-1', 'value')"
                )
                uow.execute(
                    "INSERT INTO probe_child(id, parent_id, value) "
                    "VALUES ('child-1', 'parent-1', 'value')"
                )
                if sys.argv[2] == "before_commit":
                    os._exit(91)
                uow.commit()
                os._exit(92)
            """
        )
        return subprocess.run(
            [sys.executable, "-c", script, str(self.path), mode],
            cwd=str(Path(__file__).resolve().parents[1]),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            },
        )


if __name__ == "__main__":
    unittest.main()
