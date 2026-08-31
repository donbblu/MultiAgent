from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_workflow.agent_executor import (
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionStateEnvelope,
    AgentExecutionStatus,
    BackendSessionUnavailable,
    CodexCliAgentExecutor,
    CodexCliFailureKind,
    CodexCliProcessRunner,
    CodexCliProcessResult,
    SupervisedCodexCliTransport,
)
from coding_workflow.local_execution import LocalExecutionError
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.runtime_domain import ScopedRef, ScopedSnapshotRef
from coding_workflow.runtime_domain import RuntimeProtocolError


def _state_envelope() -> AgentExecutionStateEnvelope:
    return AgentExecutionStateEnvelope(
        scope_id="scope-codex-test",
        task_ref=ScopedRef(
            "scope-codex-test", "core:task", "task-codex-test"
        ),
        snapshot_ref=ScopedSnapshotRef(
            ScopedRef(
                "scope-codex-test",
                "core:task_snapshot",
                "snapshot-codex-test",
            ),
            "1" * 64,
        ),
        permission_snapshot_ref=ScopedSnapshotRef(
            ScopedRef(
                "scope-codex-test",
                "core:permission_snapshot",
                "permission-codex-test",
            ),
            "2" * 64,
        ),
        artifact_refs=(),
        permission=AgentExecutionPermission.READ_ONLY,
    )


class _SuccessfulCodexTransport:
    def run(self, launch):
        expected = (
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "--strict-config",
            "--ask-for-approval",
            "never",
            "-c",
            "shell_environment_policy.inherit=none",
            "-c",
            "shell_environment_policy.ignore_default_excludes=false",
            "-c",
            'shell_environment_policy.set={PATH="/opt/homebrew/bin:'
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}',
            "-c",
            'shell_environment_policy.filters={CODEX_HOME="exclude"}',
            "--sandbox",
            "read-only",
            "-C",
            str(launch.workspace_root),
            "exec",
            "--ignore-user-config",
            "--json",
            "-",
        )
        if launch.argv != expected:
            raise AssertionError(f"unexpected Codex argv: {launch.argv!r}")
        if launch.stdin_text != "检查通信协议，只报告有证据的问题。":
            raise AssertionError("prompt must be passed through stdin")
        return CodexCliProcessResult(
            exit_code=0,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":"codex-session-1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"id":"reason-1",'
                '"type":"reasoning","text":"private reasoning"}}',
                '{"type":"item.completed","item":{"id":"tool-1",'
                '"type":"command_execution","command":"rg --files",'
                '"status":"completed","aggregated_output":'
                '"CODEX_RUNTIME_ENV_CHECK codex_home_present=false\\n'
                'private tool output must not leak"}}',
                '{"type":"item.completed","item":{"id":"message-1",'
                '"type":"agent_message","text":"发现一项有依据的问题。"}}',
                '{"type":"turn.completed","usage":{"input_tokens":120,'
                '"cached_input_tokens":20,"output_tokens":30,'
                '"reasoning_output_tokens":10}}',
            )),
            stderr="progress and private diagnostics",
            duration_ms=1250,
            timed_out=False,
        )


class _ResumedCodexTransport:
    def run(self, launch):
        expected = (
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "--strict-config",
            "--ask-for-approval",
            "never",
            "-c",
            "shell_environment_policy.inherit=none",
            "-c",
            "shell_environment_policy.ignore_default_excludes=false",
            "-c",
            'shell_environment_policy.set={PATH="/opt/homebrew/bin:'
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}',
            "-c",
            'shell_environment_policy.filters={CODEX_HOME="exclude"}',
            "--sandbox",
            "read-only",
            "-C",
            str(launch.workspace_root),
            "exec",
            "--ignore-user-config",
            "resume",
            "--json",
            "codex-session-1",
            "-",
        )
        if launch.argv != expected:
            raise AssertionError(f"unexpected Codex resume argv: {launch.argv!r}")
        return CodexCliProcessResult(
            exit_code=0,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":"codex-session-1"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"id":"message-2",'
                '"type":"agent_message","text":"继续评审完成。"}}',
                '{"type":"turn.completed","usage":{"input_tokens":80,'
                '"cached_input_tokens":40,"output_tokens":12,'
                '"reasoning_output_tokens":3}}',
            )),
            stderr="",
            duration_ms=900,
            timed_out=False,
        )


