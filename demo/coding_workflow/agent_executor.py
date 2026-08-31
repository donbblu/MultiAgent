from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from .local_execution import (
    CODEX_CLI_SAFE_PREFIX_OPTIONS,
    PROFILE_CODEX_CLI,
    ExecutionOutcome,
    prepare_execution,
    redact_text,
    run_prepared,
)
from .local_execution_approval import LocalExecutionApprover
from .runtime_domain import (
    RuntimeProtocolError,
    ScopedRef,
    ScopedSnapshotRef,
)


class AgentExecutionPermission(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"


class AgentExecutionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class AgentExecutionRecoveryDecision(str, Enum):
    CREATE_NEW_SESSION = "create_new_session"
    STOP_TASK = "stop_task"


class CodexCliFailureKind(str, Enum):
    NONE = "none"
    BACKEND_SESSION_UNAVAILABLE = "backend_session_unavailable"


@dataclass(frozen=True)
class AgentExecutionStateEnvelope:
    scope_id: str
    task_ref: ScopedRef
    snapshot_ref: ScopedSnapshotRef
    permission_snapshot_ref: ScopedSnapshotRef
    artifact_refs: tuple[ScopedSnapshotRef, ...]
    permission: AgentExecutionPermission

    def __post_init__(self) -> None:
        if not isinstance(self.scope_id, str) or not self.scope_id.strip():
            raise RuntimeProtocolError("state_envelope.scope_id不能为空")
        scope_id = self.scope_id.strip()
        object.__setattr__(self, "scope_id", scope_id)
        if not isinstance(self.task_ref, ScopedRef):
            raise RuntimeProtocolError("state_envelope.task_ref必须是ScopedRef")
        self.task_ref.assert_scope(scope_id, "state_envelope.task_ref")
        self.task_ref.assert_type("core:task")
        for name, value, entity_type in (
            (
                "snapshot_ref",
                self.snapshot_ref,
                "core:task_snapshot",
            ),
            (
                "permission_snapshot_ref",
                self.permission_snapshot_ref,
                "core:permission_snapshot",
            ),
        ):
            if not isinstance(value, ScopedSnapshotRef):
                raise RuntimeProtocolError(
                    f"state_envelope.{name}必须是ScopedSnapshotRef"
                )
            value.ref.assert_scope(scope_id, f"state_envelope.{name}")
            value.ref.assert_type(entity_type)
        artifacts = tuple(self.artifact_refs)
        seen: set[ScopedRef] = set()
        for artifact in artifacts:
            if not isinstance(artifact, ScopedSnapshotRef):
                raise RuntimeProtocolError(
                    "state_envelope.artifact_refs必须包含ScopedSnapshotRef"
                )
            artifact.ref.assert_scope(
                scope_id, "state_envelope.artifact_refs"
            )
            artifact.ref.assert_type("core:artifact")
            if artifact.ref in seen:
                raise RuntimeProtocolError(
                    "state_envelope.artifact_refs不能重复"
                )
            seen.add(artifact.ref)
        if not isinstance(self.permission, AgentExecutionPermission):
            raise RuntimeProtocolError(
                "state_envelope.permission必须是AgentExecutionPermission"
            )
        object.__setattr__(self, "artifact_refs", artifacts)


@dataclass(frozen=True)
class AgentExecutionContextPart:
    ref: ScopedSnapshotRef
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ScopedSnapshotRef):
            raise RuntimeProtocolError("recovery context ref必须是ScopedSnapshotRef")
        if not isinstance(self.content, str) or not self.content.strip():
            raise RuntimeProtocolError("recovery context content不能为空")
        actual = sha256(self.content.encode("utf-8")).hexdigest()
        if self.ref.content_hash != actual:
            raise RuntimeProtocolError("recovery context content hash不匹配")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "ref": dict(self.ref.to_dict()),
            "content": self.content,
        })


