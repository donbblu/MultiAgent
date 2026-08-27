from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import core_coding_ablation_run as cli

from coding_workflow import (
    AblationBudget,
    AblationStrategy,
    AblationUsage,
    AblationWorkerResponse,
    ArtifactDraft,
    CodingAblationRunner,
    FileChange,
    FixedCodingSuite,
    ImplementationPlan,
    UsageSource,
    VerificationOutcome,
    WorkerDescriptor,
    WorkerRegistry,
    build_scripted_ablation_registry,
    default_ablation_profiles,
)


class CodingAblationTests(unittest.TestCase):
    @property
    def suite(self) -> FixedCodingSuite:
        root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        return FixedCodingSuite.load(root)

    def test_scripted_dry_run_exercises_all_three_strategies(self) -> None:
        suite = self.suite
        registry, _ = build_scripted_ablation_registry(suite)
        report = CodingAblationRunner(
            suite, registry, trusted_local_execution=True
        ).run()
        summary = report.summary()

        self.assertTrue(report.dry_run)
        self.assertEqual(len(report.trials), 9)
        self.assertEqual(
            summary[AblationStrategy.SINGLE_AGENT.value]["delivered"], 0
        )
        self.assertEqual(
            summary[AblationStrategy.PLANNER_DEVELOPER.value]["first_passed"],
            3,
        )
        full = summary[AblationStrategy.TESTER_FIXER.value]
        self.assertEqual(full["first_passed"], 0)
        self.assertEqual(full["fix_attempted"], 3)
        self.assertEqual(full["fix_succeeded"], 3)
        self.assertEqual(full["delivered"], 3)
        self.assertEqual(
            sum(item.model_calls for item in report.trials), 0
        )
        self.assertGreater(
            sum(item.scripted_tokens for item in report.trials), 0
        )

    def test_visibility_policy_never_grants_hidden_tests_or_solution(self) -> None:
        suite = self.suite
        registry, workers = build_scripted_ablation_registry(suite)
        report = CodingAblationRunner(
            suite, registry, trusted_local_execution=True
        ).run()

        for worker in workers.values():
            for request in worker.requests:
                visible = request.visible_artifacts
                self.assertTrue(visible)
                self.assertFalse(any(
                    "hidden" in name.lower() or "solution" in name.lower()
                    for name in visible
                ))
                self.assertFalse(any(
                    "hidden" in item.kind.lower()
                    or "solution" in item.kind.lower()
                    for item in visible.values()
                ))
        full_trials = [
            item for item in report.trials
            if item.strategy is AblationStrategy.TESTER_FIXER
        ]
        for trial in full_trials:
            audits = {item.stage_id: item for item in trial.stage_audits}
            self.assertNotIn("core:validator_feedback", audits["implement"].visible_kinds)
            self.assertIn("core:validator_feedback", audits["diagnose"].visible_kinds)
            self.assertIn("core:test_diagnosis", audits["fix"].visible_kinds)
            self.assertNotEqual(
                audits["implement"].principal_id,
                audits["diagnose"].principal_id,
            )

    def test_all_strategies_share_frozen_budget_and_validator_set(self) -> None:
        suite = self.suite
        profiles = default_ablation_profiles()
        self.assertEqual(len({item.budget.digest for item in profiles}), 1)
        dual = profiles[1]
        relaxed = replace(
            dual.stage("implement"),
            required_kinds=frozenset({
                "core:coding_requirement", "core:source_snapshot",
            }),
        )
        with self.assertRaisesRegex(ValueError, "边界被修改"):
            type(dual)(dual.strategy, (dual.stage("plan"), relaxed), dual.budget)
        registry, _ = build_scripted_ablation_registry(suite)
        report = CodingAblationRunner(
            suite,
            registry,
            profiles,
            trusted_local_execution=True,
        ).run()

        by_task: dict[str, set[tuple[str, ...]]] = {}
        for trial in report.trials:
            by_task.setdefault(trial.task_id, set()).add(
                tuple(sorted(trial.validator_outcomes))
            )
        self.assertTrue(all(len(items) == 1 for items in by_task.values()))
        self.assertEqual(len(set(report.profile_digests.values())), 3)
        self.assertEqual(len(report.budget_digest), 64)

    def test_budget_exhaustion_stops_before_unapproved_extra_call(self) -> None:
        suite = self.suite
        budget = AblationBudget(
            max_worker_calls=1,
            max_accounted_tokens=2000,
            max_fix_rounds=1,
        )
        profiles = default_ablation_profiles(budget)
        registry, _ = build_scripted_ablation_registry(suite)
        report = CodingAblationRunner(
            suite,
            registry,
            profiles,
            trusted_local_execution=True,
        ).run()
        dual = next(
            item for item in report.trials
            if item.task_id == "python-tax-rounding"
            and item.strategy is AblationStrategy.PLANNER_DEVELOPER
        )

        self.assertEqual(dual.outcome, VerificationOutcome.UNKNOWN)
        self.assertEqual(dual.worker_calls, 1)
        self.assertEqual(len(dual.stage_audits), 1)
        self.assertIn("调用预算耗尽", " ".join(dual.failure_reasons))

    def test_patch_outside_task_scope_is_failed_and_counted(self) -> None:
        class UnauthorizedWorker:
            def run_experiment(self, request):
                return AblationWorkerResponse(
                    ArtifactDraft(
                        ImplementationPlan(
                            "tamper with public tests",
                            [FileChange(
                                "tests/test_tax_public.py",
                                "# bypass\n",
                                "bypass validator",
                            )],
                        ),
                        kind="core:patch",
                    ),
                    "unauthorized patch",
                    AblationUsage(UsageSource.SCRIPTED, 1, 1),
                )

        suite = self.suite
        registry = WorkerRegistry()
        registry.register_worker(
            WorkerDescriptor(
                "unauthorized-implementer",
                "implementer",
                frozenset({"code_generation"}),
                frozenset({
                    "core:coding_requirement", "core:source_snapshot",
                }),
                frozenset({"core:patch"}),
                frozenset({"offline-eval"}),
            ),
            UnauthorizedWorker(),
        )
        runner = CodingAblationRunner(suite, registry)
        with tempfile.TemporaryDirectory() as temp:
            trial = runner.run_trial(
                suite.task("python-tax-rounding"),
                runner.profiles[0],
                Path(temp),
            )

        self.assertEqual(trial.outcome, VerificationOutcome.FAILED)
        self.assertEqual(trial.unauthorized_attempts, 1)
        self.assertFalse(trial.delivered)
        self.assertIn("允许范围", " ".join(trial.failure_reasons))

    def test_dry_run_rejects_worker_reporting_model_usage(self) -> None:
        class MisconfiguredModelWorker:
            def run_experiment(self, request):
                return AblationWorkerResponse(
                    ArtifactDraft(
                        ImplementationPlan("no-op", []), kind="core:patch"
                    ),
                    "misconfigured model worker",
                    AblationUsage(UsageSource.MODEL, 10, 10),
                )

        suite = self.suite
        registry = WorkerRegistry()
        registry.register_worker(
            WorkerDescriptor(
                "model-implementer",
                "implementer",
                frozenset({"code_generation"}),
                frozenset({
                    "core:coding_requirement", "core:source_snapshot",
                }),
                frozenset({"core:patch"}),
                frozenset({"offline-eval"}),
            ),
            MisconfiguredModelWorker(),
        )
        runner = CodingAblationRunner(suite, registry)
        with tempfile.TemporaryDirectory() as temp:
            trial = runner.run_trial(
                suite.task("python-tax-rounding"),
                runner.profiles[0],
                Path(temp),
            )

        self.assertEqual(trial.outcome, VerificationOutcome.UNKNOWN)
        self.assertEqual(trial.model_calls, 0)
        self.assertIn("禁止登记真实模型", " ".join(trial.failure_reasons))

    def test_json_report_marks_scripted_usage_and_contains_no_hidden_source(self) -> None:
        suite = self.suite
        registry, _ = build_scripted_ablation_registry(suite)
        report = CodingAblationRunner(
            suite, registry, trusted_local_execution=True
        ).run()
        with tempfile.TemporaryDirectory() as temp:
            output = report.write_json(Path(temp) / "ablation.json")
            raw = output.read_text(encoding="utf-8")
            parsed = json.loads(raw)

        self.assertTrue(parsed["dry_run"])
        self.assertEqual(len(parsed["trials"]), 9)
        self.assertEqual(sum(
            trial["model_calls"] for trial in parsed["trials"]
        ), 0)
        self.assertGreater(sum(
            trial["scripted_calls"] for trial in parsed["trials"]
        ), 0)
        self.assertNotIn("2.675", raw)
        self.assertNotIn(".harness-hidden-tests", raw)
        self.assertNotIn("ROUND_HALF_UP", raw)

    def test_local_execution_flag_requires_a_real_bool(self) -> None:
        registry, _ = build_scripted_ablation_registry(self.suite)
        with self.assertRaisesRegex(TypeError, "真正的 bool"):
            CodingAblationRunner(
                self.suite,
                registry,
                trusted_local_execution=1,  # type: ignore[arg-type]
            )

    def test_library_default_fails_closed_at_validation(self) -> None:
        suite = self.suite
        registry, _ = build_scripted_ablation_registry(suite)
        runner = CodingAblationRunner(suite, registry)
        with tempfile.TemporaryDirectory() as temp:
            trial = runner.run_trial(
                suite.task("python-tax-rounding"),
                runner.profiles[1],
                Path(temp),
            )

        self.assertEqual(trial.outcome, VerificationOutcome.UNKNOWN)
        self.assertFalse(trial.delivered)

    def test_cli_requires_explicit_local_execution_before_ablation(self) -> None:
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
