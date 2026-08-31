from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping

from .agent_actions import (
    AgentActionRunStatus,
    SendMessageActionContext,
    SendMessageActionResult,
    SendMessageActionRuntime,
)
from .agent_runtime import AgentManager, MailboxManager
from .model import ModelClient, ModelRequest
from .runtime_domain import (
    AgentCandidate,
    Message,
    RoleAssignmentPolicy,
    RuntimeProtocolError,
    ScopedRef,
    ThreadState,
    canonical_digest,
)
from .runtime_domain.common import nonempty, positive_int
from .runtime_persistence import SQLiteRuntimeDatabase, SQLiteThreadEventStore
from .runtime_persistence.agent import AgentClosedError, AgentPausedError


CONTEXT_BUNDLE_SCHEMA_VERSION = "context-bundle/v1"
TokenCounter = Callable[[str], int]
RecipientClock = Callable[[], str]
SubjectArtifactResolver = Callable[[ScopedRef], str]


@dataclass(frozen=True)
class ReviewSubjectBinding:
    source: str
    artifact_ref: ScopedRef | None = None

    def __post_init__(self) -> None:
        source = nonempty(self.source, "review_subject.source")
        object.__setattr__(self, "source", source)
        if source not in {"inline_message", "artifact"}:
            raise RuntimeProtocolError("review_subject.source不受支持")
        if source == "inline_message" and self.artifact_ref is not None:
            raise RuntimeProtocolError("inline_message不能包含artifact_ref")
        if source == "artifact":
            if not isinstance(self.artifact_ref, ScopedRef):
                raise RuntimeProtocolError("artifact评审对象必须包含ScopedRef")
            self.artifact_ref.assert_type("core:artifact")

    @classmethod
    def inline_message(cls) -> "ReviewSubjectBinding":
        return cls("inline_message")

    @classmethod
    def artifact(cls, artifact_ref: ScopedRef) -> "ReviewSubjectBinding":
        return cls("artifact", artifact_ref)


@dataclass(frozen=True)
class ResolvedReviewSubject:
    source: str
    content: str
    artifact_ref: ScopedRef | None = None

    def to_model_payload(self) -> Mapping[str, object]:
        payload = {
            "artifact_ref": (
                dict(self.artifact_ref.to_dict()) if self.artifact_ref else None
            ),
            "source": self.source,
        }
        if self.source == "inline_message":
            payload["content_ref"] = "trigger_message.content"
        else:
            payload["content"] = self.content
        return MappingProxyType(payload)


class _ContextOverflowError(RuntimeError):
    def __init__(self, required_tokens: int, limit: int) -> None:
        super().__init__(
            f"required context needs {required_tokens} tokens; limit is {limit}"
        )


class _ReviewSubjectError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class ContextPolicy:
    max_input_tokens: int
    max_auto_hops: int = 1
    max_actions_per_invocation: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_input_tokens",
            positive_int(self.max_input_tokens, "max_input_tokens"),
        )
        if self.max_auto_hops != 1:
            raise RuntimeProtocolError("ContextBundle v1只支持max_auto_hops=1")
        if self.max_actions_per_invocation != 1:
            raise RuntimeProtocolError(
                "ContextBundle v1只支持max_actions_per_invocation=1"
            )


@dataclass(frozen=True)
class ContextBundle:
    task_goal: str
    recipient_role: str
    trigger_message: Message
    sender_role: str
    review_subject: ResolvedReviewSubject | None = None
    verified_facts: tuple[str, ...] = ()
    artifact_refs: tuple[ScopedRef, ...] = ()
    constraints: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ("send_message",)
    omitted_refs: tuple[str, ...] = ()
    estimated_input_tokens: int = 0
    schema_version: str = CONTEXT_BUNDLE_SCHEMA_VERSION

    def to_model_payload(self) -> Mapping[str, object]:
        payload = {
            "allowed_actions": list(self.allowed_actions),
            "artifact_refs": [dict(item.to_dict()) for item in self.artifact_refs],
            "constraints": list(self.constraints),
            "recipient_role": self.recipient_role,
            "task_goal": self.task_goal,
            "trigger_message": {
                "content": self.trigger_message.body,
                "message_id": self.trigger_message.message_id,
                "sender_role": self.sender_role,
            },
            "verified_facts": list(self.verified_facts),
        }
        if self.review_subject is not None:
            payload["review_subject"] = dict(
                self.review_subject.to_model_payload()
            )
        return MappingProxyType(payload)


