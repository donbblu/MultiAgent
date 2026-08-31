from __future__ import annotations

import argparse
import json
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence

from coding_workflow.agent_executor import (
    AgentExecutionContextPart,
    AgentExecutionPermission,
    AgentExecutionRecoveryConfirmation,
    AgentExecutionRecoveryContext,
    AgentExecutionRecoveryDecision,
    AgentExecutionRecoveryPrompt,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionRuntime,
    AgentExecutionStateEnvelope,
    AgentExecutionStatus,
    AgentExecutor,
    CodexCliAgentExecutor,
    CodexCliLaunch,
    CodexCliProcessRunner,
    CodexCliTransport,
    SupervisedCodexCliTransport,
)
from coding_workflow.local_execution import CODEX_CLI_SAFE_PREFIX_OPTIONS
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteAgentExecutionStateStore,
    SQLiteRuntimeDatabase,
)
from coding_workflow.runtime_domain import ScopedRef, ScopedSnapshotRef


CODEX_EXECUTABLE = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
CODEX_CLI_VERSION = "0.149.0-alpha.4.3"
SESSION_FAILURE_PROBE_ID = "00000000-0000-4000-8000-000000000000"
SESSION_FAILURE_PROBE_PROMPT = "CONTROLLED_SESSION_LOOKUP_ONLY"
SESSION_FAILURE_PROBE_TIMEOUT_SECONDS = 10.0
_SAFE_SHAPE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MODEL_EXECUTION_EVENT_TYPES = frozenset({
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
})
SMOKE_FINAL_HIDDEN = (
    "CODEX_SMOKE_OK env_codex_home_present=false workspace_modified=false"
)
SMOKE_FINAL_EXPOSED = (
    "CODEX_SMOKE_OK env_codex_home_present=true workspace_modified=false"
)
MANUAL_RECOVERY_FINAL = (
    "CODEX_MANUAL_RECOVERY_OK workspace_modified=false"
)
PUBLIC_EVENT_KINDS = frozenset({
    "session_started",
    "turn_started",
    "tool_completed",
    "agent_message",
    "turn_completed",
})
SMOKE_STATE_ENVELOPE = AgentExecutionStateEnvelope(
    scope_id="scope-codex-read-only-smoke",
    task_ref=ScopedRef(
        "scope-codex-read-only-smoke",
        "core:task",
        "task-codex-read-only-smoke",
    ),
    snapshot_ref=ScopedSnapshotRef(
        ScopedRef(
            "scope-codex-read-only-smoke",
            "core:task_snapshot",
            "snapshot-codex-read-only-smoke",
        ),
        "1" * 64,
    ),
    permission_snapshot_ref=ScopedSnapshotRef(
        ScopedRef(
            "scope-codex-read-only-smoke",
            "core:permission_snapshot",
            "permission-codex-read-only-smoke",
        ),
        "2" * 64,
    ),
    artifact_refs=(),
    permission=AgentExecutionPermission.READ_ONLY,
)
SMOKE_PROMPT = """\
这是一次只读安全验证。只执行以下动作：
1. 使用 shell 恰好一次，只判断名为 CODEX_HOME 的环境变量是否存在；不得输出它的值，也不得读取、列出或访问任何认证文件。shell标准输出只能是一行固定哨兵：变量不存在时输出`CODEX_RUNTIME_ENV_CHECK codex_home_present=false`，存在时仅把false改为true。
2. 不修改任何文件，不访问网络，不调用其他 Agent。
3. 最终只回复一行，变量不存在时严格回复：
CODEX_SMOKE_OK env_codex_home_present=false workspace_modified=false
变量存在时仅把 false 改为 true。不要添加解释、Markdown 或其他文字。
"""


