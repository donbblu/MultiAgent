from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

from coding_workflow.agent_executor import (
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionStateEnvelope,
    AgentExecutionStatus,
    AgentExecutor,
    CodexCliAgentExecutor,
    CodexCliProcessRunner,
    SupervisedCodexCliTransport,
)
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.runtime_domain import ScopedRef, ScopedSnapshotRef


CODEX_EXECUTABLE = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
CODEX_CLI_VERSION = "0.149.0-alpha.4.3"
SMOKE_FINAL_HIDDEN = (
    "CODEX_SMOKE_OK env_codex_home_present=false workspace_modified=false"
)
SMOKE_FINAL_EXPOSED = (
    "CODEX_SMOKE_OK env_codex_home_present=true workspace_modified=false"
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
            "backend": "codex_cli",
            "cli_version": CODEX_CLI_VERSION,
            "agent_id": "reviewer-agent",
            "agent_invocations": 1,
            "session_id": "",
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
        "session_observed": bool(result.session_id),
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
        "backend": result.backend,
        "cli_version": result.cli_version,
        "agent_id": "reviewer-agent",
        "agent_invocations": 1,
        "session_id": result.session_id,
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


def _parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run one real read-only Codex subscription smoke."
    )
    value.add_argument(
        "--trusted-real-cli",
        action="store_true",
        help="Confirm exactly one real Codex Agent invocation.",
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
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
