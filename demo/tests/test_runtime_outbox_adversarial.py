from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from time import monotonic

from coding_workflow.runtime_domain import (
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeEventConflictError,
    RuntimeEventSequenceConflictError,
    RuntimeOutboxConfigurationError,
    RuntimePersistenceError,
    RuntimePersistenceFaultPoint,
    RuntimeSchemaCorruptionError,
    RuntimeSQLiteConfig,
    RuntimeStateConflictError,
    RuntimeStoredDataCorruptionError,
    RuntimeUnitOfWorkStateError,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventApplyResult,
    ThreadEventMutation,
)


T0 = "2026-08-25T00:00:00+00:00"
T1 = "2026-08-25T00:01:00+00:00"
DESTINATION = "core:runtime_events"
POLICY_VERSION = "outbox-policy/test-v1"
EXPECTED_SINK = "core:test-sink"
RELEASED_V1_CHECKSUM = (
    "f193d040abb599c178002fe51180e9a7fc59966e8ef4a32cb7338c296154b7d4"
)
RELEASED_V2_CHECKSUM = (
    "a932cba7d760ecfed595ce89fe0130aaffb56bef53e4ff663c33250a0ea79867"
)


class InjectedFault(RuntimeError):
    pass


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def exception_chain(exc: BaseException) -> tuple[str, ...]:
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return tuple(chain)


def build_policy(expected_sink_id: str = EXPECTED_SINK) -> OutboxPolicy:
    return OutboxPolicy(
        policy_version=POLICY_VERSION,
        destination=DESTINATION,
        expected_sink_id=expected_sink_id,
        claim_ttl_ms=60_000,
        batch_limit=10,
        retry_delays_ms=(1_000, 5_000, 30_000),
    )


def build_mutation(
    *,
    scope_id: str = "scope-a",
    thread_id: str = "thread-a",
    version: int = 1,
    title: str | None = None,
    event_id: str | None = None,
    idempotency_key: str | None = None,
) -> ThreadEventMutation:
    participant = ScopedRef(scope_id, "core:principal", "user-1", 1)
    updated_at = T0 if version == 1 else T1
    thread = Thread(
        thread_id=thread_id,
        scope_id=scope_id,
        title=title or f"{scope_id}/{thread_id}/v{version}",
        participant_refs=(participant,),
        version=version,
        created_at=T0,
        updated_at=updated_at,
    )
    payload = {"state": "open"}
    event_type = "core:thread_created"
    if version > 1:
        payload["previous_state"] = "open"
        event_type = "core:thread_updated"
    event = RuntimeEvent(
        scope_id=scope_id,
        event_id=event_id or f"event-{scope_id}-{thread_id}-{version}",
        event_type=event_type,
        aggregate_ref=thread.reference,
        aggregate_version=version,
        sequence_no=version,
        trace_id=f"trace-{scope_id}-{thread_id}",
        correlation_id=f"correlation-{scope_id}-{thread_id}",
        actor_type=RuntimeActorType.USER,
        actor_ref=participant,
        idempotency_key=(
            idempotency_key or f"idem-{scope_id}-{thread_id}-{version}"
        ),
        occurred_at=updated_at,
        recorded_at=updated_at,
        thread_ref=thread.reference,
        payload=payload,
    )
    return ThreadEventMutation(version - 1, thread, event)


def expected_delivery_key(event_id: str) -> str:
    digest = sha256(f"{DESTINATION}\0{event_id}".encode("utf-8")).hexdigest()
    return f"obx-v1-{digest}"


def expected_event_digest(event: RuntimeEvent) -> str:
    return text_digest(canonical_json(dict(event.to_dict())))


def expected_intent_digest(event: RuntimeEvent, policy: OutboxPolicy) -> str:
    event_digest = expected_event_digest(event)
    delivery_key = expected_delivery_key(event.event_id)
    return text_digest(canonical_json({
        "schema": "outbox-intent/v1",
        "scope_id": event.scope_id,
        "source_event_id": event.event_id,
        "event_digest": event_digest,
        "destination": DESTINATION,
        "delivery_key": delivery_key,
        "created_at": event.recorded_at,
        "policy_version": policy.policy_version,
        "policy_digest": policy.policy_digest,
    }))