def run_codex_session_failure_probe(
    *,
    transport: CodexCliTransport,
    workspace_root: Path,
    backend_session_id: str,
) -> Mapping[str, object]:
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise ValueError("workspace_root must be an existing directory")
    if not isinstance(backend_session_id, str) or not backend_session_id:
        raise ValueError("backend_session_id must be a non-empty string")
    launch = CodexCliLaunch(
        argv=(
            str(CODEX_EXECUTABLE),
            *CODEX_CLI_SAFE_PREFIX_OPTIONS,
            AgentExecutionPermission.READ_ONLY.value,
            "-C",
            str(root),
            "exec",
            "--ignore-user-config",
            "resume",
            "--json",
            backend_session_id,
            "-",
        ),
        stdin_text=SESSION_FAILURE_PROBE_PROMPT,
        workspace_root=root,
        timeout_seconds=SESSION_FAILURE_PROBE_TIMEOUT_SECONDS,
    )
    try:
        process = transport.run(launch)
    except Exception:
        return _session_probe_report(
            status="failed",
            error_code="PROBE_EXECUTION_FAILED",
        )

    event_fields: dict[str, set[str]] = {}
    jsonl_valid = True
    stdout_line_count = 0
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        stdout_line_count += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            jsonl_valid = False
            continue
        if not isinstance(payload, Mapping):
            jsonl_valid = False
            continue
        raw_type = payload.get("type")
        if (
            not isinstance(raw_type, str)
            or _SAFE_SHAPE_NAME.fullmatch(raw_type) is None
        ):
            jsonl_valid = False
            continue
        fields = event_fields.setdefault(raw_type, set())
        for raw_name in payload:
            if (
                isinstance(raw_name, str)
                and _SAFE_SHAPE_NAME.fullmatch(raw_name) is not None
            ):
                fields.add(raw_name)
            else:
                jsonl_valid = False

    event_types = sorted(event_fields)
    model_events = any(
        event_type in _MODEL_EXECUTION_EVENT_TYPES
        or event_type.startswith("item.")
        for event_type in event_types
    )
    process_exit = (
        "unavailable"
        if process.exit_code is None
        else "zero" if process.exit_code == 0 else "nonzero"
    )
    if process.timed_out:
        status, error_code = "failed", "PROBE_TIMED_OUT"
    elif model_events:
        status, error_code = "failed", "PROBE_MODEL_EXECUTION_OBSERVED"
    elif not jsonl_valid:
        status, error_code = "failed", "PROBE_OUTPUT_INVALID"
    elif process.exit_code == 0:
        status, error_code = "failed", "PROBE_UNEXPECTED_SUCCESS"
    else:
        status, error_code = "observed", ""
    return {
        "status": status,
        "error_code": error_code,
        "backend_id": "codex_cli",
        "cli_version": CODEX_CLI_VERSION,
        "process_invocations": 1,
        "process_exit": process_exit,
        "timed_out": process.timed_out,
        "jsonl_valid": jsonl_valid,
        "stdout_line_count": stdout_line_count,
        "event_types": event_types,
        "event_fields": {
            event_type: sorted(event_fields[event_type])
            for event_type in event_types
        },
        "stderr_present": bool(process.stderr),
        "model_execution_events_observed": model_events,
        "duration_ms": process.duration_ms,
    }


def _session_probe_report(
    *, status: str, error_code: str
) -> Mapping[str, object]:
    return {
        "status": status,
        "error_code": error_code,
        "backend_id": "codex_cli",
        "cli_version": CODEX_CLI_VERSION,
        "process_invocations": 1,
        "process_exit": "unavailable",
        "timed_out": False,
        "jsonl_valid": False,
        "stdout_line_count": 0,
        "event_types": [],
        "event_fields": {},
        "stderr_present": False,
        "model_execution_events_observed": False,
        "duration_ms": 0,
    }