class _CodexHomePresentTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=0,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":"session-present"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"command":"safe environment check","status":"completed",'
                '"aggregated_output":"CODEX_RUNTIME_ENV_CHECK '
                'codex_home_present=true\\nprivate value=<REDACTED>"}}',
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"environment check complete"}}',
                '{"type":"turn.completed","usage":{}}',
            )),
            stderr="",
            duration_ms=2,
            timed_out=False,
        )


class _ConflictingObservationTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=0,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":"session-conflict"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"command":"safe environment check","status":"completed",'
                '"aggregated_output":"CODEX_RUNTIME_ENV_CHECK '
                'codex_home_present=false\\nCODEX_RUNTIME_ENV_CHECK '
                'codex_home_present=true"}}',
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"conflicting environment check"}}',
                '{"type":"turn.completed","usage":{}}',
            )),
            stderr="",
            duration_ms=2,
            timed_out=False,
        )


class _StructuredSessionUnavailableTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=1,
            stdout="",
            stderr="private diagnostic must not be classified or exposed",
            duration_ms=3,
            timed_out=False,
            failure_kind=CodexCliFailureKind.BACKEND_SESSION_UNAVAILABLE,
        )


class _UnstructuredSessionTextTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=1,
            stdout="",
            stderr="Session not found: private-session-id",
            duration_ms=3,
            timed_out=False,
        )


class _TimedOutSessionFailureTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=None,
            stdout="",
            stderr="private timeout diagnostic",
            duration_ms=30_000,
            timed_out=True,
            failure_kind=CodexCliFailureKind.BACKEND_SESSION_UNAVAILABLE,
        )


class _MalformedJsonTransport:
    def run(self, launch):
        del launch
        return CodexCliProcessResult(
            exit_code=1,
            stdout='{private-secret:"unterminated"',
            stderr="another private diagnostic",
            duration_ms=4,
            timed_out=False,
        )


