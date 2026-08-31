from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from coding_workflow.agent_runtime import AgentManager, MailboxManager
from coding_workflow.role_assignment import (
    AssignmentRecordResult,
    RoleAssignmentManager,
    RoleAssignmentScheduler,
)
from coding_workflow.runtime_domain import (
    AgentAvailability,
    AgentCandidate,
    AgentProfile,
    AssignmentDecision,
    AssignmentRisk,
    RoleAssignmentPolicy,
    RoleRequirement,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
    Message,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    MailboxValidationError,
    RoleAssignmentStoreConflictError,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


T0 = "2026-08-28T00:00:00+00:00"


def ref(entity_type: str, entity_id: str, *, scope_id: str = "scope-a") -> ScopedRef:
    return ScopedRef(scope_id, entity_type, entity_id, 1)


class RoleAssignmentDomainTests(unittest.TestCase):
    def test_agent_profile_can_be_role_neutral_without_breaking_legacy_payloads(self) -> None:
        neutral = AgentProfile(
            profile_id="profile-flexible",
            scope_id="scope-a",
            backend_policy_ref=ref("core:backend_policy", "deepseek-primary"),
            created_at=T0,
        )
        neutral_payload = json.loads(json.dumps(dict(neutral.to_dict())))

        self.assertIsNone(neutral.role_ref)
        self.assertIsNone(neutral_payload["role_ref"])
        self.assertEqual(AgentProfile.from_dict(neutral_payload), neutral)

        legacy = AgentProfile(
            profile_id="profile-legacy",
            scope_id="scope-a",
            role_ref=ref("core:agent_role", "collaboration:reviewer"),
            created_at=T0,
        )
        legacy_payload = json.loads(json.dumps(dict(legacy.to_dict())))
        self.assertEqual(AgentProfile.from_dict(legacy_payload), legacy)

    def test_busy_best_falls_back_only_to_an_eligible_second_agent(self) -> None:
        requirement = RoleRequirement(
            requirement_id="requirement-1",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            work_ref=ref("core:invocation", "invocation-1"),
            role_ref=ref("core:agent_role", "collaboration:reviewer"),
            required_capabilities=("core:code_review",),
            created_at=T0,
        )
        candidates = (
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-best"),
                agent_session_ref=ref("core:agent_session", "session-best"),
                profile_ref=ref("core:agent_profile", "profile-best"),
                capabilities=("core:code_review",),
                availability=AgentAvailability.BUSY,
                estimated_wait_seconds=30,
                affinity_score=100,
                quality_score=95,
            ),
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-second"),
                agent_session_ref=ref("core:agent_session", "session-second"),
                profile_ref=ref("core:agent_profile", "profile-second"),
                capabilities=("core:code_review",),
                availability=AgentAvailability.AVAILABLE,
                affinity_score=90,
                quality_score=90,
            ),
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-invalid"),
                agent_session_ref=ref("core:agent_session", "session-invalid"),
                profile_ref=ref("core:agent_profile", "profile-invalid"),
                capabilities=("core:planning",),
                provider_healthy=False,
                availability=AgentAvailability.AVAILABLE,
                affinity_score=99,
                quality_score=99,
            ),
        )

        assignment = RoleAssignmentScheduler().decide(
            assignment_id="assignment-1",
            requirement=requirement,
            candidates=candidates,
            policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/test-v1",
                max_wait_for_best_seconds=60,
            ),
            created_at=T0,
        )

        self.assertIs(assignment.decision, AssignmentDecision.ASSIGNED)
        self.assertEqual(
            assignment.selected_agent_instance_ref.entity_id,
            "agent-second",
        )
        self.assertEqual(assignment.reason_code, "best_busy_eligible_fallback")
        invalid = next(
            item
            for item in assignment.candidate_evaluations
            if item.agent_instance_ref.entity_id == "agent-invalid"
        )
        self.assertFalse(invalid.eligible)
        self.assertEqual(
            invalid.rejection_codes,
            ("missing_capability:core:code_review", "provider_unhealthy"),
        )

    def test_high_risk_waits_for_best_and_expired_wait_needs_input(self) -> None:
        requirement = RoleRequirement(
            requirement_id="requirement-high-risk",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            work_ref=ref("core:invocation", "invocation-high-risk"),
            role_ref=ref("core:agent_role", "collaboration:reviewer"),
            required_capabilities=("core:code_review",),
            risk=AssignmentRisk.HIGH,
            created_at=T0,
        )
        candidates = (
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-best"),
                agent_session_ref=ref("core:agent_session", "session-best"),
                profile_ref=ref("core:agent_profile", "profile-best"),
                capabilities=("core:code_review",),
                availability=AgentAvailability.BUSY,
                estimated_wait_seconds=30,
                affinity_score=100,
            ),
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-second"),
                agent_session_ref=ref("core:agent_session", "session-second"),
                profile_ref=ref("core:agent_profile", "profile-second"),
                capabilities=("core:code_review",),
                affinity_score=90,
            ),
        )
        scheduler = RoleAssignmentScheduler()

        waiting = scheduler.decide(
            assignment_id="assignment-waiting",
            requirement=requirement,
            candidates=candidates,
            policy=RoleAssignmentPolicy("policy/v1", 30),
            created_at=T0,
        )
        needs_input = scheduler.decide(
            assignment_id="assignment-needs-input",
            requirement=requirement,
            candidates=candidates,
            policy=RoleAssignmentPolicy("policy/v1", 29),
            created_at=T0,
        )

        self.assertIs(waiting.decision, AssignmentDecision.WAITING)
        self.assertEqual(waiting.reason_code, "waiting_for_best")
        self.assertIs(needs_input.decision, AssignmentDecision.NEEDS_INPUT)
        self.assertEqual(needs_input.reason_code, "best_wait_exceeds_policy")

    def test_equal_candidates_use_agent_id_as_a_stable_final_tiebreak(self) -> None:
        requirement = RoleRequirement(
            requirement_id="requirement-tie",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            work_ref=ref("core:invocation", "invocation-tie"),
            role_ref=ref("core:agent_role", "collaboration:reviewer"),
            created_at=T0,
        )
        agent_a = AgentCandidate(
            agent_instance_ref=ref("core:agent_instance", "agent-a"),
            agent_session_ref=ref("core:agent_session", "session-a"),
            profile_ref=ref("core:agent_profile", "profile-a"),
        )
        agent_b = AgentCandidate(
            agent_instance_ref=ref("core:agent_instance", "agent-b"),
            agent_session_ref=ref("core:agent_session", "session-b"),
            profile_ref=ref("core:agent_profile", "profile-b"),
        )
        scheduler = RoleAssignmentScheduler()
        policy = RoleAssignmentPolicy("policy/v1", 0)

        forward = scheduler.decide(
            assignment_id="assignment-forward",
            requirement=requirement,
            candidates=(agent_b, agent_a),
            policy=policy,
            created_at=T0,
        )
        reverse = scheduler.decide(
            assignment_id="assignment-reverse",
            requirement=requirement,
            candidates=(agent_a, agent_b),
            policy=policy,
            created_at=T0,
        )

        self.assertEqual(forward.selected_agent_instance_ref.entity_id, "agent-a")
        self.assertEqual(reverse.selected_agent_instance_ref.entity_id, "agent-a")


class RoleAssignmentPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.path = Path(self._temporary.name) / "runtime.sqlite3"
        self.database = self._database(self.path)
        self.database.initialize()
        self._create_thread()
        agents = AgentManager(self.database)
        for agent_id in ("sender", "agent-best", "agent-second"):
            agents.create_agent(
                agent_instance_id=agent_id,
                agent_session_id=f"session-{agent_id}",
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                profile_ref=ref("core:agent_profile", f"profile-{agent_id}"),
                principal_id=f"principal-{agent_id}",
                created_at=T0,
            )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _database(path: Path) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/role-assignment-tests-v1",
                destination="core:runtime_events",
                expected_sink_id="core:role-assignment-test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000,),
            ),
        )

    def _create_thread(self) -> None:
        participant = ref("core:principal", "user-1")
        thread = Thread(
            thread_id="thread-1",
            scope_id="scope-a",
            title="Role Assignment",
            participant_refs=(participant,),
            created_at=T0,
            updated_at=T0,
        )
        event = RuntimeEvent(
            scope_id="scope-a",
            event_id="event-thread-1",
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id="trace-thread-1",
            correlation_id="correlation-thread-1",
            actor_type=RuntimeActorType.USER,
            actor_ref=participant,
            idempotency_key="idem-thread-1",
            occurred_at=T0,
            recorded_at=T0,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        store = SQLiteThreadEventStore(self.database)
        with self.database.unit_of_work() as uow:
            store.apply(uow, ThreadEventMutation(0, thread, event))
            uow.commit()

    @staticmethod
    def _assignment():
        requirement = RoleRequirement(
            requirement_id="requirement-1",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            work_ref=ref("core:invocation", "invocation-1"),
            role_ref=ref("core:agent_role", "collaboration:reviewer"),
            required_capabilities=("core:code_review",),
            created_at=T0,
        )
        candidates = (
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-best"),
                agent_session_ref=ref("core:agent_session", "session-agent-best"),
                profile_ref=ref("core:agent_profile", "profile-agent-best"),
                capabilities=("core:code_review",),
                availability=AgentAvailability.BUSY,
                estimated_wait_seconds=30,
                affinity_score=100,
            ),
            AgentCandidate(
                agent_instance_ref=ref("core:agent_instance", "agent-second"),
                agent_session_ref=ref("core:agent_session", "session-agent-second"),
                profile_ref=ref("core:agent_profile", "profile-agent-second"),
                capabilities=("core:code_review",),
                affinity_score=90,
            ),
        )
        return RoleAssignmentScheduler().decide(
            assignment_id="assignment-1",
            requirement=requirement,
            candidates=candidates,
            policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/test-v1",
                max_wait_for_best_seconds=60,
            ),
            created_at=T0,
        )

    def test_assignment_is_idempotent_and_reopens_through_the_public_manager(self) -> None:
        assignment = self._assignment()
        manager = RoleAssignmentManager(self.database)

        self.assertIs(manager.record(assignment), AssignmentRecordResult.CREATED)
        self.assertIs(
            manager.record(assignment),
            AssignmentRecordResult.ALREADY_RECORDED,
        )

        reopened_database = self._database(self.path)
        reopened_database.initialize()
        reopened = RoleAssignmentManager(reopened_database).get_assignment(
            scope_id="scope-a",
            thread_id="thread-1",
            assignment_id="assignment-1",
        )
        self.assertEqual(reopened, assignment)
        reopened_database.verify_integrity()

    @staticmethod
    def _message(*, created_at: str = T0) -> Message:
        return Message(
            message_id="message-assignment-1",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-1"),
            sequence=1,
            sender_ref=ref("core:agent_instance", "sender"),
            recipient_refs=(ref("core:agent_instance", "agent-second"),),
            kind="core:assigned_work",
            body="review this artifact",
            created_at=created_at,
        )

    def test_assignment_and_mailbox_delivery_commit_or_rollback_together(self) -> None:
        assignment = self._assignment()
        manager = RoleAssignmentManager(self.database)

        record_result, send_result, delivery = manager.record_and_enqueue(
            assignment,
            self._message(),
            enqueued_at="2026-08-28T00:00:01+00:00",
        )
        self.assertIs(record_result, AssignmentRecordResult.CREATED)
        self.assertEqual(send_result.value, "enqueued")
        self.assertEqual(delivery.recipient_agent_instance_id, "agent-second")
        self.assertEqual(
            len(
                MailboxManager(self.database).list_mailbox(
                    scope_id="scope-a",
                    thread_id="thread-1",
                    agent_instance_id="agent-second",
                    agent_session_id="session-agent-second",
                )
            ),
            1,
        )

        failed_assignment = RoleAssignmentScheduler().decide(
            assignment_id="assignment-failed",
            requirement=RoleRequirement(
                requirement_id="requirement-failed",
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                work_ref=ref("core:invocation", "invocation-failed"),
                role_ref=ref("core:agent_role", "collaboration:reviewer"),
                required_capabilities=("core:code_review",),
                created_at=T0,
            ),
            candidates=(
                AgentCandidate(
                    agent_instance_ref=ref("core:agent_instance", "agent-second"),
                    agent_session_ref=ref("core:agent_session", "session-agent-second"),
                    profile_ref=ref("core:agent_profile", "profile-agent-second"),
                    capabilities=("core:code_review",),
                ),
            ),
            policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/test-v1",
                max_wait_for_best_seconds=60,
            ),
            created_at=T0,
        )
        late_message = Message(
            message_id="message-assignment-failed",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-1"),
            sequence=2,
            sender_ref=ref("core:agent_instance", "sender"),
            recipient_refs=(ref("core:agent_instance", "agent-second"),),
            kind="core:assigned_work",
            body="must rollback",
            created_at="2026-08-28T00:00:03+00:00",
        )
        with self.assertRaisesRegex(MailboxValidationError, "enqueued_at"):
            manager.record_and_enqueue(
                failed_assignment,
                late_message,
                enqueued_at="2026-08-28T00:00:02+00:00",
            )
        self.assertIsNone(
            manager.get_assignment(
                scope_id="scope-a",
                thread_id="thread-1",
                assignment_id="assignment-failed",
            )
        )

    def test_reassignment_requires_a_new_generation_and_supersedes_reference(self) -> None:
        first = self._assignment()
        manager = RoleAssignmentManager(self.database)
        self.assertIs(manager.record(first), AssignmentRecordResult.CREATED)

        silent_replacement = replace(first, assignment_id="assignment-silent")
        with self.assertRaisesRegex(RoleAssignmentStoreConflictError, "generation"):
            manager.record(silent_replacement)

        explicit_reassignment = replace(
            first,
            assignment_id="assignment-2",
            generation=2,
            supersedes_ref=first.reference,
        )
        self.assertIs(
            manager.record(explicit_reassignment),
            AssignmentRecordResult.CREATED,
        )
        self.assertEqual(
            [
                item.assignment_id
                for item in manager.list_assignments(
                    scope_id="scope-a",
                    thread_id="thread-1",
                )
            ],
            ["assignment-1", "assignment-2"],
        )

    def test_two_writers_cannot_create_the_same_work_role_generation(self) -> None:
        first = self._assignment()
        second = replace(first, assignment_id="assignment-racing")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def write(assignment) -> None:
            barrier.wait()
            try:
                result = RoleAssignmentManager(self.database).record(assignment)
                outcome = result.value
            except Exception as exc:
                outcome = type(exc).__name__
            with lock:
                outcomes.append(outcome)

        threads = (
            threading.Thread(target=write, args=(first,)),
            threading.Thread(target=write, args=(second,)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(outcomes.count("RoleAssignmentStoreConflictError"), 1)

    def test_selected_agent_is_revalidated_at_commit_time(self) -> None:
        assignment = self._assignment()
        AgentManager(self.database).pause_agent(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="agent-second",
            agent_session_id="session-agent-second",
            expected_session_version=1,
            updated_at="2026-08-28T00:00:01+00:00",
        )

        with self.assertRaisesRegex(
            RoleAssignmentStoreConflictError,
            "active|paused|快照",
        ):
            RoleAssignmentManager(self.database).record(assignment)

        self.assertIsNone(
            RoleAssignmentManager(self.database).get_assignment(
                scope_id="scope-a",
                thread_id="thread-1",
                assignment_id="assignment-1",
            )
        )

    def test_assignment_rows_reject_sqlite_replace_semantics(self) -> None:
        assignment = self._assignment()
        RoleAssignmentManager(self.database).record(assignment)

        with sqlite3.connect(str(self.path)) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append|collision"):
                connection.execute(
                    """INSERT OR REPLACE INTO runtime_role_assignments
                       SELECT * FROM runtime_role_assignments
                       WHERE assignment_id = ?""",
                    (assignment.assignment_id,),
                )

        self.assertEqual(
            RoleAssignmentManager(self.database).get_assignment(
                scope_id="scope-a",
                thread_id="thread-1",
                assignment_id="assignment-1",
            ),
            assignment,
        )


if __name__ == "__main__":
    unittest.main()