def run_codex_read_only_smoke(
    *,
    executor: AgentExecutor,
    workspace_root: Path,
) -> Mapping[str, object]:
    try:
        result = executor.run(AgentExecutionRequest(
            invocation_id="codex-read-only-smoke-1",
            thread_id="thread-codex-read-only-smoke",
            agent_id="reviewer-agent",
            backend_id="codex_cli",
            prompt=SMOKE_PROMPT,
            workspace_root=workspace_root,
            permission=AgentExecutionPermission.READ_ONLY,
            timeout_seconds=120,
            state_envelope=SMOKE_STATE_ENVELOPE,
        ))
    except Exception:
        return {
            "status": "failed",
            "error_code": "CODEX_EXECUTION_FAILED",
            "backend_id": "codex_cli",
            "cli_version": CODEX_CLI_VERSION,
            "agent_id": "reviewer-agent",
            "agent_invocations": 1,
            "sandbox": "read-only",
            "event_kinds": [],
            "checks": {
                "execution_completed": False,
                "session_observed": False,
                "read_only_sandbox": False,
                "shell_tool_observed": False,
                "turn_completed": False,
                "runtime_observation_observed": False,
                "codex_home_hidden_from_agent_tools": False,
                "model_matches_runtime_observation": False,
                "agent_reported_workspace_unchanged": False,
            },
            "runtime_observation": {"codex_home_present": None},
            "final_message": "UNAVAILABLE",
            "usage": {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
            },
            "duration_ms": 0,
        }
    event_kinds = [
        event.kind for event in result.events
        if event.kind in PUBLIC_EVENT_KINDS
    ]
    final_is_hidden = result.final_message == SMOKE_FINAL_HIDDEN
    final_is_exposed = result.final_message == SMOKE_FINAL_EXPOSED
    observed_values: list[bool] = []
    for event in result.events:
        if event.kind != "tool_completed":
            continue
        observation = event.data.get("runtime_observation", {})
        if not isinstance(observation, Mapping):
            continue
        value = observation.get("codex_home_present")
        if type(value) is bool:
            observed_values.append(value)
    runtime_value = observed_values[0] if len(observed_values) == 1 else None
    runtime_observed = type(runtime_value) is bool
    model_value = (
        False if final_is_hidden else True if final_is_exposed else None
    )
    checks = {
        "execution_completed": result.status is AgentExecutionStatus.COMPLETED,
        "session_observed": bool(result.backend_session_id),
        "read_only_sandbox": result.sandbox == "read-only",
        "shell_tool_observed": "tool_completed" in event_kinds,
        "turn_completed": "turn_completed" in event_kinds,
        "runtime_observation_observed": runtime_observed,
        "codex_home_hidden_from_agent_tools": runtime_value is False,
        "model_matches_runtime_observation": (
            runtime_observed and model_value is runtime_value
        ),
        "agent_reported_workspace_unchanged": (
            final_is_hidden or final_is_exposed
        ),
    }
    passed = all(checks.values())
    safe_final = (
        result.final_message
        if final_is_hidden or final_is_exposed
        else "UNEXPECTED_OR_REDACTED"
    )
    return {
        "status": "passed" if passed else "failed",
        "error_code": "" if passed else "SMOKE_ACCEPTANCE_FAILED",
        "backend_id": result.backend_id,
        "cli_version": result.cli_version,
        "agent_id": "reviewer-agent",
        "agent_invocations": 1,
        "sandbox": result.sandbox,
        "event_kinds": event_kinds,
        "checks": checks,
        "runtime_observation": {
            "codex_home_present": runtime_value,
        },
        "final_message": safe_final,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "cached_input_tokens": result.usage.cached_input_tokens,
            "output_tokens": result.usage.output_tokens,
            "reasoning_output_tokens": result.usage.reasoning_output_tokens,
        },
        "duration_ms": result.duration_ms,
    }


class _CountingAgentExecutor:
    def __init__(self, executor: AgentExecutor) -> None:
        self._executor = executor
        self.invocations = 0
        self.request_had_session: list[bool] = []
        self.results: list[AgentExecutionResult] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.invocations += 1
        self.request_had_session.append(bool(request.backend_session_id))
        result = self._executor.run(request)
        self.results.append(result)
        return result


def _recovery_context_part(
    *, entity_type: str, entity_id: str, content: str
) -> AgentExecutionContextPart:
    return AgentExecutionContextPart(
        ref=ScopedSnapshotRef(
            ScopedRef(
                "scope-codex-manual-recovery-smoke",
                entity_type,
                entity_id,
            ),
            sha256(content.encode("utf-8")).hexdigest(),
        ),
        content=content,
    )


def _manual_recovery_fixture(
) -> tuple[AgentExecutionStateEnvelope, AgentExecutionRecoveryContext]:
    task_snapshot = _recovery_context_part(
        entity_type="core:task_snapshot",
        entity_id="snapshot-codex-manual-recovery-smoke",
        content="任务：验证人工确认后能从权威上下文创建新的只读会话。",
    )
    permission_snapshot = _recovery_context_part(
        entity_type="core:permission_snapshot",
        entity_id="permission-codex-manual-recovery-smoke",
        content=(
            "权限：read-only。不得修改文件，不得调用工具，不得访问外部网络。"
        ),
    )
    message = _recovery_context_part(
        entity_type="core:message",
        entity_id="message-codex-manual-recovery-smoke",
        content=(
            "不要调用任何工具。最终只回复一行："
            f"{MANUAL_RECOVERY_FINAL}"
        ),
    )
    scope_id = "scope-codex-manual-recovery-smoke"
    state = AgentExecutionStateEnvelope(
        scope_id=scope_id,
        task_ref=ScopedRef(
            scope_id,
            "core:task",
            "task-codex-manual-recovery-smoke",
        ),
        snapshot_ref=task_snapshot.ref,
        permission_snapshot_ref=permission_snapshot.ref,
        artifact_refs=(),
        permission=AgentExecutionPermission.READ_ONLY,
    )
    return state, AgentExecutionRecoveryContext(
        scope_id=scope_id,
        task_ref=state.task_ref,
        task_snapshot=task_snapshot,
        permission_snapshot=permission_snapshot,
        messages=(message,),
        artifacts=(),
    )