class RecipientRunStatus(str, Enum):
    PROCESSED = "processed"
    NO_MESSAGE = "no_message"
    NEEDS_INPUT = "needs_input"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RecipientExecutionResult:
    status: RecipientRunStatus
    trigger_message_ref: ScopedRef | None = None
    recipient_invocation_ref: ScopedRef | None = None
    context_bundle: ContextBundle | None = None
    action_result: SendMessageActionResult | None = None
    auto_hops_used: int = 0
    actions_used: int = 0
    auto_continuation_scheduled: bool = False
    error_code: str = ""
    error_detail: str = ""


@dataclass(frozen=True)
class OneHopExchangeResult:
    sender_action: SendMessageActionResult
    recipient_execution: RecipientExecutionResult | None = None


class RecipientMessageRuntime:
    """Processes one durable mailbox Message and stops after one reply Action."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        model_client: ModelClient,
        clock: RecipientClock,
        token_counter: TokenCounter,
        subject_artifact_resolver: SubjectArtifactResolver | None = None,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database必须是SQLiteRuntimeDatabase")
        if not callable(clock):
            raise TypeError("clock必须可调用")
        if not callable(token_counter):
            raise TypeError("token_counter必须可调用")
        self._database = database
        self._model_client = model_client
        self._clock = clock
        self._token_counter = token_counter
        if subject_artifact_resolver is not None and not callable(
            subject_artifact_resolver
        ):
            raise TypeError("subject_artifact_resolver必须可调用")
        self._subject_artifact_resolver = subject_artifact_resolver
        self._mailbox = MailboxManager(database, clock=clock)
        self._agents = AgentManager(database, clock=clock)
        self._threads = SQLiteThreadEventStore(database)
        self._lock = RLock()

    def run_next(
        self,
        *,
        scope_id: str,
        thread_id: str,
        recipient_agent_instance_id: str,
        recipient_agent_session_id: str,
        recipient_role: str,
        sender_role: str,
        task_goal: str,
        review_subject: ReviewSubjectBinding | None = None,
        reply_role_candidates: Mapping[str, tuple[AgentCandidate, ...]],
        assignment_policy: RoleAssignmentPolicy,
        context_policy: ContextPolicy,
        verified_facts: tuple[str, ...] = (),
        artifact_refs: tuple[ScopedRef, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> RecipientExecutionResult:
        scope = nonempty(scope_id, "scope_id")
        thread = nonempty(thread_id, "thread_id")
        agent_id = nonempty(
            recipient_agent_instance_id,
            "recipient_agent_instance_id",
        )
        session_id = nonempty(
            recipient_agent_session_id,
            "recipient_agent_session_id",
        )
        role = nonempty(recipient_role, "recipient_role")
        sender = nonempty(sender_role, "sender_role")
        goal = nonempty(task_goal, "task_goal")
        if not isinstance(context_policy, ContextPolicy):
            raise TypeError("context_policy必须是ContextPolicy")

        with self._lock:
            current_thread = self._threads.get_thread(scope, thread)
            if current_thread is None:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.REJECTED,
                    error_code="thread_not_found",
                )
            if current_thread.state is not ThreadState.OPEN:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.REJECTED,
                    error_code=f"thread_{current_thread.state.value}",
                )
            try:
                self._agents.require_work_admission(scope, thread, agent_id)
            except AgentPausedError:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.REJECTED,
                    error_code="recipient_paused",
                )
            except AgentClosedError:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.REJECTED,
                    error_code="recipient_closed",
                )

            pending = tuple(
                item
                for item in self._mailbox.list_mailbox(
                    scope_id=scope,
                    thread_id=thread,
                    agent_instance_id=agent_id,
                    agent_session_id=session_id,
                )
                if not item.consumed
            )
            if not pending:
                return RecipientExecutionResult(status=RecipientRunStatus.NO_MESSAGE)
            preview = pending[0]
            if review_subject is None:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.NEEDS_INPUT,
                    trigger_message_ref=preview.message.reference,
                    error_code="missing_review_subject",
                    error_detail=(
                        "Reviewer需要绑定Message正文或不可变Artifact作为评审对象"
                    ),
                )
            try:
                bundle = self._compile_context(
                    task_goal=goal,
                    review_subject=review_subject,
                    recipient_role=role,
                    trigger_message=preview.message,
                    sender_role=sender,
                    verified_facts=verified_facts,
                    artifact_refs=artifact_refs,
                    constraints=constraints,
                    policy=context_policy,
                )
            except _ContextOverflowError as exc:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.NEEDS_INPUT,
                    trigger_message_ref=preview.message.reference,
                    error_code="context_overflow",
                    error_detail=str(exc),
                )
            except _ReviewSubjectError as exc:
                return RecipientExecutionResult(
                    status=RecipientRunStatus.NEEDS_INPUT,
                    trigger_message_ref=preview.message.reference,
                    error_code=exc.code,
                    error_detail=str(exc),
                )
            delivery = self._mailbox.receive_next(
                scope_id=scope,
                thread_id=thread,
                agent_instance_id=agent_id,
                agent_session_id=session_id,
            )
            if (
                delivery is None
                or delivery.message.message_id != preview.message.message_id
            ):
                raise RuntimeProtocolError("Mailbox预览与领取的Message不一致")

            invocation_ref = self._invocation_ref(delivery.message, agent_id)
            payload = json.dumps(
                dict(bundle.to_model_payload()),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            request = ModelRequest.from_text_messages([
                {
                    "role": "system",
                    "content": (
                        f"你当前承担规范Role {role}。只处理ContextBundle中的"
                        "当前任务；评审结论必须以review_subject为依据，"
                        "若它包含content_ref，必须使用该路径指向的正文；"
                        "不得用未被引用的trigger元数据或常识臆造缺失材料；"
                        "只输出一个send_message Action；"
                        "本次回复后必须停止。"
                    ),
                },
                {"role": "user", "content": payload},
            ])
            action_result = SendMessageActionRuntime(
                self._database,
                model_client=self._model_client,
                clock=self._clock,
            ).run(
                request=request,
                context=SendMessageActionContext(
                    scope_id=scope,
                    thread_ref=delivery.message.thread_ref,
                    turn_ref=delivery.message.turn_ref,
                    invocation_ref=invocation_ref,
                    sender_agent_instance_ref=ScopedRef(
                        scope,
                        "core:agent_instance",
                        agent_id,
                        1,
                    ),
                    step_index=1,
                    parent_message_ref=delivery.message.reference,
                ),
                role_candidates=reply_role_candidates,
                assignment_policy=assignment_policy,
            )
            return RecipientExecutionResult(
                status=RecipientRunStatus.PROCESSED,
                trigger_message_ref=delivery.message.reference,
                recipient_invocation_ref=invocation_ref,
                context_bundle=bundle,
                action_result=action_result,
                auto_hops_used=1,
                actions_used=1,
                auto_continuation_scheduled=False,
                error_code=action_result.error_code,
                error_detail=action_result.error_detail,
            )

    def _compile_context(
        self,
        *,
        task_goal: str,
        review_subject: ReviewSubjectBinding | None,
        recipient_role: str,
        trigger_message: Message,
        sender_role: str,
        verified_facts: tuple[str, ...],
        artifact_refs: tuple[ScopedRef, ...],
        constraints: tuple[str, ...],
        policy: ContextPolicy,
    ) -> ContextBundle:
        resolved_subject = (
            ResolvedReviewSubject(
                source="inline_message",
                content=trigger_message.body,
            )
            if review_subject is not None
            and review_subject.source == "inline_message"
            else None
        )
        if review_subject is not None and review_subject.source == "artifact":
            artifact_ref = review_subject.artifact_ref
            if artifact_ref not in trigger_message.artifact_refs:
                raise _ReviewSubjectError(
                    "subject_artifact_unbound",
                    "评审Artifact必须先绑定并持久化到触发Message",
                )
            if self._subject_artifact_resolver is None:
                raise _ReviewSubjectError(
                    "subject_artifact_unavailable",
                    "评审Artifact没有可用的Runtime解析器",
                )
            try:
                content = self._subject_artifact_resolver(artifact_ref)
            except (KeyError, FileNotFoundError) as exc:
                raise _ReviewSubjectError(
                    "subject_artifact_unavailable",
                    f"评审Artifact无法解析: {artifact_ref.entity_id}",
                ) from exc
            if not isinstance(content, str) or not content.strip():
                raise _ReviewSubjectError(
                    "subject_artifact_unavailable",
                    "评审Artifact必须解析为非空文本",
                )
            resolved_subject = ResolvedReviewSubject(
                source="artifact",
                content=content,
                artifact_ref=artifact_ref,
            )
        bundle = ContextBundle(
            task_goal=task_goal,
            recipient_role=recipient_role,
            trigger_message=trigger_message,
            sender_role=sender_role,
            review_subject=resolved_subject,
        )
        estimated = self._measure(bundle)
        if estimated > policy.max_input_tokens:
            raise _ContextOverflowError(estimated, policy.max_input_tokens)

        included_constraints: list[str] = []
        included_facts: list[str] = []
        included_artifacts: list[ScopedRef] = []
        omitted: list[str] = []
        categories = (
            ("constraints", tuple(constraints)),
            ("verified_facts", tuple(verified_facts)),
            ("artifact_refs", tuple(artifact_refs)),
        )
        for field_name, values in categories:
            for index, value in enumerate(values):
                candidate_constraints = list(included_constraints)
                candidate_facts = list(included_facts)
                candidate_artifacts = list(included_artifacts)
                if field_name == "constraints":
                    candidate_constraints.append(value)
                elif field_name == "verified_facts":
                    candidate_facts.append(value)
                else:
                    candidate_artifacts.append(value)
                candidate = ContextBundle(
                    task_goal=task_goal,
                    recipient_role=recipient_role,
                    trigger_message=trigger_message,
                    sender_role=sender_role,
                    review_subject=resolved_subject,
                    constraints=tuple(candidate_constraints),
                    verified_facts=tuple(candidate_facts),
                    artifact_refs=tuple(candidate_artifacts),
                )
                candidate_size = self._measure(candidate)
                if candidate_size <= policy.max_input_tokens:
                    included_constraints = candidate_constraints
                    included_facts = candidate_facts
                    included_artifacts = candidate_artifacts
                    estimated = candidate_size
                else:
                    omitted.append(f"{field_name}[{index}]")
        return ContextBundle(
            task_goal=task_goal,
            recipient_role=recipient_role,
            trigger_message=trigger_message,
            sender_role=sender_role,
            review_subject=resolved_subject,
            constraints=tuple(included_constraints),
            verified_facts=tuple(included_facts),
            artifact_refs=tuple(included_artifacts),
            omitted_refs=tuple(omitted),
            estimated_input_tokens=estimated,
        )

    def _measure(self, bundle: ContextBundle) -> int:
        payload = json.dumps(
            dict(bundle.to_model_payload()),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        estimated = self._token_counter(payload)
        if (
            not isinstance(estimated, int)
            or isinstance(estimated, bool)
            or estimated < 0
        ):
            raise RuntimeProtocolError("token_counter必须返回非负整数")
        return estimated

    @staticmethod
    def _invocation_ref(message: Message, recipient_agent_id: str) -> ScopedRef:
        identity = canonical_digest({
            "trigger_message_id": message.message_id,
            "recipient_agent_id": recipient_agent_id,
            "auto_hop": 1,
        })
        return ScopedRef(
            message.scope_id,
            "core:invocation",
            f"recipient-invocation-{identity}",
            1,
        )


class OneHopExchangeRuntime:
    """Runs one sender Action, one recipient Action, then stops propagation."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        sender_model_client: ModelClient,
        recipient_model_client: ModelClient,
        clock: RecipientClock,
        token_counter: TokenCounter,
        subject_artifact_resolver: SubjectArtifactResolver | None = None,
    ) -> None:
        self._database = database
        self._sender_model_client = sender_model_client
        self._clock = clock
        self._recipient = RecipientMessageRuntime(
            database,
            model_client=recipient_model_client,
            clock=clock,
            token_counter=token_counter,
            subject_artifact_resolver=subject_artifact_resolver,
        )

    def run(
        self,
        *,
        sender_request: ModelRequest,
        sender_context: SendMessageActionContext,
        sender_role_candidates: Mapping[str, tuple[AgentCandidate, ...]],
        sender_assignment_policy: RoleAssignmentPolicy,
        sender_role: str,
        task_goal: str,
        review_subject: ReviewSubjectBinding | None = None,
        reply_role_candidates: Mapping[str, tuple[AgentCandidate, ...]],
        recipient_assignment_policy: RoleAssignmentPolicy,
        context_policy: ContextPolicy,
        verified_facts: tuple[str, ...] = (),
        artifact_refs: tuple[ScopedRef, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> OneHopExchangeResult:
        sender_action = SendMessageActionRuntime(
            self._database,
            model_client=self._sender_model_client,
            clock=self._clock,
        ).run(
            request=sender_request,
            context=sender_context,
            role_candidates=sender_role_candidates,
            assignment_policy=sender_assignment_policy,
        )
        if (
            sender_action.status is not AgentActionRunStatus.DELIVERED
            or sender_action.assignment is None
            or sender_action.assignment.selected_agent_instance_ref is None
            or sender_action.assignment.selected_agent_session_ref is None
        ):
            return OneHopExchangeResult(sender_action=sender_action)
        recipient_execution = self._recipient.run_next(
            scope_id=sender_context.scope_id,
            thread_id=sender_context.thread_ref.entity_id,
            recipient_agent_instance_id=(
                sender_action.assignment.selected_agent_instance_ref.entity_id
            ),
            recipient_agent_session_id=(
                sender_action.assignment.selected_agent_session_ref.entity_id
            ),
            recipient_role=(
                sender_action.assignment.requirement.role_ref.entity_id
            ),
            sender_role=sender_role,
            task_goal=task_goal,
            review_subject=review_subject,
            reply_role_candidates=reply_role_candidates,
            assignment_policy=recipient_assignment_policy,
            context_policy=context_policy,
            verified_facts=verified_facts,
            artifact_refs=artifact_refs,
            constraints=constraints,
        )
        return OneHopExchangeResult(
            sender_action=sender_action,
            recipient_execution=recipient_execution,
        )


__all__ = [
    "CONTEXT_BUNDLE_SCHEMA_VERSION",
    "ContextBundle",
    "ContextPolicy",
    "OneHopExchangeResult",
    "OneHopExchangeRuntime",
    "RecipientExecutionResult",
    "RecipientMessageRuntime",
    "RecipientRunStatus",
    "ResolvedReviewSubject",
    "ReviewSubjectBinding",
]
