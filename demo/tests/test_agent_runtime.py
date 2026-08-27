from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from coding_workflow.agent_runtime import AgentManager
from coding_workflow.runtime_domain import (
    AgentInstance,
    AgentSession,
    AgentSessionState,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    AgentClosedError,
    AgentCreateResult,
    AgentPausedError,
    AgentStateTransitionError,
    AgentStoreAccessError,
    AgentStoreConflictError,
    AgentStoreValidationError,
    OutboxPolicy,
    RuntimeSQLiteConfig,
    RuntimeUnitOfWorkStateError,
    SQLiteAgentStore,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


T0 = "2026-08-27T00:00:00+00:00"
T1 = "2026-08-27T00:01:00+00:00"
T2 = "2026-08-27T00:02:00+00:00"
T3 = "2026-08-27T00:03:00+00:00"
T4 = "2026-08-27T00:04:00+00:00"


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "runtime.sqlite3"
        self.database = self._database(self.path)
        self.database.initialize()
        self.thread_store = SQLiteThreadEventStore(self.database)
        self._create_thread("thread-a")
        self._create_thread("thread-b")
        self.manager = AgentManager(self.database)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _database(path: Path) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/agent-tests-v1",
                destination="core:runtime_events",
                expected_sink_id="core:agent-test-sink",
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

    @staticmethod
    def _profile(agent_id: str) -> ScopedRef:
        return ScopedRef("scope-a", "core:agent_profile", f"profile-{agent_id}", 1)

    @staticmethod
    def _thread_ref(thread_id: str) -> ScopedRef:
        return ScopedRef("scope-a", "core:thread", thread_id, 1)

    def _create_agent(
        self,
        agent_id: str = "agent-a",
        *,
        session_id: str | None = None,
        thread_id: str = "thread-a",
        created_at: str = T0,
    ):
        return self.manager.create_agent(
            agent_instance_id=agent_id,
            agent_session_id=session_id or f"session-{agent_id}",
            scope_id="scope-a",
            thread_ref=self._thread_ref(thread_id),
            profile_ref=self._profile(agent_id),
            principal_id=f"principal-{agent_id}",
            created_at=created_at,
        )

    def test_create_is_idempotent_and_reopens_with_private_state(self) -> None:
        created = self._create_agent()
        duplicate = self._create_agent()
        self.assertEqual(duplicate, created)

        state = self.manager.write_private_state(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            value={
                "goal": "implement store",
                "steps": ["create", "verify"],
                "artifact_refs": ["artifact-1"],
            },
            expected_version=0,
            updated_at=T1,
        )
        self.assertEqual(state.version, 1)

        reopened_database = self._database(self.path)
        reopened_database.initialize()
        reopened = AgentManager(reopened_database)
        self.assertEqual(
            reopened.get_agent("scope-a", "thread-a", "agent-a"),
            created,
        )
        reopened_state = reopened.read_private_state(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertIsNotNone(reopened_state)
        self.assertEqual(reopened_state, state)
        reopened_database.verify_integrity()

    def test_conflicting_duplicate_identity_is_rejected_without_mutation(self) -> None:
        original = self._create_agent()

        with self.assertRaises(AgentStoreConflictError):
            self.manager.create_agent(
                agent_instance_id="agent-a",
                agent_session_id="different-session",
                scope_id="scope-a",
                thread_ref=self._thread_ref("thread-a"),
                profile_ref=self._profile("different"),
                principal_id="different-principal",
                created_at=T0,
            )

        self.assertEqual(
            self.manager.get_agent("scope-a", "thread-a", "agent-a"),
            original,
        )

    def test_pause_resume_close_and_invalid_transitions_fail_closed(self) -> None:
        self._create_agent()
        paused = self.manager.pause_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=1,
            updated_at=T1,
        )
        self.assertEqual(paused.state, AgentSessionState.PAUSED)
        with self.assertRaises(AgentPausedError):
            self.manager.require_work_admission("scope-a", "thread-a", "agent-a")
        with self.assertRaises(AgentStoreValidationError):
            self.manager.resume_agent(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                expected_session_version=2,
                updated_at=T0,
            )

        resumed = self.manager.resume_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=2,
            updated_at=T2,
        )
        self.assertEqual(resumed.state, AgentSessionState.ACTIVE)
        self.manager.require_work_admission("scope-a", "thread-a", "agent-a")
        with self.assertRaises(AgentStateTransitionError):
            self.manager.resume_agent(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                expected_session_version=3,
                updated_at=T3,
            )
        self.assertEqual(
            self.manager.get_agent("scope-a", "thread-a", "agent-a").session,
            resumed,
        )

        closed = self.manager.close_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            expected_session_version=3,
            updated_at=T3,
        )
        self.assertEqual(closed.state, AgentSessionState.CLOSED)
        self.assertEqual(closed.closed_at, T3)
        with self.assertRaises(AgentClosedError):
            self.manager.require_work_admission("scope-a", "thread-a", "agent-a")
        with self.assertRaises(AgentClosedError):
            self.manager.write_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                value={"late": True},
                expected_version=0,
                updated_at=T4,
            )

    def test_cross_thread_and_cross_agent_private_reads_are_rejected(self) -> None:
        self._create_agent("agent-a")
        self._create_agent("agent-b")
        self.manager.write_private_state(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            value={"secret": "agent-a-only"},
            expected_version=0,
            updated_at=T1,
        )

        with self.assertRaises(AgentStoreAccessError):
            self.manager.get_agent("scope-a", "thread-b", "agent-a")
        with self.assertRaises(AgentStoreAccessError):
            self.manager.read_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                requesting_agent_instance_id="agent-b",
                requesting_agent_session_id="session-agent-b",
            )
        with self.assertRaises(AgentStoreAccessError):
            self.manager.write_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                requesting_agent_instance_id="agent-b",
                requesting_agent_session_id="session-agent-b",
                value={"tampered": True},
                expected_version=1,
                updated_at=T2,
            )
        with self.assertRaises(AgentStoreValidationError):
            self.manager.read_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                requesting_agent_instance_id="",
            )

        state = self.manager.read_private_state(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
        )
        self.assertEqual(dict(state.value), {"secret": "agent-a-only"})

    def test_store_transaction_without_commit_rolls_back_agent_and_state(self) -> None:
        store = SQLiteAgentStore(self.database)
        instance = AgentInstance(
            agent_instance_id="agent-rollback",
            scope_id="scope-a",
            thread_ref=self._thread_ref("thread-a"),
            profile_ref=self._profile("rollback"),
            principal_id="principal-rollback",
            created_at=T0,
        )
        session = AgentSession(
            agent_session_id="session-rollback",
            scope_id="scope-a",
            thread_ref=self._thread_ref("thread-a"),
            agent_instance_ref=instance.reference,
            created_at=T0,
            updated_at=T0,
        )

        with self.database.unit_of_work() as uow:
            self.assertIs(
                store.create(uow, instance, session),
                AgentCreateResult.CREATED,
            )
            store.write_private_state(
                uow,
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-rollback",
                agent_session_id="session-rollback",
                requesting_agent_instance_id="agent-rollback",
                requesting_agent_session_id="session-rollback",
                value={"uncommitted": True},
                expected_version=0,
                updated_at=T1,
            )

        self.assertIsNone(
            store.get_agent("scope-a", "thread-a", "agent-rollback")
        )
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM runtime_agent_private_state
                       WHERE agent_instance_id = 'agent-rollback'"""
                ).fetchone()[0],
                0,
            )

    def test_private_state_uses_cas_and_rejects_non_json_values(self) -> None:
        self._create_agent()
        first = self.manager.write_private_state(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="agent-a",
            agent_session_id="session-agent-a",
            value={"step": 1},
            expected_version=0,
            updated_at=T1,
        )
        self.assertEqual(first.version, 1)
        with self.assertRaises(AgentStoreConflictError):
            self.manager.write_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                value={"step": 2},
                expected_version=0,
                updated_at=T2,
            )
        with self.assertRaises(AgentStoreValidationError):
            self.manager.write_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                value={"step": 2},
                expected_version=1,
                updated_at=T0,
            )
        with self.assertRaises(AgentStoreValidationError):
            self.manager.write_private_state(
                scope_id="scope-a",
                thread_id="thread-a",
                agent_instance_id="agent-a",
                agent_session_id="session-agent-a",
                value={"unsafe": object()},
                expected_version=1,
                updated_at=T2,
            )
        self.assertEqual(
            dict(
                self.manager.read_private_state(
                    scope_id="scope-a",
                    thread_id="thread-a",
                    agent_instance_id="agent-a",
                    agent_session_id="session-agent-a",
                ).value
            ),
            {"step": 1},
        )

    def test_create_requires_authoritative_thread_and_single_session(self) -> None:
        with self.assertRaises(AgentStoreValidationError):
            self._create_agent("orphan", thread_id="missing-thread")
        self._create_agent()
        with self.assertRaises(AgentStoreConflictError):
            self.manager.create_agent(
                agent_instance_id="agent-a",
                agent_session_id="session-second",
                scope_id="scope-a",
                thread_ref=self._thread_ref("thread-a"),
                profile_ref=self._profile("agent-a"),
                principal_id="principal-agent-a",
                created_at=T0,
            )

    def test_public_uow_cannot_bypass_agent_store_writes(self) -> None:
        self._create_agent()

        with self.database.unit_of_work() as uow:
            with self.assertRaises(RuntimeUnitOfWorkStateError):
                uow.execute(
                    """UPDATE runtime_agent_sessions SET state = 'closed'
                       WHERE agent_instance_id = 'agent-a'"""
                )

        record = self.manager.get_agent("scope-a", "thread-a", "agent-a")
        self.assertEqual(record.session.state, AgentSessionState.ACTIVE)


if __name__ == "__main__":
    unittest.main()