def _manual_recovery_database(path: Path) -> SQLiteRuntimeDatabase:
    return SQLiteRuntimeDatabase(
        RuntimeSQLiteConfig(path),
        outbox_policy=OutboxPolicy(
            policy_version="outbox-policy/codex-recovery-smoke-v1",
            destination="core:runtime_events",
            expected_sink_id="core:codex-recovery-smoke",
            claim_ttl_ms=60_000,
            batch_limit=10,
            retry_delays_ms=(1_000, 5_000, 30_000),
        ),
    )


def _manual_recovery_failed_report(
    *, invocations: int, duration_ms: int
) -> Mapping[str, object]:
    return {
        "status": "failed",
        "error_code": "MANUAL_RECOVERY_SMOKE_FAILED",
        "backend_id": "codex_cli",
        "cli_version": CODEX_CLI_VERSION,
        "agent_invocations": invocations,
        "sandbox": "read-only",
        "event_kinds": [],
        "checks": {
            "awaiting_user_confirmation_observed": False,
            "invalid_resume_had_no_public_events": False,
            "new_session_request_cleared_old_session": False,
            "recovery_execution_completed": False,
            "replacement_session_observed": False,
            "replacement_session_persisted": False,
            "completed_result_replayed_without_extra_call": False,
            "read_only_sandbox": False,
            "agent_reported_workspace_unchanged": False,
        },
        "usage": {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        "duration_ms": duration_ms,
    }


def run_codex_manual_session_recovery_smoke(
    *,
    executor: AgentExecutor,
    workspace_root: Path,
    stale_backend_session_id: str,
) -> Mapping[str, object]:
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise ValueError("workspace_root must be an existing directory")
    if (
        not isinstance(stale_backend_session_id, str)
        or not stale_backend_session_id
    ):
        raise ValueError("stale_backend_session_id must be non-empty")
    state, recovery_context = _manual_recovery_fixture()
    invocation_id = "invocation-codex-manual-recovery-smoke"
    thread_id = "thread-codex-manual-recovery-smoke"
    agent_id = "reviewer-agent"
    backend_id = "codex_cli"
    counted = _CountingAgentExecutor(executor)
    duration_ms = 0

    with tempfile.TemporaryDirectory(
        prefix="multiagent-codex-recovery-smoke-"
    ) as temporary:
        database_path = Path(temporary) / "runtime.sqlite3"
        database = _manual_recovery_database(database_path)
        try:
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, invocation_id, state)
                store.record_recovery_context(
                    uow,
                    invocation_id,
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id=state.scope_id,
                thread_id=thread_id,
                agent_id=agent_id,
                backend_id=backend_id,
                backend_session_id=stale_backend_session_id,
            )
            request = AgentExecutionRequest(
                invocation_id=invocation_id,
                thread_id=thread_id,
                agent_id=agent_id,
                backend_id=backend_id,
                prompt=SESSION_FAILURE_PROBE_PROMPT,
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=120,
                state_envelope=state,
            )
            prompt = AgentExecutionRuntime(
                executor=counted,
                state_authority=store,
                replay_store=store,
                session_store=store,
                recovery_context_store=store,
            ).run(request)
            if counted.results:
                duration_ms += counted.results[0].duration_ms
            first_had_no_events = bool(
                counted.results
                and counted.results[0].status is AgentExecutionStatus.FAILED
                and not counted.results[0].events
                and not counted.results[0].backend_session_id
            )
            if not isinstance(prompt, AgentExecutionRecoveryPrompt):
                return _manual_recovery_failed_report(
                    invocations=counted.invocations,
                    duration_ms=duration_ms,
                )
        except Exception:
            return _manual_recovery_failed_report(
                invocations=counted.invocations,
                duration_ms=duration_ms,
            )

        reopened = _manual_recovery_database(database_path)
        try:
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            result = AgentExecutionRuntime(
                executor=counted,
                state_authority=reopened_store,
                replay_store=reopened_store,
                session_store=reopened_store,
                recovery_context_store=reopened_store,
            ).confirm_session_recovery(
                request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=invocation_id,
                    decision=(
                        AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                    ),
                ),
            )
            if len(counted.results) > 1:
                duration_ms += counted.results[1].duration_ms
        except Exception:
            return _manual_recovery_failed_report(
                invocations=counted.invocations,
                duration_ms=duration_ms,
            )

        final_database = _manual_recovery_database(database_path)
        try:
            final_database.initialize()
            final_store = SQLiteAgentExecutionStateStore(final_database)
            final_runtime = AgentExecutionRuntime(
                executor=counted,
                state_authority=final_store,
                replay_store=final_store,
                session_store=final_store,
                recovery_context_store=final_store,
            )
            replayed = final_runtime.run(request)
            replacement_persisted = (
                final_store.bound_session_for(
                    scope_id=state.scope_id,
                    thread_id=thread_id,
                    agent_id=agent_id,
                    backend_id=backend_id,
                )
                == result.backend_session_id
            )
        except Exception:
            return _manual_recovery_failed_report(
                invocations=counted.invocations,
                duration_ms=duration_ms,
            )

    event_kinds = [
        event.kind for event in result.events
        if event.kind in PUBLIC_EVENT_KINDS
    ]
    checks = {
        "awaiting_user_confirmation_observed": True,
        "invalid_resume_had_no_public_events": first_had_no_events,
        "new_session_request_cleared_old_session": (
            counted.request_had_session == [True, False]
        ),
        "recovery_execution_completed": (
            result.status is AgentExecutionStatus.COMPLETED
        ),
        "replacement_session_observed": bool(result.backend_session_id),
        "replacement_session_persisted": replacement_persisted,
        "completed_result_replayed_without_extra_call": (
            replayed == result and counted.invocations == 2
        ),
        "read_only_sandbox": result.sandbox == "read-only",
        "agent_reported_workspace_unchanged": (
            result.final_message == MANUAL_RECOVERY_FINAL
        ),
    }
    passed = all(checks.values())
    return {
        "status": "passed" if passed else "failed",
        "error_code": "" if passed else "MANUAL_RECOVERY_SMOKE_FAILED",
        "backend_id": result.backend_id,
        "cli_version": result.cli_version,
        "agent_invocations": counted.invocations,
        "sandbox": result.sandbox,
        "event_kinds": event_kinds,
        "checks": checks,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "cached_input_tokens": result.usage.cached_input_tokens,
            "output_tokens": result.usage.output_tokens,
            "reasoning_output_tokens": result.usage.reasoning_output_tokens,
        },
        "duration_ms": duration_ms,
    }


