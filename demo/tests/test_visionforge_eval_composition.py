from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import visionforge_eval_run as eval_cli
from coding_workflow.local_execution import LocalExecutionError, SANDBOX_REQUIRED
from coding_workflow.model import ModelCapability
from coding_workflow.visionforge import (
    BrowserProcessRunner,
    EvaluationConfig,
    EvaluationModelBudget,
    EvaluationSuite,
    EvaluationVariant,
    ReferenceImageRenderer,
    RuntimeEvaluationTrialExecutor,
    VisionForgeLocalExecutionApprover,
    VisionForgeWebError,
    VisionForgeWebRuntime,
)


ROOT = Path(__file__).parents[1]
SUITE_PATH = ROOT / "visionforge_eval" / "v1" / "suite.json"
RENDERER_PATH = ROOT / "visionforge_eval" / "render-reference.mjs"
TEMPLATE_PATH = ROOT / "visionforge_vue_template"


class _NoModelClient:
    capabilities = frozenset(ModelCapability)

    def generate_structured(self, request):
        del request
        raise AssertionError("composition regression must not call a model")


@dataclass(frozen=True)
class _ModelConfig:
    provider: str
    model: str
    base_url: str
    max_tokens: int
    include_max_tokens: bool = False


class _Report:
    def __init__(self) -> None:
        self.output: Path | None = None

    def write(self, output: Path) -> None:
        self.output = output


