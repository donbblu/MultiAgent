from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import coding_agent_cli as cli
from coding_workflow.models import (
    CommandResult,
    TaskContext,
    TaskState,
    VerificationResult,
)


class CodingAgentCliLocalExecutionReportTests(unittest.TestCase):
    @staticmethod
    def _completed_run() -> cli.CodingRun:
        command_result = CommandResult(
            command=["python3", "-V"],
            exit_code=0,
            stdout="Python 3.9.6\n token=not-a-real-secret",
            stderr="",
            profile_manifest={"profile_id": "legacy_workspace_verify"},
            cleanup_evidence={"status": "terminal", "verified": True},
            cleanup_evidence_digest="a" * 64,
        )
        task = TaskContext(
            "CLI-REPORT",
            "report",
            ["command succeeds"],
            [["python3", "-V"]],
            state=TaskState.COMPLETED,
            verification=VerificationResult(
                True,
                "全部验证命令通过",
                command_results=[command_result],
            ),
        )
        return cli.CodingRun(
            task,
            Path("/private/tmp/cli-report-mock"),
            "mock",
            "deterministic",
        )

    def test_default_cli_rejects_before_task_and_reports_zero_spawn(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            cli,
            "run_requirement",
            side_effect=AssertionError("default path reached task/model boundary"),
        ) as run_requirement, redirect_stdout(output):
            exit_code = cli.main([
                "request token=not-a-real-secret",
                "--local-execution-report",
                "json",
            ])

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "rejected_before_task")
        self.assertEqual(report["task_outcome"], "not_started")
        self.assertEqual(report["spawn_count"], 0)
        self.assertEqual(report["spawn_count_source"], "preflight_zero")
        self.assertEqual(report["terminal_execution_count"], 0)
        self.assertEqual(report["command_result_count"], 0)
        self.assertFalse(report["approval"]["requested"])
        self.assertFalse(report["approval"]["opaque_token_exposed"])
        self.assertNotIn("not-a-real-secret", output.getvalue())
        run_requirement.assert_not_called()

    def test_explicit_approval_is_forwarded_and_terminal_data_is_visible(self) -> None:
        run = self._completed_run()
        output = io.StringIO()
        with mock.patch.object(
            cli, "run_requirement", return_value=run
        ) as run_requirement, mock.patch.object(
            cli.time, "monotonic", side_effect=(10.0, 10.012)
        ), redirect_stdout(output):
            exit_code = cli.main([
                "request",
                "--trusted-local-execution",
                "--local-execution-report",
                "json",
            ])

        self.assertEqual(exit_code, 0)
        run_requirement.assert_called_once()
        self.assertIs(
            run_requirement.call_args.kwargs["trusted_local_execution"], True
        )
        rendered = output.getvalue()
        report = json.loads(rendered)
        self.assertEqual(report["status"], "terminal")
        self.assertTrue(report["approval"]["requested"])
        self.assertIsNone(report["spawn_count"])
        self.assertEqual(report["spawn_count_source"], "not_instrumented")
        self.assertEqual(report["terminal_execution_count"], 1)
        self.assertEqual(report["command_result_count"], 1)
        self.assertEqual(report["duration_ms"], 12)
        self.assertNotIn("not-a-real-secret", rendered)

    def test_json_and_markdown_share_the_same_token_free_projection(self) -> None:
        report = cli.build_local_execution_report(
            approved=True,
            verification_command=["python3", "-V"],
            duration_ms=12,
            run=self._completed_run(),
        )
        encoded = cli.render_local_execution_report(report, "json")
        markdown = cli.render_local_execution_report(report, "markdown")

        decoded = json.loads(encoded)
        self.assertEqual(decoded["schema"], cli.LOCAL_EXECUTION_REPORT_SCHEMA)
        self.assertIsNone(decoded["spawn_count"])
        self.assertEqual(decoded["terminal_execution_count"], 1)
        self.assertEqual(decoded["results"][0]["profile_id"], "legacy_workspace_verify")
        self.assertTrue(decoded["results"][0]["cleanup_verified"])
        self.assertIn("| [\"python3\", \"-V\"] | legacy_workspace_verify |", markdown)
        self.assertIn("`1`", markdown)
        self.assertNotIn("not-a-real-secret", encoded)
        self.assertNotIn("not-a-real-secret", markdown)
        self.assertNotIn("_TrustedLocalConfirmation", encoded)

    def test_command_projection_redacts_values_split_across_argv(self) -> None:
        report = cli.build_local_execution_report(
            approved=False,
            verification_command=[
                "python3", "tool.py", "--token", "split-secret-value",
                "Bearer", "bearer-secret-value", "api_key=inline-secret",
            ],
            duration_ms=0,
        )
        encoded = cli.render_local_execution_report(report, "json")
        self.assertNotIn("split-secret-value", encoded)
        self.assertNotIn("bearer-secret-value", encoded)
        self.assertNotIn("inline-secret", encoded)
        self.assertGreaterEqual(encoded.count("[REDACTED]"), 3)

        private_key_report = cli.build_local_execution_report(
            approved=False,
            verification_command=[
                "python3", "tool.py", "-----BEGIN", "PRIVATE", "KEY-----",
                "private-material",
            ],
            duration_ms=0,
        )
        private_encoded = cli.render_local_execution_report(
            private_key_report, "json"
        )
        self.assertNotIn("private-material", private_encoded)
        self.assertIn("[REDACTED PRIVATE KEY COMMAND]", private_encoded)

    def test_approved_request_without_command_result_is_not_claimed_as_spawn(self) -> None:
        task = TaskContext(
            "CLI-REJECTED",
            "report",
            ["command succeeds"],
            [["python3", "-V"]],
            state=TaskState.FAILED,
            verification=VerificationResult(
                False,
                "验证命令未获本地执行准入",
            ),
        )
        run = cli.CodingRun(
            task,
            Path("/private/tmp/cli-report-rejected"),
            "mock",
            "deterministic",
        )
        report = cli.build_local_execution_report(
            approved=True,
            verification_command=["python3", "-V"],
            duration_ms=1,
            run=run,
        )
        self.assertEqual(report["status"], "rejected")
        self.assertIsNone(report["spawn_count"])
        self.assertEqual(report["terminal_execution_count"], 0)
        self.assertEqual(report["command_result_count"], 0)
        self.assertIn(
            "| — | — | — | — | — | — |",
            cli.render_local_execution_report(report, "markdown"),
        )

    def test_report_format_is_strict(self) -> None:
        report = cli.build_local_execution_report(
            approved=False,
            verification_command=["python3", "-V"],
            duration_ms=0,
        )
        with self.assertRaisesRegex(ValueError, "text、json 或 markdown"):
            cli.render_local_execution_report(report, "html")
        with self.assertRaisesRegex(TypeError, "真正的 bool"):
            cli.build_local_execution_report(
                approved=1,  # type: ignore[arg-type]
                verification_command=["python3", "-V"],
                duration_ms=0,
            )

    def test_run_requirement_builds_fresh_exact_bool_approvers(self) -> None:
        approved_values: list[bool] = []
        approver_ids: list[int] = []

        class RecordingApprover:
            def __init__(self, approved: bool) -> None:
                approved_values.append(approved)

        def fake_dag(task, _client, _workspace, **kwargs):
            factory = kwargs["approver_factory"]
            first = factory()
            second = factory()
            approver_ids.extend((id(first), id(second)))
            return SimpleNamespace(task=task)

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            cli, "OUTPUT_ROOT", Path(temp)
        ), mock.patch.object(
            cli, "LocalExecutionApprover", RecordingApprover
        ), mock.patch.object(
            cli, "load_env_file"
        ), mock.patch.object(
            cli.ModelClientFactory,
            "config_from_env",
            return_value=SimpleNamespace(provider="mock", model="deterministic"),
        ), mock.patch.object(
            cli.ModelClientFactory, "create", return_value=object()
        ), mock.patch.object(
            cli, "run_dag_task", side_effect=fake_dag
        ):
            run = cli.run_requirement(
                "request",
                "fresh-approvers",
                verification_command=["python3", "-V"],
                trusted_local_execution=True,
            )

        self.assertEqual(run.provider, "mock")
        self.assertEqual(approved_values, [True, True])
        self.assertEqual(len(set(approver_ids)), 2)


if __name__ == "__main__":
    unittest.main()
