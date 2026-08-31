from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Callable, Mapping

from .agent_runtime import MailboxManager
from .model import (
    ImageContentPart,
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContentPart,
)
from .role_assignment import RoleAssignmentManager, RoleAssignmentScheduler
from .runtime_domain import (
    AgentCandidate,
    AssignmentDecision,
    Message,
    RoleAssignment,
    RoleAssignmentPolicy,
    RoleRequirement,
    RuntimeProtocolError,
    ScopedRef,
    ThreadState,
    canonical_digest,
)
from .runtime_domain.common import nonempty, positive_int, require_fields, scoped_refs
from .runtime_persistence import (
    MailboxDelivery,
    MailboxSendResult,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
)


AGENT_ACTION_SCHEMA_VERSION = "agent-action/v1"
SEND_MESSAGE_ACTION_SCHEMA = MappingProxyType({
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "action",
        "recipient_role",
        "content",
    ],
    "properties": {
        "schema_version": {"const": AGENT_ACTION_SCHEMA_VERSION},
        "action": {"const": "send_message"},
        "recipient_role": {"type": "string", "minLength": 1},
        "content": {"type": "string", "minLength": 1},
    },
})


AgentActionClock = Callable[[], str]


class AgentActionRunStatus(str, Enum):
    DELIVERED = "delivered"
    NEEDS_INPUT = "needs_input"
    PROTOCOL_ERROR = "protocol_error"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SendMessageAction:
    recipient_role: str
    content: str
    schema_version: str = AGENT_ACTION_SCHEMA_VERSION
    action: str = "send_message"

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_ACTION_SCHEMA_VERSION:
            raise RuntimeProtocolError(
                f"SendMessageAction 只支持 schema_version "
                f"{AGENT_ACTION_SCHEMA_VERSION}"
            )
        if self.action != "send_message":
            raise RuntimeProtocolError("action 必须是 send_message")
        object.__setattr__(
            self,
            "recipient_role",
            nonempty(self.recipient_role, "recipient_role"),
        )
        object.__setattr__(self, "content", nonempty(self.content, "content"))

    @classmethod
    def from_model_output(cls, value: Mapping[str, object]) -> "SendMessageAction":
        root = require_fields(
            value,
            type_name="SendMessageAction",
            required=frozenset({
                "schema_version",
                "action",
                "recipient_role",
                "content",
            }),
        )
        return cls(
            schema_version=root["schema_version"],
            action=root["action"],
            recipient_role=root["recipient_role"],
            content=root["content"],
        )


@dataclass(frozen=True)
class SendMessageActionContext:
    scope_id: str
    thread_ref: ScopedRef
    turn_ref: ScopedRef
    invocation_ref: ScopedRef
    sender_agent_instance_ref: ScopedRef
    step_index: int
    artifact_refs: tuple[ScopedRef, ...] = ()
    parent_message_ref: ScopedRef | None = None

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        for field_name, reference, entity_type in (
            ("thread_ref", self.thread_ref, "core:thread"),
            ("turn_ref", self.turn_ref, "core:turn"),
            ("invocation_ref", self.invocation_ref, "core:invocation"),
            (
                "sender_agent_instance_ref",
                self.sender_agent_instance_ref,
                "core:agent_instance",
            ),
        ):
            if not isinstance(reference, ScopedRef):
                raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef")
            reference.assert_scope(scope_id, field_name)
            reference.assert_type(entity_type)
        if self.parent_message_ref is not None:
            if not isinstance(self.parent_message_ref, ScopedRef):
                raise RuntimeProtocolError("parent_message_ref 必须是 ScopedRef")
            self.parent_message_ref.assert_scope(scope_id, "parent_message_ref")
            self.parent_message_ref.assert_type("core:message")
        object.__setattr__(
            self,
            "artifact_refs",
            scoped_refs(
                self.artifact_refs,
                "artifact_refs",
                scope_id=scope_id,
                entity_types=("core:artifact",),
            ),
        )
        object.__setattr__(
            self,
            "step_index",
            positive_int(self.step_index, "step_index"),
        )