@dataclass(frozen=True)
class AgentExecutionRecoveryContext:
    scope_id: str
    task_ref: ScopedRef
    task_snapshot: AgentExecutionContextPart
    permission_snapshot: AgentExecutionContextPart
    messages: tuple[AgentExecutionContextPart, ...]
    artifacts: tuple[AgentExecutionContextPart, ...]
    schema_version: str = "agent-execution-recovery-context/v1"

    def __post_init__(self) -> None:
        if not isinstance(self.scope_id, str) or not self.scope_id.strip():
            raise RuntimeProtocolError("recovery context scope_id不能为空")
        scope_id = self.scope_id.strip()
        object.__setattr__(self, "scope_id", scope_id)
        if not isinstance(self.task_ref, ScopedRef):
            raise RuntimeProtocolError("recovery context task_ref必须是ScopedRef")
        self.task_ref.assert_scope(scope_id, "recovery_context.task_ref")
        self.task_ref.assert_type("core:task")
        for field_name, part, entity_type in (
            ("task_snapshot", self.task_snapshot, "core:task_snapshot"),
            (
                "permission_snapshot",
                self.permission_snapshot,
                "core:permission_snapshot",
            ),
        ):
            self._validate_part(field_name, part, entity_type, scope_id)
        messages = tuple(self.messages)
        artifacts = tuple(self.artifacts)
        if not messages:
            raise RuntimeProtocolError("recovery context至少需要一条Message")
        for index, part in enumerate(messages):
            self._validate_part(
                f"messages[{index}]", part, "core:message", scope_id
            )
        for index, part in enumerate(artifacts):
            self._validate_part(
                f"artifacts[{index}]", part, "core:artifact", scope_id
            )
        if self.schema_version != "agent-execution-recovery-context/v1":
            raise RuntimeProtocolError("recovery context schema_version不受支持")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "artifacts", artifacts)

    @staticmethod
    def _validate_part(
        field_name: str,
        part: AgentExecutionContextPart,
        entity_type: str,
        scope_id: str,
    ) -> None:
        if not isinstance(part, AgentExecutionContextPart):
            raise RuntimeProtocolError(
                f"recovery context {field_name}必须是ContextPart"
            )
        part.ref.ref.assert_scope(scope_id, f"recovery_context.{field_name}")
        part.ref.ref.assert_type(entity_type)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "task_ref": dict(self.task_ref.to_dict()),
            "task_snapshot": dict(self.task_snapshot.to_dict()),
            "permission_snapshot": dict(self.permission_snapshot.to_dict()),
            "messages": [dict(item.to_dict()) for item in self.messages],
            "artifacts": [dict(item.to_dict()) for item in self.artifacts],
        })

    def to_prompt(self) -> str:
        return json.dumps(
            dict(self.to_dict()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def context_digest(self) -> str:
        return sha256(self.to_prompt().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentExecutionRecoveryPrompt:
    confirmation_id: str
    invocation_id: str
    schema_version: str = "agent-execution-recovery-prompt/v1"
    status: str = "awaiting_user_confirmation"

    def __post_init__(self) -> None:
        for name in ("confirmation_id", "invocation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeProtocolError(f"{name}不能为空")
        if self.schema_version != "agent-execution-recovery-prompt/v1":
            raise RuntimeProtocolError("recovery prompt schema_version不受支持")
        if self.status != "awaiting_user_confirmation":
            raise RuntimeProtocolError("recovery prompt status不受支持")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "status": self.status,
            "confirmation_id": self.confirmation_id,
            "invocation_id": self.invocation_id,
            "message": "上次 Agent 会话无法恢复。是否创建新会话继续？",
            "allowed_decisions": [
                AgentExecutionRecoveryDecision.CREATE_NEW_SESSION.value,
                AgentExecutionRecoveryDecision.STOP_TASK.value,
            ],
        })


@dataclass(frozen=True)
class AgentExecutionRecoveryConfirmation:
    confirmation_id: str
    invocation_id: str
    decision: AgentExecutionRecoveryDecision

    def __post_init__(self) -> None:
        for name in ("confirmation_id", "invocation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeProtocolError(f"{name}不能为空")
        if not isinstance(self.decision, AgentExecutionRecoveryDecision):
            raise RuntimeProtocolError("recovery confirmation decision无效")


@dataclass(frozen=True)
class AgentExecutionRecoveryStopped:
    invocation_id: str
    schema_version: str = "agent-execution-recovery-stopped/v1"
    status: str = "stopped"

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise RuntimeProtocolError("invocation_id不能为空")
        if self.schema_version != "agent-execution-recovery-stopped/v1":
            raise RuntimeProtocolError("recovery stopped schema_version不受支持")
        if self.status != "stopped":
            raise RuntimeProtocolError("recovery stopped status不受支持")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "status": self.status,
            "invocation_id": self.invocation_id,
        })


@dataclass(frozen=True)
class AgentExecutionRecoveryBlocked:
    invocation_id: str
    schema_version: str = "agent-execution-recovery-blocked/v1"
    status: str = "recovery_attempt_unresolved"

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise RuntimeProtocolError("invocation_id不能为空")
        if self.schema_version != "agent-execution-recovery-blocked/v1":
            raise RuntimeProtocolError("recovery blocked schema_version不受支持")
        if self.status != "recovery_attempt_unresolved":
            raise RuntimeProtocolError("recovery blocked status不受支持")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "status": self.status,
            "invocation_id": self.invocation_id,
        })


