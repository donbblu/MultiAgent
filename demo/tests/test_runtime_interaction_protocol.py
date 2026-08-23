import json
import unittest
from dataclasses import FrozenInstanceError, replace

from coding_workflow.runtime_domain.common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
)
from coding_workflow.runtime_domain.interaction import (
    AgentInstance,
    AgentProfile,
    AgentRole,
    AgentSession,
    AgentSessionState,
    Message,
    Scope,
    ScopeState,
    Thread,
    ThreadState,
    Turn,
    TurnState,
    validate_agent_instance_binding,
    validate_agent_session_binding,
    validate_message_binding,
    validate_turn_binding,
)


NOW = "2026-08-23T08:00:00+00:00"
LATER = "2026-08-23T08:01:00+00:00"


def ref(entity_type, entity_id, *, scope_id="scope-a", version=1):
    return ScopedRef(scope_id, entity_type, entity_id, version)


class RuntimeInteractionProtocolTests(unittest.TestCase):
    def records(self):
        scope = Scope("scope-a", "Local Runtime", created_at=NOW)
        role = AgentRole(
            "collaboration:analyst",
            "scope-a",
            "理解输入并公开可验证结论",
            ("分析需求", "标明不确定性"),
            ("不能扩大权限",),
            capability_ceiling=("core:read_artifact", "core:propose_message"),
            created_at=NOW,
        )
        profile = AgentProfile(
            "profile-analyst",
            "scope-a",
            role.reference,
            backend_policy_ref=ref("core:backend_policy", "backend-default"),
            tool_policy_ref=ref("core:tool_policy", "tools-readonly"),
            context_policy_ref=ref("core:context_policy", "context-minimal"),
            output_contract_ref=ref("core:output_contract", "message-v1"),
            budget_policy_ref=ref("core:budget_policy", "budget-default"),
            created_at=NOW,
        )
        thread = Thread(
            "thread-1",
            "scope-a",
            "产品讨论",
            (ref("core:principal", "user-1"),),
            policy_ref=ref("core:thread_policy", "interactive-default"),
            created_at=NOW,
        )
        message = Message(
            "message-1",
            "scope-a",
            thread.reference,
            ref("core:turn", "turn-1"),
            1,
            ref("core:principal", "user-1"),
            (ref("core:agent_instance", "agent-1"),),
            "core:user_message",
            "请分析这张图片",
            (ref("core:artifact", "image-1"),),
            created_at=NOW,
        )
        turn = Turn(
            "turn-1",
            "scope-a",
            thread.reference,
            message.reference,
            created_at=NOW,
        )
        instance = AgentInstance(
            "agent-1",
            "scope-a",
            thread.reference,
            profile.reference,
            "principal-agent-1",
            mailbox_ref=ref("core:mailbox", "mailbox-agent-1"),
            created_at=NOW,
        )
        session = AgentSession(
            "session-1",
            "scope-a",
            thread.reference,
            instance.reference,
            summary_ref=ref("core:artifact", "summary-1"),
            created_at=NOW,
        )
        return (scope, thread, turn, message, role, profile, instance, session)

    def test_all_contracts_round_trip_through_plain_json(self):
        for record in self.records():
            with self.subTest(record=type(record).__name__):
                payload = json.loads(json.dumps(dict(record.to_dict())))
                restored = type(record).from_dict(payload)
                self.assertEqual(restored, record)
                self.assertEqual(payload["schema_version"], RUNTIME_PROTOCOL_VERSION)

    def test_all_contracts_reject_unknown_fields_and_wrong_version(self):
        for record in self.records():
            with self.subTest(record=type(record).__name__):
                payload = dict(record.to_dict())
                payload["unexpected"] = True
                with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
                    type(record).from_dict(payload)

                payload = dict(record.to_dict())
                payload["schema_version"] = "2.0"
                with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
                    type(record).from_dict(payload)

    def test_contracts_and_scoped_references_are_frozen(self):
        scope = self.records()[0]
        with self.assertRaises(FrozenInstanceError):
            scope.name = "changed"
        with self.assertRaises(FrozenInstanceError):
            scope.reference.scope_id = "scope-b"

    def test_scope_thread_turn_and_session_terminal_timestamps_are_consistent(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "archived_at"):
            Scope("scope-a", "scope", ScopeState.ARCHIVED, created_at=NOW)
        with self.assertRaisesRegex(RuntimeProtocolError, "非终态"):
            Thread(
                "thread-1",
                "scope-a",
                "title",
                (ref("core:principal", "user-1"),),
                archived_at=LATER,
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "closed_at"):
            Turn(
                "turn-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:message", "message-1"),
                TurnState.CLOSED,
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "非终态"):
            AgentSession(
                "session-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_instance", "agent-1"),
                closed_at=LATER,
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "不能晚于 updated_at"):
            Scope(
                "scope-a",
                "scope",
                ScopeState.ARCHIVED,
                created_at=NOW,
                updated_at=NOW,
                archived_at=LATER,
            )

        archived = Thread(
            "thread-1",
            "scope-a",
            "title",
            (ref("core:principal", "user-1"),),
            ThreadState.ARCHIVED,
            created_at=NOW,
            updated_at=LATER,
            archived_at=LATER,
        )
        closed = AgentSession(
            "session-1",
            "scope-a",
            ref("core:thread", "thread-1"),
            ref("core:agent_instance", "agent-1"),
            AgentSessionState.CLOSED,
            created_at=NOW,
            updated_at=LATER,
            closed_at=LATER,
        )
        self.assertEqual(archived.state, ThreadState.ARCHIVED)
        self.assertEqual(closed.state, AgentSessionState.CLOSED)

    def test_thread_lifecycle_has_no_accepted_state(self):
        self.assertNotIn("accepted", {state.value for state in ThreadState})
        with self.assertRaises(RuntimeProtocolError):
            Thread(
                "thread-1",
                "scope-a",
                "title",
                (ref("core:principal", "user-1"),),
                state="accepted",
                created_at=NOW,
            )

    def test_parent_relationships_use_authoritative_typed_scoped_refs(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "thread_ref"):
            Turn(
                "turn-1",
                "scope-a",
                None,
                ref("core:message", "message-1"),
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            AgentInstance(
                "agent-1",
                "scope-a",
                ref("core:artifact", "not-a-thread"),
                ref("core:agent_profile", "profile-1"),
                "principal-1",
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            AgentSession(
                "session-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_profile", "not-an-instance"),
                created_at=NOW,
            )

        turn = Turn(
            "turn-1",
            "scope-a",
            ref("core:thread", "thread-1", version=3),
            ref("core:message", "message-1", version=2),
            created_at=NOW,
        )
        self.assertEqual(turn.thread_id, "thread-1")
        self.assertEqual(turn.thread_ref.version, 3)

        with self.assertRaisesRegex(RuntimeProtocolError, "version 必须为 1"):
            Turn(
                "turn-event-version",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:runtime_event", "event-1", version=2),
                created_at=NOW,
            )

        with self.assertRaisesRegex(RuntimeProtocolError, "version 必须为 1"):
            Message(
                "message-event-version",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:turn", "turn-1"),
                2,
                ref("core:principal", "user-1"),
                (ref("core:agent_instance", "agent-1"),),
                "core:user_message",
                "hello",
                causation_ref=ref(
                    "core:runtime_event", "event-1", version=2
                ),
                created_at=NOW,
            )

    def test_aggregate_validators_reject_same_scope_cross_thread_links(self):
        _, thread, turn, message, _, _, instance, session = self.records()
        validate_turn_binding(thread, turn)
        validate_message_binding(thread, turn, message)
        validate_agent_instance_binding(thread, instance)
        validate_agent_session_binding(thread, instance, session)

        with self.assertRaisesRegex(RuntimeProtocolError, "错误 Thread"):
            validate_turn_binding(
                thread,
                replace(
                    turn,
                    thread_ref=ref("core:thread", "thread-other"),
                ),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "错误 Turn"):
            validate_message_binding(
                thread,
                turn,
                replace(
                    message,
                    turn_ref=ref("core:turn", "turn-other"),
                ),
            )

        other_parent = replace(
            message,
            message_id="message-other",
            thread_ref=ref("core:thread", "thread-other"),
        )
        child = replace(
            message,
            message_id="message-child",
            sequence=2,
            parent_ref=other_parent.reference,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "其他 Thread"):
            validate_message_binding(
                thread,
                turn,
                child,
                parent=other_parent,
            )

        with self.assertRaisesRegex(RuntimeProtocolError, "错误 AgentInstance"):
            validate_agent_session_binding(
                thread,
                instance,
                replace(
                    session,
                    agent_instance_ref=ref(
                        "core:agent_instance", "agent-other"
                    ),
                ),
            )

    def test_every_nested_reference_fails_closed_across_scope(self):
        foreign_principal = ref(
            "core:principal", "user-foreign", scope_id="scope-b"
        )
        with self.assertRaises(ScopeBoundaryError):
            Thread(
                "thread-1", "scope-a", "title", (foreign_principal,),
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            Turn(
                "turn-1",
                "scope-a",
                ref("core:thread", "thread-1", scope_id="scope-b"),
                ref("core:message", "message-1"),
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            Message(
                "message-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:turn", "turn-1"),
                1,
                ref("core:principal", "user-1"),
                (ref("core:principal", "runtime"),),
                "core:user_message",
                artifact_refs=(
                    ref("core:artifact", "foreign", scope_id="scope-b"),
                ),
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            AgentProfile(
                "profile-1",
                "scope-a",
                ref("core:agent_role", "role-1", scope_id="scope-b"),
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            AgentInstance(
                "agent-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_profile", "profile-1", scope_id="scope-b"),
                "principal-1",
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            AgentSession(
                "session-1",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_instance", "agent-1"),
                summary_ref=ref(
                    "core:artifact", "summary", scope_id="scope-b"
                ),
                created_at=NOW,
            )

    def test_message_owns_body_and_only_references_artifacts(self):
        attachment_only = Message(
            "message-1",
            "scope-a",
            ref("core:thread", "thread-1"),
            ref("core:turn", "turn-1"),
            1,
            ref("core:principal", "user-1"),
            (ref("core:principal", "runtime"),),
            "core:user_message",
            artifact_refs=(ref("core:artifact", "image-1"),),
            created_at=NOW,
        )
        payload = dict(attachment_only.to_dict())
        self.assertEqual(payload["body"], "")
        self.assertEqual(payload["artifact_refs"][0]["entity_type"], "core:artifact")
        self.assertNotIn("artifact", payload)
        self.assertNotIn("content", payload)

        with self.assertRaisesRegex(RuntimeProtocolError, "正文或 Artifact"):
            Message(
                "message-2",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:turn", "turn-1"),
                2,
                ref("core:principal", "user-1"),
                (ref("core:principal", "runtime"),),
                "core:user_message",
                created_at=NOW,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            Message(
                "message-3",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:turn", "turn-1"),
                3,
                ref("core:principal", "user-1"),
                (ref("core:principal", "runtime"),),
                "core:user_message",
                artifact_refs=(ref("core:message", "not-an-artifact"),),
                created_at=NOW,
            )
        payload["artifact"] = {"bytes": "inline"}
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            Message.from_dict(payload)

        exact_body = "  保留用户正文的首尾空白\n"
        body_message = Message(
            "message-4",
            "scope-a",
            ref("core:thread", "thread-1"),
            ref("core:turn", "turn-1"),
            4,
            ref("core:principal", "user-1"),
            (ref("core:principal", "runtime"),),
            "core:user_message",
            body=exact_body,
            created_at=NOW,
        )
        self.assertEqual(body_message.body, exact_body)
        self.assertEqual(dict(body_message.to_dict())["body"], exact_body)

    def test_message_validates_sequence_kind_parent_and_causation(self):
        common = dict(
            message_id="message-1",
            scope_id="scope-a",
            thread_ref=ref("core:thread", "thread-1"),
            turn_ref=ref("core:turn", "turn-1"),
            sender_ref=ref("core:principal", "user-1"),
            recipient_refs=(ref("core:principal", "runtime"),),
            body="hello",
            created_at=NOW,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "sequence"):
            Message(sequence=0, kind="core:user_message", **common)
        with self.assertRaisesRegex(RuntimeProtocolError, "namespace:name"):
            Message(sequence=1, kind="user_message", **common)
        with self.assertRaisesRegex(RuntimeProtocolError, "自身"):
            Message(
                sequence=1,
                kind="core:user_message",
                parent_ref=ref("core:message", "message-1"),
                **common,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            Message(
                sequence=1,
                kind="core:user_message",
                causation_ref=ref("core:artifact", "image-1"),
                **common,
            )

    def test_role_is_generic_and_namespaced(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "namespace:name"):
            AgentRole(
                "planner",
                "scope-a",
                "分析",
                ("理解输入",),
                created_at=NOW,
            )
        role = AgentRole(
            "research:analyst",
            "scope-a",
            "分析一般问题",
            ("整理证据",),
            ("不执行副作用",),
            capability_ceiling=("core:read_artifact",),
            created_at=NOW,
        )
        self.assertNotIn("code", json.dumps(dict(role.to_dict())))
        self.assertEqual(role.capability_ceiling, ("core:read_artifact",))
        with self.assertRaisesRegex(RuntimeProtocolError, "namespace:name"):
            AgentRole(
                "research:writer",
                "scope-a",
                "写作",
                ("生成草稿",),
                capability_ceiling=("write",),
                created_at=NOW,
            )

    def test_profile_keeps_policies_as_opaque_scoped_refs(self):
        role = ref("core:agent_role", "research:analyst")
        profile = AgentProfile(
            "profile-1",
            "scope-a",
            role,
            backend_policy_ref=ref("core:backend_policy", "backend-1"),
            tool_policy_ref=ref("core:tool_policy", "tools-1"),
            context_policy_ref=ref("core:context_policy", "context-1"),
            output_contract_ref=ref("core:output_contract", "output-1"),
            budget_policy_ref=ref("core:budget_policy", "budget-1"),
            created_at=NOW,
        )
        payload = dict(profile.to_dict())
        self.assertNotIn("model", payload)
        self.assertNotIn("client", payload)
        self.assertEqual(
            payload["backend_policy_ref"]["entity_id"], "backend-1"
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            AgentProfile(
                "profile-2",
                "scope-a",
                role,
                backend_policy_ref=ref("core:artifact", "wrong-kind"),
                created_at=NOW,
            )

    def test_agent_instance_keeps_mailbox_as_an_opaque_reference_only(self):
        instance = AgentInstance(
            "agent-1",
            "scope-a",
            ref("core:thread", "thread-1"),
            ref("core:agent_profile", "profile-1"),
            "principal-1",
            mailbox_ref=ref("core:mailbox", "mailbox-1"),
            created_at=NOW,
        )
        payload = dict(instance.to_dict())
        self.assertEqual(payload["mailbox_ref"]["entity_id"], "mailbox-1")
        self.assertNotIn("messages", payload)
        self.assertNotIn("delivery_state", payload)
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型"):
            AgentInstance(
                "agent-2",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_profile", "profile-1"),
                "principal-2",
                mailbox_ref=ref("core:artifact", "not-a-mailbox"),
                created_at=NOW,
            )
        with self.assertRaises(ScopeBoundaryError):
            AgentInstance(
                "agent-3",
                "scope-a",
                ref("core:thread", "thread-1"),
                ref("core:agent_profile", "profile-1"),
                "principal-3",
                mailbox_ref=ref(
                    "core:mailbox", "foreign-mailbox", scope_id="scope-b"
                ),
                created_at=NOW,
            )

    def test_agent_session_is_runtime_owned_not_a_provider_binding(self):
        session = AgentSession(
            "session-1",
            "scope-a",
            ref("core:thread", "thread-1"),
            ref("core:agent_instance", "agent-1"),
            created_at=NOW,
        )
        payload = dict(session.to_dict())
        forbidden = {
            "provider", "provider_session_id", "session_binding",
            "backend_session_id", "model",
        }
        self.assertFalse(forbidden.intersection(payload))
        payload["provider_session_id"] = "provider-secret-session"
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            AgentSession.from_dict(payload)

    def test_timestamps_must_include_timezone(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "时区"):
            Scope("scope-a", "scope", created_at="2026-08-23T08:00:00")


if __name__ == "__main__":
    unittest.main()
