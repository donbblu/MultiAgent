from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from coding_workflow.runtime_domain import (
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
    ThreadState,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeDatabaseIntegrityError,
    RuntimeEventConflictError,
    RuntimeEventSequenceConflictError,
    RuntimeIdempotencyConflictError,
    RuntimePersistenceFaultPoint,
    RuntimeSQLiteConfig,
    RuntimeStateConflictError,
    RuntimeStateEventValidationError,
    RuntimeStoredDataCorruptionError,
    RuntimeUnitOfWorkStateError,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventApplyResult,
    ThreadEventMutation,
)
from coding_workflow.runtime_sqlite import SQLiteRuntimeStore


T0 = "2026-08-25T00:00:00+00:00"
T1 = "2026-08-25T00:01:00+00:00"
T2 = "2026-08-25T00:02:00+00:00"
T3 = "2026-08-25T00:03:00+00:00"


class InjectedFault(RuntimeError):
    pass


class RuntimeThreadEventStoreTests(unittest.TestCase):
    """Executable contract for INV-PROD-01B-2-THREAD-EVENT-ATOMICITY-v1."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "runtime.sqlite3"
        self.database, self.store = self._new_store(self.path)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _new_store(path: Path, *, fault_hook=None):
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
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
        database.initialize()
        return database, SQLiteThreadEventStore(database)

    @staticmethod
    def _participant(scope_id: str, participant_id: str = "user-1") -> ScopedRef:
        return ScopedRef(scope_id, "core:principal", participant_id, 1)

    def thread(
        self,
        *,
        scope_id: str = "scope-a",
        thread_id: str = "thread-a",
        version: int = 1,
        state: ThreadState = ThreadState.OPEN,
        title: str | None = None,
        participants: tuple[ScopedRef, ...] | None = None,
        policy_ref: ScopedRef | None = None,
        updated_at: str | None = None,
    ) -> Thread:
        update_time = updated_at or (T0, T1, T2, T3)[min(version - 1, 3)]
        return Thread(
            thread_id=thread_id,
            scope_id=scope_id,
            title=title or f"Thread v{version}",
            participant_refs=participants or (self._participant(scope_id),),
            state=state,
            policy_ref=policy_ref,
            version=version,
            created_at=T0,
            updated_at=update_time,
            archived_at=update_time if state is ThreadState.ARCHIVED else "",
        )

    def event(
        self,
        thread: Thread,
        *,
        sequence_no: int,
        previous_state: ThreadState | None = None,
        event_id: str | None = None,
        idempotency_key: str | None = None,
        event_type: str | None = None,
        aggregate_ref: ScopedRef | None = None,
        thread_ref: ScopedRef | None = None,
        payload: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        if event_type is None:
            if previous_state is None:
                event_type = "core:thread_created"
            elif previous_state is ThreadState.OPEN and thread.state is ThreadState.PAUSED:
                event_type = "core:thread_paused"
            elif previous_state is ThreadState.PAUSED and thread.state is ThreadState.OPEN:
                event_type = "core:thread_resumed"
            elif thread.state is ThreadState.ARCHIVED:
                event_type = "core:thread_archived"
            else:
                event_type = "core:thread_updated"
        if payload is None:
            payload = {"state": thread.state.value}
            if previous_state is not None:
                payload["previous_state"] = previous_state.value
        return RuntimeEvent(
            scope_id=thread.scope_id,
            event_id=event_id or f"event-{thread.scope_id}-{thread.thread_id}-{sequence_no}",
            event_type=event_type,
            aggregate_ref=aggregate_ref or thread.reference,
            aggregate_version=thread.version,
            sequence_no=sequence_no,
            trace_id=f"trace-{thread.scope_id}-{thread.thread_id}",
            correlation_id=f"correlation-{thread.scope_id}-{thread.thread_id}",
            actor_type=RuntimeActorType.USER,
            actor_ref=self._participant(thread.scope_id),
            idempotency_key=(
                idempotency_key
                or f"idem-{thread.scope_id}-{thread.thread_id}-{sequence_no}"
            ),
            occurred_at=thread.updated_at,
            recorded_at=thread.updated_at,
            thread_ref=thread_ref or thread.reference,
            payload=payload,
        )

    def mutation(
        self,
        thread: Thread,
        *,
        expected_version: int,
        sequence_no: int,
        previous_state: ThreadState | None = None,
        **event_changes,
    ) -> ThreadEventMutation:
        return ThreadEventMutation(
            expected_version=expected_version,
            thread=thread,
            event=self.event(
                thread,
                sequence_no=sequence_no,
                previous_state=previous_state,
                **event_changes,
            ),
        )

    @staticmethod
    def apply_and_commit(database, store, mutation):
        with database.unit_of_work() as uow:
            result = store.apply(uow, mutation)
            uow.commit()
        return result

    def test_create_commits_thread_and_event_and_supports_minimal_reads(self) -> None:
        thread = self.thread()
        event = self.event(thread, sequence_no=1)
        mutation = ThreadEventMutation(0, thread, event)

        result = self.apply_and_commit(self.database, self.store, mutation)

        self.assertIs(result, ThreadEventApplyResult.APPLIED)
        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), thread)
        self.assertEqual(self.store.get_event(event.event_id), event)
        self.assertEqual(
            self.store.list_events("scope-a", "core:thread", "thread-a"),
            (event,),
        )
        self.assertEqual(
            self.store.list_events(
                "scope-a",
                "core:thread",
                "thread-a",
                after_sequence_no=1,
                limit=100,
            ),
            (),
        )

    def test_clean_uow_exit_rolls_back_state_and_event_together(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)

        with self.database.unit_of_work() as uow:
            self.assertIs(
                self.store.apply(uow, mutation), ThreadEventApplyResult.APPLIED
            )

        self.assertIsNone(self.store.get_thread("scope-a", "thread-a"))
        self.assertIsNone(self.store.get_event(mutation.event.event_id))

    def test_store_rejects_uow_from_another_database_and_rolls_it_back(self) -> None:
        other_database, other_store = self._new_store(
            self.root / "other-runtime.sqlite3"
        )
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)

        with other_database.unit_of_work() as other_uow:
            with self.assertRaises(RuntimeStateEventValidationError):
                self.store.apply(other_uow, mutation)
            self.assertTrue(other_uow.is_closed)

        self.assertIsNone(self.store.get_thread("scope-a", "thread-a"))
        self.assertIsNone(other_store.get_thread("scope-a", "thread-a"))

    def test_cross_thread_apply_preserves_typed_error_and_owner_rollback(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        uow = self.database.unit_of_work()
        uow.__enter__()
        failures = []

        def apply_from_other_thread():
            try:
                self.store.apply(uow, mutation)
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=apply_from_other_thread)
        worker.start()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeUnitOfWorkStateError)
        self.assertFalse(uow.is_closed)
        uow.rollback()
        self.assertIsNone(self.store.get_thread("scope-a", "thread-a"))

    def test_same_state_field_update_advances_thread_and_journal(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, created)
        updated_thread = self.thread(version=2, title="Renamed thread")
        updated = self.mutation(
            updated_thread,
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )

        result = self.apply_and_commit(self.database, self.store, updated)

        self.assertIs(result, ThreadEventApplyResult.APPLIED)
        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), updated_thread)
        self.assertEqual(
            self.store.list_events("scope-a", "core:thread", "thread-a"),
            (created.event, updated.event),
        )

    def test_pause_then_reopen_uses_explicit_lifecycle_events(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        paused = self.mutation(
            self.thread(version=2, state=ThreadState.PAUSED),
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )
        reopened = self.mutation(
            self.thread(version=3, state=ThreadState.OPEN),
            expected_version=2,
            sequence_no=3,
            previous_state=ThreadState.PAUSED,
        )

        for mutation in (created, paused, reopened):
            self.assertIs(
                self.apply_and_commit(self.database, self.store, mutation),
                ThreadEventApplyResult.APPLIED,
            )

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), reopened.thread)
        self.assertEqual(
            [event.event_type for event in self.store.list_events(
                "scope-a", "core:thread", "thread-a"
            )],
            ["core:thread_created", "core:thread_paused", "core:thread_resumed"],
        )

    def test_archived_thread_is_terminal(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        archived = self.mutation(
            self.thread(version=2, state=ThreadState.ARCHIVED),
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )
        self.apply_and_commit(self.database, self.store, created)
        self.apply_and_commit(self.database, self.store, archived)
        illegal_reopen = self.mutation(
            self.thread(version=3, state=ThreadState.OPEN),
            expected_version=2,
            sequence_no=3,
            previous_state=ThreadState.ARCHIVED,
        )

        with self.assertRaises(RuntimeStateEventValidationError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, illegal_reopen)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), archived.thread)
        self.assertIsNone(self.store.get_event(illegal_reopen.event.event_id))

    def test_create_requires_zero_expected_version_v1_open_and_created_event(self) -> None:
        invalid = (
            self.mutation(self.thread(), expected_version=1, sequence_no=1),
            self.mutation(
                self.thread(version=2), expected_version=0, sequence_no=1
            ),
            self.mutation(
                self.thread(state=ThreadState.PAUSED),
                expected_version=0,
                sequence_no=1,
            ),
            self.mutation(
                self.thread(),
                expected_version=0,
                sequence_no=1,
                event_type="core:thread_updated",
            ),
        )
        for mutation in invalid:
            with self.subTest(mutation=mutation):
                with self.assertRaises(RuntimeStateEventValidationError):
                    with self.database.unit_of_work() as uow:
                        self.store.apply(uow, mutation)

        self.assertIsNone(self.store.get_thread("scope-a", "thread-a"))

    def test_update_requires_next_version_later_timestamp_and_real_change(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, created)
        invalid_threads = (
            self.thread(version=3),
            self.thread(version=2, updated_at=T0),
            self.thread(version=2, title=created.thread.title),
        )
        for index, thread in enumerate(invalid_threads, start=1):
            mutation = self.mutation(
                thread,
                expected_version=1,
                sequence_no=2,
                previous_state=ThreadState.OPEN,
                event_id=f"invalid-update-{index}",
                idempotency_key=f"invalid-update-idem-{index}",
            )
            with self.subTest(thread=thread):
                with self.assertRaises(RuntimeStateEventValidationError):
                    with self.database.unit_of_work() as uow:
                        self.store.apply(uow, mutation)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), created.thread)

    def test_event_must_bind_post_state_type_payload_and_previous_state(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, created)
        thread = self.thread(version=2, title="Changed")
        wrong_ref = ScopedRef("scope-a", "core:thread", "other-thread", 2)
        invalid = (
            self.mutation(
                thread, expected_version=1, sequence_no=2,
                previous_state=ThreadState.OPEN, aggregate_ref=wrong_ref,
            ),
            self.mutation(
                thread, expected_version=1, sequence_no=2,
                previous_state=ThreadState.OPEN, thread_ref=wrong_ref,
            ),
            self.mutation(
                thread, expected_version=1, sequence_no=2,
                previous_state=ThreadState.OPEN, event_type="core:thread_paused",
            ),
            self.mutation(
                thread, expected_version=1, sequence_no=2,
                previous_state=ThreadState.OPEN,
                payload={"state": "paused", "previous_state": "open"},
            ),
            self.mutation(
                thread, expected_version=1, sequence_no=2,
                previous_state=ThreadState.PAUSED,
            ),
        )
        for index, mutation in enumerate(invalid):
            mutation = ThreadEventMutation(
                mutation.expected_version,
                mutation.thread,
                replace(
                    mutation.event,
                    event_id=f"invalid-binding-{index}",
                    idempotency_key=f"invalid-binding-idem-{index}",
                ),
            )
            with self.subTest(index=index):
                with self.assertRaises(RuntimeStateEventValidationError):
                    with self.database.unit_of_work() as uow:
                        self.store.apply(uow, mutation)

    def test_exact_retry_is_recognized_after_thread_has_advanced(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        second = self.mutation(
            self.thread(version=2, title="Later"),
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )
        self.apply_and_commit(self.database, self.store, first)
        self.apply_and_commit(self.database, self.store, second)

        result = self.apply_and_commit(self.database, self.store, first)

        self.assertIs(result, ThreadEventApplyResult.ALREADY_COMMITTED)
        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), second.thread)
        self.assertEqual(
            self.store.list_events("scope-a", "core:thread", "thread-a"),
            (first.event, second.event),
        )

    def test_exact_retry_fails_closed_when_durable_event_is_corrupt(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)
        with sqlite3.connect(str(self.path)) as connection:
            trigger_sql = connection.execute(
                """SELECT sql FROM sqlite_schema
                   WHERE type = 'trigger'
                     AND name = 'runtime_events_deny_update'"""
            ).fetchone()[0]
            connection.execute("DROP TRIGGER runtime_events_deny_update")
            connection.execute(
                "UPDATE runtime_events SET event_json = '{}' WHERE event_id = ?",
                (mutation.event.event_id,),
            )
            connection.execute(trigger_sql)

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, mutation)

    def test_exact_retry_fails_closed_when_current_thread_head_is_missing(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """DELETE FROM runtime_threads
                   WHERE scope_id = 'scope-a' AND thread_id = 'thread-a'"""
            )

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, mutation)
        with self.assertRaises(RuntimeDatabaseIntegrityError):
            self.database.verify_integrity()

    def test_old_retry_fails_when_current_head_event_is_corrupt(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        second = self.mutation(
            self.thread(version=2, title="Current head"),
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )
        self.apply_and_commit(self.database, self.store, first)
        self.apply_and_commit(self.database, self.store, second)
        with sqlite3.connect(str(self.path)) as connection:
            trigger_sql = connection.execute(
                """SELECT sql FROM sqlite_schema
                   WHERE type = 'trigger'
                     AND name = 'runtime_events_deny_update'"""
            ).fetchone()[0]
            connection.execute("DROP TRIGGER runtime_events_deny_update")
            connection.execute(
                "UPDATE runtime_events SET event_json = '{}' WHERE event_id = ?",
                (second.event.event_id,),
            )
            connection.execute(trigger_sql)

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, first)

    def test_same_event_envelope_with_different_result_state_is_not_a_noop(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, first)
        forged = ThreadEventMutation(
            expected_version=0,
            thread=replace(first.thread, title="Forged result"),
            event=first.event,
        )

        with self.assertRaises(RuntimeEventConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, forged)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), first.thread)

    def test_reused_event_id_with_different_mutation_is_typed_conflict(self) -> None:
        first = self.mutation(
            self.thread(), expected_version=0, sequence_no=1, event_id="event-global"
        )
        self.apply_and_commit(self.database, self.store, first)
        other = self.mutation(
            self.thread(scope_id="scope-b", thread_id="thread-b"),
            expected_version=0,
            sequence_no=1,
            event_id="event-global",
        )

        with self.assertRaises(RuntimeEventConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, other)

        self.assertIsNone(self.store.get_thread("scope-b", "thread-b"))

    def test_reused_idempotency_key_with_different_mutation_is_typed_conflict(self) -> None:
        first = self.mutation(
            self.thread(),
            expected_version=0,
            sequence_no=1,
            idempotency_key="idem-global",
        )
        self.apply_and_commit(self.database, self.store, first)
        other = self.mutation(
            self.thread(scope_id="scope-b", thread_id="thread-b"),
            expected_version=0,
            sequence_no=1,
            idempotency_key="idem-global",
        )

        with self.assertRaises(RuntimeIdempotencyConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, other)

        self.assertIsNone(self.store.get_thread("scope-b", "thread-b"))

    def test_duplicate_aggregate_sequence_with_different_content_is_typed_conflict(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, first)
        wrong_sequence = self.mutation(
            self.thread(version=2, title="Changed"),
            expected_version=1,
            sequence_no=1,
            previous_state=ThreadState.OPEN,
            event_id="event-wrong-sequence",
            idempotency_key="idem-wrong-sequence",
        )

        with self.assertRaises(RuntimeEventSequenceConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, wrong_sequence)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), first.thread)

    def test_aggregate_sequence_gap_is_typed_conflict(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, first)
        gap = self.mutation(
            self.thread(version=2, title="Gap"),
            expected_version=1,
            sequence_no=3,
            previous_state=ThreadState.OPEN,
            event_id="event-gap",
            idempotency_key="idem-gap",
        )

        with self.assertRaises(RuntimeEventSequenceConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, gap)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), first.thread)

    def test_stale_expected_version_is_typed_conflict_and_zero_write(self) -> None:
        first = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        winner = self.mutation(
            self.thread(version=2, title="Winner"),
            expected_version=1,
            sequence_no=2,
            previous_state=ThreadState.OPEN,
        )
        self.apply_and_commit(self.database, self.store, first)
        self.apply_and_commit(self.database, self.store, winner)
        stale = self.mutation(
            self.thread(version=2, title="Stale branch"),
            expected_version=1,
            sequence_no=3,
            previous_state=ThreadState.OPEN,
            event_id="event-stale",
            idempotency_key="idem-stale",
        )

        with self.assertRaises(RuntimeStateConflictError):
            with self.database.unit_of_work() as uow:
                self.store.apply(uow, stale)

        self.assertEqual(self.store.get_thread("scope-a", "thread-a"), winner.thread)
        self.assertIsNone(self.store.get_event("event-stale"))

    def test_aggregate_sequence_is_scope_scoped(self) -> None:
        scope_a = self.mutation(
            self.thread(scope_id="scope-a", thread_id="same-id"),
            expected_version=0,
            sequence_no=1,
        )
        scope_b = self.mutation(
            self.thread(scope_id="scope-b", thread_id="same-id"),
            expected_version=0,
            sequence_no=1,
        )

        self.apply_and_commit(self.database, self.store, scope_a)
        self.apply_and_commit(self.database, self.store, scope_b)

        self.assertEqual(
            self.store.list_events("scope-a", "core:thread", "same-id"),
            (scope_a.event,),
        )
        self.assertEqual(
            self.store.list_events("scope-b", "core:thread", "same-id"),
            (scope_b.event,),
        )

    def test_concurrent_same_expected_version_has_one_atomic_winner(self) -> None:
        created = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, created)
        candidates = (
            self.mutation(
                self.thread(version=2, title="Branch A"),
                expected_version=1,
                sequence_no=2,
                previous_state=ThreadState.OPEN,
                event_id="event-branch-a",
                idempotency_key="idem-branch-a",
            ),
            self.mutation(
                self.thread(version=2, title="Branch B"),
                expected_version=1,
                sequence_no=2,
                previous_state=ThreadState.OPEN,
                event_id="event-branch-b",
                idempotency_key="idem-branch-b",
            ),
        )
        barrier = threading.Barrier(3)
        outcomes = []
        outcome_lock = threading.Lock()

        def compete(mutation):
            barrier.wait()
            try:
                outcome = self.apply_and_commit(self.database, self.store, mutation)
            except (RuntimeStateConflictError, RuntimeEventSequenceConflictError) as exc:
                outcome = type(exc)
            with outcome_lock:
                outcomes.append(outcome)

        workers = [threading.Thread(target=compete, args=(item,)) for item in candidates]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(outcomes.count(ThreadEventApplyResult.APPLIED), 1)
        self.assertEqual(len(outcomes), 2)
        current = self.store.get_thread("scope-a", "thread-a")
        self.assertIn(current, tuple(item.thread for item in candidates))
        events = self.store.list_events("scope-a", "core:thread", "thread-a")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1].aggregate_ref, current.reference)

    def test_public_uow_sql_cannot_mutate_managed_thread_or_event_tables(self) -> None:
        statements = (
            "INSERT INTO runtime_threads DEFAULT VALUES",
            "UPDATE runtime_threads SET thread_id = thread_id",
            "DELETE FROM runtime_threads",
            "INSERT INTO runtime_events DEFAULT VALUES",
            "UPDATE runtime_events SET event_id = event_id",
            "DELETE FROM runtime_events",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                with self.database.unit_of_work() as uow:
                    with self.assertRaises(RuntimeUnitOfWorkStateError):
                        uow.execute(statement)

    def test_raw_sql_cannot_rewrite_or_delete_an_appended_event(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)

        for statement in (
            "UPDATE runtime_events SET event_type = event_type WHERE event_id = ?",
            "DELETE FROM runtime_events WHERE event_id = ?",
        ):
            with self.subTest(statement=statement):
                with sqlite3.connect(str(self.path)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, (mutation.event.event_id,))

        self.assertEqual(self.store.get_event(mutation.event.event_id), mutation.event)

    def test_raw_insert_or_replace_cannot_rewrite_an_appended_event(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)
        projection = """event_id, scope_id, event_type, aggregate_type,
            aggregate_id, aggregate_version, sequence_no, event_version,
            idempotency_key, trace_id, correlation_id, occurred_at, recorded_at,
            event_json, event_digest, result_state_digest, mutation_digest"""
        for prefix in ("INSERT OR REPLACE", "REPLACE"):
            with self.subTest(prefix=prefix):
                with sqlite3.connect(str(self.path)) as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f"""{prefix} INTO runtime_events({projection})
                                SELECT event_id, scope_id, event_type,
                                       aggregate_type, aggregate_id,
                                       aggregate_version, sequence_no,
                                       event_version, idempotency_key, trace_id,
                                       correlation_id, occurred_at, recorded_at,
                                       event_json, event_digest,
                                       result_state_digest, ?
                                FROM runtime_events WHERE event_id = ?""",
                            ("0" * 64, mutation.event.event_id),
                        )

        self.assertEqual(self.store.get_event(mutation.event.event_id), mutation.event)
        self.database.verify_integrity()

    def test_event_table_has_no_hidden_rowid_replace_channel(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)

        with sqlite3.connect(str(self.path)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "SELECT rowid FROM runtime_events WHERE event_id = ?",
                    (mutation.event.event_id,),
                ).fetchone()

    def test_both_intra_apply_fault_windows_roll_back_state_and_event(self) -> None:
        points = (
            RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_STATE_WRITE,
            RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_EVENT_APPEND,
        )
        for index, target in enumerate(points):
            path = self.root / f"fault-{index}.sqlite3"

            def fault_hook(point, *, expected=target):
                if point is expected:
                    raise InjectedFault(expected.value)

            database, store = self._new_store(path, fault_hook=fault_hook)
            mutation = self.mutation(
                self.thread(thread_id=f"fault-thread-{index}"),
                expected_version=0,
                sequence_no=1,
            )
            with self.subTest(point=target):
                uow = database.unit_of_work()
                uow.__enter__()
                with self.assertRaises(InjectedFault):
                    store.apply(uow, mutation)
                self.assertTrue(uow.is_closed)
                with self.assertRaises(RuntimeUnitOfWorkStateError):
                    uow.commit()
                self.assertIsNone(
                    store.get_thread("scope-a", f"fault-thread-{index}")
                )
                self.assertIsNone(store.get_event(mutation.event.event_id))

    def test_commit_fault_rolls_back_state_and_event(self) -> None:
        target = RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT

        def fault_hook(point):
            if point is target:
                raise InjectedFault(point.value)

        database, store = self._new_store(
            self.root / "commit-fault.sqlite3", fault_hook=fault_hook
        )
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)

        with self.assertRaises(InjectedFault):
            with database.unit_of_work() as uow:
                store.apply(uow, mutation)
                uow.commit()

        self.assertIsNone(store.get_thread("scope-a", "thread-a"))
        self.assertIsNone(store.get_event(mutation.event.event_id))

    def test_thread_json_corruption_fails_closed_with_typed_error(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)
        with sqlite3.connect(str(self.path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(runtime_threads)")
            }
            json_column = next(
                (name for name in ("thread_json", "state_json", "payload") if name in columns),
                None,
            )
            self.assertIsNotNone(json_column, "runtime_threads 必须持久化 canonical Thread JSON")
            connection.execute(
                f'UPDATE runtime_threads SET "{json_column}" = ? '
                "WHERE scope_id = ? AND thread_id = ?",
                ("{}", "scope-a", "thread-a"),
            )

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            self.store.get_thread("scope-a", "thread-a")

    def test_event_json_and_digest_corruption_fail_closed(self) -> None:
        corruptions = (
            ("event_json", "{}"),
            ("event_digest", "0" * 64),
            ("result_state_digest", "1" * 64),
            ("mutation_digest", "2" * 64),
        )
        for index, (column, value) in enumerate(corruptions):
            path = self.root / f"event-corruption-{index}.sqlite3"
            database, store = self._new_store(path)
            mutation = self.mutation(
                self.thread(thread_id=f"corrupt-thread-{index}"),
                expected_version=0,
                sequence_no=1,
            )
            self.apply_and_commit(database, store, mutation)
            with sqlite3.connect(str(path)) as connection:
                trigger_sql = connection.execute(
                    """SELECT sql FROM sqlite_schema
                       WHERE type = 'trigger'
                         AND name = 'runtime_events_deny_update'"""
                ).fetchone()[0]
                connection.execute("DROP TRIGGER runtime_events_deny_update")
                connection.execute(
                    f'UPDATE runtime_events SET "{column}" = ? WHERE event_id = ?',
                    (value, mutation.event.event_id),
                )
                connection.execute(trigger_sql)

            with self.subTest(column=column):
                with self.assertRaises(RuntimeStoredDataCorruptionError):
                    store.get_event(mutation.event.event_id)
                with self.assertRaises(RuntimeDatabaseIntegrityError):
                    database.verify_integrity()

    def test_tampered_thread_to_last_event_link_fails_closed(self) -> None:
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)
        self.apply_and_commit(self.database, self.store, mutation)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """UPDATE runtime_threads SET last_event_id = 'missing-event'
                   WHERE scope_id = 'scope-a' AND thread_id = 'thread-a'"""
            )

        with self.assertRaises(RuntimeStoredDataCorruptionError):
            self.store.get_thread("scope-a", "thread-a")

    def test_process_exit_before_commit_recovers_none_and_after_commit_recovers_both(self) -> None:
        expected_codes = {
            "after_state": 81,
            "after_event": 82,
            "after_commit": 83,
        }
        for mode, code in expected_codes.items():
            path = self.root / f"process-{mode}.sqlite3"
            database, store = self._new_store(path)

            result = self._run_process_mutation(path, mode)

            with self.subTest(mode=mode):
                self.assertEqual(result.returncode, code, result.stderr)
                if mode == "after_commit":
                    self.assertIsNotNone(store.get_thread("scope-a", "process-thread"))
                    self.assertIsNotNone(store.get_event("process-event"))
                else:
                    self.assertIsNone(store.get_thread("scope-a", "process-thread"))
                    self.assertIsNone(store.get_event("process-event"))
                database.verify_integrity()

    @staticmethod
    def _run_process_mutation(path: Path, mode: str):
        script = textwrap.dedent(
            """
            import os
            import sys
            from pathlib import Path
            from coding_workflow.runtime_domain import (
                RuntimeActorType, RuntimeEvent, ScopedRef, Thread,
            )
            from coding_workflow.runtime_persistence import (
                OutboxPolicy,
                RuntimePersistenceFaultPoint, RuntimeSQLiteConfig,
                SQLiteRuntimeDatabase, SQLiteThreadEventStore,
                ThreadEventMutation,
            )

            mode = sys.argv[2]
            def fault(point):
                if mode == 'after_state' and point is RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_STATE_WRITE:
                    os._exit(81)
                if mode == 'after_event' and point is RuntimePersistenceFaultPoint.STATE_EVENT_AFTER_EVENT_APPEND:
                    os._exit(82)

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
                fault_hook=fault,
            )
            database.initialize()
            store = SQLiteThreadEventStore(database)
            participant = ScopedRef('scope-a', 'core:principal', 'user-1', 1)
            thread = Thread(
                'process-thread', 'scope-a', 'Process thread', (participant,),
                created_at='2026-08-25T00:00:00+00:00',
                updated_at='2026-08-25T00:00:00+00:00',
            )
            event = RuntimeEvent(
                'scope-a', 'process-event', 'core:thread_created',
                thread.reference, 1, 1, 'trace-process', 'correlation-process',
                RuntimeActorType.USER, participant, 'idem-process',
                thread.updated_at, thread.updated_at,
                thread_ref=thread.reference, payload={'state': 'open'},
            )
            with database.unit_of_work() as uow:
                store.apply(uow, ThreadEventMutation(0, thread, event))
                uow.commit()
            os._exit(83)
            """
        )
        return subprocess.run(
            [sys.executable, "-c", script, str(path), mode],
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

    def test_legacy_sqlite_runtime_store_coexists_without_backfill_or_dual_write(self) -> None:
        path = self.root / "coexist.sqlite3"
        SQLiteRuntimeStore(path)
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                """INSERT INTO runtime_snapshots(
                    snapshot_id, task_id, project_id, phase, payload, version
                ) VALUES ('legacy', 'task', 'project', 'running', '{}', 7)"""
            )
        database, store = self._new_store(path)
        mutation = self.mutation(self.thread(), expected_version=0, sequence_no=1)

        self.apply_and_commit(database, store, mutation)

        with sqlite3.connect(str(path)) as connection:
            legacy = connection.execute(
                """SELECT snapshot_id, task_id, project_id, phase, payload, version
                   FROM runtime_snapshots WHERE snapshot_id = 'legacy'"""
            ).fetchone()
        self.assertEqual(legacy, ("legacy", "task", "project", "running", "{}", 7))
        self.assertEqual(store.get_thread("scope-a", "thread-a"), mutation.thread)


if __name__ == "__main__":
    unittest.main()