class RuntimeOutboxAdversarialTests(unittest.TestCase):
    """Independent post-implementation challenges for PROD-01B-3A only."""

    _V3_DROP_STATEMENTS = (
        "DROP TRIGGER runtime_outbox_receipts_deny_replace",
        "DROP TRIGGER runtime_outbox_receipts_deny_delete",
        "DROP TRIGGER runtime_outbox_receipts_deny_update",
        "DROP TRIGGER runtime_outbox_deny_replace",
        "DROP TRIGGER runtime_outbox_deny_delete",
        "DROP TRIGGER runtime_outbox_deny_identity_update",
        "DROP TRIGGER runtime_outbox_policy_deny_replace",
        "DROP TRIGGER runtime_outbox_policy_deny_delete",
        "DROP TRIGGER runtime_outbox_policy_deny_update",
        "DROP INDEX runtime_outbox_scope_state_idx",
        "DROP TABLE runtime_outbox_receipts",
        "DROP TABLE runtime_outbox",
        "DROP TABLE runtime_outbox_policy",
        "DROP INDEX runtime_events_event_scope_uq",
    )
    _V4_DROP_STATEMENTS = (
        "DROP TABLE runtime_agent_private_state",
        "DROP TABLE runtime_agent_sessions",
        "DROP INDEX runtime_agent_instances_thread_idx",
        "DROP TABLE runtime_agent_instances",
    )
    _V5_DROP_STATEMENTS = (
        "DROP INDEX runtime_agent_messages_pending_idx",
        "DROP TABLE runtime_agent_messages",
        "DROP TABLE runtime_agent_mailbox_cursors",
    )

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def runtime(
        self,
        path: Path,
        *,
        policy: OutboxPolicy | None = None,
        fault_hook=None,
        initialize: bool = True,
    ):
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=policy or build_policy(),
            fault_hook=fault_hook,
        )
        if initialize:
            database.initialize()
        return database, SQLiteThreadEventStore(database)

    @staticmethod
    def apply_and_commit(database, store, mutation):
        with database.unit_of_work() as uow:
            result = store.apply(uow, mutation)
            uow.commit()
        return result

    @staticmethod
    def raw_rows(path: Path, statement: str, parameters=()):
        with sqlite3.connect(str(path)) as connection:
            return connection.execute(statement, parameters).fetchall()

    @staticmethod
    def three_counts(path: Path) -> tuple[int, int, int]:
        with sqlite3.connect(str(path)) as connection:
            return tuple(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in (
                    "runtime_threads",
                    "runtime_events",
                    "runtime_outbox",
                )
            )

    @staticmethod
    def outbox_rows(path: Path):
        return RuntimeOutboxAdversarialTests.raw_rows(
            path,
            """SELECT delivery_key, source_event_id, scope_id, destination,
                      event_digest, created_at, intent_digest, policy_version,
                      policy_digest, state, updated_at, claim_generation,
                      attempt_count, available_at, claim_token, publisher_id,
                      claim_expires_at, last_error_code, suppress_reason,
                      published_at, receipt_id
               FROM runtime_outbox ORDER BY source_event_id""",
        )

    @staticmethod
    def v2_snapshot(path: Path):
        with sqlite3.connect(str(path)) as connection:
            schema = connection.execute(
                """SELECT type, name, tbl_name, sql
                   FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%'
                   ORDER BY type, name"""
            ).fetchall()
            metadata = connection.execute(
                """SELECT component, schema_version, initialized_at, updated_at
                   FROM runtime_schema_metadata ORDER BY component"""
            ).fetchall()
            migrations = connection.execute(
                """SELECT component, schema_version, migration_name,
                          migration_checksum, applied_at
                   FROM runtime_schema_migrations
                   ORDER BY component, schema_version"""
            ).fetchall()
            threads = connection.execute(
                "SELECT * FROM runtime_threads ORDER BY scope_id, thread_id"
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM runtime_events ORDER BY event_id"
            ).fetchall()
        return schema, metadata, migrations, threads, events

    def strip_v3_to_released_v2(self, path: Path) -> None:
        with sqlite3.connect(str(path)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            for statement in self._V5_DROP_STATEMENTS:
                connection.execute(statement)
            for statement in self._V4_DROP_STATEMENTS:
                connection.execute(statement)
            for statement in self._V3_DROP_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                """DELETE FROM runtime_schema_migrations
                   WHERE component = 'runtime_kernel' AND schema_version >= 3"""
            )
            connection.execute(
                """UPDATE runtime_schema_metadata SET schema_version = 2
                   WHERE component = 'runtime_kernel' AND schema_version = 5"""
            )
        self.assert_released_v2(path)

    def assert_released_v2(self, path: Path) -> None:
        ledger = self.raw_rows(
            path,
            """SELECT schema_version, migration_name, migration_checksum
               FROM runtime_schema_migrations ORDER BY schema_version""",
        )
        self.assertEqual(
            ledger,
            [
                (1, "runtime_kernel_base_v1", RELEASED_V1_CHECKSUM),
                (2, "runtime_thread_event_v2", RELEASED_V2_CHECKSUM),
            ],
        )
        self.assertEqual(
            self.raw_rows(
                path,
                "SELECT schema_version FROM runtime_schema_metadata",
            ),
            [(2,)],
        )
        objects = self.raw_rows(
            path,
            """SELECT type, name FROM sqlite_schema
               WHERE name NOT LIKE 'sqlite_%'
               ORDER BY type, name""",
        )
        self.assertEqual(
            objects,
            [
                ("index", "runtime_events_recorded_idx"),
                ("table", "runtime_events"),
                ("table", "runtime_schema_metadata"),
                ("table", "runtime_schema_migrations"),
                ("table", "runtime_threads"),
                ("trigger", "runtime_events_deny_delete"),
                ("trigger", "runtime_events_deny_replace"),
                ("trigger", "runtime_events_deny_update"),
            ],
        )

    def build_v2_fixture(self, path: Path):
        database, store = self.runtime(path)
        mutations = (
            build_mutation(scope_id="scope-a", thread_id="legacy-a"),
            build_mutation(scope_id="scope-b", thread_id="legacy-b"),
        )
        for mutation in mutations:
            self.assertIs(
                self.apply_and_commit(database, store, mutation),
                ThreadEventApplyResult.APPLIED,
            )
        self.strip_v3_to_released_v2(path)
        return mutations

    @staticmethod
    def mutate_behind_trigger(
        path: Path,
        *,
        trigger_name: str,
        statement: str,
        parameters=(),
    ) -> None:
        with sqlite3.connect(str(path)) as connection:
            trigger_sql = connection.execute(
                """SELECT sql FROM sqlite_schema
                   WHERE type = 'trigger' AND name = ?""",
                (trigger_name,),
            ).fetchone()[0]
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
            connection.execute(statement, parameters)
            connection.execute(trigger_sql)

    def test_policy_rejects_ambiguous_or_unsafe_boundary_values(self) -> None:
        valid = {
            "policy_version": POLICY_VERSION,
            "destination": DESTINATION,
            "expected_sink_id": EXPECTED_SINK,
            "claim_ttl_ms": 60_000,
            "batch_limit": 10,
            "retry_delays_ms": (1_000, 5_000),
        }
        invalid = (
            ("policy_version", ""),
            ("policy_version", " leading"),
            ("policy_version", "\ud800"),
            ("destination", "model:chosen-topic"),
            ("expected_sink_id", ""),
            ("expected_sink_id", "\ud800"),
            ("claim_ttl_ms", True),
            ("claim_ttl_ms", 0),
            ("claim_ttl_ms", -1),
            ("claim_ttl_ms", 10**100),
            ("batch_limit", True),
            ("batch_limit", 0),
            ("batch_limit", 10**100),
            ("retry_delays_ms", ()),
            ("retry_delays_ms", [1_000]),
            ("retry_delays_ms", (True,)),
            ("retry_delays_ms", (-1,)),
            ("retry_delays_ms", (1.5,)),
        )
        for field_name, value in invalid:
            with self.subTest(field_name=field_name, value=value):
                arguments = dict(valid)
                arguments[field_name] = value
                with self.assertRaises(RuntimeOutboxConfigurationError):
                    OutboxPolicy(**arguments)

        policy = OutboxPolicy(**valid)
        with self.assertRaises(FrozenInstanceError):
            policy.claim_ttl_ms = 1

    def test_empty_database_and_direct_uow_both_reject_policy_drift(self) -> None:
        path = self.root / "policy-drift.sqlite3"
        policy_a = build_policy("core:sink-a")
        policy_b = build_policy("core:sink-b")
        self.runtime(path, policy=policy_a)
        before = self.raw_rows(path, "SELECT * FROM runtime_outbox_policy")

        drifted, _ = self.runtime(
            path,
            policy=policy_b,
            initialize=False,
        )
        with self.assertRaises(RuntimeOutboxConfigurationError):
            drifted.initialize()
        with self.assertRaises(RuntimeOutboxConfigurationError):
            with drifted.unit_of_work():
                self.fail("a drifted policy must never enter the UoW body")

        self.assertEqual(
            self.raw_rows(path, "SELECT * FROM runtime_outbox_policy"),
            before,
        )
        self.assertEqual(self.three_counts(path), (0, 0, 0))

    def test_read_store_without_policy_fails_before_creating_database_file(self) -> None:
        operations = (
            (
                "get_thread",
                lambda store: store.get_thread("scope-a", "thread-a"),
            ),
            (
                "get_event",
                lambda store: store.get_event("event-a"),
            ),
            (
                "list_events",
                lambda store: store.list_events(
                    "scope-a",
                    "core:thread",
                    "thread-a",
                ),
            ),
        )
        for name, operation in operations:
            path = self.root / f"missing-read-policy-{name}.sqlite3"
            database = SQLiteRuntimeDatabase(RuntimeSQLiteConfig(path))
            store = SQLiteThreadEventStore(database)
            self.assertFalse(path.exists())
            observed_error = None
            try:
                operation(store)
            except BaseException as exc:
                observed_error = exc

            with self.subTest(operation=name):
                self.assertTrue(
                    isinstance(
                        observed_error,
                        RuntimeOutboxConfigurationError,
                    )
                    and not path.exists(),
                    "missing Policy read boundary was not side-effect free: "
                    f"error={type(observed_error).__name__ if observed_error else None}, "
                    f"path_exists={path.exists()}",
                )

    def test_orphan_managed_object_is_rejected_before_wal_mutation(self) -> None:
        path = self.root / "orphan-managed-object.sqlite3"
        with sqlite3.connect(str(path)) as connection:
            connection.execute("CREATE TABLE runtime_events(rogue TEXT)")
            before_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            before_schema = connection.execute(
                """SELECT type, name, tbl_name, sql FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
            ).fetchall()
        database, _ = self.runtime(path, initialize=False)

        with self.assertRaises(RuntimeSchemaCorruptionError):
            database.initialize()

        with sqlite3.connect(str(path)) as connection:
            after_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            after_schema = connection.execute(
                """SELECT type, name, tbl_name, sql FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
            ).fetchall()
        self.assertEqual(after_mode, before_mode)
        self.assertEqual(after_schema, before_schema)

    def test_v2_with_reserved_v3_object_is_rejected_before_wal_mutation(self) -> None:
        path = self.root / "v2-with-future-object.sqlite3"
        self.build_v2_fixture(path)
        with sqlite3.connect(str(path)) as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("CREATE TABLE runtime_outbox(rogue TEXT)")
            before_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            before_schema = connection.execute(
                """SELECT type, name, tbl_name, sql FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
            ).fetchall()
        database, _ = self.runtime(path, initialize=False)

        with self.assertRaises(RuntimeSchemaCorruptionError):
            database.initialize()

        with sqlite3.connect(str(path)) as connection:
            after_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            after_schema = connection.execute(
                """SELECT type, name, tbl_name, sql FROM sqlite_schema
                   WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
            ).fetchall()
        self.assertEqual(after_mode, before_mode)
        self.assertEqual(after_schema, before_schema)

    def test_wal_bootstrap_honors_one_busy_timeout_deadline(self) -> None:
        path = self.root / "wal-deadline.sqlite3"
        with sqlite3.connect(str(path)) as connection:
            connection.execute("CREATE TABLE unrelated_canary(value TEXT)")
        blocker = sqlite3.connect(str(path), isolation_level=None)
        blocker.execute("BEGIN")
        blocker.execute("SELECT * FROM unrelated_canary").fetchall()
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path, busy_timeout_ms=200),
            outbox_policy=build_policy(),
        )
        candidate = database._connect()
        started = monotonic()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                database._ensure_wal(candidate)
        finally:
            elapsed = monotonic() - started
            candidate.close()
            blocker.rollback()
            blocker.close()
        self.assertLess(
            elapsed,
            0.230,
            f"WAL bootstrap exceeded one configured deadline: {elapsed:.3f}s",
        )

    def test_real_v2_upgrade_backfills_exact_legacy_intents(self) -> None:
        path = self.root / "released-v2.sqlite3"
        policy = build_policy()
        mutations = self.build_v2_fixture(path)
        old_threads_events = self.v2_snapshot(path)[3:]

        database, store = self.runtime(path, policy=policy)

        self.assertEqual(self.v2_snapshot(path)[3:], old_threads_events)
        rows = self.outbox_rows(path)
        self.assertEqual(len(rows), len(mutations))
        for mutation, row in zip(
            sorted(mutations, key=lambda item: item.event.event_id),
            rows,
        ):
            event = mutation.event
            event_digest = expected_event_digest(event)
            expected = (
                expected_delivery_key(event.event_id),
                event.event_id,
                event.scope_id,
                DESTINATION,
                event_digest,
                event.recorded_at,
                expected_intent_digest(event, policy),
                policy.policy_version,
                policy.policy_digest,
                "LEGACY_SUPPRESSED",
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
            self.assertEqual(row, expected)

        historical_before_retry = tuple(rows)
        self.assertIs(
            self.apply_and_commit(database, store, mutations[0]),
            ThreadEventApplyResult.ALREADY_COMMITTED,
        )
        self.assertEqual(tuple(self.outbox_rows(path)), historical_before_retry)

        new_mutation = build_mutation(
            scope_id="scope-a",
            thread_id="post-cutover",
        )
        self.assertIs(
            self.apply_and_commit(database, store, new_mutation),
            ThreadEventApplyResult.APPLIED,
        )
        self.assertEqual(
            self.raw_rows(
                path,
                """SELECT state, suppress_reason FROM runtime_outbox
                   WHERE source_event_id = ?""",
                (new_mutation.event.event_id,),
            ),
            [("PENDING", None)],
        )
        database.verify_integrity()

    def test_v2_migration_fault_restores_exact_schema_ledger_and_data(self) -> None:
        path = self.root / "migration-fault.sqlite3"
        self.build_v2_fixture(path)
        before = self.v2_snapshot(path)

        def fail(point):
            if point is RuntimePersistenceFaultPoint.MIGRATION_BEFORE_COMMIT:
                raise InjectedFault("v3-before-commit")

        database, _ = self.runtime(
            path,
            fault_hook=fail,
            initialize=False,
        )
        with self.assertRaisesRegex(InjectedFault, "v3-before-commit"):
            database.initialize()

        self.assertEqual(self.v2_snapshot(path), before)
        self.assert_released_v2(path)
        self.runtime(path)[0].verify_integrity()

    def test_corrupt_v2_is_rejected_before_any_v3_backfill(self) -> None:
        path = self.root / "corrupt-v2.sqlite3"
        mutations = self.build_v2_fixture(path)
        self.mutate_behind_trigger(
            path,
            trigger_name="runtime_events_deny_update",
            statement="UPDATE runtime_events SET event_digest = ? WHERE event_id = ?",
            parameters=("0" * 64, mutations[0].event.event_id),
        )
        before = self.v2_snapshot(path)
        database, _ = self.runtime(path, initialize=False)

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            database.initialize()

        self.assertEqual(self.v2_snapshot(path), before)
        self.assert_released_v2(path)

    def test_all_three_write_windows_and_commit_window_roll_back_everything(self) -> None:
        points = (
            RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_STATE_WRITE,
            RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_EVENT_APPEND,
            RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_OUTBOX_ENQUEUE,
            RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT,
        )
        for index, target in enumerate(points):
            path = self.root / f"fault-window-{index}.sqlite3"

            def fail(point, *, expected=target):
                if point is expected:
                    raise InjectedFault(expected.value)

            database, store = self.runtime(path, fault_hook=fail)
            mutation = build_mutation(thread_id=f"fault-{index}")
            with self.subTest(point=target):
                with self.assertRaises(InjectedFault):
                    self.apply_and_commit(database, store, mutation)
                self.assertEqual(self.three_counts(path), (0, 0, 0))

    def test_hard_process_exit_recovers_none_or_all_across_write_windows(self) -> None:
        modes = {
            "after_state": 91,
            "after_event": 92,
            "after_outbox": 93,
            "before_commit": 94,
            "after_commit": 95,
        }
        for mode, expected_code in modes.items():
            path = self.root / f"process-{mode}.sqlite3"
            self.runtime(path)

            result = self.run_process_mutation(path, mode)

            with self.subTest(mode=mode):
                self.assertEqual(result.returncode, expected_code, result.stderr)
                expected = (1, 1, 1) if mode == "after_commit" else (0, 0, 0)
                self.assertEqual(self.three_counts(path), expected)
                self.runtime(path)[0].verify_integrity()

    @staticmethod
    def run_process_mutation(path: Path, mode: str):
        script = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path
            from coding_workflow.runtime_persistence import (
                RuntimePersistenceFaultPoint,
                RuntimeSQLiteConfig,
                SQLiteRuntimeDatabase,
                SQLiteThreadEventStore,
            )
            from tests.test_runtime_outbox_adversarial import (
                build_mutation,
                build_policy,
            )

            mode = sys.argv[2]
            exits = {
                'after_state': (
                    RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_STATE_WRITE,
                    91,
                ),
                'after_event': (
                    RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_EVENT_APPEND,
                    92,
                ),
                'after_outbox': (
                    RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_OUTBOX_ENQUEUE,
                    93,
                ),
                'before_commit': (
                    RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT,
                    94,
                ),
            }

            def fault(point):
                target = exits.get(mode)
                if target is not None and point is target[0]:
                    os._exit(target[1])

            database = SQLiteRuntimeDatabase(
                RuntimeSQLiteConfig(Path(sys.argv[1])),
                outbox_policy=build_policy(),
                fault_hook=fault,
            )
            database.initialize()
            store = SQLiteThreadEventStore(database)
            with database.unit_of_work() as uow:
                store.apply(uow, build_mutation(thread_id='process-thread'))
                uow.commit()
            os._exit(95)
            """
        )
        demo_root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, "-c", script, str(path), mode],
            cwd=str(demo_root),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(demo_root),
            },
        )

    def test_public_uow_denies_every_outbox_policy_and_receipt_dml_form(self) -> None:
        path = self.root / "public-dml.sqlite3"
        database, store = self.runtime(path)
        self.apply_and_commit(database, store, build_mutation())
        before_outbox = self.outbox_rows(path)
        before_policy = self.raw_rows(path, "SELECT * FROM runtime_outbox_policy")
        statements = (
            "INSERT INTO runtime_outbox DEFAULT VALUES",
            "UPDATE runtime_outbox SET state = state",
            "DELETE FROM runtime_outbox",
            "REPLACE INTO runtime_outbox DEFAULT VALUES",
            "INSERT OR REPLACE INTO runtime_outbox DEFAULT VALUES",
            "INSERT INTO runtime_outbox_receipts DEFAULT VALUES",
            "UPDATE runtime_outbox_receipts SET ack_id = ack_id",
            "DELETE FROM runtime_outbox_receipts",
            "REPLACE INTO runtime_outbox_receipts DEFAULT VALUES",
            "INSERT INTO runtime_outbox_policy DEFAULT VALUES",
            "UPDATE runtime_outbox_policy SET policy_version = policy_version",
            "DELETE FROM runtime_outbox_policy",
            "REPLACE INTO runtime_outbox_policy DEFAULT VALUES",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                with database.unit_of_work() as uow:
                    with self.assertRaises(RuntimeUnitOfWorkStateError):
                        uow.execute(statement)

        self.assertEqual(self.outbox_rows(path), before_outbox)
        self.assertEqual(
            self.raw_rows(path, "SELECT * FROM runtime_outbox_policy"),
            before_policy,
        )

    def test_raw_sql_cannot_rewrite_delete_replace_or_use_hidden_rowid(self) -> None:
        path = self.root / "raw-guard.sqlite3"
        database, store = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        before_outbox = self.outbox_rows(path)
        before_policy = self.raw_rows(path, "SELECT * FROM runtime_outbox_policy")
        identity_columns = (
            "delivery_key",
            "source_event_id",
            "scope_id",
            "destination",
            "event_digest",
            "created_at",
            "intent_digest",
            "policy_version",
            "policy_digest",
        )
        for column in identity_columns:
            with self.subTest(column=column):
                with sqlite3.connect(str(path)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"UPDATE runtime_outbox SET {column} = {column}"
                        )

        attacks = (
            "DELETE FROM runtime_outbox",
            "REPLACE INTO runtime_outbox SELECT * FROM runtime_outbox",
            "INSERT OR REPLACE INTO runtime_outbox SELECT * FROM runtime_outbox",
            "UPDATE runtime_outbox SET state = 'CLAIMED'",
            "UPDATE runtime_outbox_policy SET policy_version = policy_version",
            "DELETE FROM runtime_outbox_policy",
            "REPLACE INTO runtime_outbox_policy SELECT * FROM runtime_outbox_policy",
        )
        for statement in attacks:
            with self.subTest(statement=statement):
                with sqlite3.connect(str(path)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement)

        for table in (
            "runtime_outbox_policy",
            "runtime_outbox",
            "runtime_outbox_receipts",
        ):
            with self.subTest(table=table):
                with sqlite3.connect(str(path)) as connection:
                    with self.assertRaises(sqlite3.OperationalError):
                        connection.execute(f'SELECT rowid FROM "{table}"').fetchone()

        self.assertEqual(self.outbox_rows(path), before_outbox)
        self.assertEqual(
            self.raw_rows(path, "SELECT * FROM runtime_outbox_policy"),
            before_policy,
        )
        database.verify_integrity()

    def test_raw_sql_rejects_initial_lifecycle_timestamp_drift(self) -> None:
        pending_path = self.root / "pending-timestamp-check.sqlite3"
        pending_database, pending_store = self.runtime(pending_path)
        pending = build_mutation(thread_id="pending-check")
        self.apply_and_commit(pending_database, pending_store, pending)
        for column in ("updated_at", "available_at"):
            with self.subTest(state="PENDING", column=column):
                with sqlite3.connect(str(pending_path)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"UPDATE runtime_outbox SET {column} = ?",
                            (T1,),
                        )

        legacy_path = self.root / "legacy-timestamp-check.sqlite3"
        self.build_v2_fixture(legacy_path)
        self.runtime(legacy_path)
        with sqlite3.connect(str(legacy_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE runtime_outbox SET updated_at = ?",
                    (T1,),
                )

    def test_exact_retry_is_zero_write_and_preserves_the_complete_intent(self) -> None:
        path = self.root / "exact-retry.sqlite3"
        database, store = self.runtime(path)
        mutation = build_mutation()
        self.assertIs(
            self.apply_and_commit(database, store, mutation),
            ThreadEventApplyResult.APPLIED,
        )
        before = self.outbox_rows(path)

        self.assertIs(
            self.apply_and_commit(database, store, mutation),
            ThreadEventApplyResult.ALREADY_COMMITTED,
        )

        self.assertEqual(self.outbox_rows(path), before)
        self.assertEqual(self.three_counts(path), (1, 1, 1))

    def test_exact_retry_rejects_missing_or_corrupt_intent_without_healing(self) -> None:
        for mode in ("missing", "intent_digest", "updated_at"):
            path = self.root / f"retry-{mode}.sqlite3"
            database, store = self.runtime(path)
            mutation = build_mutation(thread_id=f"retry-{mode}")
            self.apply_and_commit(database, store, mutation)
            if mode == "missing":
                self.mutate_behind_trigger(
                    path,
                    trigger_name="runtime_outbox_deny_delete",
                    statement=(
                        "DELETE FROM runtime_outbox WHERE source_event_id = ?"
                    ),
                    parameters=(mutation.event.event_id,),
                )
            elif mode == "intent_digest":
                self.mutate_behind_trigger(
                    path,
                    trigger_name="runtime_outbox_deny_identity_update",
                    statement=(
                        "UPDATE runtime_outbox SET intent_digest = ? "
                        "WHERE source_event_id = ?"
                    ),
                    parameters=("f" * 64, mutation.event.event_id),
                )
            else:
                with sqlite3.connect(str(path)) as connection:
                    connection.execute("PRAGMA ignore_check_constraints = ON")
                    connection.execute(
                        """UPDATE runtime_outbox SET updated_at = ?
                           WHERE source_event_id = ?""",
                        (T1, mutation.event.event_id),
                    )

            corrupted = self.outbox_rows(path)
            with self.subTest(mode=mode):
                with self.assertRaises(RuntimeStoredDataCorruptionError):
                    with database.unit_of_work() as uow:
                        store.apply(uow, mutation)
                self.assertEqual(self.outbox_rows(path), corrupted)

    def test_new_mutation_rejects_missing_current_head_outbox_before_writing(self) -> None:
        path = self.root / "missing-head-outbox.sqlite3"
        database, store = self.runtime(path)
        initial = build_mutation()
        self.apply_and_commit(database, store, initial)
        self.mutate_behind_trigger(
            path,
            trigger_name="runtime_outbox_deny_delete",
            statement="DELETE FROM runtime_outbox WHERE source_event_id = ?",
            parameters=(initial.event.event_id,),
        )
        self.assertEqual(self.three_counts(path), (1, 1, 0))
        incoming = build_mutation(version=2, title="must-not-commit")
        observed_error = None
        observed_result = None
        try:
            observed_result = self.apply_and_commit(
                database,
                store,
                incoming,
            )
        except RuntimePersistenceError as exc:
            observed_error = exc

        if observed_error is None:
            self.fail(
                "missing current-head Outbox was accepted: "
                f"result={observed_result!r}, counts={self.three_counts(path)!r}"
            )
        self.assertEqual(self.three_counts(path), (1, 1, 0))
        self.assertEqual(
            self.raw_rows(
                path,
                "SELECT COUNT(*) FROM runtime_events WHERE event_id = ?",
                (incoming.event.event_id,),
            ),
            [(0,)],
        )

    def test_integrity_scan_detects_length_valid_projection_and_policy_corruption(self) -> None:
        cases = (
            (
                "event_digest",
                "runtime_outbox_deny_identity_update",
                "UPDATE runtime_outbox SET event_digest = ?",
                ("e" * 64,),
                RuntimeStoredDataCorruptionError,
            ),
            (
                "intent_digest",
                "runtime_outbox_deny_identity_update",
                "UPDATE runtime_outbox SET intent_digest = ?",
                ("f" * 64,),
                RuntimeStoredDataCorruptionError,
            ),
            (
                "policy_digest",
                "runtime_outbox_policy_deny_update",
                "UPDATE runtime_outbox_policy SET policy_digest = ?",
                ("d" * 64,),
                RuntimeSchemaCorruptionError,
            ),
        )
        for name, trigger, statement, parameters, expected_error in cases:
            path = self.root / f"semantic-corruption-{name}.sqlite3"
            database, store = self.runtime(path)
            self.apply_and_commit(
                database,
                store,
                build_mutation(thread_id=f"corrupt-{name}"),
            )
            self.mutate_behind_trigger(
                path,
                trigger_name=trigger,
                statement=statement,
                parameters=parameters,
            )

            with self.subTest(name=name):
                with self.assertRaises(expected_error):
                    database.verify_integrity()

    def test_enqueue_collision_from_corrupt_identity_is_typed_and_atomic(self) -> None:
        path = self.root / "corrupt-delivery-key-collision.sqlite3"
        database, store = self.runtime(path)
        existing = build_mutation(thread_id="existing")
        incoming = build_mutation(thread_id="incoming")
        self.apply_and_commit(database, store, existing)
        forged_delivery_key = expected_delivery_key(incoming.event.event_id)
        self.mutate_behind_trigger(
            path,
            trigger_name="runtime_outbox_deny_identity_update",
            statement=(
                "UPDATE runtime_outbox SET delivery_key = ? "
                "WHERE source_event_id = ?"
            ),
            parameters=(forged_delivery_key, existing.event.event_id),
        )
        forged_before = self.outbox_rows(path)

        with self.assertRaises(RuntimePersistenceError):
            with database.unit_of_work() as uow:
                store.apply(uow, incoming)

        self.assertEqual(self.three_counts(path), (1, 1, 1))
        self.assertEqual(self.outbox_rows(path), forged_before)
        self.assertIsNone(store.get_thread("scope-a", "incoming"))

    def test_scopes_keep_distinct_composite_parents_and_event_id_collision_is_zero_write(self) -> None:
        path = self.root / "scope.sqlite3"
        database, store = self.runtime(path)
        scope_a = build_mutation(scope_id="scope-a", thread_id="shared")
        scope_b = build_mutation(scope_id="scope-b", thread_id="shared")
        self.apply_and_commit(database, store, scope_a)
        self.apply_and_commit(database, store, scope_b)

        joined = self.raw_rows(
            path,
            """SELECT outbox.source_event_id, outbox.scope_id,
                      event.event_id, event.scope_id
               FROM runtime_outbox AS outbox
               JOIN runtime_events AS event
                 ON event.event_id = outbox.source_event_id
                AND event.scope_id = outbox.scope_id
               ORDER BY outbox.scope_id""",
        )
        self.assertEqual(
            joined,
            [
                (
                    scope_a.event.event_id,
                    "scope-a",
                    scope_a.event.event_id,
                    "scope-a",
                ),
                (
                    scope_b.event.event_id,
                    "scope-b",
                    scope_b.event.event_id,
                    "scope-b",
                ),
            ],
        )

        collision = build_mutation(
            scope_id="scope-c",
            thread_id="collision",
            event_id=scope_a.event.event_id,
            idempotency_key="idem-scope-c-collision",
        )
        with self.assertRaises(RuntimeEventConflictError):
            with database.unit_of_work() as uow:
                store.apply(uow, collision)
        self.assertEqual(self.three_counts(path), (2, 2, 2))
        self.assertEqual(
            self.raw_rows(
                path,
                "SELECT COUNT(*) FROM runtime_outbox WHERE scope_id = 'scope-c'",
            ),
            [(0,)],
        )

    def test_concurrent_mutations_keep_exactly_one_intent_per_committed_event(self) -> None:
        path = self.root / "concurrent-mutations.sqlite3"
        database, store = self.runtime(path)
        initial = build_mutation()
        self.apply_and_commit(database, store, initial)
        candidates = (
            build_mutation(
                version=2,
                title="branch-a",
                event_id="event-branch-a",
                idempotency_key="idem-branch-a",
            ),
            build_mutation(
                version=2,
                title="branch-b",
                event_id="event-branch-b",
                idempotency_key="idem-branch-b",
            ),
        )
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def compete(mutation):
            barrier.wait()
            try:
                outcome = self.apply_and_commit(database, store, mutation)
            except (
                RuntimeStateConflictError,
                RuntimeEventSequenceConflictError,
            ) as exc:
                outcome = type(exc)
            with lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(target=compete, args=(mutation,))
            for mutation in candidates
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(outcomes.count(ThreadEventApplyResult.APPLIED), 1)
        typed_losers = outcomes.count(RuntimeStateConflictError) + outcomes.count(
            RuntimeEventSequenceConflictError
        )
        self.assertEqual(typed_losers, 1)
        self.assertEqual(self.three_counts(path), (1, 2, 2))
        self.assertEqual(
            self.raw_rows(
                path,
                """SELECT COUNT(*) FROM runtime_events AS event
                   JOIN runtime_outbox AS outbox
                     ON outbox.source_event_id = event.event_id
                    AND outbox.scope_id = event.scope_id""",
            ),
            [(2,)],
        )

        identical_path = self.root / "concurrent-identical.sqlite3"
        identical_database, identical_store = self.runtime(identical_path)
        identical = build_mutation(thread_id="identical")
        barrier = threading.Barrier(3)
        identical_outcomes = []

        def submit_identical():
            barrier.wait()
            result = self.apply_and_commit(
                identical_database,
                identical_store,
                identical,
            )
            with lock:
                identical_outcomes.append(result)

        workers = [threading.Thread(target=submit_identical) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertCountEqual(
            identical_outcomes,
            [
                ThreadEventApplyResult.APPLIED,
                ThreadEventApplyResult.ALREADY_COMMITTED,
            ],
        )
        self.assertEqual(self.three_counts(identical_path), (1, 1, 1))

    def test_concurrent_initialize_is_idempotent_or_rejects_policy_loser(self) -> None:
        def initialize_pair(path, policies):
            barrier = threading.Barrier(3)
            outcomes = []
            lock = threading.Lock()

            def initialize(policy):
                barrier.wait()
                database, _ = self.runtime(
                    path,
                    policy=policy,
                    initialize=False,
                )
                try:
                    database.initialize()
                    outcome = "ok"
                except BaseException as exc:
                    outcome = exc
                with lock:
                    outcomes.append(outcome)

            workers = [
                threading.Thread(target=initialize, args=(policy,))
                for policy in policies
            ]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=10)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            return outcomes

        same_failures = []
        for attempt in range(50):
            same_path = self.root / f"initialize-same-{attempt}.sqlite3"
            same = initialize_pair(
                same_path,
                (build_policy(), build_policy()),
            )
            if same != ["ok", "ok"]:
                same_failures.append(
                    tuple(
                        outcome
                        if outcome == "ok"
                        else exception_chain(outcome)
                        for outcome in same
                    )
                )
            else:
                self.assertEqual(
                    self.raw_rows(
                        same_path,
                        """SELECT COUNT(*) FROM runtime_schema_migrations
                           WHERE schema_version = 3""",
                    ),
                    [(1,)],
                )
        self.assertEqual(same_failures, [])

        drift_path = self.root / "initialize-drift.sqlite3"
        drift = initialize_pair(
            drift_path,
            (build_policy("core:sink-a"), build_policy("core:sink-b")),
        )
        self.assertEqual(drift.count("ok"), 1, drift)
        failures = [outcome for outcome in drift if outcome != "ok"]
        self.assertEqual(len(failures), 1, drift)
        self.assertTrue(
            isinstance(failures[0], RuntimePersistenceError),
            exception_chain(failures[0]),
        )
        self.assertEqual(
            self.raw_rows(
                drift_path,
                "SELECT COUNT(*) FROM runtime_outbox_policy",
            ),
            [(1,)],
        )


if __name__ == "__main__":
    unittest.main()
