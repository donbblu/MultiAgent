from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from codex_cli_smoke import (
    run_codex_manual_session_recovery_smoke,
    run_codex_read_only_smoke,
    run_codex_session_failure_probe,
)
from coding_workflow.agent_executor import (
    AgentExecutionEvent,
    AgentExecutionPermission,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentExecutionUsage,
    CodexCliProcessResult,
)


class _SuccessfulAgentExecutor:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend_id="codex_cli",
            cli_version="0.149.0-alpha.4.3",
            backend_session_id="session-smoke-1",
            sandbox="read-only",
            final_message=(
                "CODEX_SMOKE_OK env_codex_home_present=false "
                "workspace_modified=false"
            ),
            events=(
                AgentExecutionEvent(
                    "session_started",
                    MappingProxyType({}),
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
            backend_id="codex_cli",
            cli_version="0.149.0-alpha.4.3",
            backend_session_id="session-disagreement",
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


class _SessionFailureProbeTransport:
    def __init__(self) -> None:
        self.launches = []

    def run(self, launch):
        self.launches.append(launch)
        return CodexCliProcessResult(
            exit_code=1,
            stdout=(
                '{"type":"error","message":"Session not found: '
                'private-session-id","details":{"token":"must-not-leak"}}'
            ),
            stderr=(
                "CODEX_HOME=/private/credential-cache "
                "token=stderr-must-not-leak"
            ),
            duration_ms=7,
            timed_out=False,
        )


class _UnexpectedModelExecutionTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=1,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":"must-not-leak"}',
                '{"type":"turn.started"}',
            )),
            stderr="",
            duration_ms=8,
            timed_out=False,
        )


class _ManualRecoveryExecutor:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if request.backend_session_id:
            return AgentExecutionResult(
                status=AgentExecutionStatus.FAILED,
                backend_id="codex_cli",
                cli_version="0.149.0-alpha.4.3",
                backend_session_id="",
                sandbox="read-only",
                final_message="",
                events=(),
                usage=AgentExecutionUsage(),
                duration_ms=7,
            )
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend_id="codex_cli",
            cli_version="0.149.0-alpha.4.3",
            backend_session_id="private-replacement-session",
            sandbox="read-only",
            final_message=(
                "CODEX_MANUAL_RECOVERY_OK workspace_modified=false"
            ),
            events=(
                AgentExecutionEvent("session_started"),
                AgentExecutionEvent("turn_started"),
                AgentExecutionEvent("agent_message"),
                AgentExecutionEvent("turn_completed"),
            ),
            usage=AgentExecutionUsage(
                input_tokens=111,
                cached_input_tokens=22,
                output_tokens=13,
                reasoning_output_tokens=4,
            ),
            duration_ms=11,
        )


class CodexCliSmokeTests(unittest.TestCase):
    def test_manual_session_recovery_smoke_is_durable_and_sanitized(
        self,
    ) -> None:
        executor = _ManualRecoveryExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            private_root = Path(temporary)
            report = run_codex_manual_session_recovery_smoke(
                executor=executor,
                workspace_root=private_root,
                stale_backend_session_id="private-stale-session",
            )

        self.assertEqual(2, len(executor.requests))
        self.assertEqual(
            "private-stale-session",
            executor.requests[0].backend_session_id,
        )
        self.assertEqual("", executor.requests[1].backend_session_id)
        self.assertEqual(
            {
                "status": "passed",
                "error_code": "",
                "backend_id": "codex_cli",
                "cli_version": "0.149.0-alpha.4.3",
                "agent_invocations": 2,
                "sandbox": "read-only",
                "event_kinds": [
                    "session_started",
                    "turn_started",
                    "agent_message",
                    "turn_completed",
                ],
                "checks": {
                    "awaiting_user_confirmation_observed": True,
                    "invalid_resume_had_no_public_events": True,
                    "new_session_request_cleared_old_session": True,
                    "recovery_execution_completed": True,
                    "replacement_session_observed": True,
                    "replacement_session_persisted": True,
                    "completed_result_replayed_without_extra_call": True,
                    "read_only_sandbox": True,
                    "agent_reported_workspace_unchanged": True,
                },
                "usage": {
                    "input_tokens": 111,
                    "cached_input_tokens": 22,
                    "output_tokens": 13,
                    "reasoning_output_tokens": 4,
                },
                "duration_ms": 18,
            },
            report,
        )
        public_report = repr(report)
        self.assertNotIn("private-stale-session", public_report)
        self.assertNotIn("private-replacement-session", public_report)
        self.assertNotIn(str(private_root), public_report)
        self.assertNotIn("confirmation_id", public_report)

    def test_session_failure_probe_reports_shape_without_private_values(
        self,
    ) -> None:
        transport = _SessionFailureProbeTransport()
        with tempfile.TemporaryDirectory() as temporary:
            report = run_codex_session_failure_probe(
                transport=transport,
                workspace_root=Path(temporary),
                backend_session_id="private-session-id",
            )

        self.assertEqual(1, len(transport.launches))
        self.assertIn("resume", transport.launches[0].argv)
        self.assertEqual(
            {
                "status": "observed",
                "error_code": "",
                "backend_id": "codex_cli",
                "cli_version": "0.149.0-alpha.4.3",
                "process_invocations": 1,
                "process_exit": "nonzero",
                "timed_out": False,
                "jsonl_valid": True,
                "stdout_line_count": 1,
                "event_types": ["error"],
                "event_fields": {
                    "error": ["details", "message", "type"],
                },
                "stderr_present": True,
                "model_execution_events_observed": False,
                "duration_ms": 7,
            },
            report,
        )
        self.assertNotIn("private-session-id", repr(report))
        self.assertNotIn("must-not-leak", repr(report))
        self.assertNotIn("credential-cache", repr(report))

    def test_session_probe_rejects_model_execution_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_codex_session_failure_probe(
                transport=_UnexpectedModelExecutionTransport(),
                workspace_root=Path(temporary),
                backend_session_id="private-session-id",
            )

        self.assertEqual("failed", report["status"])
        self.assertEqual(
            "PROBE_MODEL_EXECUTION_OBSERVED", report["error_code"]
        )
        self.assertTrue(report["model_execution_events_observed"])
        self.assertNotIn("must-not-leak", repr(report))

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
        self.assertNotIn("backend_session_id", report)
        self.assertNotIn("session-smoke-1", repr(report))
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
