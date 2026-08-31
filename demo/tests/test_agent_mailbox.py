from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from coding_workflow.agent_runtime import (
    AgentLaneRuntime,
    AgentManager,
    MailboxManager,
)
from coding_workflow.runtime_domain import (
    Message,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    AgentClosedError,
    AgentPausedError,
    AgentStoreAccessError,
    MailboxConflictError,
    MailboxSendResult,
    MailboxValidationError,
    OutboxPolicy,
    RuntimeSQLiteConfig,
    RuntimeUnitOfWorkStateError,
    SQLiteMailboxStore,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


T0 = "2026-08-27T00:00:00+00:00"
T1 = "2026-08-27T00:01:00+00:00"
T2 = "2026-08-27T00:02:00+00:00"
T3 = "2026-08-27T00:03:00+00:00"
T4 = "2026-08-27T00:04:00+00:00"


class TickClock:
    def __init__(self) -> None:
        self._instant = datetime(2026, 8, 27, 0, 10, tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            value = self._instant
            self._instant += timedelta(microseconds=1)
        return value.isoformat()


class AgentMailboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "runtime.sqlite3"
        self.database = self._database(self.path)
        self.database.initialize()
        self.thread_store = SQLiteThreadEventStore(self.database)
        self._create_thread("thread-a")
        self._create_thread("thread-b")
        self.agents = AgentManager(self.database)
        self._create_agent("sender")
        self._create_agent("agent-a")
        self._create_agent("agent-b")
        self._create_agent("other-thread", thread_id="thread-b")
        self.clock = TickClock()
        self.mailbox = MailboxManager(self.database, clock=self.clock)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _database(path: Path) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/mailbox-tests-v1",
                destination="core:runtime_events",
                expected_sink_id="core:mailbox-test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000, 5_000),
            ),
        )

    def _create_thread(self, thread_id: str) -> Thread:
        participant = ScopedRef("scope-a", "core:principal", "user-1", 1)
        thread = Thread(
            thread_id=thread_id,
            scope_id="scope-a",
            title=thread_id,
            participant_refs=(participant,),
            created_at=T0,
            updated_at=T0,
        )
        event = RuntimeEvent(
            scope_id="scope-a",
            event_id=f"event-{thread_id}",
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id=f"trace-{thread_id}",
            correlation_id=f"correlation-{thread_id}",
            actor_type=RuntimeActorType.USER,
            actor_ref=participant,
            idempotency_key=f"idem-{thread_id}",
            occurred_at=T0,
            recorded_at=T0,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        with self.database.unit_of_work() as uow:
            self.thread_store.apply(uow, ThreadEventMutation(0, thread, event))
            uow.commit()
        return thread

    def _create_agent(self, agent_id: str, *, thread_id: str = "thread-a"):
        return self.agents.create_agent(
            agent_instance_id=agent_id,
            agent_session_id=f"session-{agent_id}",
            scope_id="scope-a",
            thread_ref=ScopedRef("scope-a", "core:thread", thread_id, 1),
            profile_ref=ScopedRef(
                "scope-a",
                "core:agent_profile",
                f"profile-{agent_id}",
                1,
            ),
            principal_id=f"principal-{agent_id}",
            created_at=T0,
        )

    @staticmethod
    def _agent_ref(agent_id: str) -> ScopedRef:
        return ScopedRef("scope-a", "core:agent_instance", agent_id, 1)

    def message(
        self,
        index: int,
        recipient_id: str,
        *,
        sender_id: str = "sender",
        message_id: str | None = None,
        body: str | None = None,
        recipients: tuple[ScopedRef, ...] | None = None,
        thread_id: str = "thread-a",
    ) -> Message:
        return Message(
            message_id=message_id or f"message-{recipient_id}-{index}",
            scope_id="scope-a",
            thread_ref=ScopedRef("scope-a", "core:thread", thread_id, 1),
            turn_ref=ScopedRef("scope-a", "core:turn", "turn-1", 1),
            sequence=index,
            sender_ref=self._agent_ref(sender_id),
            recipient_refs=(
                recipients
                if recipients is not None
                else (self._agent_ref(recipient_id),)
            ),
            kind="core:agent_work",
            body=body or f"work-{index}",
            created_at=T0,
        )

    def send(
        self,
        index: int,
        recipient_id: str = "agent-a",
        *,
        enqueued_at: str = T1,
    ):
        return self.mailbox.send_message(
            self.message(index, recipient_id),
            recipient_agent_instance_id=recipient_id,
            recipient_agent_session_id=f"session-{recipient_id}",
            enqueued_at=enqueued_at,
        )

    def test_send_is_idempotent_fifo_cursor_and_reopens(self) -> None:
        first_message = self.message(1, "agent-a")
        result, first = self.mailbox.send_message(
            first_message,
            recipient_agent_instance_id="agent-a",
            recipient_agent_session_id="session-agent-a",
            enqueued_at=T1,
        )
        self.assertIs(result, MailboxSendResult.ENQUEUED)
        duplicate_result, duplicate = self.mailbox.send_message(
            first_message,
            recipient_agent_instance_id="agent-a",
            recipient_agent_session_id="session-agent-a",
            enqueued_at=T1,
        )
        self.assertIs(duplicate_result, MailboxSendResult.ALREADY_ENQUEUED)
        self.assertEqual(duplicate, first)
        self.send(2)
        self.send(3)

        queued = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual([item.mailbox_sequence for item in queued], [1, 2, 3])
        self.assertFalse(any(item.consumed for item in queued))
        received = self.mailbox.receive_next(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            consumed_at=T2,
        )
        self.assertEqual(received.mailbox_sequence, 1)

        reopened_database = self._database(self.path)
        reopened_database.initialize()
        reopened = MailboxManager(reopened_database)
        cursor = reopened.get_cursor(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual(cursor.last_enqueued_sequence, 3)
        self.assertEqual(cursor.consume_cursor, 1)
        reopened_rows = reopened.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual([item.consumed for item in reopened_rows], [True, False, False])
        reopened_database.verify_integrity()

    def test_same_agent_lane_is_serial_fifo_and_reuses_future(self) -> None:
        for index in (1, 2, 3):
            self.send(index)
        first_started = threading.Event()
        release_first = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        order: list[int] = []

        def handler(delivery):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                order.append(delivery.mailbox_sequence)
            if delivery.mailbox_sequence == 1:
                first_started.set()
                self.assertTrue(release_first.wait(timeout=2))
            with lock:
                active -= 1

        with AgentLaneRuntime(self.mailbox, max_workers=3) as lanes:
            future = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=handler,
            )
            self.assertTrue(first_started.wait(timeout=2))
            duplicate = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=handler,
            )
            self.assertIs(duplicate, future)
            self.assertEqual(order, [1])
            release_first.set()
            self.assertEqual(future.result(timeout=2), 3)

        self.assertEqual(order, [1, 2, 3])
        self.assertEqual(maximum_active, 1)

    def test_different_agent_lanes_run_in_parallel_without_sleep(self) -> None:
        self.send(1, "agent-a")
        self.send(1, "agent-b")
        barrier = threading.Barrier(3, timeout=2)
        thread_names: list[str] = []
        lock = threading.Lock()

        def handler(delivery):
            with lock:
                thread_names.append(threading.current_thread().name)
            barrier.wait()

        with AgentLaneRuntime(self.mailbox, max_workers=2) as lanes:
            future_a = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=handler,
            )
            future_b = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-b",
                agent_session_id="session-agent-b",
                handler=handler,
            )
            barrier.wait()
            self.assertEqual(future_a.result(timeout=2), 1)
            self.assertEqual(future_b.result(timeout=2), 1)

        self.assertEqual(len(thread_names), 2)
        self.assertEqual(len(set(thread_names)), 2)

    def test_pause_resume_close_controls_delivery_and_claiming(self) -> None:
        paused = self.agents.pause_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=1,
            updated_at=T1,
        )
        self.assertEqual(paused.state.value, "paused")
        self.send(1, "agent-a", enqueued_at=T2)
        with self.assertRaises(AgentPausedError):
            self.mailbox.receive_next(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                consumed_at=T3,
            )
        with AgentLaneRuntime(self.mailbox) as lanes:
            with self.assertRaises(AgentPausedError):
                lanes.schedule(
                    scope_id="scope-a",
                    thread_id="thread-a",
                    agent_instance_id="agent-a",
                    agent_session_id="session-agent-a",
                    handler=lambda delivery: None,
                )

        self.agents.resume_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=2,
            updated_at=T3,
        )
        processed = []
        with AgentLaneRuntime(self.mailbox) as lanes:
            future = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=lambda delivery: processed.append(delivery.message.message_id),
            )
            self.assertEqual(future.result(timeout=2), 1)
        self.assertEqual(processed, ["message-agent-a-1"])

        self.agents.close_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=3,
            updated_at=T4,
        )
        with self.assertRaises(AgentClosedError):
            self.send(2, "agent-a", enqueued_at=T4)
        with AgentLaneRuntime(self.mailbox) as lanes:
            with self.assertRaises(AgentClosedError):
                lanes.schedule(
                    scope_id="scope-a",
                    thread_id="thread-a",
                    agent_instance_id="agent-a",
                    agent_session_id="session-agent-a",
                    handler=lambda delivery: None,
                )

    def test_close_during_handler_prevents_next_claim(self) -> None:
        self.send(1)
        self.send(2)
        first_started = threading.Event()
        release_first = threading.Event()
        seen: list[int] = []

        def handler(delivery):
            seen.append(delivery.mailbox_sequence)
            if delivery.mailbox_sequence == 1:
                first_started.set()
                self.assertTrue(release_first.wait(timeout=2))

        with AgentLaneRuntime(self.mailbox) as lanes:
            future = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=handler,
            )
            self.assertTrue(first_started.wait(timeout=2))
            self.agents.close_agent(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                expected_session_version=1,
                updated_at=T2,
            )
            release_first.set()
            self.assertEqual(future.result(timeout=2), 1)

        self.assertEqual(seen, [1])
        rows = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual([item.consumed for item in rows], [True, False])

    def test_conflict_cross_boundary_and_multi_recipient_fail_closed(self) -> None:
        original = self.message(1, "agent-a", message_id="same-message")
        self.mailbox.send_message(
            original,
            recipient_agent_instance_id="agent-a",
            recipient_agent_session_id="session-agent-a",
            enqueued_at=T1,
        )
        with self.assertRaises(MailboxConflictError):
            self.mailbox.send_message(
                self.message(
                    1,
                    "agent-a",
                    message_id="same-message",
                    body="different",
                ),
                recipient_agent_instance_id="agent-a",
                recipient_agent_session_id="session-agent-a",
                enqueued_at=T1,
            )
        with self.assertRaises(MailboxValidationError):
            self.mailbox.send_message(
                self.message(
                    2,
                    "agent-a",
                    recipients=(
                        self._agent_ref("agent-a"),
                        self._agent_ref("agent-b"),
                    ),
                ),
                recipient_agent_instance_id="agent-a",
                recipient_agent_session_id="session-agent-a",
                enqueued_at=T1,
            )
        with self.assertRaises(AgentStoreAccessError):
            self.mailbox.send_message(
                self.message(3, "agent-a", sender_id="other-thread"),
                recipient_agent_instance_id="agent-a",
                recipient_agent_session_id="session-agent-a",
                enqueued_at=T1,
            )
        with self.assertRaises(AgentStoreAccessError):
            self.mailbox.list_mailbox(
                scope_id="scope-a",
                thread_id="thread-b",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
            )

    def test_transaction_rollback_and_public_uow_dml_rejection(self) -> None:
        store = SQLiteMailboxStore(self.database)
        message = self.message(1, "agent-a")
        with self.database.unit_of_work() as uow:
            store.send(
                uow,
                message=message,
                recipient_agent_instance_id="agent-a",
                recipient_agent_session_id="session-agent-a",
                enqueued_at=T1,
            )

        self.assertEqual(
            self.mailbox.list_mailbox(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
            ),
            (),
        )
        self.send(1)
        with self.database.unit_of_work() as uow:
            with self.assertRaises(RuntimeUnitOfWorkStateError):
                uow.execute(
                    """UPDATE runtime_agent_messages SET consumed_at = ?
                       WHERE message_id = ?""",
                    (T2, "message-agent-a-1"),
                )
        with sqlite3.connect(str(self.path)) as connection:
            consumed = connection.execute(
                """SELECT consumed_at FROM runtime_agent_messages
                   WHERE message_id = 'message-agent-a-1'"""
            ).fetchone()[0]
        self.assertEqual(consumed, "")

    def test_v4_agent_data_upgrades_through_v7_without_loss(self) -> None:
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE runtime_agent_execution_results")
            connection.execute("DROP TABLE runtime_agent_execution_states")
            connection.execute("DROP TABLE runtime_role_assignments")
            connection.execute("DROP INDEX runtime_agent_messages_pending_idx")
            connection.execute("DROP TABLE runtime_agent_messages")
            connection.execute("DROP TABLE runtime_agent_mailbox_cursors")
            connection.execute(
                """DELETE FROM runtime_schema_migrations
                   WHERE component = 'runtime_kernel' AND schema_version >= 5"""
            )
            connection.execute(
                """UPDATE runtime_schema_metadata SET schema_version = 4
                   WHERE component = 'runtime_kernel' AND schema_version = 7"""
            )

        upgraded = self._database(self.path)
        upgraded.initialize()
        self.assertEqual(upgraded.schema_version(), 7)
        upgraded_agents = AgentManager(upgraded)
        self.assertIsNotNone(
            upgraded_agents.get_agent("scope-a", "thread-a", "agent-a")
        )
        upgraded_mailbox = MailboxManager(upgraded)
        result, delivery = upgraded_mailbox.send_message(
            self.message(1, "agent-a"),
            recipient_agent_instance_id="agent-a",
            recipient_agent_session_id="session-agent-a",
            enqueued_at=T1,
        )
        self.assertIs(result, MailboxSendResult.ENQUEUED)
        self.assertEqual(delivery.mailbox_sequence, 1)

    def test_handler_failure_is_consumed_and_not_misreported_as_retry(self) -> None:
        self.send(1)

        def fail_handler(delivery):
            raise ValueError("handler-failed")

        with AgentLaneRuntime(self.mailbox) as lanes:
            failed = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=fail_handler,
            )
            with self.assertRaisesRegex(ValueError, "handler-failed"):
                failed.result(timeout=2)
            retry = lanes.schedule(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                handler=lambda delivery: self.fail("must not redeliver"),
            )
            self.assertEqual(retry.result(timeout=2), 0)

        cursor = self.mailbox.get_cursor(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual(cursor.consume_cursor, 1)


if __name__ == "__main__":
    unittest.main()
