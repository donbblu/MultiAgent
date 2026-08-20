from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from coding_workflow.artifacts import Artifact, ArtifactStore
from coding_workflow.model import (
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from coding_workflow.models import FileChange, ImplementationPlan
from coding_workflow.visionforge import (
    BROWSER_RUN,
    BudgetedModelClient,
    BrowserProcessRunner,
    BrowserRunResult,
    EvaluationConfig,
    EvaluationBudgetExceeded,
    EvaluationBudgetSnapshot,
    EvaluationModelBudget,
    EvaluationSuite,
    EvaluationTrialResult,
    EvaluationVariant,
    ReferenceImageRenderer,
    RUN,
    RuntimeEvaluationTrialExecutor,
    VISUAL_REVIEW,
    VisionForgeCycle,
    VisionForgeEvaluator,
    VisionForgeRunResult,
    estimate_model_calls,
)


ROOT = Path(__file__).parents[1]
SUITE_PATH = ROOT / "visionforge_eval" / "v1" / "suite.json"
RENDERER_PATH = ROOT / "visionforge_eval" / "render-reference.mjs"


class StubRenderer:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def render(self, task, output_path):
        self.tasks.append(task.task_id)
        return ROOT / "docs" / "multi-agent-architecture.png"


class ScriptedExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, EvaluationVariant, int]] = []

    def execute(
        self,
        *,
        task,
        variant,
        repetition,
        reference_image_path,
        config,
    ):
        self.calls.append((task.task_id, variant, repetition))
        if variant is EvaluationVariant.LLM_ONCE:
            return EvaluationTrialResult(
                task.task_id, variant, repetition,
                True, True, False, False, False, 0, 72, 100, 1000,
            )
        if variant is EvaluationVariant.LLM_BROWSER:
            return EvaluationTrialResult(
                task.task_id, variant, repetition,
                True, True, False, False, False, 0, 78, 130, 1300,
            )
        return EvaluationTrialResult(
            task.task_id, variant, repetition,
            True, True, True, False, True, 1, 91, 190, 1800,
        )


