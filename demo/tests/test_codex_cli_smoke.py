from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from codex_cli_smoke import run_codex_read_only_smoke
from coding_workflow.agent_executor import (
    AgentExecutionEvent,
    AgentExecutionPermission,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentExecutionUsage,
)


class _SuccessfulAgentExecutor:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend="codex_cli",
            cli_version="0.149.0-alpha.4.3",
            session_id="session-smoke-1",
            sandbox="read-only",
            final_message=(
                "CODEX_SMOKE_OK env_codex_home_present=false "
                "workspace_modified=false"
            ),
            events=(
                AgentExecutionEvent(
                    "session_started",
                    MappingProxyType({"session_id": "session-smoke-1"}),
                ),
                AgentExecutionEvent("turn_started"),
                AgentExecutionEvent(
                    "tool_completed",
                    MappingProxyType({
                        "tool": "shell",
                        "command": "printf token=must-not-be-public",
                        "status": "completed",
                        "runtime_observation": {
                            "codex_home_present": False,
                        },
                    }),
                ),
                AgentExecutionEvent(
                    "agent_message",
                    MappingProxyType({"text": "public final"}),
                ),
                AgentExecutionEvent("turn_completed"),
            ),
            usage=AgentExecutionUsage(
                input_tokens=101,
                cached_input_tokens=20,
                output_tokens=12,
                reasoning_output_tokens=3,
            ),
            duration_ms=1400,
        )


class _FailingAgentExecutor:
    def run(self, request):
        del request
        raise RuntimeError(
            "CODEX_HOME=/private/credential-cache token=must-not-leak"
        )


class _DisagreeingAgentExecutor:
    def run(self, request):
        del request
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend="codex_cli",
            cli_version="0.149.0-alpha.4.3",
            session_id="session-disagreement",
            sandbox="read-only",
            final_message=(
                "CODEX_SMOKE_OK env_codex_home_present=false "
                "workspace_modified=false"
            ),
            events=(
                AgentExecutionEvent("session_started"),
                AgentExecutionEvent("turn_started"),
                AgentExecutionEvent(
                    "tool_completed",
                    MappingProxyType({
                        "tool": "shell",
                        "status": "completed",
                        "runtime_observation": {
                            "codex_home_present": True,
                        },
                    }),
                ),
                AgentExecutionEvent("agent_message"),
                AgentExecutionEvent("turn_completed"),
            ),
            usage=AgentExecutionUsage(),
            duration_ms=3,
        )


class CodexCliSmokeTests(unittest.TestCase):
    def test_read_only_smoke_returns_a_sanitized_verifiable_report(self) -> None:
        executor = _SuccessfulAgentExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            report = run_codex_read_only_smoke(
                executor=executor,
                workspace_root=Path(temporary),
            )

        self.assertEqual(len(executor.requests), 1)
        request = executor.requests[0]
        self.assertEqual(request.permission, AgentExecutionPermission.READ_ONLY)
        self.assertEqual(request.agent_id, "reviewer-agent")
        self.assertNotIn("/Users/", request.prompt)
        self.assertIn(
            "CODEX_RUNTIME_ENV_CHECK codex_home_present=false",
            request.prompt,
        )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["agent_invocations"], 1)
        self.assertEqual(report["session_id"], "session-smoke-1")
        self.assertEqual(report["sandbox"], "read-only")
        self.assertEqual(
            report["event_kinds"],
            [
                "session_started",
                "turn_started",
                "tool_completed",
                "agent_message",
                "turn_completed",
            ],
        )
        self.assertEqual(
            report["checks"],
            {
                "execution_completed": True,
                "session_observed": True,
                "read_only_sandbox": True,
                "shell_tool_observed": True,
                "turn_completed": True,
                "runtime_observation_observed": True,
                "codex_home_hidden_from_agent_tools": True,
                "model_matches_runtime_observation": True,
                "agent_reported_workspace_unchanged": True,
            },
        )
        self.assertEqual(
            report["runtime_observation"],
            {"codex_home_present": False},
        )
        self.assertEqual(
            report["usage"],
            {
                "input_tokens": 101,
                "cached_input_tokens": 20,
                "output_tokens": 12,
                "reasoning_output_tokens": 3,
            },
        )
        self.assertEqual(report["duration_ms"], 1400)
        self.assertNotIn("command", repr(report))
        self.assertNotIn("must-not-be-public", repr(report))
        self.assertNotIn("CODEX_HOME=/", repr(report))

    def test_execution_failure_returns_only_a_fixed_public_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_codex_read_only_smoke(
                executor=_FailingAgentExecutor(),
                workspace_root=Path(temporary),
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "CODEX_EXECUTION_FAILED")
        self.assertEqual(report["agent_invocations"], 1)
        self.assertEqual(report["sandbox"], "read-only")
        self.assertNotIn("credential-cache", repr(report))
        self.assertNotIn("must-not-leak", repr(report))

    def test_runtime_observation_overrules_a_disagreeing_model_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_codex_read_only_smoke(
                executor=_DisagreeingAgentExecutor(),
                workspace_root=Path(temporary),
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "SMOKE_ACCEPTANCE_FAILED")
        self.assertEqual(
            report["runtime_observation"],
            {"codex_home_present": True},
        )
        self.assertTrue(report["checks"]["runtime_observation_observed"])
        self.assertFalse(
            report["checks"]["codex_home_hidden_from_agent_tools"]
        )
        self.assertFalse(
            report["checks"]["model_matches_runtime_observation"]
        )


if __name__ == "__main__":
    unittest.main()
