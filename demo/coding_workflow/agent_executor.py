from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
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
class AgentExecutionRequest:
    invocation_id: str
    thread_id: str
    agent_id: str
    prompt: str
    workspace_root: Path
    permission: AgentExecutionPermission
    timeout_seconds: float
    state_envelope: AgentExecutionStateEnvelope
    session_id: str = ""

    def __post_init__(self) -> None:
        for name in ("invocation_id", "thread_id", "agent_id", "prompt"):
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
        if not isinstance(self.session_id, str):
            raise TypeError("session_id must be a string")
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
    backend: str
    cli_version: str
    session_id: str
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


class AgentExecutionRuntime:
    def __init__(
        self,
        *,
        executor: AgentExecutor,
        state_authority: AgentExecutionStateAuthority,
        replay_store: AgentExecutionReplayStore | None = None,
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

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
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
        if self._replay_store is not None:
            completed = self._replay_store.completed_for(
                request.invocation_id,
                expected,
            )
            if completed is not None:
                return completed
        result = self._executor.run(request)
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
            ("exec", "resume", "--json", request.session_id, "-")
            if request.session_id
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
        events: list[AgentExecutionEvent] = []
        session_id = ""
        final_message = ""
        usage = AgentExecutionUsage()
        completed = False

        for line in process.stdout.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            event_type = payload.get("type")
            if event_type == "thread.started":
                session_id = redact_text(str(payload.get("thread_id", "")))
                events.append(AgentExecutionEvent(
                    "session_started",
                    {"session_id": session_id},
                ))
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
            backend="codex_cli",
            cli_version=self._cli_version,
            session_id=session_id,
            sandbox=request.permission.value,
            final_message=final_message,
            events=tuple(events),
            usage=usage,
            duration_ms=process.duration_ms,
        )