class CodexCliAgentExecutorTests(unittest.TestCase):
    def test_tool_environment_inherits_nothing_and_sets_only_safe_path(
        self,
    ) -> None:
        class _CaptureTransport:
            def __init__(self) -> None:
                self.argv = ()

            def run(self, launch):
                self.argv = launch.argv
                return CodexCliProcessResult(
                    exit_code=0,
                    stdout="\n".join((
                        '{"type":"thread.started","thread_id":"session-filter"}',
                        '{"type":"turn.started"}',
                        '{"type":"item.completed","item":{"type":"agent_message",'
                        '"text":"filter contract captured"}}',
                        '{"type":"turn.completed","usage":{}}',
                    )),
                    stderr="",
                    duration_ms=1,
                    timed_out=False,
                )

        transport = _CaptureTransport()
        executable = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        with tempfile.TemporaryDirectory() as temporary:
            executor = CodexCliAgentExecutor(
                executable=executable,
                cli_version="0.149.0-alpha.4.3",
                transport=transport,
            )
            executor.run(AgentExecutionRequest(
                invocation_id="invocation-canonical-filter",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="只验证规范工具环境过滤合同。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
            ))

        self.assertIn(
            'shell_environment_policy.filters={CODEX_HOME="exclude"}',
            transport.argv,
        )
        self.assertIn(
            "shell_environment_policy.inherit=none",
            transport.argv,
        )
        self.assertNotIn(
            "shell_environment_policy.inherit=core",
            transport.argv,
        )
        self.assertIn(
            'shell_environment_policy.set={PATH="/opt/homebrew/bin:'
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}',
            transport.argv,
        )
        environment_sets = tuple(
            argument
            for argument in transport.argv
            if argument.startswith("shell_environment_policy.set=")
        )
        self.assertEqual(1, len(environment_sets))
        self.assertNotIn("HOME=", environment_sets[0])
        self.assertNotIn("CODEX_HOME", environment_sets[0])
        self.assertNotIn(
            'shell_environment_policy.exclude=["CODEX_HOME"]',
            transport.argv,
        )

    def test_read_only_invocation_returns_only_public_codex_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            executor = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_SuccessfulCodexTransport(),
            )

            result = executor.run(AgentExecutionRequest(
                invocation_id="invocation-1",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="检查通信协议，只报告有证据的问题。",
                workspace_root=workspace,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
            ))

        self.assertEqual(result.status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(result.backend_id, "codex_cli")
        self.assertEqual(result.cli_version, "0.149.0-alpha.4.3")
        self.assertEqual(result.backend_session_id, "codex-session-1")
        self.assertEqual(result.events[0].data, {})
        self.assertEqual(result.sandbox, "read-only")
        self.assertEqual(result.final_message, "发现一项有依据的问题。")
        self.assertEqual(
            [event.kind for event in result.events],
            [
                "session_started",
                "turn_started",
                "tool_completed",
                "agent_message",
                "turn_completed",
            ],
        )
        self.assertEqual(result.events[2].data["tool"], "shell")
        self.assertEqual(result.events[2].data["command"], "rg --files")
        self.assertEqual(
            result.events[2].data["runtime_observation"],
            {"codex_home_present": False},
        )
        self.assertNotIn("aggregated_output", result.events[2].data)
        self.assertNotIn("private tool output", repr(result))
        self.assertNotIn("private reasoning", repr(result))
        self.assertNotIn("private diagnostics", repr(result))
        self.assertEqual(result.usage.input_tokens, 120)
        self.assertEqual(result.usage.cached_input_tokens, 20)
        self.assertEqual(result.usage.output_tokens, 30)
        self.assertEqual(result.usage.reasoning_output_tokens, 10)
        self.assertEqual(result.duration_ms, 1250)

    def test_runtime_can_report_presence_without_exposing_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_CodexHomePresentTransport(),
            ).run(AgentExecutionRequest(
                invocation_id="invocation-present-observation",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="执行固定安全环境检查。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
            ))

        tool_event = next(
            event for event in result.events if event.kind == "tool_completed"
        )
        self.assertEqual(
            tool_event.data["runtime_observation"],
            {"codex_home_present": True},
        )
        self.assertNotIn("private value", repr(result))

    def test_conflicting_runtime_sentinels_do_not_choose_a_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_ConflictingObservationTransport(),
            ).run(AgentExecutionRequest(
                invocation_id="invocation-conflicting-observation",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="执行固定安全环境检查。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
            ))

        tool_event = next(
            event for event in result.events if event.kind == "tool_completed"
        )
        self.assertNotIn("runtime_observation", tool_event.data)
        self.assertNotIn("CODEX_RUNTIME_ENV_CHECK", repr(result))

    def test_agent_session_can_resume_only_by_its_explicit_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            executor = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_ResumedCodexTransport(),
            )

            result = executor.run(AgentExecutionRequest(
                invocation_id="invocation-2",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="继续上一轮评审。",
                workspace_root=workspace,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
                backend_session_id="codex-session-1",
            ))

        self.assertEqual(result.status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(result.backend_session_id, "codex-session-1")
        self.assertEqual(result.final_message, "继续评审完成。")

    def test_structured_session_failure_becomes_typed_runtime_signal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executor = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_StructuredSessionUnavailableTransport(),
            )
            with self.assertRaises(BackendSessionUnavailable) as raised:
                executor.run(AgentExecutionRequest(
                    invocation_id="invocation-session-unavailable",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    prompt="继续上一轮评审。",
                    workspace_root=Path(temporary),
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=_state_envelope(),
                    backend_session_id="codex-session-stale",
                ))

        self.assertEqual("codex_cli", raised.exception.backend_id)
        self.assertEqual(
            "codex-session-stale", raised.exception.backend_session_id
        )
        self.assertNotIn("private diagnostic", repr(raised.exception))

    def test_stderr_text_cannot_claim_session_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_UnstructuredSessionTextTransport(),
            ).run(AgentExecutionRequest(
                invocation_id="invocation-unstructured-error",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="继续上一轮评审。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=_state_envelope(),
                backend_session_id="codex-session-stale",
            ))

        self.assertEqual(AgentExecutionStatus.FAILED, result.status)
        self.assertNotIn("Session not found", repr(result))
        self.assertNotIn("private-session-id", repr(result))

    def test_timeout_cannot_claim_session_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executor = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_TimedOutSessionFailureTransport(),
            )
            with self.assertRaises(RuntimeProtocolError) as raised:
                executor.run(AgentExecutionRequest(
                    invocation_id="invocation-timeout",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    prompt="继续上一轮评审。",
                    workspace_root=Path(temporary),
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=_state_envelope(),
                    backend_session_id="codex-session-stale",
                ))

        self.assertNotIsInstance(raised.exception, BackendSessionUnavailable)
        self.assertNotIn("private timeout diagnostic", repr(raised.exception))

    def test_malformed_jsonl_fails_without_exposing_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executor = CodexCliAgentExecutor(
                executable=Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex"
                ),
                cli_version="0.149.0-alpha.4.3",
                transport=_MalformedJsonTransport(),
            )
            with self.assertRaises(RuntimeProtocolError) as raised:
                executor.run(AgentExecutionRequest(
                    invocation_id="invocation-malformed-jsonl",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    prompt="继续上一轮评审。",
                    workspace_root=Path(temporary),
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=_state_envelope(),
                    backend_session_id="codex-session-stale",
                ))

        self.assertNotIsInstance(raised.exception, BackendSessionUnavailable)
        self.assertNotIn("private-secret", repr(raised.exception))
        self.assertNotIn("private-secret", repr(raised.exception.__dict__))

    def test_supervised_transport_uses_stdin_and_one_shot_approval(self) -> None:
        class _FakeCodexProcess:
            pid = 424290
            returncode = 0
            stdout = None
            stderr = None

            def __init__(self) -> None:
                self.stdin_received = None

            def communicate(self, input=None, timeout=None):
                del timeout
                self.stdin_received = input
                return "\n".join((
                    '{"type":"thread.started","thread_id":"session-safe-1"}',
                    '{"type":"turn.started"}',
                    '{"type":"item.completed","item":{"type":"agent_message",'
                    '"text":"离线受控执行完成。"}}',
                    '{"type":"turn.completed","usage":{"input_tokens":8,'
                    '"cached_input_tokens":0,"output_tokens":5,'
                    '"reasoning_output_tokens":0}}',
                )), 'token="fake-stderr-secret"'

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def poll(self):
                return self.returncode

        process = _FakeCodexProcess()
        executable = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            executor = CodexCliAgentExecutor(
                executable=executable,
                cli_version="0.149.0-alpha.4.3",
                transport=SupervisedCodexCliTransport(
                    runner=CodexCliProcessRunner(executable=executable),
                    approver_factory=lambda: LocalExecutionApprover(True),
                ),
            )
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=process,
            ) as spawn, mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=lambda _pgid, sig: (
                    (_ for _ in ()).throw(ProcessLookupError())
                    if sig == 0 else None
                ),
            ):
                result = executor.run(AgentExecutionRequest(
                    invocation_id="invocation-supervised-1",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    prompt="只检查公开协议，不输出私有推理。",
                    workspace_root=workspace,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=_state_envelope(),
                ))

        self.assertEqual(process.stdin_received, "只检查公开协议，不输出私有推理。")
        self.assertEqual(result.status, AgentExecutionStatus.COMPLETED)
        self.assertEqual(result.final_message, "离线受控执行完成。")
        self.assertNotIn("fake-stderr-secret", repr(result))
        private_environment = spawn.call_args.args[1]
        self.assertEqual(
            private_environment["CODEX_HOME"],
            str(Path.home().joinpath(".codex").resolve()),
        )
        self.assertNotIn("OPENAI_API_KEY", private_environment)
        self.assertNotIn("DEEPSEEK_API_KEY", private_environment)
        spawn.assert_called_once()

    def test_supervised_transport_fails_closed_without_approval(self) -> None:
        executable = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            executor = CodexCliAgentExecutor(
                executable=executable,
                cli_version="0.149.0-alpha.4.3",
                transport=SupervisedCodexCliTransport(
                    runner=CodexCliProcessRunner(executable=executable),
                    approver_factory=lambda: LocalExecutionApprover(False),
                ),
            )
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                side_effect=AssertionError("unapproved Codex must not spawn"),
            ) as spawn:
                with self.assertRaises(LocalExecutionError) as raised:
                    executor.run(AgentExecutionRequest(
                        invocation_id="invocation-supervised-denied",
                        thread_id="thread-1",
                        agent_id="reviewer-agent",
                        prompt="不得执行。",
                        workspace_root=workspace,
                        permission=AgentExecutionPermission.READ_ONLY,
                        timeout_seconds=30,
                        state_envelope=_state_envelope(),
                    ))

        self.assertIn("trusted local confirmation required", str(raised.exception))
        self.assertNotIn("不得执行", repr(raised.exception.confirmation_request))
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
