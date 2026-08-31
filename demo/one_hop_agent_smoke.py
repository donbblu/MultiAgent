from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from coding_workflow.agent_actions import (  # noqa: E402
    AgentActionRunStatus,
    SendMessageActionContext,
    SendMessageActionResult,
)
from coding_workflow.agent_runtime import AgentManager, MailboxManager  # noqa: E402
from coding_workflow.model import (  # noqa: E402
    ModelClientFactory,
    ModelRequest,
    OpenAICompatibleClient,
    load_env_file,
)
from coding_workflow.recipient_runtime import (  # noqa: E402
    ContextPolicy,
    OneHopExchangeRuntime,
    RecipientRunStatus,
    ReviewSubjectBinding,
)
from coding_workflow.runtime_domain import (  # noqa: E402
    AgentCandidate,
    RoleAssignmentPolicy,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (  # noqa: E402
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


SCOPE_ID = "real-one-hop-smoke"
THREAD_ID = "thread-real-one-hop-smoke"
DEFAULT_REVIEW_SUBJECT = (
    "Agent通信协议v1：Agent只能输出结构化send_message Action，字段为"
    "schema_version、action、recipient_role、content；Runtime使用当前允许的"
    "规范Role ID校验recipient_role，负责选择具体Agent、持久化Message、投递"
    "Mailbox并记录parent与causation；Reviewer只回复Planner一次，Runtime"
    "在一次自动hop后停止，不允许Agent私下直连或自由广播；message_id由"
    "scope、invocation和step确定性派生，同一Invocation重放时返回原Message，"
    "不重复调用模型或投递Mailbox；同一幂等身份绑定不同规范请求时拒绝并返回"
    "idempotency_conflict，不把它静默当成新消息。"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ref(entity_type: str, entity_id: str) -> ScopedRef:
    return ScopedRef(SCOPE_ID, entity_type, entity_id, 1)


class CountedClient:
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self.client = client
        self.calls = 0

    @property
    def capabilities(self):
        return self.client.capabilities

    def generate_structured(self, request):
        self.calls += 1
        return self.client.generate_structured(request)

    def generate_json(self, messages):
        return self.client.generate_json(messages)


def conservative_token_estimate(text: str) -> int:
    """A conservative context-only estimate, not provider-billed Usage."""

    byte_count = len(text.encode("utf-8"))
    return max(1, (byte_count + 2) // 3)


def create_thread(database: SQLiteRuntimeDatabase, instant: str) -> None:
    participant = ref("core:principal", "user-1")
    thread = Thread(
        thread_id=THREAD_ID,
        scope_id=SCOPE_ID,
        title="DeepSeek one-hop Agent smoke",
        participant_refs=(participant,),
        created_at=instant,
        updated_at=instant,
    )
    event = RuntimeEvent(
        scope_id=SCOPE_ID,
        event_id="event-thread-real-one-hop-smoke",
        event_type="core:thread_created",
        aggregate_ref=thread.reference,
        aggregate_version=1,
        sequence_no=1,
        trace_id="trace-real-one-hop-smoke",
        correlation_id="correlation-real-one-hop-smoke",
        actor_type=RuntimeActorType.USER,
        actor_ref=participant,
        idempotency_key="idem-thread-real-one-hop-smoke",
        occurred_at=instant,
        recorded_at=instant,
        thread_ref=thread.reference,
        payload={"state": "open"},
    )
    store = SQLiteThreadEventStore(database)
    with database.unit_of_work() as uow:
        store.apply(uow, ThreadEventMutation(0, thread, event))
        uow.commit()


def create_agents(database: SQLiteRuntimeDatabase, instant: str) -> None:
    manager = AgentManager(database)
    for role in ("planner", "reviewer"):
        manager.create_agent(
            agent_instance_id=f"{role}-agent",
            agent_session_id=f"{role}-session",
            scope_id=SCOPE_ID,
            thread_ref=ref("core:thread", THREAD_ID),
            profile_ref=ref("core:agent_profile", f"{role}-profile"),
            principal_id=f"{role}-principal",
            created_at=instant,
        )


def candidate(role: str) -> AgentCandidate:
    return AgentCandidate(
        agent_instance_ref=ref("core:agent_instance", f"{role}-agent"),
        agent_session_ref=ref("core:agent_session", f"{role}-session"),
        profile_ref=ref("core:agent_profile", f"{role}-profile"),
    )


def action_public(result: SendMessageActionResult | None) -> Mapping[str, object] | None:
    if result is None or result.model_response is None:
        return None
    data = result.model_response.data
    return {
        "schema_version": data.get("schema_version", ""),
        "action": data.get("action", ""),
        "recipient_role": data.get("recipient_role", ""),
        "content": data.get("content", ""),
    }


def assignment_public(
    result: SendMessageActionResult | None,
) -> Mapping[str, object] | None:
    if result is None or result.assignment is None:
        return None
    assignment = result.assignment
    return {
        "decision": assignment.decision.value,
        "role": assignment.requirement.role_ref.entity_id,
        "recipient_agent": (
            assignment.selected_agent_instance_ref.entity_id
            if assignment.selected_agent_instance_ref
            else ""
        ),
        "recipient_session": (
            assignment.selected_agent_session_ref.entity_id
            if assignment.selected_agent_session_ref
            else ""
        ),
    }


def message_public(
    result: SendMessageActionResult | None,
) -> Mapping[str, object] | None:
    if result is None or result.message is None:
        return None
    message = result.message
    return {
        "message_id": message.message_id,
        "kind": message.kind,
        "sender_agent": message.sender_ref.entity_id,
        "recipient_agents": [item.entity_id for item in message.recipient_refs],
        "body": message.body,
        "parent_message_id": message.parent_ref.entity_id if message.parent_ref else "",
        "causation_id": (
            message.causation_ref.entity_id if message.causation_ref else ""
        ),
    }


def usage_public(
    result: SendMessageActionResult | None,
) -> Mapping[str, object] | None:
    if result is None or result.model_response is None:
        return None
    response = result.model_response
    return {
        "provider": response.provider,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.total_tokens,
        "latency_ms": response.latency_ms,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run one real Planner → Reviewer → Planner DeepSeek hop."
    )
    value.add_argument(
        "--trusted-real-api",
        action="store_true",
        help="Confirm real network calls and API cost (normally 2, at most 4).",
    )
    value.add_argument(
        "--model",
        default="deepseek-v4-pro",
        help="DeepSeek model name configured for the smoke.",
    )
    value.add_argument(
        "--task",
        default="请Reviewer评审给定通信协议，并指出一项最重要且有依据的改进。",
        help="Public smoke task. Do not include secrets.",
    )
    value.add_argument(
        "--subject",
        default=DEFAULT_REVIEW_SUBJECT,
        help="Public review subject copied into the durable Message. No secrets.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    if not args.trusted_real_api:
        parser().error(
            "真实smoke会调用DeepSeek并产生少量费用；"
            "确认后显式添加 --trusted-real-api"
        )

    load_env_file(ROOT / ".env")
    config = replace(
        ModelClientFactory.config_for_provider(
            "deepseek",
            model=args.model,
            max_tokens=256,
            max_retries=0,
            temperature=0.0,
        ),
        timeout_seconds=60,
        request_options=(("thinking", {"type": "disabled"}),),
    )
    planner_client = CountedClient(OpenAICompatibleClient(config))
    reviewer_client = CountedClient(OpenAICompatibleClient(config))
    instant = now()
    context_limit = 4_000

    with tempfile.TemporaryDirectory(prefix="one-hop-agent-smoke-") as temp:
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(Path(temp) / "runtime.sqlite3"),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/one-hop-real-smoke-v1",
                destination="core:runtime_events",
                expected_sink_id="core:one-hop-real-smoke-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000,),
            ),
        )
        database.initialize()
        create_thread(database, instant)
        create_agents(database, instant)
        assignment_policy = RoleAssignmentPolicy(
            policy_version="role-assignment-policy/one-hop-real-smoke-v1",
            max_wait_for_best_seconds=0,
        )
        exchange = OneHopExchangeRuntime(
            database,
            sender_model_client=planner_client,
            recipient_model_client=reviewer_client,
            clock=now,
            token_counter=conservative_token_estimate,
        ).run(
            sender_request=ModelRequest.from_text_messages([
                {
                    "role": "system",
                    "content": (
                        "你是Planner。只输出一个send_message Action，"
                        "recipient_role必须是reviewer，content必须逐字复制"
                        "用户JSON中的review_subject，不要解释、摘要、改写，"
                        "不要指定具体Agent。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_goal": args.task,
                            "review_subject": args.subject,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            ]),
            sender_context=SendMessageActionContext(
                scope_id=SCOPE_ID,
                thread_ref=ref("core:thread", THREAD_ID),
                turn_ref=ref("core:turn", "turn-real-one-hop-smoke"),
                invocation_ref=ref(
                    "core:invocation", "planner-invocation-real-one-hop-smoke"
                ),
                sender_agent_instance_ref=ref(
                    "core:agent_instance", "planner-agent"
                ),
                step_index=1,
            ),
            sender_role_candidates={"reviewer": (candidate("reviewer"),)},
            sender_assignment_policy=assignment_policy,
            sender_role="planner",
            task_goal=args.task,
            review_subject=ReviewSubjectBinding.inline_message(),
            reply_role_candidates={"planner": (candidate("planner"),)},
            recipient_assignment_policy=assignment_policy,
            context_policy=ContextPolicy(max_input_tokens=context_limit),
            verified_facts=("SEND_MESSAGE v1已通过真实DeepSeek单向验证。",),
            constraints=("Reviewer只回复Planner一次，不启动第二个自动hop。",),
        )

        recipient = exchange.recipient_execution
        reviewer_action_result = recipient.action_result if recipient else None
        mailbox = MailboxManager(database)
        reviewer_mailbox = mailbox.list_mailbox(
            scope_id=SCOPE_ID,
            thread_id=THREAD_ID,
            agent_instance_id="reviewer-agent",
            agent_session_id="reviewer-session",
        )
        planner_mailbox = mailbox.list_mailbox(
            scope_id=SCOPE_ID,
            thread_id=THREAD_ID,
            agent_instance_id="planner-agent",
            agent_session_id="planner-session",
        )
        context_bundle = recipient.context_bundle if recipient else None
        context_payload = (
            dict(context_bundle.to_model_payload()) if context_bundle else None
        )
        planner_action = action_public(exchange.sender_action)
        reviewer_action = action_public(reviewer_action_result)
        planner_message = message_public(exchange.sender_action)
        reviewer_message = message_public(reviewer_action_result)
        provider_calls = planner_client.calls + reviewer_client.calls

        passed = bool(
            exchange.sender_action.status is AgentActionRunStatus.DELIVERED
            and recipient is not None
            and recipient.status is RecipientRunStatus.PROCESSED
            and reviewer_action_result is not None
            and reviewer_action_result.status is AgentActionRunStatus.DELIVERED
            and planner_action is not None
            and reviewer_action is not None
            and planner_message is not None
            and reviewer_message is not None
            and planner_action["content"] == planner_message["body"]
            and planner_message["body"] == args.subject
            and context_payload is not None
            and context_payload["trigger_message"]["content"] == args.subject
            and context_payload["review_subject"] == {
                "artifact_ref": None,
                "content_ref": "trigger_message.content",
                "source": "inline_message",
            }
            and reviewer_action["content"] == reviewer_message["body"]
            and len(reviewer_mailbox) == 1
            and reviewer_mailbox[0].consumed
            and len(planner_mailbox) == 1
            and not planner_mailbox[0].consumed
            and recipient.auto_hops_used == 1
            and not recipient.auto_continuation_scheduled
            and 2 <= provider_calls <= 4
            and planner_client.calls <= 2
            and reviewer_client.calls <= 2
        )
        output = {
            "status": "passed" if passed else "failed",
            "planner": {
                "action": planner_action,
                "assignment": assignment_public(exchange.sender_action),
                "message": planner_message,
                "usage": usage_public(exchange.sender_action),
                "provider_calls": planner_client.calls,
                "protocol_repairs": max(planner_client.calls - 1, 0),
            },
            "reviewer_context": (
                {
                    "public_bundle": context_payload,
                    "estimated_context_tokens": (
                        context_bundle.estimated_input_tokens
                    ),
                    "max_context_tokens": context_limit,
                    "omitted_refs": list(context_bundle.omitted_refs),
                    "estimate_is_provider_usage": False,
                }
                if context_bundle
                else None
            ),
            "reviewer": {
                "execution_status": recipient.status.value if recipient else "",
                "action": reviewer_action,
                "assignment": assignment_public(reviewer_action_result),
                "message": reviewer_message,
                "usage": usage_public(reviewer_action_result),
                "provider_calls": reviewer_client.calls,
                "protocol_repairs": max(reviewer_client.calls - 1, 0),
            },
            "runtime": {
                "provider_calls": provider_calls,
                "auto_hops_used": recipient.auto_hops_used if recipient else 0,
                "actions_used_by_recipient": (
                    recipient.actions_used if recipient else 0
                ),
                "auto_continuation_scheduled": (
                    recipient.auto_continuation_scheduled if recipient else False
                ),
                "reviewer_mailbox": {
                    "messages": len(reviewer_mailbox),
                    "consumed": [item.consumed for item in reviewer_mailbox],
                },
                "planner_mailbox": {
                    "messages": len(planner_mailbox),
                    "consumed": [item.consumed for item in planner_mailbox],
                },
            },
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
