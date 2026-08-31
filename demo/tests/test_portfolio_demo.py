from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import portfolio_demo as cli
from coding_workflow import (
    AblationStageAudit,
    AblationStrategy,
    AblationTrialResult,
    CodingAblationReport,
    UsageSource,
    VerificationOutcome,
)
from coding_workflow.portfolio_agent_runtime import PortfolioAgentRun


class PortfolioDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = SimpleNamespace(
            suite_id=cli.SUITE_ID,
            manifest_sha256=cli.SUITE_MANIFEST_SHA256,
            tasks=tuple(SimpleNamespace(task_id=item) for item in cli.TASK_IDS),
        )

    @staticmethod
    def _audits(strategy: AblationStrategy) -> tuple[AblationStageAudit, ...]:
        definitions = {
            AblationStrategy.SINGLE_AGENT: (
                ("implement", "implementer", "core:patch"),
            ),
            AblationStrategy.PLANNER_DEVELOPER: (
                ("plan", "planner", "core:plan"),
                ("implement", "implementer", "core:patch"),
            ),
            AblationStrategy.TESTER_FIXER: (
                ("plan", "planner", "core:plan"),
                ("implement", "implementer", "core:patch"),
                ("diagnose", "tester", "core:test_diagnosis"),
                ("fix", "fixer", "core:patch"),
            ),
        }
        return tuple(
            AblationStageAudit(
                stage,
                role,
                f"scripted-{role}",
                f"scripted-{role}-principal",
                {},
                (),
                output_kind,
                UsageSource.SCRIPTED,
                1,
            )
            for stage, role, output_kind in definitions[strategy]
        )

    @classmethod
    def _trial(
        cls,
        task_id: str,
        strategy: AblationStrategy,
    ) -> AblationTrialResult:
        if strategy is AblationStrategy.SINGLE_AGENT:
            initial = final = VerificationOutcome.FAILED
            delivered = first = fix_attempted = fixed = False
            rounds = 0
            calls = 1
            failures = ("expected control failure",)
        elif strategy is AblationStrategy.PLANNER_DEVELOPER:
            initial = final = VerificationOutcome.PASSED
            delivered = first = True
            fix_attempted = fixed = False
            rounds = 0
            calls = 2
            failures = ()
        else:
            initial = VerificationOutcome.FAILED
            final = VerificationOutcome.PASSED
            delivered = fix_attempted = fixed = True
            first = False
            rounds = 1
            calls = 4
            failures = ()
        return AblationTrialResult(
            task_id,
            strategy,
            final,
            initial,
            delivered,
            first,
            fix_attempted,
            fixed,
            rounds,
            1,
            calls,
            calls,
            calls,
            calls,
            0,
            0,
            0,
            0,
            {"core:test": final.value},
            cls._audits(strategy),
            failures,
        )

    @classmethod
    def _report(cls) -> CodingAblationReport:
        return CodingAblationReport(
            cli.SUITE_ID,
            cli.SUITE_MANIFEST_SHA256,
            "budget-digest",
            {strategy.value: "profile" for strategy in AblationStrategy},
            "2026-08-27T00:00:00+00:00",
            "2026-08-27T00:00:01+00:00",
            tuple(
                cls._trial(task_id, strategy)
                for task_id in cli.TASK_IDS
                for strategy in AblationStrategy
            ),
        )

    @classmethod
    def _runtime(cls) -> dict[str, object]:
        agents: list[dict[str, object]] = []
        handoffs: list[dict[str, object]] = []
        for task_id in cli.TASK_IDS:
            for strategy in AblationStrategy:
                previous: str | None = None
                for audit in cls._audits(strategy):
                    role = {
                        "planner": "Planner",
                        "implementer": "Developer",
                        "tester": "Tester",
                        "fixer": "Fixer",
                    }[audit.role]
                    message_id = f"message-{task_id}-{strategy.value}-{audit.stage_id}"
                    agent_id = f"agent-{task_id}-{strategy.value}-{audit.role}"
                    agents.append({
                        "task_id": task_id,
                        "strategy": strategy.value,
                        "thread_id": f"thread-{task_id}-{strategy.value}",
                        "role": role,
                        "agent_instance_id": agent_id,
                        "agent_session_id": f"session-{agent_id}",
                        "session_state": "closed",
                        "session_version": 4,
                        "lifecycle": ["created", "paused", "resumed", "closed"],
                        "mailbox_sequences": [1, 2],
                        "consumed_sequences": [1, 2],
                        "private_state_version": 4,
                    })
                    sender_agent_id = (
                        agent_id if previous is None else previous_agent_id
                    )
                    handoffs.append({
                        "contract": "portfolio-agent-handoff/v1",
                        "task_id": task_id,
                        "strategy": strategy.value,
                        "stage": audit.stage_id,
                        "kind": f"core:{audit.stage_id}_handoff",
                        "message_id": message_id,
                        "thread_id": f"thread-{task_id}-{strategy.value}",
                        "parent_message_id": previous,
                        "causation_message_id": previous,
                        "sender_agent_id": sender_agent_id,
                        "recipient_agent_id": agent_id,
                        "recipient_role": role,
                        "recipient_session_id": f"session-{agent_id}",
                        "mailbox_sequence": 2,
                        "input_artifact_refs": [],
                        "output_artifact_ref": f"artifact://{audit.output_kind}",
                        "consumed": True,
                        "lane_thread": "agent-lane_0",
                        "queued_mailbox_sequence": 2,
                        "is_handoff": sender_agent_id != agent_id,
                    })
                    previous = message_id
                    previous_agent_id = agent_id
        cross_agent_handoffs = [
            item for item in handoffs
            if item["sender_agent_id"] != item["recipient_agent_id"]
        ]
        return {
            "contract": "portfolio-agent-runtime/v1",
            "scope_id": "portfolio-demo",
            "run_id": "run-test",
            "database_path": "runtime.sqlite3",
            "thread_count": 9,
            "agent_count": 21,
            "stage_message_count": 21,
            "handoff_count": 12,
            "agents": agents,
            "stage_messages": handoffs,
            "handoffs": cross_agent_handoffs,
            "mailbox": {
                "enqueued": 42,
                "consumed": 42,
                "all_consumed": True,
                "consume_semantics": "receive-time cursor; no ack or redelivery",
            },
            "sessions": {"states": {"closed": 21}, "all_closed": True},
            "lane_evidence": {
                "fifo_observed": True,
                "per_agent_expected_sequences": [1, 2],
                "max_parallel_agents": 3,
                "shared_pool": True,
                "single_active_drain_per_agent": True,
            },
            "validator": {"owner": "runtime", "is_agent": False},
            "limitations": [],
        }

    def _run_mocked(
        self,
        report: CodingAblationReport,
        output: Path,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(cli.FixedCodingSuite, "load", return_value=self.suite),
            patch.object(
                cli,
                "build_scripted_ablation_registry",
                return_value=(object(), {}),
            ),
            patch.object(
                cli.PortfolioAgentAblationRunner,
                "run",
                return_value=PortfolioAgentRun(report, self._runtime()),
            ),
            patch.object(cli, "REPORT_PATH", output),
            patch.object(cli, "RUNTIME_DB_PATH", output.with_suffix(".sqlite3")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = cli.main(["--trusted-local-execution"])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_success_writes_exact_contract_and_public_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            code, stdout, stderr = self._run_mocked(self._report(), output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            set(payload),
            {
                "schema_version", "demo_id", "status", "mode", "suite",
                "execution", "workflow", "summary", "verification",
                "trials", "agent_runtime", "output", "limitations",
            },
        )
        self.assertEqual(payload["schema_version"], cli.REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["schema_version"], "portfolio-demo-report/v2")
        self.assertEqual(payload["demo_id"], cli.DEMO_ID)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["mode"], "offline_scripted")
        self.assertFalse(payload["execution"]["network_access"])
        self.assertFalse(payload["execution"]["real_provider"])
        self.assertEqual(payload["summary"]["trials"], 9)
        self.assertEqual(payload["summary"]["delivered"], 6)
        self.assertEqual(payload["summary"]["expected_failures"], 3)
        self.assertEqual(payload["summary"]["repaired"], 3)
        self.assertEqual(payload["execution"]["scripted_worker_calls"], 21)
        self.assertEqual(payload["execution"]["external_model_calls"], 0)
        self.assertEqual(payload["agent_runtime"]["agent_count"], 21)
        self.assertEqual(payload["agent_runtime"]["stage_message_count"], 21)
        self.assertEqual(payload["agent_runtime"]["handoff_count"], 12)
        self.assertTrue(payload["agent_runtime"]["sessions"]["all_closed"])
        self.assertTrue(payload["agent_runtime"]["lane_evidence"]["fifo_observed"])
        self.assertGreaterEqual(
            payload["agent_runtime"]["lane_evidence"]["max_parallel_agents"],
            2,
        )
        self.assertEqual(payload["verification"]["mismatches"], [])
        self.assertNotIn("reasoning", json.dumps(payload["trials"]))
        lines = stdout.strip().splitlines()
        self.assertIn("mode=scripted/offline", lines[0])
        self.assertEqual(
            [line.split()[0] for line in lines[2:8]],
            [
                "role=Planner", "role=Developer", "role=Validator",
                "role=Tester", "role=Fixer", "role=Validator",
            ],
        )
        self.assertIn("threads=9", lines[1])
        self.assertIn("agents=21", lines[1])
        self.assertIn("mailbox_sent=42", lines[1])
        self.assertIn("mailbox_received=42", lines[1])
        self.assertIn("handoffs=12", lines[1])
        self.assertIn("fifo=true", lines[1])
        self.assertIn("max_parallel_agents=3", lines[1])
        self.assertIn("result=failed", lines[4])
        self.assertIn("result=passed", lines[7])
        self.assertIn("thread_id=", lines[2])
        self.assertIn("agent_id=", lines[2])
        self.assertIn("session_id=", lines[2])
        self.assertIn("session_state=closed", lines[2])
        self.assertIn("lifecycle=created>paused>resumed>closed", lines[2])
        self.assertIn("message_id=", lines[2])
        self.assertIn("handoff=false", lines[2])
        self.assertIn("handoff=true", lines[3])
        self.assertIn("Artifact=core:plan", lines[2])
        self.assertIn("ArtifactRef=artifact://", lines[2])
        self.assertEqual(
            lines[-1],
            "status=passed tasks=3 trials=9 delivered=6 "
            "expected_failures=3 repaired=3 external_model_calls=0 "
            "report=demo/.runs/portfolio-demo/report.json",
        )

    def test_illegal_argument_rejects_before_suite_runner_and_report(self) -> None:
        with (
            patch.object(cli.FixedCodingSuite, "load") as loader,
            patch.object(cli.PortfolioAgentAblationRunner, "run") as runner,
            patch.object(cli, "write_report_atomic") as writer,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main(["--suite", "unexpected"])

        self.assertEqual(raised.exception.code, 2)
        loader.assert_not_called()
        runner.assert_not_called()
        writer.assert_not_called()

    def test_missing_approval_rejects_before_suite_runner_and_report(self) -> None:
        with (
            patch.object(cli.FixedCodingSuite, "load") as loader,
            patch.object(cli.PortfolioAgentAblationRunner, "run") as runner,
            patch.object(cli, "write_report_atomic") as writer,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main([])

        self.assertEqual(raised.exception.code, 2)
        loader.assert_not_called()
        runner.assert_not_called()
        writer.assert_not_called()

    def test_unexpected_validator_failure_exits_one_and_keeps_evidence(self) -> None:
        report = self._report()
        trials = list(report.trials)
        target = next(
            index for index, trial in enumerate(trials)
            if trial.task_id == "python-tax-rounding"
            and trial.strategy is AblationStrategy.PLANNER_DEVELOPER
        )
        trials[target] = replace(
            trials[target],
            outcome=VerificationOutcome.FAILED,
            initial_outcome=VerificationOutcome.FAILED,
            delivered=False,
            first_passed=False,
            validator_outcomes={"core:test": "failed"},
            failure_reasons=("injected Validator failure",),
        )
        report = replace(report, trials=tuple(trials))

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            code, stdout, stderr = self._run_mocked(report, output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(payload["verification"]["mismatches"])
        failed_trial = next(
            item for item in payload["trials"]
            if item["task_id"] == "python-tax-rounding"
            and item["strategy"] == AblationStrategy.PLANNER_DEVELOPER.value
        )
        self.assertEqual(failed_trial["validator_outcomes"]["core:test"], "failed")
        self.assertIn("injected Validator failure", failed_trial["failure_reasons"])
        self.assertTrue(stdout.rstrip().splitlines()[-1].startswith("status=failed"))

    def test_atomic_write_failure_is_setup_exit_three(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            isolated_runtime = Path(temporary) / "runtime.sqlite3"
            with (
                patch.object(cli, "RUNTIME_DB_PATH", isolated_runtime),
                patch.object(
                    cli.FixedCodingSuite,
                    "load",
                    return_value=self.suite,
                ),
                patch.object(
                    cli,
                    "build_scripted_ablation_registry",
                    return_value=(object(), {}),
                ),
                patch.object(
                    cli.PortfolioAgentAblationRunner,
                    "run",
                    return_value=PortfolioAgentRun(
                        self._report(),
                        self._runtime(),
                    ),
                ),
                patch.object(
                    cli,
                    "write_report_atomic",
                    side_effect=OSError("injected write failure"),
                ),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(stderr),
            ):
                code = cli.main(["--trusted-local-execution"])

        self.assertEqual(code, 3)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("OSError: injected write failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
