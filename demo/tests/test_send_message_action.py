from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_workflow.agent_actions import (
    AgentActionRunStatus,
    SendMessageActionContext,
    SendMessageActionRuntime,
)
from coding_workflow.agent_runtime import AgentManager, MailboxManager
from coding_workflow.model import (
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from coding_workflow.role_assignment import RoleAssignmentManager
from coding_workflow.runtime_domain import (
    AgentCandidate,
    RoleAssignmentPolicy,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
    ThreadState,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


T0 = "2026-08-29T00:00:00+00:00"
T1 = "2026-08-29T00:01:00+00:00"


def ref(entity_type: str, entity_id: str) -> ScopedRef:
    return ScopedRef("scope-a", entity_type, entity_id, 1)


class FakeSendMessageModel:
    capabilities = frozenset({
        ModelCapability.TEXT,
        ModelCapability.STRUCTURED_OUTPUT,
    })

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": "reviewer",
                "content": "请检查这份方案是否存在遗漏。",
            },
            provider="fake",
            model="fake-send-message",
            usage=ModelUsage(input_tokens=20, output_tokens=15, total_tokens=35),
            latency_ms=7,
        )

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        raise AssertionError("SEND_MESSAGE 纵切只使用 generate_structured")


class FakeRepairingSendMessageModel(FakeSendMessageModel):
    def __init__(self) -> None:
        self._responses = [
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": "reviewer",
                "unexpected": "不应该出现",
            },
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": "reviewer",
                "content": "修正后的评审请求。",
            },
        ]

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        if not self._responses:
            raise AssertionError("协议修正不得超过一次")
        return ModelResponse(
            self._responses.pop(0),
            provider="fake",
            model="fake-repairing-send-message",
        )


class FakeInvalidSendMessageModel(FakeSendMessageModel):
    def __init__(self) -> None:
        self._remaining = 2

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        if self._remaining == 0:
            raise AssertionError("第二次无效后必须终止")
        self._remaining -= 1
        return ModelResponse(
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": "reviewer",
            },
            provider="fake",
            model="fake-invalid-send-message",
        )


class FakeSingleUseSendMessageModel(FakeSendMessageModel):
    def __init__(self) -> None:
        self._used = False

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        if self._used:
            raise AssertionError("幂等重放不得再次调用模型")
        self._used = True
        return super().generate_structured(request)


class FakeNeverCalledSendMessageModel(FakeSendMessageModel):
    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("Thread 非 open 时不得调用模型")


class FakeSchemaAwareSendMessageModel(FakeSendMessageModel):
    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        allowed_roles = request.response_schema["properties"][
            "recipient_role"
        ]["enum"]
        if allowed_roles != ["planner", "reviewer"]:
            raise AssertionError(
                f"Provider 必须看到稳定的规范 Role ID: {allowed_roles!r}"
            )
        instructions = "\n".join(
            part.text
            for message in request.messages
            if message.role == "system"
            for part in message.content
            if hasattr(part, "text")
        )
        if '["planner","reviewer"]' not in instructions:
            raise AssertionError("Prompt 必须要求原样使用规范 Role ID")
        return ModelResponse(
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": "reviewer",
                "content": "请检查通信协议是否清晰。",
            },
            provider="fake",
            model="fake-schema-aware-send-message",
        )


class SendMessageActionVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(Path(self._temporary.name) / "runtime.sqlite3"),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/send-message-action-tests-v1",
                destination="core:runtime_events",
                expected_sink_id="core:send-message-action-test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000,),
            ),
        )
        self.database.initialize()
        self._create_thread()
        agents = AgentManager(self.database)
        agents.create_agent(
            agent_instance_id="planner-agent",
            agent_session_id="planner-session",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            profile_ref=ref("core:agent_profile", "planner-profile"),
            principal_id="planner-principal",
            created_at=T0,
        )
        agents.create_agent(
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            profile_ref=ref("core:agent_profile", "reviewer-profile"),
            principal_id="reviewer-principal",
            created_at=T0,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _create_thread(self) -> None:
        participant = ref("core:principal", "user-1")
        thread = Thread(
            thread_id="thread-1",
            scope_id="scope-a",
            title="SEND_MESSAGE v1",
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

    def _pause_thread(self) -> None:
        participant = ref("core:principal", "user-1")
        thread = Thread(
            thread_id="thread-1",
            scope_id="scope-a",
            title="SEND_MESSAGE v1",
            participant_refs=(participant,),
            state=ThreadState.PAUSED,
            version=2,
            created_at=T0,
            updated_at=T1,
        )
        event = RuntimeEvent(
            scope_id="scope-a",
            event_id="event-thread-1-paused",
            event_type="core:thread_paused",
            aggregate_ref=thread.reference,
            aggregate_version=2,
            sequence_no=2,
            trace_id="trace-thread-1-paused",
            correlation_id="correlation-thread-1",
            actor_type=RuntimeActorType.USER,
            actor_ref=participant,
            idempotency_key="idem-thread-1-paused",
            occurred_at=T1,
            recorded_at=T1,
            thread_ref=thread.reference,
            payload={"state": "paused", "previous_state": "open"},
        )
        store = SQLiteThreadEventStore(self.database)
        with self.database.unit_of_work() as uow:
            store.apply(uow, ThreadEventMutation(1, thread, event))
            uow.commit()

    def test_valid_model_action_is_persisted_and_visible_in_recipient_mailbox(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeSendMessageModel(),
            clock=lambda: T1,
        )
        context = SendMessageActionContext(
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-1"),
            invocation_ref=ref("core:invocation", "invocation-1"),
            sender_agent_instance_ref=ref(
                "core:agent_instance", "planner-agent"
            ),
            step_index=1,
        )
        candidates = {
            "reviewer": (
                AgentCandidate(
                    agent_instance_ref=ref(
                        "core:agent_instance", "reviewer-agent"
                    ),
                    agent_session_ref=ref(
                        "core:agent_session", "reviewer-session"
                    ),
                    profile_ref=ref(
                        "core:agent_profile", "reviewer-profile"
                    ),
                ),
            ),
        }

        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案。"},
            ]),
            context=context,
            role_candidates=candidates,
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.DELIVERED)
        self.assertEqual(result.message.body, "请检查这份方案是否存在遗漏。")
        self.assertEqual(result.message.kind, "core:agent_message")
        self.assertEqual(
            result.message.sender_ref,
            ref("core:agent_instance", "planner-agent"),
        )
        self.assertEqual(
            result.message.recipient_refs,
            (ref("core:agent_instance", "reviewer-agent"),),
        )
        self.assertEqual(
            result.message.causation_ref,
            ref("core:invocation", "invocation-1"),
        )

        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual(len(mailbox), 1)
        self.assertEqual(mailbox[0].message, result.message)
        self.assertFalse(mailbox[0].consumed)

    def test_invalid_model_action_is_repaired_once_before_delivery(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeRepairingSendMessageModel(),
            clock=lambda: T1,
        )
        context = SendMessageActionContext(
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-2"),
            invocation_ref=ref("core:invocation", "invocation-repair"),
            sender_agent_instance_ref=ref(
                "core:agent_instance", "planner-agent"
            ),
            step_index=1,
        )
        candidates = {
            "reviewer": (
                AgentCandidate(
                    agent_instance_ref=ref(
                        "core:agent_instance", "reviewer-agent"
                    ),
                    agent_session_ref=ref(
                        "core:agent_session", "reviewer-session"
                    ),
                    profile_ref=ref(
                        "core:agent_profile", "reviewer-profile"
                    ),
                ),
            ),
        }

        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案。"},
            ]),
            context=context,
            role_candidates=candidates,
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.DELIVERED)
        self.assertEqual(result.message.body, "修正后的评审请求。")
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.message for item in mailbox], [result.message])

    def test_second_invalid_action_ends_as_protocol_error_without_delivery(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeInvalidSendMessageModel(),
            clock=lambda: T1,
        )
        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案。"},
            ]),
            context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                turn_ref=ref("core:turn", "turn-protocol-error"),
                invocation_ref=ref(
                    "core:invocation", "invocation-protocol-error"
                ),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            role_candidates={
                "reviewer": (
                    AgentCandidate(
                        agent_instance_ref=ref(
                            "core:agent_instance", "reviewer-agent"
                        ),
                        agent_session_ref=ref(
                            "core:agent_session", "reviewer-session"
                        ),
                        profile_ref=ref(
                            "core:agent_profile", "reviewer-profile"
                        ),
                    ),
                ),
            },
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.PROTOCOL_ERROR)
        self.assertEqual(result.error_code, "invalid_action_after_repair")
        self.assertIsNone(result.assignment)
        self.assertIsNone(result.message)
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual(mailbox, ())

    def test_missing_eligible_role_candidate_is_persisted_as_needs_input(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeSendMessageModel(),
            clock=lambda: T1,
        )
        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案。"},
            ]),
            context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                turn_ref=ref("core:turn", "turn-no-candidate"),
                invocation_ref=ref("core:invocation", "invocation-no-candidate"),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            role_candidates={},
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.NEEDS_INPUT)
        self.assertEqual(result.error_code, "no_eligible_agent")
        self.assertEqual(result.assignment.reason_code, "no_eligible_agent")
        persisted = RoleAssignmentManager(self.database).get_assignment(
            scope_id="scope-a",
            thread_id="thread-1",
            assignment_id=result.assignment.assignment_id,
        )
        self.assertEqual(persisted, result.assignment)
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual(mailbox, ())

    def test_same_invocation_step_reuses_message_without_duplicate_delivery(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeSingleUseSendMessageModel(),
            clock=lambda: T1,
        )
        context = SendMessageActionContext(
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-idempotent"),
            invocation_ref=ref("core:invocation", "invocation-idempotent"),
            sender_agent_instance_ref=ref(
                "core:agent_instance", "planner-agent"
            ),
            step_index=1,
        )
        candidates = {
            "reviewer": (
                AgentCandidate(
                    agent_instance_ref=ref(
                        "core:agent_instance", "reviewer-agent"
                    ),
                    agent_session_ref=ref(
                        "core:agent_session", "reviewer-session"
                    ),
                    profile_ref=ref(
                        "core:agent_profile", "reviewer-profile"
                    ),
                ),
            ),
        }
        request = ModelRequest.from_text_messages([
            {"role": "user", "content": "请让 Reviewer 检查方案。"},
        ])
        policy = RoleAssignmentPolicy(
            policy_version="role-assignment-policy/send-message-v1",
            max_wait_for_best_seconds=0,
        )

        first = runtime.run(
            request=request,
            context=context,
            role_candidates=candidates,
            assignment_policy=policy,
        )
        replay = runtime.run(
            request=request,
            context=context,
            role_candidates=candidates,
            assignment_policy=policy,
        )

        self.assertIs(first.status, AgentActionRunStatus.DELIVERED)
        self.assertIs(replay.status, AgentActionRunStatus.DELIVERED)
        self.assertEqual(replay.message, first.message)
        self.assertIsNone(replay.model_response)
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.message for item in mailbox], [first.message])

    def test_same_invocation_step_rejects_a_different_request_as_conflict(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeSingleUseSendMessageModel(),
            clock=lambda: T1,
        )
        context = SendMessageActionContext(
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-idempotency-conflict"),
            invocation_ref=ref(
                "core:invocation", "invocation-idempotency-conflict"
            ),
            sender_agent_instance_ref=ref(
                "core:agent_instance", "planner-agent"
            ),
            step_index=1,
        )
        candidates = {
            "reviewer": (
                AgentCandidate(
                    agent_instance_ref=ref(
                        "core:agent_instance", "reviewer-agent"
                    ),
                    agent_session_ref=ref(
                        "core:agent_session", "reviewer-session"
                    ),
                    profile_ref=ref(
                        "core:agent_profile", "reviewer-profile"
                    ),
                ),
            ),
        }
        policy = RoleAssignmentPolicy(
            policy_version="role-assignment-policy/send-message-v1",
            max_wait_for_best_seconds=0,
        )

        first = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案A。"},
            ]),
            context=context,
            role_candidates=candidates,
            assignment_policy=policy,
        )
        conflict = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案B。"},
            ]),
            context=context,
            role_candidates=candidates,
            assignment_policy=policy,
        )

        self.assertIs(first.status, AgentActionRunStatus.DELIVERED)
        self.assertIs(conflict.status, AgentActionRunStatus.REJECTED)
        self.assertEqual(conflict.error_code, "idempotency_conflict")
        self.assertEqual(conflict.assignment, first.assignment)
        self.assertIsNone(conflict.model_response)
        self.assertIsNone(conflict.message)
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.message for item in mailbox], [first.message])

    def test_paused_thread_is_rejected_before_model_or_mailbox_side_effects(self) -> None:
        self._pause_thread()
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeNeverCalledSendMessageModel(),
            clock=lambda: T1,
        )

        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查方案。"},
            ]),
            context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                turn_ref=ref("core:turn", "turn-paused"),
                invocation_ref=ref("core:invocation", "invocation-paused"),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            role_candidates={},
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.REJECTED)
        self.assertEqual(result.error_code, "thread_paused")
        self.assertIsNone(result.assignment)
        self.assertIsNone(result.model_response)
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual(mailbox, ())

    def test_model_receives_current_canonical_role_ids_before_routing(self) -> None:
        runtime = SendMessageActionRuntime(
            self.database,
            model_client=FakeSchemaAwareSendMessageModel(),
            clock=lambda: T1,
        )
        result = runtime.run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请让 Reviewer 检查通信协议。"},
            ]),
            context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-1"),
                turn_ref=ref("core:turn", "turn-role-enum"),
                invocation_ref=ref("core:invocation", "invocation-role-enum"),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            role_candidates={
                "reviewer": (
                    AgentCandidate(
                        agent_instance_ref=ref(
                            "core:agent_instance", "reviewer-agent"
                        ),
                        agent_session_ref=ref(
                            "core:agent_session", "reviewer-session"
                        ),
                        profile_ref=ref(
                            "core:agent_profile", "reviewer-profile"
                        ),
                    ),
                ),
                "planner": (
                    AgentCandidate(
                        agent_instance_ref=ref(
                            "core:agent_instance", "planner-agent"
                        ),
                        agent_session_ref=ref(
                            "core:agent_session", "planner-session"
                        ),
                        profile_ref=ref(
                            "core:agent_profile", "planner-profile"
                        ),
                    ),
                ),
            },
            assignment_policy=RoleAssignmentPolicy(
                policy_version="role-assignment-policy/send-message-v1",
                max_wait_for_best_seconds=0,
            ),
        )

        self.assertIs(result.status, AgentActionRunStatus.DELIVERED)
        self.assertEqual(
            result.message.recipient_refs,
            (ref("core:agent_instance", "reviewer-agent"),),
        )
        mailbox = MailboxManager(self.database).list_mailbox(
            scope_id="scope-a",
            thread_id="thread-1",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.message for item in mailbox], [result.message])


if __name__ == "__main__":
    unittest.main()
