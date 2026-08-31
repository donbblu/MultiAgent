from __future__ import annotations

import json
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
)
from coding_workflow.recipient_runtime import (
    ContextPolicy,
    OneHopExchangeRuntime,
    RecipientMessageRuntime,
    RecipientRunStatus,
    ReviewSubjectBinding,
)
from coding_workflow.runtime_domain import (
    AgentCandidate,
    RoleAssignmentPolicy,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


T0 = "2026-08-30T00:00:00+00:00"
T1 = "2026-08-30T00:01:00+00:00"


def ref(entity_type: str, entity_id: str) -> ScopedRef:
    return ScopedRef("scope-a", entity_type, entity_id, 1)


class FixedActionModel:
    capabilities = frozenset({
        ModelCapability.TEXT,
        ModelCapability.STRUCTURED_OUTPUT,
    })

    def __init__(self, *, recipient_role: str, content: str) -> None:
        self.recipient_role = recipient_role
        self.content = content
        self.requests: list[ModelRequest] = []

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            {
                "schema_version": "agent-action/v1",
                "action": "send_message",
                "recipient_role": self.recipient_role,
                "content": self.content,
            },
            provider="fake",
            model="fake-action-model",
        )

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        raise AssertionError("接收纵切只允许结构化Action")


class RecipientMessageRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(Path(self._temporary.name) / "runtime.sqlite3"),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/recipient-runtime-tests-v1",
                destination="core:runtime_events",
                expected_sink_id="core:recipient-runtime-test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000,),
            ),
        )
        self.database.initialize()
        self._create_thread()
        self.agents = AgentManager(self.database)
        self._create_agent("planner")
        self._create_agent("reviewer")
        self.mailbox = MailboxManager(self.database, clock=lambda: T1)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_review_subject_source_is_stored_as_its_canonical_value(self) -> None:
        binding = ReviewSubjectBinding(" inline_message ")

        self.assertEqual(binding.source, "inline_message")

    def test_inline_review_subject_is_bound_to_the_persisted_message_body(self) -> None:
        trigger = self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="评审对象明确。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="评审给定的通信协议。",
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={"planner": (self._candidate("planner"),)},
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(result.trigger_message_ref, trigger.message.reference)
        payload = json.loads(reviewer_model.requests[0].messages[-1].content[0].text)
        self.assertEqual(payload["review_subject"], {
            "artifact_ref": None,
            "content_ref": "trigger_message.content",
            "source": "inline_message",
        })
        self.assertEqual(
            payload["trigger_message"]["content"],
            "请检查通信协议是否清晰。",
        )

    def test_missing_review_subject_needs_input_without_model_guessing(self) -> None:
        self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="没有评审对象时不应生成这条回复。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="评审通信协议。",
            reply_role_candidates={"planner": (self._candidate("planner"),)},
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.status, RecipientRunStatus.NEEDS_INPUT)
        self.assertEqual(result.error_code, "missing_review_subject")
        self.assertEqual(reviewer_model.requests, [])
        reviewer_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.consumed for item in reviewer_mailbox], [False])

    def test_artifact_review_subject_is_persisted_resolved_and_bound(self) -> None:
        subject_ref = ref("core:artifact", "protocol-v1")
        trigger = self._send_planner_message(artifact_refs=(subject_ref,))
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="协议规定Runtime路由，评审意见有明确依据。",
        )
        resolved_refs: list[ScopedRef] = []

        def resolve(reference: ScopedRef) -> str:
            resolved_refs.append(reference)
            return "所有Agent消息必须由Runtime校验、持久化并投递。"

        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
            subject_artifact_resolver=resolve,
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="评审通信协议。",
            review_subject=ReviewSubjectBinding.artifact(subject_ref),
            reply_role_candidates={"planner": (self._candidate("planner"),)},
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(trigger.message.artifact_refs, (subject_ref,))
        self.assertEqual(resolved_refs, [subject_ref])
        payload = json.loads(reviewer_model.requests[0].messages[-1].content[0].text)
        self.assertEqual(payload["review_subject"], {
            "artifact_ref": dict(subject_ref.to_dict()),
            "content": "所有Agent消息必须由Runtime校验、持久化并投递。",
            "source": "artifact",
        })

    def test_unbound_artifact_subject_needs_input_without_consuming_message(self) -> None:
        self._send_planner_message()
        subject_ref = ref("core:artifact", "not-bound-to-message")
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="未绑定的Artifact不应进入模型。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
            subject_artifact_resolver=lambda reference: "不应解析",
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="评审通信协议。",
            review_subject=ReviewSubjectBinding.artifact(subject_ref),
            reply_role_candidates={"planner": (self._candidate("planner"),)},
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.status, RecipientRunStatus.NEEDS_INPUT)
        self.assertEqual(result.error_code, "subject_artifact_unbound")
        self.assertEqual(reviewer_model.requests, [])
        reviewer_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.consumed for item in reviewer_mailbox], [False])

    def test_reviewer_receives_allowlisted_context_and_replies_once(self) -> None:
        trigger = self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="协议目标明确，但需要补充超长上下文的失败状态。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="设计清晰、可审计且不会无限循环的Agent通信协议。",
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={
                "planner": (self._candidate("planner"),),
            },
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(
                max_input_tokens=2_000,
                max_auto_hops=1,
                max_actions_per_invocation=1,
            ),
            verified_facts=("SEND_MESSAGE v1已经通过真实DeepSeek验证。",),
            constraints=("Reviewer只能回复一次。",),
        )

        self.assertEqual(result.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(result.auto_hops_used, 1)
        self.assertEqual(result.actions_used, 1)
        self.assertFalse(result.auto_continuation_scheduled)
        self.assertEqual(result.trigger_message_ref, trigger.message.reference)
        self.assertIsNotNone(result.context_bundle)
        self.assertEqual(result.context_bundle.allowed_actions, ("send_message",))
        self.assertEqual(result.context_bundle.omitted_refs, ())
        self.assertEqual(result.action_result.status, AgentActionRunStatus.DELIVERED)

        self.assertEqual(len(reviewer_model.requests), 1)
        context_payload = json.loads(
            reviewer_model.requests[0].messages[-1].content[0].text
        )
        self.assertEqual(
            context_payload,
            {
                "allowed_actions": ["send_message"],
                "artifact_refs": [],
                "constraints": ["Reviewer只能回复一次。"],
                "recipient_role": "reviewer",
                "review_subject": {
                    "artifact_ref": None,
                    "content_ref": "trigger_message.content",
                    "source": "inline_message",
                },
                "task_goal": "设计清晰、可审计且不会无限循环的Agent通信协议。",
                "trigger_message": {
                    "content": "请检查通信协议是否清晰。",
                    "message_id": trigger.message.message_id,
                    "sender_role": "planner",
                },
                "verified_facts": ["SEND_MESSAGE v1已经通过真实DeepSeek验证。"],
            },
        )
        planner_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="planner-agent",
            agent_session_id="planner-session",
        )
        self.assertEqual(len(planner_mailbox), 1)
        self.assertEqual(
            planner_mailbox[0].message.body,
            "协议目标明确，但需要补充超长上下文的失败状态。",
        )
        self.assertEqual(
            planner_mailbox[0].message.parent_ref,
            trigger.message.reference,
        )

    def test_processed_message_is_not_run_or_replied_to_twice(self) -> None:
        self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="只发送一次的评审结论。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )
        arguments = {
            "scope_id": "scope-a",
            "thread_id": "thread-a",
            "recipient_agent_instance_id": "reviewer-agent",
            "recipient_agent_session_id": "reviewer-session",
            "recipient_role": "reviewer",
            "sender_role": "planner",
            "task_goal": "检查通信协议。",
            "review_subject": ReviewSubjectBinding.inline_message(),
            "reply_role_candidates": {
                "planner": (self._candidate("planner"),),
            },
            "assignment_policy": self._assignment_policy(),
            "context_policy": ContextPolicy(max_input_tokens=2_000),
        }

        first = runtime.run_next(**arguments)
        replay = runtime.run_next(**arguments)

        self.assertEqual(first.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(replay.status, RecipientRunStatus.NO_MESSAGE)
        self.assertEqual(len(reviewer_model.requests), 1)
        planner_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="planner-agent",
            agent_session_id="planner-session",
        )
        self.assertEqual(len(planner_mailbox), 1)

    def test_paused_reviewer_rejects_without_consuming_or_calling_model(self) -> None:
        self._send_planner_message()
        self.agents.pause_agent(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
            expected_session_version=1,
            updated_at=T1,
        )
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="暂停时不应该生成这条回复。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="检查通信协议。",
            reply_role_candidates={
                "planner": (self._candidate("planner"),),
            },
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.status, RecipientRunStatus.REJECTED)
        self.assertEqual(result.error_code, "recipient_paused")
        self.assertEqual(reviewer_model.requests, [])
        reviewer_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.consumed for item in reviewer_mailbox], [False])

    def test_required_context_overflow_needs_input_without_silent_truncation(self) -> None:
        self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="超预算时不应该生成回复。",
        )
        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="这是不能被静默截断的用户原始目标。",
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={
                "planner": (self._candidate("planner"),),
            },
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=1),
        )

        self.assertEqual(result.status, RecipientRunStatus.NEEDS_INPUT)
        self.assertEqual(result.error_code, "context_overflow")
        self.assertIn("required context", result.error_detail)
        self.assertEqual(reviewer_model.requests, [])
        reviewer_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        self.assertEqual([item.consumed for item in reviewer_mailbox], [False])

    def test_optional_context_is_trimmed_by_stable_priority_with_omission_refs(self) -> None:
        self._send_planner_message()
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="基于保留下来的约束完成评审。",
        )

        def policy_counter(text: str) -> int:
            payload = json.loads(text)
            return (
                100
                + 10 * len(payload["constraints"])
                + 20 * len(payload["verified_facts"])
                + 30 * len(payload["artifact_refs"])
            )

        runtime = RecipientMessageRuntime(
            self.database,
            model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=policy_counter,
        )

        result = runtime.run_next(
            scope_id="scope-a",
            thread_id="thread-a",
            recipient_agent_instance_id="reviewer-agent",
            recipient_agent_session_id="reviewer-session",
            recipient_role="reviewer",
            sender_role="planner",
            task_goal="检查通信协议。",
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={
                "planner": (self._candidate("planner"),),
            },
            assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=110),
            constraints=("必须检查终止条件。",),
            verified_facts=("发送链已经通过真实模型验证。",),
            artifact_refs=(ref("core:artifact", "artifact-a"),),
        )

        self.assertEqual(result.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(result.context_bundle.constraints, ("必须检查终止条件。",))
        self.assertEqual(result.context_bundle.verified_facts, ())
        self.assertEqual(result.context_bundle.artifact_refs, ())
        self.assertEqual(
            result.context_bundle.omitted_refs,
            ("verified_facts[0]", "artifact_refs[0]"),
        )
        self.assertEqual(result.context_bundle.estimated_input_tokens, 110)
        context_payload = json.loads(
            reviewer_model.requests[0].messages[-1].content[0].text
        )
        self.assertEqual(context_payload["constraints"], ["必须检查终止条件。"])
        self.assertEqual(context_payload["verified_facts"], [])
        self.assertEqual(context_payload["artifact_refs"], [])

    def test_one_hop_exchange_auto_runs_recipient_and_stops_at_reply(self) -> None:
        planner_model = FixedActionModel(
            recipient_role="reviewer",
            content="请检查通信协议是否清晰。",
        )
        reviewer_model = FixedActionModel(
            recipient_role="planner",
            content="协议清晰，建议保留context_overflow状态。",
        )
        runtime = OneHopExchangeRuntime(
            self.database,
            sender_model_client=planner_model,
            recipient_model_client=reviewer_model,
            clock=lambda: T1,
            token_counter=lambda text: len(text),
        )

        result = runtime.run(
            sender_request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请Reviewer检查通信协议。"},
            ]),
            sender_context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-a"),
                turn_ref=ref("core:turn", "turn-a"),
                invocation_ref=ref("core:invocation", "planner-invocation"),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            sender_role_candidates={
                "reviewer": (self._candidate("reviewer"),),
            },
            sender_assignment_policy=self._assignment_policy(),
            sender_role="planner",
            task_goal="设计清晰且不会无限循环的通信协议。",
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={
                "planner": (self._candidate("planner"),),
            },
            recipient_assignment_policy=self._assignment_policy(),
            context_policy=ContextPolicy(max_input_tokens=2_000),
        )

        self.assertEqual(result.sender_action.status, AgentActionRunStatus.DELIVERED)
        self.assertEqual(result.recipient_execution.status, RecipientRunStatus.PROCESSED)
        self.assertEqual(len(planner_model.requests), 1)
        self.assertEqual(len(reviewer_model.requests), 1)
        self.assertFalse(result.recipient_execution.auto_continuation_scheduled)
        planner_mailbox = self.mailbox.list_mailbox(
            scope_id="scope-a",
            thread_id="thread-a",
            agent_instance_id="planner-agent",
            agent_session_id="planner-session",
        )
        self.assertEqual([item.message.body for item in planner_mailbox], [
            "协议清晰，建议保留context_overflow状态。",
        ])
        self.assertEqual([item.consumed for item in planner_mailbox], [False])

    def _send_planner_message(
        self,
        *,
        artifact_refs: tuple[ScopedRef, ...] = (),
    ):
        model = FixedActionModel(
            recipient_role="reviewer",
            content="请检查通信协议是否清晰。",
        )
        result = SendMessageActionRuntime(
            self.database,
            model_client=model,
            clock=lambda: T1,
        ).run(
            request=ModelRequest.from_text_messages([
                {"role": "user", "content": "请Reviewer检查通信协议。"},
            ]),
            context=SendMessageActionContext(
                scope_id="scope-a",
                thread_ref=ref("core:thread", "thread-a"),
                turn_ref=ref("core:turn", "turn-a"),
                invocation_ref=ref("core:invocation", "planner-invocation"),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
                artifact_refs=artifact_refs,
            ),
            role_candidates={
                "reviewer": (self._candidate("reviewer"),),
            },
            assignment_policy=self._assignment_policy(),
        )
        self.assertEqual(result.status, AgentActionRunStatus.DELIVERED)
        return result

    def _create_thread(self) -> None:
        participant = ref("core:principal", "user-1")
        thread = Thread(
            thread_id="thread-a",
            scope_id="scope-a",
            title="Recipient runtime test",
            participant_refs=(participant,),
            created_at=T0,
            updated_at=T0,
        )
        event = RuntimeEvent(
            scope_id="scope-a",
            event_id="event-thread-a",
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id="trace-thread-a",
            correlation_id="correlation-thread-a",
            actor_type=RuntimeActorType.USER,
            actor_ref=participant,
            idempotency_key="idem-thread-a",
            occurred_at=T0,
            recorded_at=T0,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        store = SQLiteThreadEventStore(self.database)
        with self.database.unit_of_work() as uow:
            store.apply(uow, ThreadEventMutation(0, thread, event))
            uow.commit()

    def _create_agent(self, name: str) -> None:
        self.agents.create_agent(
            agent_instance_id=f"{name}-agent",
            agent_session_id=f"{name}-session",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-a"),
            profile_ref=ref("core:agent_profile", f"{name}-profile"),
            principal_id=f"{name}-principal",
            created_at=T0,
        )

    @staticmethod
    def _candidate(name: str) -> AgentCandidate:
        return AgentCandidate(
            agent_instance_ref=ref("core:agent_instance", f"{name}-agent"),
            agent_session_ref=ref("core:agent_session", f"{name}-session"),
            profile_ref=ref("core:agent_profile", f"{name}-profile"),
        )

    @staticmethod
    def _assignment_policy() -> RoleAssignmentPolicy:
        return RoleAssignmentPolicy(
            policy_version="role-assignment-policy/recipient-runtime-tests-v1",
            max_wait_for_best_seconds=0,
        )


if __name__ == "__main__":
    unittest.main()