@dataclass(frozen=True)
class AgentExecutionRequest:
    invocation_id: str
    thread_id: str
    agent_id: str
    prompt: str
    workspace_root: Path
    permission: AgentExecutionPermission
    timeout_seconds: float
    state_envelope: AgentExecutionStateEnvelope
    backend_id: str = "codex_cli"
    backend_session_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "invocation_id",
            "thread_id",
            "agent_id",
            "prompt",
            "backend_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        root = Path(self.workspace_root).resolve()
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        if not isinstance(self.permission, AgentExecutionPermission):
            raise TypeError("permission must be AgentExecutionPermission")
        if not isinstance(self.state_envelope, AgentExecutionStateEnvelope):
            raise TypeError(
                "state_envelope must be AgentExecutionStateEnvelope"
            )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be greater than zero")
        if not isinstance(self.backend_session_id, str):
            raise TypeError("backend_session_id must be a string")
        object.__setattr__(self, "workspace_root", root)


@dataclass(frozen=True)
class AgentExecutionUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    def __post_init__(self) -> None:
        if min(
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
        ) < 0:
            raise ValueError("Agent execution token usage cannot be negative")


@dataclass(frozen=True)
class AgentExecutionEvent:
    kind: str
    data: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("Agent execution event kind cannot be empty")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class AgentExecutionResult:
    status: AgentExecutionStatus
    backend_id: str
    cli_version: str
    backend_session_id: str
    sandbox: str
    final_message: str
    events: tuple[AgentExecutionEvent, ...]
    usage: AgentExecutionUsage
    duration_ms: int


@dataclass(frozen=True)
class CodexCliLaunch:
    argv: tuple[str, ...]
    stdin_text: str
    workspace_root: Path
    timeout_seconds: float


@dataclass(frozen=True)
class CodexCliProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    failure_kind: str = CodexCliFailureKind.NONE.value

    def __post_init__(self) -> None:
        kind = self.failure_kind
        if isinstance(kind, CodexCliFailureKind):
            kind = kind.value
        if kind not in {item.value for item in CodexCliFailureKind}:
            raise ValueError("unsupported Codex CLI failure kind")
        object.__setattr__(self, "failure_kind", kind)


class CodexCliTransport(Protocol):
    def run(self, launch: CodexCliLaunch) -> CodexCliProcessResult: ...


class AgentExecutor(Protocol):
    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult: ...


class AgentExecutionStateAuthority(Protocol):
    def expected_for(
        self,
        invocation_id: str,
    ) -> AgentExecutionStateEnvelope | None: ...


