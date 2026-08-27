from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import core_coding_eval_run as cli

from coding_workflow import (
    FixedCodingEvaluationRunner,
    FixedCodingSuite,
    FixedRevision,
    VerificationOutcome,
)


class FixedCodingEvaluationRuntimeTests(unittest.TestCase):
    @property
    def suite_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "coding_eval" / "v1"

    def test_three_task_suite_calibrates_starter_and_reference_solution(self) -> None:
        suite = FixedCodingSuite.load(self.suite_root)
        self.assertEqual(
            [task.task_id for task in suite.tasks],
            [
                "python-tax-rounding",
                "python-user-payload",
                "python-inventory-cli",
            ],
        )

        report = FixedCodingEvaluationRunner(
            suite, trusted_local_execution=True
        ).run()

        self.assertTrue(report.calibration_passed)
        self.assertEqual(len(report.trials), 6)
        starters = [
            item for item in report.trials
            if item.revision is FixedRevision.STARTER
        ]
        solutions = [
            item for item in report.trials
            if item.revision is FixedRevision.REFERENCE_SOLUTION
        ]
        self.assertTrue(all(
            item.outcome is VerificationOutcome.FAILED for item in starters
        ))
        self.assertTrue(all(
            item.outcome is VerificationOutcome.PASSED for item in solutions
        ))
        inventory = next(
            item for item in solutions
            if item.task_id == "python-inventory-cli"
        )
        self.assertEqual(
            {item.validator_kind: item.outcome for item in inventory.validators},
            {
                "core:build": VerificationOutcome.PASSED,
                "core:cli": VerificationOutcome.PASSED,
                "core:test": VerificationOutcome.PASSED,
            },
        )

    def test_versioned_report_writes_metrics_without_hidden_source(self) -> None:
        report = FixedCodingEvaluationRunner(
            FixedCodingSuite.load(self.suite_root),
            trusted_local_execution=True,
        ).run()
        with tempfile.TemporaryDirectory() as temp:
            output = report.write_json(Path(temp) / "nested" / "report.json")
            raw = output.read_text(encoding="utf-8")
            parsed = json.loads(raw)

        self.assertEqual(parsed["schema_version"], "1.0")
        self.assertEqual(parsed["summary"]["task_count"], 3)
        self.assertEqual(parsed["summary"]["trial_count"], 6)
        self.assertEqual(parsed["summary"]["starter"]["delivery_rate"], 0.0)
        self.assertEqual(
            parsed["summary"]["reference_solution"]["delivery_rate"], 1.0
        )
        self.assertTrue(parsed["calibration_passed"])
        self.assertEqual(len(report.digest), 64)
        self.assertNotIn("2.675", raw)
        self.assertNotIn("0.045", raw)
        self.assertNotIn(".harness-hidden-tests/validate.py", raw)

    def test_each_workspace_is_reset_from_frozen_starter(self) -> None:
        task = FixedCodingSuite.load(self.suite_root).task(
            "python-user-payload"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = task.prepare_workspace(root / "first")
            (first / "user_api.py").write_text("broken = True\n")
            second = task.prepare_workspace(root / "second")
            expected = task.task_root / "starter" / "user_api.py"
            self.assertEqual(
                second.joinpath("user_api.py").read_bytes(),
                expected.read_bytes(),
            )

    def test_suite_with_passing_starter_fails_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "suite"
            shutil.copytree(self.suite_root, copied)
            task_root = copied / "tasks/python-tax-rounding"
            starter = task_root / "starter/tax.py"
            starter.write_bytes(task_root.joinpath("solution/tax.py").read_bytes())
            manifest_path = copied / "suite.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            task = next(
                item for item in manifest["tasks"]
                if item["task_id"] == "python-tax-rounding"
            )
            next(
                item for item in task["starter_files"]
                if item["path"] == "tax.py"
            )["sha256"] = sha256(starter.read_bytes()).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            report = FixedCodingEvaluationRunner(
                FixedCodingSuite.load(copied),
                trusted_local_execution=True,
            ).run()

        self.assertFalse(report.calibration_passed)
        self.assertFalse(report.calibration_by_task["python-tax-rounding"])
        starter_trial = next(
            item for item in report.trials
            if item.task_id == "python-tax-rounding"
            and item.revision is FixedRevision.STARTER
        )
        self.assertEqual(starter_trial.outcome, VerificationOutcome.PASSED)

    def test_local_execution_flag_requires_a_real_bool(self) -> None:
        with self.assertRaisesRegex(TypeError, "真正的 bool"):
            FixedCodingEvaluationRunner(
                FixedCodingSuite.load(self.suite_root),
                trusted_local_execution=1,  # type: ignore[arg-type]
            )

    def test_library_default_reports_unknown_without_approval(self) -> None:
        report = FixedCodingEvaluationRunner(
            FixedCodingSuite.load(self.suite_root)
        ).run()

        self.assertFalse(report.calibration_passed)
        self.assertTrue(all(
            trial.outcome is VerificationOutcome.UNKNOWN
            for trial in report.trials
        ))

    def test_cli_requires_explicit_local_execution_before_evaluation(self) -> None:
        with patch.object(cli.FixedCodingSuite, "load") as loader:
            with self.assertRaises(SystemExit) as raised:
                cli.main([])
        loader.assert_not_called()
        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(cli.parse_args([
            "--trusted-local-execution",
        ]).trusted_local_execution)


if __name__ == "__main__":
    unittest.main()
