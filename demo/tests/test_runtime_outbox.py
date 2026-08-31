from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

import coding_workflow.runtime_persistence as runtime_persistence
from coding_workflow.runtime_domain import (
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    RUNTIME_DB_SCHEMA_VERSION,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventApplyResult,
    ThreadEventMutation,
)


T0 = "2026-08-25T00:00:00+00:00"
DESTINATION = "core:runtime_events"
EXPECTED_SINK = "core:test-sink"
POLICY_VERSION = "outbox-policy/test-v1"
EXPECTED_POLICY_DIGEST = (
    "03bf3a070aef896ef6912d29935d2250eeff5164bc77aaf4c94283ed52da41bc"
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def expected_delivery_key(event_id: str) -> str:
    digest = sha256(f"{DESTINATION}\0{event_id}".encode("utf-8")).hexdigest()
    return f"obx-v1-{digest}"


class RuntimeOutboxExpectedRedTests(unittest.TestCase):
    """First executable slice of INV-PROD-01B-3-EVENT-OUTBOX-ATOMICITY-v1."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.path = Path(self._temporary.name) / "runtime.sqlite3"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def explicit_policy(self):
        policy_type = getattr(runtime_persistence, "OutboxPolicy", None)
        self.assertIsNotNone(
            policy_type,
            "PROD-01B-3 requires a public, explicit OutboxPolicy",
        )
        policy = policy_type(
            policy_version=POLICY_VERSION,
            destination=DESTINATION,
            expected_sink_id=EXPECTED_SINK,
            claim_ttl_ms=60_000,
            batch_limit=10,
            retry_delays_ms=(1_000, 5_000, 30_000),
        )
        self.assertEqual(policy.policy_digest, EXPECTED_POLICY_DIGEST)
        return policy

    def runtime(self):
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(self.path),
            outbox_policy=self.explicit_policy(),
        )
        database.initialize()
        return database, SQLiteThreadEventStore(database)

    @staticmethod
    def participant() -> ScopedRef:
        return ScopedRef("scope-a", "core:principal", "user-1", 1)

    def mutation(self) -> ThreadEventMutation:
        participant = self.participant()
        thread = Thread(
            thread_id="thread-a",
            scope_id="scope-a",
            title="Outbox atomicity",
            participant_refs=(participant,),
            created_at=T0,
            updated_at=T0,
        )
        event = RuntimeEvent(
            scope_id="scope-a",
            event_id="event-outbox-1",
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id="trace-outbox-1",
            correlation_id="correlation-outbox-1",
            actor_type=RuntimeActorType.USER,
            actor_ref=participant,
            idempotency_key="idem-outbox-1",
            occurred_at=T0,
            recorded_at=T0,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        return ThreadEventMutation(0, thread, event)

    @staticmethod
    def table_count(connection: sqlite3.Connection, table: str) -> int | None:
        exists = connection.execute(
            """SELECT 1 FROM sqlite_schema
               WHERE type = 'table' AND name = ?""",
            (table,),
        ).fetchone()
        if exists is None:
            return None
        return connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_policy_is_explicit_and_digest_is_independently_reproducible(self) -> None:
        policy = self.explicit_policy()
        expected = text_digest(canonical_json({
            "schema": "outbox-policy/v1",
            "policy_version": POLICY_VERSION,
            "destination": DESTINATION,
            "expected_sink_id": EXPECTED_SINK,
            "claim_ttl_ms": 60_000,
            "batch_limit": 10,
            "retry_delays_ms": [1_000, 5_000, 30_000],
        }))
        self.assertEqual(expected, EXPECTED_POLICY_DIGEST)
        self.assertEqual(policy.policy_digest, expected)

    def test_database_without_outbox_policy_fails_closed_with_typed_error(self) -> None:
        error_type = getattr(
            runtime_persistence,
            "RuntimeOutboxConfigurationError",
            None,
        )
        self.assertIsNotNone(
            error_type,
            "missing OutboxPolicy must use a typed fail-closed error",
        )
        with self.assertRaises(error_type):
            database = SQLiteRuntimeDatabase(RuntimeSQLiteConfig(self.path))
            database.initialize()

    def test_current_database_keeps_v3_outbox_tables_and_event_parent_key(self) -> None:
        """The current schema must retain the released v3 Outbox contract."""

        database, _ = self.runtime()
        self.assertEqual(RUNTIME_DB_SCHEMA_VERSION, 7)
        self.assertEqual(database.schema_version(), 7)
        with sqlite3.connect(str(self.path)) as connection:
            table_sql = dict(connection.execute(
                """SELECT name, sql FROM sqlite_schema
                   WHERE type = 'table'
                     AND name IN ('runtime_outbox', 'runtime_outbox_receipts')"""
            ))
            self.assertEqual(
                set(table_sql),
                {"runtime_outbox", "runtime_outbox_receipts"},
            )
            self.assertTrue(
                all("WITHOUT ROWID" in sql.upper() for sql in table_sql.values())
            )

            unique_indexes = []
            for index_row in connection.execute("PRAGMA index_list(runtime_events)"):
                if index_row[2] == 1:
                    unique_indexes.append(tuple(
                        column[2]
                        for column in connection.execute(
                            f'PRAGMA index_info("{index_row[1]}")'
                        )
                    ))
            self.assertIn(("event_id", "scope_id"), unique_indexes)

    def test_apply_commit_adds_exactly_one_pending_intent(self) -> None:
        """The existing ThreadEventStore public write must atomically enqueue."""

        mutation = self.mutation()
        database, store = self.runtime()
        with database.unit_of_work() as uow:
            result = store.apply(uow, mutation)
            uow.commit()

        self.assertIs(result, ThreadEventApplyResult.APPLIED)
        event_json = canonical_json(dict(mutation.event.to_dict()))
        event_digest = text_digest(event_json)
        delivery_key = expected_delivery_key(mutation.event.event_id)
        with sqlite3.connect(str(self.path)) as connection:
            rows = connection.execute(
                """SELECT delivery_key, source_event_id, scope_id, destination,
                          event_digest, created_at, state, updated_at,
                          claim_generation, attempt_count, available_at,
                          claim_token, publisher_id, claim_expires_at,
                          last_error_code, suppress_reason, published_at,
                          receipt_id
                   FROM runtime_outbox"""
            ).fetchall()
            counts = (
                self.table_count(connection, "runtime_threads"),
                self.table_count(connection, "runtime_events"),
                self.table_count(connection, "runtime_outbox"),
            )

        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[0], delivery_key)
        self.assertEqual(row[1], mutation.event.event_id)
        self.assertEqual(row[2], mutation.event.scope_id)
        self.assertEqual(row[3], DESTINATION)
        self.assertEqual(row[4], event_digest)
        self.assertEqual(row[5], mutation.event.recorded_at)
        self.assertEqual(row[6], "PENDING")
        self.assertEqual(row[7], mutation.event.recorded_at)
        self.assertEqual(row[8:11], (0, 0, mutation.event.recorded_at))
        self.assertEqual(row[11:], (None, None, None, None, None, None, None))

    def test_apply_without_commit_leaves_thread_event_and_outbox_at_zero(self) -> None:
        """A clean UoW exit must roll back all three members of the bundle."""

        mutation = self.mutation()
        database, store = self.runtime()
        with database.unit_of_work() as uow:
            self.assertIs(
                store.apply(uow, mutation),
                ThreadEventApplyResult.APPLIED,
            )
            inside = tuple(
                uow.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "runtime_threads",
                    "runtime_events",
                    "runtime_outbox",
                )
            )
            self.assertEqual(inside, (1, 1, 1))

        with sqlite3.connect(str(self.path)) as connection:
            counts = (
                self.table_count(connection, "runtime_threads"),
                self.table_count(connection, "runtime_events"),
                self.table_count(connection, "runtime_outbox"),
            )
        self.assertEqual(counts, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