class AgentExecutionReplayStore(Protocol):
    def completed_for(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> AgentExecutionResult | None: ...

    def record_completed(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult: ...


class BackendSessionBindingStore(Protocol):
    def bound_session_for(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> str | None: ...

    def record_session_binding(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        backend_session_id: str,
    ) -> str: ...


class AgentExecutionRecoveryContextStore(Protocol):
    def recovery_context_for(
        self,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> AgentExecutionRecoveryContext | None: ...

    def record_session_recovery(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
        replacement_backend_session_id: str,
    ) -> str: ...

    def request_session_recovery_confirmation(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
    ) -> AgentExecutionRecoveryPrompt: ...

    def record_session_recovery_confirmation(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
        stale_backend_session_id: str,
    ) -> None: ...

    def pending_session_recovery_confirmation(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryPrompt | None: ...

    def validate_recorded_session_recovery_confirmation(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> None: ...

    def stopped_session_recovery_for(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryStopped | None: ...

    def claim_session_recovery_attempt(
        self,
        *,
        confirmation: AgentExecutionRecoveryConfirmation,
        state: AgentExecutionStateEnvelope,
        context: AgentExecutionRecoveryContext,
    ) -> None: ...

    def unresolved_session_recovery_attempt_for(
        self,
        *,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
        thread_id: str,
        agent_id: str,
        backend_id: str,
    ) -> AgentExecutionRecoveryBlocked | None: ...


class FrozenAgentExecutionStateAuthority:
    def __init__(
        self,
        states: Mapping[str, AgentExecutionStateEnvelope],
    ) -> None:
        frozen: dict[str, AgentExecutionStateEnvelope] = {}
        for invocation_id, state in states.items():
            if not isinstance(invocation_id, str) or not invocation_id.strip():
                raise ValueError("state authority invocation_id cannot be empty")
            if not isinstance(state, AgentExecutionStateEnvelope):
                raise TypeError(
                    "state authority values must be AgentExecutionStateEnvelope"
                )
            frozen[invocation_id.strip()] = state
        self._states = MappingProxyType(frozen)

    def expected_for(
        self,
        invocation_id: str,
    ) -> AgentExecutionStateEnvelope | None:
        return self._states.get(invocation_id)


class AgentExecutionStateRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AgentExecutionRecoveryConfirmationRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BackendSessionUnavailable(RuntimeError):
    def __init__(self, *, backend_id: str, backend_session_id: str) -> None:
        super().__init__("backend_session_unavailable")
        self.backend_id = backend_id
        self.backend_session_id = backend_session_id


class AgentExecutionRuntime:
    def __init__(
        self,
        *,
        executor: AgentExecutor,
        state_authority: AgentExecutionStateAuthority,
        replay_store: AgentExecutionReplayStore | None = None,
        session_store: BackendSessionBindingStore | None = None,
        recovery_context_store: AgentExecutionRecoveryContextStore | None = None,
    ) -> None:
        if not callable(getattr(executor, "run", None)):
            raise TypeError("executor must implement AgentExecutor")
        if not callable(getattr(state_authority, "expected_for", None)):
            raise TypeError(
                "state_authority must implement AgentExecutionStateAuthority"
            )
        self._executor = executor
        self._state_authority = state_authority
        if replay_store is not None:
            for method in ("completed_for", "record_completed"):
                if not callable(getattr(replay_store, method, None)):
                    raise TypeError(
                        "replay_store must implement AgentExecutionReplayStore"
                    )
        self._replay_store = replay_store
        if session_store is not None:
            for method in ("bound_session_for", "record_session_binding"):
                if not callable(getattr(session_store, method, None)):
                    raise TypeError(
                        "session_store must implement BackendSessionBindingStore"
                    )
        self._session_store = session_store
        if recovery_context_store is not None:
            for method in (
                "recovery_context_for",
                "record_session_recovery",
                "request_session_recovery_confirmation",
                "record_session_recovery_confirmation",
                "pending_session_recovery_confirmation",
                "validate_recorded_session_recovery_confirmation",
                "stopped_session_recovery_for",
                "claim_session_recovery_attempt",
                "unresolved_session_recovery_attempt_for",
            ):
                if not callable(getattr(recovery_context_store, method, None)):
                    raise TypeError(
                        "recovery_context_store must implement "
                        "AgentExecutionRecoveryContextStore"
                    )
        self._recovery_context_store = recovery_context_store

    def run(
        self,
        request: AgentExecutionRequest,
    ) -> (
        AgentExecutionResult
        | AgentExecutionRecoveryPrompt
        | AgentExecutionRecoveryStopped
        | AgentExecutionRecoveryBlocked
    ):
        if not isinstance(request, AgentExecutionRequest):
            raise TypeError("request must be AgentExecutionRequest")
        expected = self._state_authority.expected_for(request.invocation_id)
        if expected is None:
            raise AgentExecutionStateRejected("state_not_found")
        if (
            request.permission is not request.state_envelope.permission
            or request.state_envelope != expected
        ):
            raise AgentExecutionStateRejected("state_mismatch")
        effective_request = request
        bound_session = None
        if self._session_store is not None:
            bound_session = self._session_store.bound_session_for(
                scope_id=expected.scope_id,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
            )
            if (
                request.backend_session_id
                and request.backend_session_id != bound_session
            ):
                raise AgentExecutionStateRejected("backend_session_mismatch")
            if bound_session is not None:
                effective_request = replace(
                    request,
                    backend_session_id=bound_session,
                )
        if self._replay_store is not None:
            completed = self._replay_store.completed_for(
                request.invocation_id,
                expected,
            )
            if completed is not None:
                if completed.backend_id != request.backend_id:
                    raise AgentExecutionStateRejected("backend_mismatch")
                if (
                    bound_session is not None
                    and completed.backend_session_id != bound_session
                ):
                    raise AgentExecutionStateRejected(
                        "backend_session_mismatch"
                    )
                return completed
        if self._recovery_context_store is not None:
            stopped_recovery = (
                self._recovery_context_store.stopped_session_recovery_for(
                    invocation_id=request.invocation_id,
                    state=expected,
                    thread_id=request.thread_id,
                    agent_id=request.agent_id,
                    backend_id=request.backend_id,
                )
            )
            if stopped_recovery is not None:
                return stopped_recovery
            blocked_recovery = self._recovery_context_store.unresolved_session_recovery_attempt_for(
                invocation_id=request.invocation_id,
                state=expected,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
            )
            if blocked_recovery is not None:
                return blocked_recovery
            pending_recovery = (
                self._recovery_context_store.pending_session_recovery_confirmation(
                    invocation_id=request.invocation_id,
                    state=expected,
                    thread_id=request.thread_id,
                    agent_id=request.agent_id,
                    backend_id=request.backend_id,
                )
            )
            if pending_recovery is not None:
                return pending_recovery
        recovered_context = None
        recovered_from_session = ""
        try:
            result = self._executor.run(effective_request)
        except BackendSessionUnavailable as exc:
            if (
                self._recovery_context_store is None
                or bound_session is None
                or exc.backend_id != request.backend_id
                or exc.backend_session_id != bound_session
            ):
                raise AgentExecutionStateRejected(
                    "backend_session_unavailable"
                ) from exc
            recovered_context = (
                self._recovery_context_store.recovery_context_for(
                    request.invocation_id,
                    expected,
                )
            )
            if recovered_context is None:
                raise AgentExecutionStateRejected(
                    "recovery_context_not_found"
                ) from exc
            return self._recovery_context_store.request_session_recovery_confirmation(
                invocation_id=request.invocation_id,
                state=expected,
                context=recovered_context,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
                stale_backend_session_id=bound_session,
            )
        if result.backend_id != request.backend_id:
            raise AgentExecutionStateRejected("backend_mismatch")
        if (
            result.status is AgentExecutionStatus.FAILED
            and bound_session is not None
            and not result.events
            and not result.backend_session_id
            and self._recovery_context_store is not None
        ):
            recovered_context = self._recovery_context_store.recovery_context_for(
                request.invocation_id,
                expected,
            )
            if recovered_context is None:
                raise AgentExecutionStateRejected("recovery_context_not_found")
            return self._recovery_context_store.request_session_recovery_confirmation(
                invocation_id=request.invocation_id,
                state=expected,
                context=recovered_context,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
                stale_backend_session_id=bound_session,
            )
        if self._session_store is not None and result.backend_session_id:
            self._session_store.record_session_binding(
                scope_id=expected.scope_id,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
                backend_session_id=result.backend_session_id,
            )
        if self._replay_store is None:
            return result
        return self._replay_store.record_completed(
            request.invocation_id,
            expected,
            result,
        )

    def confirm_session_recovery(
        self,
        request: AgentExecutionRequest,
        confirmation: AgentExecutionRecoveryConfirmation,
    ) -> AgentExecutionResult | AgentExecutionRecoveryStopped:
        if not isinstance(request, AgentExecutionRequest):
            raise TypeError("request must be AgentExecutionRequest")
        if not isinstance(
            confirmation,
            AgentExecutionRecoveryConfirmation,
        ):
            raise TypeError(
                "confirmation must be AgentExecutionRecoveryConfirmation"
            )
        if confirmation.invocation_id != request.invocation_id:
            raise AgentExecutionStateRejected("recovery_confirmation_mismatch")
        expected = self._state_authority.expected_for(request.invocation_id)
        if expected is None:
            raise AgentExecutionStateRejected("state_not_found")
        if (
            request.permission is not request.state_envelope.permission
            or request.state_envelope != expected
        ):
            raise AgentExecutionStateRejected("state_mismatch")
        if self._session_store is None or self._recovery_context_store is None:
            raise AgentExecutionStateRejected("recovery_confirmation_unavailable")
        context = self._recovery_context_store.recovery_context_for(
            request.invocation_id,
            expected,
        )
        if context is None:
            raise AgentExecutionStateRejected("recovery_context_not_found")
        if self._replay_store is not None:
            completed = self._replay_store.completed_for(
                request.invocation_id,
                expected,
            )
            if completed is not None:
                try:
                    self._recovery_context_store.validate_recorded_session_recovery_confirmation(
                        confirmation=confirmation,
                        state=expected,
                        context=context,
                        thread_id=request.thread_id,
                        agent_id=request.agent_id,
                        backend_id=request.backend_id,
                    )
                except AgentExecutionRecoveryConfirmationRejected as exc:
                    raise AgentExecutionStateRejected(exc.code) from exc
                if completed.backend_id != request.backend_id:
                    raise AgentExecutionStateRejected("backend_mismatch")
                return completed
        bound_session = self._session_store.bound_session_for(
            scope_id=expected.scope_id,
            thread_id=request.thread_id,
            agent_id=request.agent_id,
            backend_id=request.backend_id,
        )
        if bound_session is None:
            raise AgentExecutionStateRejected("backend_session_unavailable")
        try:
            self._recovery_context_store.record_session_recovery_confirmation(
                confirmation=confirmation,
                state=expected,
                context=context,
                thread_id=request.thread_id,
                agent_id=request.agent_id,
                backend_id=request.backend_id,
                stale_backend_session_id=bound_session,
            )
        except AgentExecutionRecoveryConfirmationRejected as exc:
            raise AgentExecutionStateRejected(exc.code) from exc
        if confirmation.decision is AgentExecutionRecoveryDecision.STOP_TASK:
            return AgentExecutionRecoveryStopped(
                invocation_id=request.invocation_id,
            )
        try:
            self._recovery_context_store.claim_session_recovery_attempt(
                confirmation=confirmation,
                state=expected,
                context=context,
            )
        except AgentExecutionRecoveryConfirmationRejected as exc:
            raise AgentExecutionStateRejected(exc.code) from exc
        recovery_request = replace(
            request,
            prompt=context.to_prompt(),
            backend_session_id="",
        )
        try:
            result = self._executor.run(recovery_request)
        except BackendSessionUnavailable as exc:
            raise AgentExecutionStateRejected(
                "backend_session_recovery_failed"
            ) from exc
        if (
            result.backend_id != request.backend_id
            or result.status is not AgentExecutionStatus.COMPLETED
            or not result.backend_session_id
            or result.backend_session_id == bound_session
        ):
            raise AgentExecutionStateRejected(
                "backend_session_recovery_failed"
            )
        self._recovery_context_store.record_session_recovery(
            invocation_id=request.invocation_id,
            state=expected,
            context=context,
            thread_id=request.thread_id,
            agent_id=request.agent_id,
            backend_id=request.backend_id,
            stale_backend_session_id=bound_session,
            replacement_backend_session_id=result.backend_session_id,
        )
        if self._replay_store is None:
            return result
        return self._replay_store.record_completed(
            request.invocation_id,
            expected,
            result,
        )


class CodexCliProcessRunner:
    """Exact Codex CLI entrypoint backed by the Harness process owner."""

    def __init__(self, *, executable: Path) -> None:
        path = Path(executable)
        if not path.is_absolute():
            raise ValueError("Codex executable must be an absolute path")
        self._executable = path

    def run(
        self,
        launch: CodexCliLaunch,
        *,
        trusted_local: object = None,
    ) -> CodexCliProcessResult:
        if type(launch) is not CodexCliLaunch:
            raise TypeError("launch must be CodexCliLaunch")
        if not launch.argv or launch.argv[0] != str(self._executable):
            raise ValueError("Codex launch executable does not match runner")
        prepared = prepare_execution(
            profile_id=PROFILE_CODEX_CLI,
            workspace_root=launch.workspace_root,
            executable=str(self._executable),
            command=launch.argv,
            wall_deadline_seconds=launch.timeout_seconds,
            output_limit_chars=10_000,
            output_kind="stdout_stderr",
            python_profile=False,
            stdin_text=launch.stdin_text,
        )
        outcome = run_prepared(prepared, trusted_local=trusted_local)
        return self._result_from_outcome(outcome)

    @staticmethod
    def _result_from_outcome(
        outcome: ExecutionOutcome,
    ) -> CodexCliProcessResult:
        return CodexCliProcessResult(
            exit_code=outcome.exit_code,
            stdout=outcome.stdout.text,
            stderr=outcome.stderr.text,
            duration_ms=outcome.duration_ms,
            timed_out=outcome.timed_out,
        )


class SupervisedCodexCliTransport:
    """Composition-approved transport; it cannot mint its own permission."""

    def __init__(
        self,
        *,
        runner: CodexCliProcessRunner,
        approver_factory: Callable[[], LocalExecutionApprover],
    ) -> None:
        if type(runner) is not CodexCliProcessRunner:
            raise TypeError("runner must be CodexCliProcessRunner")
        if not callable(approver_factory):
            raise TypeError("approver_factory must be callable")
        self._runner = runner
        self._approver_factory = approver_factory

    def run(self, launch: CodexCliLaunch) -> CodexCliProcessResult:
        approver = self._approver_factory()
        if type(approver) is not LocalExecutionApprover:
            raise TypeError("approver_factory returned an invalid approver")
        result = approver.run_codex(self._runner, launch)
        if type(result) is not CodexCliProcessResult:
            raise TypeError("Codex Runtime returned an invalid process result")
        return result


class CodexCliAgentExecutor:
    def __init__(
        self,
        *,
        executable: Path,
        cli_version: str,
        transport: CodexCliTransport,
    ) -> None:
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            raise ValueError("Codex executable must be an absolute path")
        if not cli_version.strip():
            raise ValueError("Codex CLI version cannot be empty")
        self._executable = executable_path
        self._cli_version = cli_version
        self._transport = transport

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        execution_command = (
            ("exec", "resume", "--json", request.backend_session_id, "-")
            if request.backend_session_id
            else ("exec", "--json", "-")
        )
        launch = CodexCliLaunch(
            argv=(
                str(self._executable),
                *CODEX_CLI_SAFE_PREFIX_OPTIONS,
                request.permission.value,
                "-C",
                str(request.workspace_root),
                "exec",
                "--ignore-user-config",
                *execution_command[1:],
            ),
            stdin_text=request.prompt,
            workspace_root=request.workspace_root,
            timeout_seconds=float(request.timeout_seconds),
        )
        process = self._transport.run(launch)
        if (
            process.failure_kind
            == CodexCliFailureKind.BACKEND_SESSION_UNAVAILABLE.value
        ):
            if (
                not request.backend_session_id
                or process.timed_out
                or process.exit_code in {None, 0}
            ):
                raise RuntimeProtocolError(
                    "Codex CLI Session failure evidence is inconsistent"
                )
            raise BackendSessionUnavailable(
                backend_id=request.backend_id,
                backend_session_id=request.backend_session_id,
            )
        events: list[AgentExecutionEvent] = []
        session_id = ""
        final_message = ""
        usage = AgentExecutionUsage()
        completed = False

        for line in process.stdout.splitlines():
            if not line.strip():
                continue
            invalid_json = False
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
                invalid_json = True
            if invalid_json or not isinstance(payload, Mapping):
                raise RuntimeProtocolError(
                    "Codex CLI emitted invalid JSONL"
                )
            event_type = payload.get("type")
            if event_type == "thread.started":
                session_id = redact_text(str(payload.get("thread_id", "")))
                events.append(AgentExecutionEvent("session_started"))
            elif event_type == "turn.started":
                events.append(AgentExecutionEvent("turn_started"))
            elif event_type == "item.completed":
                item = payload.get("item", {})
                if not isinstance(item, Mapping):
                    continue
                item_type = item.get("type")
                if item_type == "command_execution":
                    event_data: dict[str, object] = {
                        "tool": "shell",
                        "command": redact_text(str(item.get("command", ""))),
                        "status": redact_text(str(item.get("status", ""))),
                    }
                    output_lines = str(
                        item.get("aggregated_output", "")
                    ).splitlines()
                    observed_values: set[bool] = set()
                    if (
                        "CODEX_RUNTIME_ENV_CHECK "
                        "codex_home_present=false"
                    ) in output_lines:
                        observed_values.add(False)
                    if (
                        "CODEX_RUNTIME_ENV_CHECK "
                        "codex_home_present=true"
                    ) in output_lines:
                        observed_values.add(True)
                    if len(observed_values) == 1:
                        event_data["runtime_observation"] = {
                            "codex_home_present": observed_values.pop(),
                        }
                    events.append(AgentExecutionEvent(
                        "tool_completed",
                        event_data,
                    ))
                elif item_type == "agent_message":
                    final_message = redact_text(str(item.get("text", "")))
                    events.append(AgentExecutionEvent(
                        "agent_message",
                        {"text": final_message},
                    ))
            elif event_type == "turn.completed":
                raw_usage = payload.get("usage", {})
                if not isinstance(raw_usage, Mapping):
                    raw_usage = {}
                usage = AgentExecutionUsage(
                    input_tokens=int(raw_usage.get("input_tokens", 0)),
                    cached_input_tokens=int(
                        raw_usage.get("cached_input_tokens", 0)
                    ),
                    output_tokens=int(raw_usage.get("output_tokens", 0)),
                    reasoning_output_tokens=int(
                        raw_usage.get("reasoning_output_tokens", 0)
                    ),
                )
                completed = True
                events.append(AgentExecutionEvent("turn_completed"))

        status = (
            AgentExecutionStatus.COMPLETED
            if process.exit_code == 0 and not process.timed_out and completed
            else AgentExecutionStatus.FAILED
        )
        return AgentExecutionResult(
            status=status,
            backend_id="codex_cli",
            cli_version=self._cli_version,
            backend_session_id=session_id,
            sandbox=request.permission.value,
            final_message=final_message,
            events=tuple(events),
            usage=usage,
            duration_ms=process.duration_ms,
        )