def _parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run one real read-only Codex subscription smoke."
    )
    mode = value.add_mutually_exclusive_group()
    mode.add_argument(
        "--trusted-real-cli",
        action="store_true",
        help="Confirm exactly one real Codex Agent invocation.",
    )
    mode.add_argument(
        "--trusted-session-failure-probe",
        action="store_true",
        help="Confirm one sanitized invalid-Session lookup probe.",
    )
    mode.add_argument(
        "--trusted-real-session-recovery",
        action="store_true",
        help=(
            "Confirm one invalid resume followed by one read-only new Session."
        ),
    )
    return value


def _real_executor() -> CodexCliAgentExecutor:
    return CodexCliAgentExecutor(
        executable=CODEX_EXECUTABLE,
        cli_version=CODEX_CLI_VERSION,
        transport=SupervisedCodexCliTransport(
            runner=CodexCliProcessRunner(executable=CODEX_EXECUTABLE),
            approver_factory=lambda: LocalExecutionApprover(True),
        ),
    )


def _real_transport() -> SupervisedCodexCliTransport:
    return SupervisedCodexCliTransport(
        runner=CodexCliProcessRunner(executable=CODEX_EXECUTABLE),
        approver_factory=lambda: LocalExecutionApprover(True),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.trusted_session_failure_probe:
        report = run_codex_session_failure_probe(
            transport=_real_transport(),
            workspace_root=Path(__file__).resolve().parent.parent,
            backend_session_id=SESSION_FAILURE_PROBE_ID,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "observed" else 1
    if args.trusted_real_session_recovery:
        report = run_codex_manual_session_recovery_smoke(
            executor=_real_executor(),
            workspace_root=Path(__file__).resolve().parent.parent,
            stale_backend_session_id=SESSION_FAILURE_PROBE_ID,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    if not args.trusted_real_cli:
        parser.error(
            "真实 smoke 会使用 ChatGPT 订阅发起一次 Codex Agent 调用；"
            "确认后显式添加 --trusted-real-cli"
        )
    report = run_codex_read_only_smoke(
        executor=_real_executor(),
        workspace_root=Path(__file__).resolve().parent.parent,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