class VisionForgeEvaluationCompositionTests(unittest.TestCase):
    def test_trial_uses_fresh_workspace_bound_runner_and_approvers(self) -> None:
        suite = EvaluationSuite.load(SUITE_PATH)
        captured: dict[str, object] = {}
        created_roots: list[Path] = []
        approvers: list[VisionForgeLocalExecutionApprover] = []

        def runner_factory(project_root: Path) -> BrowserProcessRunner:
            created_roots.append(project_root)
            return BrowserProcessRunner(
                executable_overrides={
                    "node": "/usr/bin/node",
                    "pnpm": "/usr/bin/pnpm",
                },
                workspace_root=project_root,
            )

        def approver_factory() -> VisionForgeLocalExecutionApprover:
            approver = VisionForgeLocalExecutionApprover(True)
            approvers.append(approver)
            return approver

        class CapturingScenarioRunner:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def run(self, **kwargs):
                del kwargs
                raise RuntimeError("captured before any model or process call")

        budget = EvaluationModelBudget(max_model_calls=5, max_total_tokens=100)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.visionforge.evaluation_runtime."
            "VisionForgeScenarioRunner",
            CapturingScenarioRunner,
        ), mock.patch(
            "coding_workflow.local_execution._spawn",
            side_effect=AssertionError("must not spawn"),
        ), mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("must not use network"),
        ):
            runtime_root = Path(temp)
            executor = RuntimeEvaluationTrialExecutor(
                template_root=TEMPLATE_PATH,
                runtime_root=runtime_root,
                runner_factory=runner_factory,
                text_client=_NoModelClient(),
                vision_client=_NoModelClient(),
                budget=budget,
                approver_factory=approver_factory,
            )
            trial = executor.execute(
                task=suite.tasks[0],
                variant=EvaluationVariant.LLM_ONCE,
                repetition=0,
                reference_image_path=(
                    ROOT / "docs" / "multi-agent-architecture.png"
                ),
                config=EvaluationConfig("fake", "text", "test"),
            )

        tester = captured["browser_tester"]
        project_root = Path(trial.artifact_refs["project_root"]).resolve()
        self.assertEqual(created_roots, [project_root])
        self.assertEqual(tester.process_runner.workspace_root, project_root)
        self.assertEqual(tester.project.project_root, project_root)
        self.assertNotIn(
            "VISIONFORGE_BROWSER_EXECUTABLE",
            tester.process_runner._environment(),
        )
        first = tester.approver_factory()
        second = tester.approver_factory()
        self.assertIs(type(first), VisionForgeLocalExecutionApprover)
        self.assertIs(type(second), VisionForgeLocalExecutionApprover)
        self.assertIsNot(first, second)
        self.assertEqual(approvers, [first, second])
        self.assertEqual(budget.snapshot().attempted_model_calls, 0)
        self.assertEqual(trial.status, "failed")

    def test_real_cli_composes_factories_without_environment_injection(self) -> None:
        captured: dict[str, object] = {}
        approved_values: list[bool] = []
        report = _Report()
        suite = SimpleNamespace(
            suite_id="mock-suite",
            version="1.0",
            content_sha256="a" * 64,
            tasks=(object(),),
        )

        class FakeApprover:
            def __init__(self, approved: bool) -> None:
                approved_values.append(approved)

        def capture_executor(**kwargs):
            captured["executor"] = kwargs
            return object()

        def capture_renderer(process_runner, renderer_path):
            captured["renderer_runner"] = process_runner
            captured["renderer_path"] = renderer_path
            return object()

        class FakeEvaluator:
            def __init__(self, *args) -> None:
                captured["evaluator"] = args

            def run(self):
                return report

        text_config = _ModelConfig(
            "fake-text", "text-model", "https://text.invalid", 1000
        )
        vision_config = _ModelConfig(
            "fake-vision", "vision-model", "https://vision.invalid", 1000
        )
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            eval_cli, "ROOT", Path(temp)
        ), mock.patch.object(
            eval_cli, "load_env_file"
        ), mock.patch.object(
            eval_cli.EvaluationSuite, "load", return_value=suite
        ), mock.patch.object(
            eval_cli.ModelClientFactory,
            "config_from_env",
            return_value=text_config,
        ), mock.patch.object(
            eval_cli.ModelClientFactory,
            "vision_config_from_env",
            return_value=vision_config,
        ), mock.patch.object(
            eval_cli, "_check_endpoint"
        ) as endpoint, mock.patch.object(
            eval_cli,
            "_required_file",
            side_effect=lambda name: Path(
                "/usr/bin/node" if name == "VISIONFORGE_NODE" else "/usr/bin/pnpm"
            ),
        ), mock.patch.object(
            eval_cli.ModelClientFactory, "create", side_effect=(object(), object())
        ) as create_client, mock.patch.object(
            eval_cli,
            "RuntimeEvaluationTrialExecutor",
            side_effect=capture_executor,
        ), mock.patch.object(
            eval_cli, "ReferenceImageRenderer", side_effect=capture_renderer
        ), mock.patch.object(
            eval_cli, "VisionForgeEvaluator", FakeEvaluator
        ), mock.patch.object(
            eval_cli, "VisionForgeLocalExecutionApprover", FakeApprover
        ), mock.patch(
            "coding_workflow.local_execution._spawn",
            side_effect=AssertionError("must not spawn"),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                result = eval_cli.main([
                    "--confirm-real-calls",
                    "--trusted-local-execution",
                    "--run-id",
                    "composition-mock",
                ])
            executor_kwargs = captured["executor"]
            trial_root = Path(temp) / "trial-project"
            trial_root.mkdir()
            first_runner = executor_kwargs["runner_factory"](trial_root)
            second_runner = executor_kwargs["runner_factory"](trial_root)
            first_approver = executor_kwargs["approver_factory"]()
            second_approver = executor_kwargs["approver_factory"]()

        self.assertEqual(result, 0)
        self.assertIn('"will_execute_local_commands": true', output.getvalue())
        self.assertEqual(endpoint.call_count, 2)
        self.assertEqual(create_client.call_count, 2)
        self.assertNotIn("process_runner", executor_kwargs)
        self.assertEqual(first_runner.workspace_root, trial_root.resolve())
        self.assertEqual(second_runner.workspace_root, trial_root.resolve())
        self.assertIsNot(first_runner, second_runner)
        self.assertNotIn(
            "VISIONFORGE_BROWSER_EXECUTABLE", first_runner._environment()
        )
        self.assertIsNot(first_approver, second_approver)
        self.assertEqual(approved_values, [True, True])
        renderer_runner = captured["renderer_runner"]
        self.assertEqual(
            renderer_runner.workspace_root,
            RENDERER_PATH.parent.resolve(),
        )
        self.assertNotIn(
            "VISIONFORGE_BROWSER_EXECUTABLE", renderer_runner._environment()
        )

    def test_real_cli_requires_local_authorization_before_external_preflight(self) -> None:
        text_config = _ModelConfig(
            "fake-text", "text-model", "https://text.invalid", 1000
        )
        vision_config = _ModelConfig(
            "fake-vision", "vision-model", "https://vision.invalid", 1000
        )
        with mock.patch.object(
            eval_cli, "load_env_file"
        ) as load_env, mock.patch.object(
            eval_cli.EvaluationSuite,
            "load",
            return_value=SimpleNamespace(
                suite_id="mock-suite",
                version="1.0",
                content_sha256="a" * 64,
                tasks=(object(),),
            ),
        ) as load_suite, mock.patch.object(
            eval_cli.ModelClientFactory,
            "config_from_env",
            return_value=text_config,
        ) as text_config_from_env, mock.patch.object(
            eval_cli.ModelClientFactory,
            "vision_config_from_env",
            return_value=vision_config,
        ) as vision_config_from_env, mock.patch.object(
            eval_cli, "_check_endpoint"
        ) as endpoint, mock.patch.object(
            eval_cli.ModelClientFactory, "create"
        ) as create_client, mock.patch.object(
            eval_cli, "_required_file"
        ) as required_file:
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    RuntimeError, "--trusted-local-execution"
                ):
                    eval_cli.main(["--confirm-real-calls"])

        load_env.assert_not_called()
        load_suite.assert_not_called()
        text_config_from_env.assert_not_called()
        vision_config_from_env.assert_not_called()
        endpoint.assert_not_called()
        create_client.assert_not_called()
        required_file.assert_not_called()

    def test_budget_only_mode_remains_available_without_local_authority(self) -> None:
        suite = SimpleNamespace(
            suite_id="mock-suite",
            version="1.0",
            content_sha256="a" * 64,
            tasks=(object(),),
        )
        text_config = _ModelConfig(
            "fake-text", "text-model", "https://text.invalid", 1000
        )
        vision_config = _ModelConfig(
            "fake-vision", "vision-model", "https://vision.invalid", 1000
        )
        with mock.patch.object(
            eval_cli, "load_env_file"
        ), mock.patch.object(
            eval_cli.EvaluationSuite, "load", return_value=suite
        ), mock.patch.object(
            eval_cli.ModelClientFactory,
            "config_from_env",
            return_value=text_config,
        ), mock.patch.object(
            eval_cli.ModelClientFactory,
            "vision_config_from_env",
            return_value=vision_config,
        ), mock.patch.object(
            eval_cli, "_check_endpoint"
        ) as endpoint, mock.patch.object(
            eval_cli.ModelClientFactory, "create"
        ) as create_client, mock.patch.object(
            eval_cli, "_required_file"
        ) as required_file, mock.patch(
            "coding_workflow.local_execution._spawn",
            side_effect=AssertionError("must not spawn"),
        ) as spawn:
            default_output = io.StringIO()
            with redirect_stdout(default_output):
                result = eval_cli.main([])
            approved_output = io.StringIO()
            with redirect_stdout(approved_output):
                approved_result = eval_cli.main([
                    "--trusted-local-execution",
                ])

        self.assertEqual(result, 0)
        self.assertEqual(approved_result, 0)
        self.assertIn(
            '"will_call_external_models": false',
            default_output.getvalue(),
        )
        self.assertIn(
            '"will_execute_local_commands": false',
            default_output.getvalue(),
        )
        self.assertIn(
            '"local_execution_approved": false',
            default_output.getvalue(),
        )
        self.assertIn(
            '"will_execute_local_commands": false',
            approved_output.getvalue(),
        )
        self.assertIn(
            '"local_execution_approved": true',
            approved_output.getvalue(),
        )
        endpoint.assert_not_called()
        create_client.assert_not_called()
        required_file.assert_not_called()
        spawn.assert_not_called()

    def test_web_real_executor_denies_before_plugin_model_or_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = VisionForgeWebRuntime(
                Path(temp) / "runtime",
                TEMPLATE_PATH,
                executor=lambda *args: None,
            )
            task_id = "VF-denied"
            runtime._tasks[task_id] = {
                "_trusted_local_execution": False,
            }
            with mock.patch.object(
                runtime,
                "resolve_scenario",
                side_effect=AssertionError("must not resolve plugin"),
            ) as resolve_scenario, mock.patch(
                "coding_workflow.visionforge.web_runtime.load_env_file",
                side_effect=AssertionError("must not load env"),
            ) as load_env, mock.patch(
                "coding_workflow.visionforge.web_runtime."
                "ModelClientFactory.create",
                side_effect=AssertionError("must not create model"),
            ) as create_model, mock.patch(
                "coding_workflow.visionforge.web_runtime.BrowserProcessRunner",
                side_effect=AssertionError("must not create browser runner"),
            ) as create_runner, mock.patch(
                "coding_workflow.visionforge.web_runtime.ProjectWorkspace",
                side_effect=AssertionError("must not create workspace"),
            ) as create_workspace, mock.patch(
                "coding_workflow.local_execution._spawn",
                side_effect=AssertionError("must not spawn"),
            ) as spawn:
                with self.assertRaisesRegex(
                    VisionForgeWebError,
                    "trusted_local_execution",
                ):
                    runtime._execute_real(
                        task_id,
                        "实现页面",
                        "artifact://reference",
                        Path(temp) / "task",
                        Path(temp) / "project",
                        mock.Mock(),
                        mock.Mock(),
                    )

        resolve_scenario.assert_not_called()
        load_env.assert_not_called()
        create_model.assert_not_called()
        create_runner.assert_not_called()
        create_workspace.assert_not_called()
        spawn.assert_not_called()

    def test_reference_renderer_remains_fail_closed_without_spawn(self) -> None:
        suite = EvaluationSuite.load(SUITE_PATH)
        runner = BrowserProcessRunner(
            executable_overrides={"node": "/usr/bin/node"},
            workspace_root=RENDERER_PATH.parent,
        )
        renderer = ReferenceImageRenderer(runner, RENDERER_PATH)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            side_effect=AssertionError("unregistered renderer must not spawn"),
        ) as spawn:
            with self.assertRaises(LocalExecutionError) as raised:
                renderer.render(suite.tasks[0], Path(temp) / "reference.png")

        self.assertEqual(raised.exception.code, SANDBOX_REQUIRED)
        spawn.assert_not_called()

    def test_approver_factory_rejects_non_bool_authority(self) -> None:
        with self.assertRaises(TypeError):
            eval_cli._approver_factory(1)


if __name__ == "__main__":
    unittest.main()