class EvaluationContractTests(unittest.TestCase):
    def test_call_estimate_has_explicit_three_variant_upper_bound(self) -> None:
        estimate = estimate_model_calls(
            task_count=3, repetitions=1, max_fix_attempts=2
        )
        self.assertEqual(estimate.text_calls, 21)
        self.assertEqual(estimate.vision_calls, 30)
        self.assertEqual(estimate.total_calls, 51)
        self.assertEqual(
            estimate.by_variant[EvaluationVariant.LLM_ONCE.value],
            {"text": 1, "vision": 2},
        )

    def test_budgeted_client_stops_before_call_limit_is_exceeded(self) -> None:
        class Client:
            capabilities = frozenset(ModelCapability)

            def generate_structured(self, request):
                return ModelResponse(
                    {"ok": True}, "fake", "budget", ModelUsage(3, 2, 5), 1
                )

            def generate_json(self, messages):
                raise AssertionError("不应直接调用")

        budget = EvaluationModelBudget(max_model_calls=1, max_total_tokens=10)
        client = BudgetedModelClient(Client(), budget)
        request = ModelRequest.from_text_messages([{
            "role": "user", "content": "return json"
        }])
        client.generate_structured(request)
        with self.assertRaises(EvaluationBudgetExceeded):
            client.generate_structured(request)
        snapshot = budget.snapshot()
        self.assertEqual(snapshot.attempted_model_calls, 1)
        self.assertEqual(snapshot.observed_total_tokens, 5)

    def test_runtime_executor_persists_auditable_artifact_bundle(self) -> None:
        suite = EvaluationSuite.load(SUITE_PATH)
        artifacts = ArtifactStore()
        artifacts.put(Artifact.create(
            "plan",
            suite.tasks[0].task_id,
            ImplementationPlan(
                "change page",
                [FileChange("src/App.vue", "<template />", "match reference")],
                [],
            ),
            kind="implementation_plan",
        ))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "artifacts.json"
            RuntimeEvaluationTrialExecutor._write_artifact_bundle(
                output,
                artifacts,
                task=suite.tasks[0],
                variant=EvaluationVariant.LLM_ONCE,
                repetition=0,
                budget=EvaluationBudgetSnapshot(51, 600_000, 3, 1200),
            )
            data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(data["variant"], "llm_once")
        self.assertEqual(data["budget"]["observed_total_tokens"], 1200)
        self.assertEqual(
            data["artifacts"][0]["content"]["changes"][0]["path"],
            "src/App.vue",
        )

    def test_runtime_executor_keeps_tokens_when_protocol_validation_fails(self) -> None:
        class InvalidAnalystClient:
            capabilities = frozenset(ModelCapability)

            def generate_structured(self, request):
                data = suite.tasks[0].acceptance_spec.to_dict()
                data["components"][0]["region_id"] = "missing-region"
                return ModelResponse(
                    data, "fake", "invalid-analyst", ModelUsage(7, 3, 10), 1
                )

            def generate_json(self, messages):
                raise AssertionError("不应直接调用")

        suite = EvaluationSuite.load(SUITE_PATH)
        budget = EvaluationModelBudget(max_model_calls=5, max_total_tokens=100)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executor = RuntimeEvaluationTrialExecutor(
                template_root=ROOT / "visionforge_vue_template",
                runtime_root=root,
                process_runner=BrowserProcessRunner(),
                text_client=InvalidAnalystClient(),
                vision_client=InvalidAnalystClient(),
                budget=budget,
            )
            trial = executor.execute(
                task=suite.tasks[0],
                variant=EvaluationVariant.LLM_ONCE,
                repetition=0,
                reference_image_path=ROOT / "docs" / "multi-agent-architecture.png",
                config=EvaluationConfig("fake", "text", "test"),
            )
            self.assertTrue(Path(trial.artifact_refs["artifact_bundle"]).is_file())
        self.assertEqual(trial.status, "failed")
        self.assertEqual(trial.total_tokens, 10)
        self.assertIn("不存在的布局区域", trial.error)

    def test_fixed_suite_loads_three_tasks_with_runtime_owned_acceptance(self) -> None:
        suite = EvaluationSuite.load(SUITE_PATH)
        self.assertEqual(suite.suite_id, "visionforge-mvp-pages")
        self.assertEqual(len(suite.tasks), 3)
        self.assertEqual(len(suite.content_sha256), 64)
        self.assertEqual(
            {item.task_id for item in suite.tasks},
            {"saas-signup", "analytics-dashboard", "commerce-product"},
        )
        for task in suite.tasks:
            self.assertEqual(task.acceptance_spec.viewport.width, 1440)
            self.assertEqual(task.acceptance_spec.viewport.height, 900)
            self.assertGreaterEqual(len(task.acceptance_spec.interactions), 5)
            self.assertIn("[data-testid=page-shell]", {
                item.target for item in task.acceptance_spec.interactions
            })

    def test_config_fixes_experiment_variables_without_credentials(self) -> None:
        config = EvaluationConfig("openai-compatible", "vision-model", "vf-1.0", 2)
        data = config.to_dict()
        self.assertEqual(data["repetitions"], 2)
        self.assertEqual(data["max_fix_attempts"], 2)
        self.assertEqual(data["browser_engine"], "chromium")
        self.assertEqual(data["temperature"], 0)
        self.assertEqual(data["playwright_version"], "1.62.0")
        self.assertNotIn("api_key", data)
        with self.assertRaises(ValueError):
            EvaluationConfig("provider", "model", "vf-1.0", max_fix_attempts=3)

    def test_evaluator_compares_all_variants_with_one_reference_per_task(self) -> None:
        suite = EvaluationSuite.load(SUITE_PATH)
        renderer = StubRenderer()
        executor = ScriptedExecutor()
        with tempfile.TemporaryDirectory() as temp:
            evaluator = VisionForgeEvaluator(
                suite,
                EvaluationConfig("fake", "fixed-model", "vf-1.0"),
                renderer,
                executor,
                Path(temp) / "runtime",
            )
            report = evaluator.run()
            output = Path(temp) / "report.json"
            report.write(output)
            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(renderer.tasks, [item.task_id for item in suite.tasks])
        self.assertEqual(len(executor.calls), 9)
        self.assertEqual(len(report.trials), 9)
        once = report.variants[EvaluationVariant.LLM_ONCE.value]
        browser = report.variants[EvaluationVariant.LLM_BROWSER.value]
        full = report.variants[EvaluationVariant.LLM_BROWSER_VLM.value]
        self.assertEqual(once["visual_acceptance_rate"], 0)
        self.assertEqual(browser["delivery_success_rate"], 0)
        self.assertEqual(full["delivery_success_rate"], 1)
        self.assertEqual(full["auto_fix_success_rate"], 1)
        self.assertEqual(full["average_fix_rounds"], 1)
        self.assertEqual(persisted["suite"]["content_sha256"], suite.content_sha256)

    def test_executor_error_becomes_failed_trial_and_human_intervention(self) -> None:
        class BrokenExecutor(ScriptedExecutor):
            def execute(self, **kwargs):
                raise RuntimeError("provider unavailable api_key=do-not-persist")

        suite = EvaluationSuite.load(SUITE_PATH)
        with tempfile.TemporaryDirectory() as temp:
            report = VisionForgeEvaluator(
                suite,
                EvaluationConfig("fake", "fixed-model", "vf-1.0"),
                StubRenderer(),
                BrokenExecutor(),
                Path(temp),
            ).run()
        self.assertEqual(len(report.trials), 9)
        self.assertTrue(all(item.status == "failed" for item in report.trials))
        self.assertTrue(all(item.human_interventions == 1 for item in report.trials))
        self.assertTrue(all("do-not-persist" not in item.error for item in report.trials))

    def test_run_adapter_scores_first_and_final_cycle_from_artifacts(self) -> None:
        artifacts = ArtifactStore()
        task_id = "eval-adapter"

        def cycle(round_index: int, score: int, passed: bool) -> VisionForgeCycle:
            build = artifacts.put(Artifact.create(
                f"build-{round_index}", task_id, {"passed": True}, kind="build_result"
            ))
            browser_result = BrowserRunResult.from_runner_payload({
                "schema_version": "1.0",
                "passed": True,
                "url": "http://127.0.0.1:4173/",
                "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
                "assertions": [{
                    "interaction_id": "page-visible",
                    "action": "expect_visible",
                    "target": "[data-testid=page-shell]",
                    "passed": True,
                    "evidence": "目标可见",
                    "error": "",
                    "duration_ms": 1,
                }],
                "console_messages": [],
                "page_errors": [],
                "network_errors": [],
                "duration_ms": 10,
            }, f"artifact://screenshot-{round_index}")
            browser = artifacts.put(Artifact.create(
                f"browser-{round_index}", task_id, browser_result.to_dict(),
                kind=BROWSER_RUN,
            ))
            review = artifacts.put(Artifact.create(
                f"review-{round_index}", task_id,
                {
                    "schema_version": "1.0",
                    "passed": passed,
                    "score": score,
                    "summary": "通过" if passed else "间距偏差",
                    "issues": [] if passed else [{
                        "severity": "P2", "region": "hero", "category": "spacing",
                        "expected": "间距一致", "actual": "间距偏小",
                        "evidence": "截图可见", "suggestion": "增加间距",
                    }],
                },
                kind=VISUAL_REVIEW,
            ))
            return VisionForgeCycle(
                round_index, "artifact://plan", "artifact://integration", build,
                f"artifact://screenshot-{round_index}", browser, review,
                f"artifact://gate-{round_index}", passed,
            )

        first = cycle(0, 70, False)
        final = cycle(1, 92, True)
        run_ref = artifacts.put(Artifact.create(
            "run", task_id, {"total_tokens": 321}, kind=RUN
        ))
        result = VisionForgeRunResult(
            task_id, "artifact://reference", "artifact://ui", "artifact://plan",
            "artifact://integration", final.build_artifact_ref,
            final.actual_screenshot_artifact_ref, final.browser_run_artifact_ref,
            final.visual_review_artifact_ref, run_ref, ("src/App.vue",), True, 92,
            False, "completed", 1, final.quality_gate_artifact_ref, (first, final),
        )
        trial = EvaluationTrialResult.from_visionforge_run(
            task_id=task_id,
            variant=EvaluationVariant.LLM_BROWSER_VLM,
            repetition=0,
            result=result,
            artifacts=artifacts,
            minimum_visual_score=85,
            duration_ms=2400,
        )
        self.assertFalse(trial.first_passed)
        self.assertTrue(trial.delivery_passed)
        self.assertTrue(trial.auto_fix_succeeded)
        self.assertEqual(trial.total_tokens, 321)


@unittest.skipUnless(
    os.environ.get("VISIONFORGE_E2E") == "1",
    "设置 VISIONFORGE_E2E=1 后渲染固定评测参考图",
)
class ReferenceRendererE2ETests(unittest.TestCase):
    def test_all_reference_pages_render_to_png(self) -> None:
        node = os.environ.get("VISIONFORGE_NODE", "")
        browser = os.environ.get("VISIONFORGE_BROWSER_EXECUTABLE", "")
        if not node or not Path(node).is_file() or not browser or not Path(browser).is_file():
            self.fail("真实参考图渲染需要 VISIONFORGE_NODE 和浏览器路径")
        runner = BrowserProcessRunner(
            executable_overrides={"node": node},
            environment={"VISIONFORGE_BROWSER_EXECUTABLE": browser},
        )
        renderer = ReferenceImageRenderer(runner, RENDERER_PATH)
        suite = EvaluationSuite.load(SUITE_PATH)
        with tempfile.TemporaryDirectory() as temp:
            outputs = [
                renderer.render(task, Path(temp) / f"{task.task_id}.png")
                for task in suite.tasks
            ]
            self.assertTrue(all(item.stat().st_size > 20_000 for item in outputs))


if __name__ == "__main__":
    unittest.main()