@dataclass(frozen=True)
class SendMessageActionResult:
    status: AgentActionRunStatus
    assignment: RoleAssignment | None
    model_response: ModelResponse | None
    message: Message | None = None
    delivery: MailboxDelivery | None = None
    send_result: MailboxSendResult | None = None
    error_code: str = ""
    error_detail: str = ""


class _InvalidActionAfterRepair(RuntimeError):
    def __init__(self, response: ModelResponse, detail: str) -> None:
        super().__init__(detail)
        self.response = response
        self.detail = detail


class SendMessageActionRuntime:
    """Runs exactly one model SEND_MESSAGE action and stops after delivery."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        model_client: ModelClient,
        clock: AgentActionClock,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database 必须是 SQLiteRuntimeDatabase")
        if not callable(clock):
            raise TypeError("clock 必须可调用")
        self._model_client = model_client
        self._clock = clock
        self._assignments = RoleAssignmentManager(database)
        self._scheduler = RoleAssignmentScheduler()
        self._mailbox = MailboxManager(database, clock=clock)
        self._threads = SQLiteThreadEventStore(database)

    def run(
        self,
        *,
        request: ModelRequest,
        context: SendMessageActionContext,
        role_candidates: Mapping[str, tuple[AgentCandidate, ...]],
        assignment_policy: RoleAssignmentPolicy,
    ) -> SendMessageActionResult:
        if not isinstance(request, ModelRequest):
            raise TypeError("request 必须是 ModelRequest")
        if not isinstance(context, SendMessageActionContext):
            raise TypeError("context 必须是 SendMessageActionContext")
        if not isinstance(role_candidates, Mapping):
            raise TypeError("role_candidates 必须是映射")
        if not isinstance(assignment_policy, RoleAssignmentPolicy):
            raise TypeError("assignment_policy 必须是 RoleAssignmentPolicy")

        identity = canonical_digest({
            "scope_id": context.scope_id,
            "invocation_id": context.invocation_ref.entity_id,
            "step_index": context.step_index,
        })
        allowed_role_ids = self._allowed_role_ids(role_candidates)
        request_digest = self._request_digest(
            request=request,
            context=context,
            allowed_role_ids=allowed_role_ids,
            assignment_policy=assignment_policy,
        )
        assignment_id = f"send-message-assignment-{identity}"
        message_id = f"send-message-{identity}"
        requirement_id = f"send-message-requirement-{request_digest}"
        existing = self._assignments.get_assignment(
            scope_id=context.scope_id,
            thread_id=context.thread_ref.entity_id,
            assignment_id=assignment_id,
        )
        if existing is not None:
            if existing.requirement.requirement_id != requirement_id:
                return SendMessageActionResult(
                    status=AgentActionRunStatus.REJECTED,
                    assignment=existing,
                    model_response=None,
                    error_code="idempotency_conflict",
                    error_detail=(
                        "同一scope/invocation/step不能绑定不同的规范请求"
                    ),
                )
            return self._replay(existing, message_id)

        thread = self._threads.get_thread(
            context.scope_id,
            context.thread_ref.entity_id,
        )
        if thread is None:
            return SendMessageActionResult(
                status=AgentActionRunStatus.REJECTED,
                assignment=None,
                model_response=None,
                error_code="thread_not_found",
            )
        if thread.state is not ThreadState.OPEN:
            return SendMessageActionResult(
                status=AgentActionRunStatus.REJECTED,
                assignment=None,
                model_response=None,
                error_code=f"thread_{thread.state.value}",
            )

        response_schema = self._response_schema(allowed_role_ids)
        role_instruction = ModelMessage(
            "system",
            (TextContentPart(
                "recipient_role 必须从以下规范 Role ID 中选择并原样复制；"
                "不得翻译、改写大小写、使用显示名称或创造新 Role："
                + json.dumps(
                    list(allowed_role_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),),
        )
        schema_request = ModelRequest(
            messages=(role_instruction,) + request.messages,
            required_capabilities=request.required_capabilities,
            response_schema=response_schema,
        )
        try:
            response, action = self._generate_action(schema_request)
        except _InvalidActionAfterRepair as exc:
            return SendMessageActionResult(
                status=AgentActionRunStatus.PROTOCOL_ERROR,
                assignment=None,
                model_response=exc.response,
                error_code="invalid_action_after_repair",
                error_detail=exc.detail,
            )
        candidates = tuple(role_candidates.get(action.recipient_role, ()))
        instant = self._clock()
        requirement = RoleRequirement(
            requirement_id=requirement_id,
            scope_id=context.scope_id,
            thread_ref=context.thread_ref,
            work_ref=context.invocation_ref,
            role_ref=ScopedRef(
                context.scope_id,
                "core:agent_role",
                action.recipient_role,
                1,
            ),
            created_at=instant,
        )
        assignment = self._scheduler.decide(
            assignment_id=assignment_id,
            requirement=requirement,
            candidates=candidates,
            policy=assignment_policy,
            created_at=instant,
        )
        if assignment.decision is not AssignmentDecision.ASSIGNED:
            self._assignments.record(assignment)
            return SendMessageActionResult(
                status=AgentActionRunStatus.NEEDS_INPUT,
                assignment=assignment,
                model_response=response,
                error_code=assignment.reason_code,
            )

        message = Message(
            message_id=message_id,
            scope_id=context.scope_id,
            thread_ref=context.thread_ref,
            turn_ref=context.turn_ref,
            sequence=context.step_index,
            sender_ref=context.sender_agent_instance_ref,
            recipient_refs=(assignment.selected_agent_instance_ref,),
            kind="core:agent_message",
            body=action.content,
            artifact_refs=context.artifact_refs,
            parent_ref=context.parent_message_ref,
            causation_ref=context.invocation_ref,
            created_at=instant,
        )
        _, send_result, delivery = self._assignments.record_and_enqueue(
            assignment,
            message,
            enqueued_at=instant,
        )
        return SendMessageActionResult(
            status=AgentActionRunStatus.DELIVERED,
            assignment=assignment,
            model_response=response,
            message=message,
            delivery=delivery,
            send_result=send_result,
        )

    def _replay(
        self,
        assignment: RoleAssignment,
        message_id: str,
    ) -> SendMessageActionResult:
        if assignment.decision is not AssignmentDecision.ASSIGNED:
            return SendMessageActionResult(
                status=AgentActionRunStatus.NEEDS_INPUT,
                assignment=assignment,
                model_response=None,
                error_code=assignment.reason_code,
            )
        deliveries = self._mailbox.list_mailbox(
            scope_id=assignment.scope_id,
            thread_id=assignment.thread_id,
            agent_instance_id=assignment.selected_agent_instance_ref.entity_id,
            agent_session_id=assignment.selected_agent_session_ref.entity_id,
        )
        matching = tuple(
            item for item in deliveries if item.message.message_id == message_id
        )
        if len(matching) != 1:
            raise RuntimeProtocolError(
                "已完成的 SEND_MESSAGE Assignment 必须对应唯一 Mailbox 消息"
            )
        delivery = matching[0]
        return SendMessageActionResult(
            status=AgentActionRunStatus.DELIVERED,
            assignment=assignment,
            model_response=None,
            message=delivery.message,
            delivery=delivery,
            send_result=MailboxSendResult.ALREADY_ENQUEUED,
        )

    @staticmethod
    def _allowed_role_ids(
        role_candidates: Mapping[str, tuple[AgentCandidate, ...]],
    ) -> tuple[str, ...]:
        parsed: list[str] = []
        for role_id in role_candidates:
            canonical = nonempty(role_id, "role_candidates Role ID")
            if canonical != role_id:
                raise RuntimeProtocolError(
                    "role_candidates 必须使用无外围空格的规范 Role ID"
                )
            parsed.append(canonical)
        return tuple(sorted(parsed))

    @classmethod
    def _request_digest(
        cls,
        *,
        request: ModelRequest,
        context: SendMessageActionContext,
        allowed_role_ids: tuple[str, ...],
        assignment_policy: RoleAssignmentPolicy,
    ) -> str:
        messages = []
        for message in request.messages:
            parts = []
            for part in message.content:
                if isinstance(part, TextContentPart):
                    parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ImageContentPart):
                    parts.append({
                        "type": "image",
                        "artifact_ref": part.artifact_ref,
                        "mime_type": part.mime_type,
                        "data_sha256": sha256(part.data).hexdigest(),
                        "detail": part.detail,
                    })
                else:
                    raise RuntimeProtocolError("ModelRequest包含未知内容类型")
            messages.append({"role": message.role, "content": parts})
        return canonical_digest({
            "request": {
                "messages": messages,
                "required_capabilities": sorted(
                    item.value for item in request.required_capabilities
                ),
                "response_schema": cls._canonical_json_value(
                    request.response_schema
                ),
            },
            "context": {
                "scope_id": context.scope_id,
                "thread_ref": dict(context.thread_ref.to_dict()),
                "turn_ref": dict(context.turn_ref.to_dict()),
                "invocation_ref": dict(context.invocation_ref.to_dict()),
                "sender_agent_instance_ref": dict(
                    context.sender_agent_instance_ref.to_dict()
                ),
                "step_index": context.step_index,
                "artifact_refs": [
                    dict(item.to_dict()) for item in context.artifact_refs
                ],
                "parent_message_ref": (
                    dict(context.parent_message_ref.to_dict())
                    if context.parent_message_ref
                    else None
                ),
            },
            "allowed_role_ids": list(allowed_role_ids),
            "assignment_policy": {
                "policy_version": assignment_policy.policy_version,
                "max_wait_for_best_seconds": (
                    assignment_policy.max_wait_for_best_seconds
                ),
            },
        })

    @classmethod
    def _canonical_json_value(cls, value: object) -> object:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Mapping):
            return {
                str(key): cls._canonical_json_value(item)
                for key, item in sorted(
                    value.items(), key=lambda pair: str(pair[0])
                )
            }
        if isinstance(value, (tuple, list)):
            return [cls._canonical_json_value(item) for item in value]
        raise RuntimeProtocolError("ModelRequest response_schema包含非JSON值")

    @staticmethod
    def _response_schema(
        allowed_role_ids: tuple[str, ...],
    ) -> Mapping[str, object]:
        properties = {
            name: dict(rule)
            for name, rule in SEND_MESSAGE_ACTION_SCHEMA["properties"].items()
        }
        if allowed_role_ids:
            properties["recipient_role"]["enum"] = list(allowed_role_ids)
        return MappingProxyType({
            **dict(SEND_MESSAGE_ACTION_SCHEMA),
            "properties": properties,
        })

    def _generate_action(
        self,
        request: ModelRequest,
    ) -> tuple[ModelResponse, SendMessageAction]:
        first = self._model_client.generate_structured(request)
        try:
            return first, SendMessageAction.from_model_output(first.data)
        except RuntimeProtocolError as exc:
            repair_request = ModelRequest(
                messages=request.messages + (
                    ModelMessage(
                        "assistant",
                        (TextContentPart(json.dumps(
                            dict(first.data),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )),),
                    ),
                    ModelMessage(
                        "user",
                        (TextContentPart(
                            "Runtime 拒绝了上一个Action："
                            f"{exc}。请只输出一个符合原JSON Schema的"
                            "send_message Action。"
                        ),),
                    ),
                ),
                required_capabilities=request.required_capabilities,
                response_schema=request.response_schema,
            )
            repaired = self._model_client.generate_structured(repair_request)
            try:
                return repaired, SendMessageAction.from_model_output(repaired.data)
            except RuntimeProtocolError as repaired_error:
                raise _InvalidActionAfterRepair(
                    repaired,
                    str(repaired_error),
                ) from repaired_error


__all__ = [
    "AGENT_ACTION_SCHEMA_VERSION",
    "SEND_MESSAGE_ACTION_SCHEMA",
    "AgentActionRunStatus",
    "SendMessageAction",
    "SendMessageActionContext",
    "SendMessageActionResult",
    "SendMessageActionRuntime",
]
