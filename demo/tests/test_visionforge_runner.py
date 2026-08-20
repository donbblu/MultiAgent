from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from coding_workflow.artifacts import Artifact, ArtifactDraft, ArtifactStore
from coding_workflow.harness import (
    SQLiteScenarioRunStore,
    TaskRunRequest,
    TaskSpec,
)
from coding_workflow.integration import IntegrationError, PatchIntegrator
from coding_workflow.model import (
    ImageContentPart,
    ModelCapability,
    ModelCapabilityError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OpenAICompatibleClient,
)
from coding_workflow.workspace import ProjectWorkspace
from coding_workflow.runtime_sqlite import RuntimeRecoveryError
from coding_workflow.models import TaskContext
from coding_workflow.visionforge.dag import UIAnalystWorker
from coding_workflow.visionforge import (
    ACTUAL_SCREENSHOT,
    BROWSER_RUN,
    BrowserAssertion,
    BrowserProcessRunner,
    BrowserRunResult,
    BrowserTestArtifacts,
    ImageAssetStore,
    PlaywrightBrowserTester,
    RequirementAnalyst,
    RUN,
    VISIONFORGE_PLUGIN_VERSION,
    WEB_VISUAL_REFERENCE,
    UISpec,
    VisionForgeDeveloper,
    VisionForgeScenarioRunner,
    VisionForgeRunner,
    VisualReviewer,
    create_visionforge_plugin_registry,
)


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"
ALL_CAPABILITIES = frozenset(ModelCapability)


def minimal_png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def ui_spec_data() -> dict[str, object]:
    return json.loads(
        (TEMPLATE / "visionforge.ui-spec.json").read_text(encoding="utf-8")
    )


def visual_review_data(*, passed: bool = True) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "passed": passed,
        "score": 92 if passed else 72,
        "summary": "页面与参考结构一致" if passed else "主区域间距偏差明显",
        "issues": [] if passed else [{
            "severity": "P2",
            "region": "hero",
            "category": "spacing",
            "expected": "主区域上下留白均衡",
            "actual": "顶部留白明显不足",
            "evidence": "实际截图中标题距离顶部过近",
            "suggestion": "增加 hero 顶部 padding",
        }],
    }


class QueueModelClient:
    def __init__(
        self,
        *responses: dict[str, object],
        capabilities: frozenset[ModelCapability] = ALL_CAPABILITIES,
        model: str = "fake-model",
    ) -> None:
        self._responses = list(responses)
        self._capabilities = capabilities
        self.model = model
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> frozenset[ModelCapability]:
        return self._capabilities

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("Fake Model 没有剩余响应")
        return ModelResponse(
            self._responses.pop(0),
            "fake",
            self.model,
            ModelUsage(10, 5, 15),
            7,
        )

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        raise AssertionError("VisionForge 角色必须使用 generate_structured")


class StubBrowserTester:
    def __init__(
        self,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
    ) -> None:
        self.artifacts = artifacts
        self.image_assets = image_assets

    def run(
        self,
        *,
        task_id: str,
        ui_spec: UISpec,
        artifact_prefix: str = "browser",
        lifecycle: object | None = None,
    ) -> BrowserTestArtifacts:
        build_ref = self.artifacts.put(Artifact.create(
            f"{artifact_prefix}-build",
            task_id,
            {"passed": True, "exit_code": 0},
            kind="build_result",
        ))
        screenshot_ref, _ = self.image_assets.create_artifact(
            self.artifacts,
            name=f"{artifact_prefix}-actual-screenshot",
            task_id=task_id,
            data=minimal_png(ui_spec.viewport.width, ui_spec.viewport.height),
            kind=ACTUAL_SCREENSHOT,
        )
        result = BrowserRunResult(
            "1.0",
            True,
            "http://127.0.0.1:4173/",
            ui_spec.viewport,
            tuple(
                BrowserAssertion(
                    item.interaction_id,
                    item.action,
                    item.target,
                    True,
                    "stub assertion passed",
                    "",
                    1,
                )
                for item in ui_spec.interactions
            ),
            (),
            (),
            (),
            screenshot_ref,
            5,
        )
        browser_ref = self.artifacts.put(Artifact.create(
            f"{artifact_prefix}-run",
            task_id,
            result.to_dict(),
            kind=BROWSER_RUN,
            metadata={"screenshot_artifact_ref": screenshot_ref, "passed": True},
        ))
        return BrowserTestArtifacts(build_ref, screenshot_ref, browser_ref, result)


class VisionForgeRunnerTests(unittest.TestCase):
    def test_dag_worker_returns_draft_without_mutating_shared_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = ArtifactStore()
            images = ImageAssetStore(Path(temp) / "assets")
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-draft",
                data=minimal_png(1440, 900),
            )
            model = QueueModelClient(ui_spec_data())
            worker = UIAnalystWorker(
                RequirementAnalyst(model, artifacts, images)
            )
            task = TaskContext(
                "vf-draft", "分析页面", ["生成 UI Spec"],
                user_request="分析页面",
            )
            spec = TaskSpec(
                "ui-analysis", "分析", "生成 UI Spec", "ui_analyst",
                acceptance_criteria=("生成 UI Spec",),
                input_artifacts=("reference_image",),
                output_artifacts=("ui_spec",),
            )

            result = worker.run_task(TaskRunRequest(
                spec, task, None,
                {"reference_image": artifacts.get(reference_ref)}, 1,
            ))

            self.assertEqual(len(artifacts.snapshot()), 1)
            self.assertIsInstance(result.artifacts["ui_spec"], ArtifactDraft)

    def _components(
        self,
        root: Path,
        *,
        developer_path: str = "src/App.vue",
        visual_passed: bool = True,
    ) -> tuple[
        VisionForgeRunner,
        ArtifactStore,
        ImageAssetStore,
        QueueModelClient,
        QueueModelClient,
        QueueModelClient,
    ]:
        (root / "src").mkdir(parents=True)
        (root / "src" / "App.vue").write_text(
            "<template><main>old</main></template>", encoding="utf-8"
        )
        workspace = ProjectWorkspace(root)
        artifacts = ArtifactStore()
        images = ImageAssetStore(root.parent / "assets")
        analyst_model = QueueModelClient(ui_spec_data(), model="fake-vlm")
        developer_model = QueueModelClient({
            "summary": "实现固定测试页面",
            "changes": [{
                "path": developer_path,
                "content": "<template><main data-testid=\"page-shell\">new</main></template>",
                "reason": "实现 UI Spec",
            }],
            "suggested_checks": [],
        }, model="fake-llm")
        reviewer_model = QueueModelClient(
            visual_review_data(passed=visual_passed), model="fake-vlm"
        )
        runner = VisionForgeRunner(
            artifacts=artifacts,
            workspace=workspace,
            integrator=PatchIntegrator(workspace, ("src/**", "public/**")),
            analyst=RequirementAnalyst(analyst_model, artifacts, images),
            developer=VisionForgeDeveloper(developer_model, artifacts),
            browser_tester=StubBrowserTester(artifacts, images),
            visual_reviewer=VisualReviewer(reviewer_model, artifacts, images),
        )
        return (
            runner, artifacts, images,
            analyst_model, developer_model, reviewer_model,
        )

    def test_single_pass_chain_applies_patch_and_links_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            (
                runner, artifacts, images,
                analyst_model, developer_model, reviewer_model,
            ) = self._components(root)
            reference_ref, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-runner",
                data=minimal_png(1440, 900),
            )
            result = runner.run(
                task_id="vf-runner",
                requirement="实现一个邮箱注册页面",
                reference_image_artifact_ref=reference_ref,
            )

            self.assertEqual(result.changed_files, ("src/App.vue",))
            self.assertIn("data-testid", (root / "src" / "App.vue").read_text())
            self.assertTrue(result.browser_passed)
            self.assertFalse(result.needs_fix)
            run = artifacts.get(result.run_artifact_ref)
            self.assertEqual(run.kind, RUN)
            self.assertEqual(
                run.content["artifact_chain"]["visual_review"],
                result.visual_review_artifact_ref,
            )
            self.assertEqual(run.content["total_tokens"], 45)
            self.assertEqual(
                analyst_model.requests[0].required_capabilities,
                frozenset({
                    ModelCapability.TEXT,
                    ModelCapability.VISION,
                    ModelCapability.STRUCTURED_OUTPUT,
                }),
            )
            self.assertIn(
                ModelCapability.TOOL_CALLING,
                developer_model.requests[0].required_capabilities,
            )
            reviewer_images = [
                part
                for message in reviewer_model.requests[0].messages
                for part in message.content
                if isinstance(part, ImageContentPart)
            ]
            self.assertEqual(len(reviewer_images), 2)
            self.assertEqual(
                artifacts.get(result.visual_review_artifact_ref).metadata[
                    "actual_screenshot_artifact_ref"
                ],
                result.actual_screenshot_artifact_ref,
            )

    def test_dag_scene_runs_full_single_pass_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, *_ = self._components(root)
            dag = VisionForgeScenarioRunner(
                artifacts=artifacts,
                workspace=runner.workspace,
                integrator=runner.integrator,
                analyst=runner.analyst,
                developer=runner.developer,
                browser_tester=runner.browser_tester,
                visual_reviewer=runner.visual_reviewer,
                runtime_path=Path(temp) / "scenario.sqlite3",
            )
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-dag",
                data=minimal_png(1440, 900),
            )

            result = dag.run(
                task_id="vf-dag",
                requirement="实现一个邮箱注册页面",
                reference_image_artifact_ref=reference_ref,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.fix_attempts, 0)
            self.assertEqual(result.changed_files, ("src/App.vue",))
            self.assertEqual(
                artifacts.get(result.run_artifact_ref).content["engine"],
                "scenario_dag",
            )

    def test_plugin_scenario_persists_visionforge_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, *_ = self._components(root)
            runtime_path = Path(temp) / "scenario.sqlite3"
            registry = create_visionforge_plugin_registry()
            scenario = VisionForgeScenarioRunner(
                artifacts=artifacts,
                workspace=runner.workspace,
                integrator=runner.integrator,
                analyst=runner.analyst,
                developer=runner.developer,
                browser_tester=runner.browser_tester,
                visual_reviewer=runner.visual_reviewer,
                runtime_path=runtime_path,
                scenario_registration=registry.resolve_reference(
                    WEB_VISUAL_REFERENCE
                ),
            )
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-plugin",
                data=minimal_png(1440, 900),
            )

            result = scenario.run(
                task_id="vf-plugin",
                requirement="实现插件场景页面",
                reference_image_artifact_ref=reference_ref,
                run_id="visionforge-plugin-run",
            )
            state = SQLiteScenarioRunStore(runtime_path).load(
                "visionforge-plugin-run"
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(state.plugin_id, "visionforge")
            self.assertEqual(state.plugin_version, VISIONFORGE_PLUGIN_VERSION)
            self.assertEqual(state.scenario, "web_visual")

    def test_scenario_resumes_completed_round_without_recalling_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, analyst, developer, reviewer = (
                self._components(root)
            )
            scenario = VisionForgeScenarioRunner(
                artifacts=artifacts,
                workspace=runner.workspace,
                integrator=runner.integrator,
                analyst=runner.analyst,
                developer=runner.developer,
                browser_tester=runner.browser_tester,
                visual_reviewer=runner.visual_reviewer,
                runtime_path=Path(temp) / "scenario.sqlite3",
            )
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-resume",
                data=minimal_png(1440, 900),
            )
            first = scenario.run(
                task_id="vf-resume", requirement="实现注册页面",
                reference_image_artifact_ref=reference_ref,
            )
            calls = (
                len(analyst.requests), len(developer.requests),
                len(reviewer.requests),
            )

            restored = scenario.run(
                task_id="vf-resume", requirement="实现注册页面",
                reference_image_artifact_ref=reference_ref,
            )

            self.assertEqual(restored.run_artifact_ref, first.run_artifact_ref)
            self.assertEqual(calls, (
                len(analyst.requests), len(developer.requests),
                len(reviewer.requests),
            ))

    def test_scenario_resumes_after_graph_completed_before_decision(self) -> None:
        class SimulatedCrash(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, analyst, developer, reviewer = (
                self._components(root)
            )
            crashed = False

            def hook(event, state):
                nonlocal crashed
                if event == "round_graph_completed" and not crashed:
                    crashed = True
                    raise SimulatedCrash("模拟进程退出")

            scenario = VisionForgeScenarioRunner(
                artifacts=artifacts,
                workspace=runner.workspace,
                integrator=runner.integrator,
                analyst=runner.analyst,
                developer=runner.developer,
                browser_tester=runner.browser_tester,
                visual_reviewer=runner.visual_reviewer,
                runtime_path=Path(temp) / "scenario.sqlite3",
                checkpoint_hook=hook,
            )
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-crash",
                data=minimal_png(1440, 900),
            )
            with self.assertRaises(SimulatedCrash):
                scenario.run(
                    task_id="vf-crash", requirement="实现注册页面",
                    reference_image_artifact_ref=reference_ref,
                )
            calls = (
                len(analyst.requests), len(developer.requests),
                len(reviewer.requests),
            )
            scenario.checkpoint_hook = None

            result = scenario.run(
                task_id="vf-crash", requirement="实现注册页面",
                reference_image_artifact_ref=reference_ref,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(calls, (
                len(analyst.requests), len(developer.requests),
                len(reviewer.requests),
            ))

    def test_scenario_recovery_rejects_workspace_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, *_ = self._components(root)
            scenario = VisionForgeScenarioRunner(
                artifacts=artifacts,
                workspace=runner.workspace,
                integrator=runner.integrator,
                analyst=runner.analyst,
                developer=runner.developer,
                browser_tester=runner.browser_tester,
                visual_reviewer=runner.visual_reviewer,
                runtime_path=Path(temp) / "scenario.sqlite3",
            )
            reference_ref, _ = images.create_artifact(
                artifacts, name="reference-image", task_id="vf-drift",
                data=minimal_png(1440, 900),
            )
            scenario.run(
                task_id="vf-drift", requirement="实现注册页面",
                reference_image_artifact_ref=reference_ref,
            )
            (root / "src" / "App.vue").write_text(
                "<template>external change</template>", encoding="utf-8"
            )

            with self.assertRaises(RuntimeRecoveryError):
                scenario.run(
                    task_id="vf-drift", requirement="实现注册页面",
                    reference_image_artifact_ref=reference_ref,
                )

    def test_integrator_rejects_developer_patch_outside_allowed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, *_ = self._components(
                root, developer_path="package.json"
            )
            reference_ref, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-unsafe",
                data=minimal_png(),
            )
            with self.assertRaisesRegex(IntegrationError, "允许范围"):
                runner.run(
                    task_id="vf-unsafe",
                    requirement="修改页面",
                    reference_image_artifact_ref=reference_ref,
                )
            self.assertFalse((root / "package.json").exists())

    def test_visual_p2_requests_fix_but_does_not_run_fixer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            runner, artifacts, images, *_ = self._components(
                root, visual_passed=False
            )
            reference_ref, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-needs-fix",
                data=minimal_png(),
            )
            result = runner.run(
                task_id="vf-needs-fix",
                requirement="修改页面",
                reference_image_artifact_ref=reference_ref,
            )
            self.assertTrue(result.needs_fix)
            self.assertEqual(result.changed_files, ("src/App.vue",))
            self.assertEqual(
                artifacts.get(result.run_artifact_ref).content["blocking_issue_count"],
                1,
            )

    def test_agent_checks_capabilities_before_fake_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactStore()
            images = ImageAssetStore(root / "assets")
            reference_ref, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-capability",
                data=minimal_png(),
            )
            model = QueueModelClient(
                ui_spec_data(),
                capabilities=frozenset({
                    ModelCapability.TEXT,
                    ModelCapability.STRUCTURED_OUTPUT,
                }),
            )
            analyst = RequirementAnalyst(model, artifacts, images)
            with self.assertRaisesRegex(ModelCapabilityError, "vision"):
                analyst.analyze(
                    task_id="vf-capability",
                    requirement="分析页面",
                    reference_image_artifact_ref=reference_ref,
                )
            self.assertFalse(model.requests)

    def test_openai_compatible_uses_supplied_json_schema(self) -> None:
        request = ModelRequest.from_text_messages(
            [{"role": "user", "content": "生成 JSON"}],
            response_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
        response_format = OpenAICompatibleClient._response_format(request)
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(
            response_format["json_schema"]["name"], "structured_response"
        )
        self.assertTrue(response_format["json_schema"]["strict"])


@unittest.skipUnless(
    os.environ.get("VISIONFORGE_E2E") == "1",
    "设置 VISIONFORGE_E2E=1 后运行真实 VisionForge 纵向链路测试",
)
class VisionForgeRunnerIntegrationTests(unittest.TestCase):
    def test_fake_models_drive_real_vue_browser_and_visual_review_chain(self) -> None:
        node = Path(os.environ.get("VISIONFORGE_NODE", ""))
        pnpm = Path(os.environ.get("VISIONFORGE_PNPM", ""))
        browser = Path(os.environ.get("VISIONFORGE_BROWSER_EXECUTABLE", ""))
        if not node.is_file() or not pnpm.is_file() or not browser.is_file():
            self.fail(
                "真实纵向链路测试需要 VISIONFORGE_NODE、VISIONFORGE_PNPM "
                "和 VISIONFORGE_BROWSER_EXECUTABLE"
            )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            shutil.copytree(
                TEMPLATE,
                project,
                symlinks=True,
            )
            workspace = ProjectWorkspace(project)
            artifacts = ArtifactStore()
            images = ImageAssetStore(root / "assets")
            reference_ref, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-real-chain",
                data=minimal_png(1440, 900),
            )
            analyst_model = QueueModelClient(ui_spec_data(), model="fake-vlm")
            developer_model = QueueModelClient({
                "summary": "根据 UI Spec 生成固定页面",
                "changes": [{
                    "path": "src/App.vue",
                    "content": (TEMPLATE / "src" / "App.vue").read_text(encoding="utf-8"),
                    "reason": "生成可运行页面并保留稳定 DOM hooks",
                }],
                "suggested_checks": [],
            }, model="fake-llm")
            reviewer_model = QueueModelClient(
                visual_review_data(), model="fake-vlm"
            )
            process_runner = BrowserProcessRunner(
                executable_overrides={"node": str(node), "pnpm": str(pnpm)},
                environment={
                    "PATH": f"{node.parent}:/usr/bin:/bin",
                    "VISIONFORGE_BROWSER_EXECUTABLE": str(browser),
                },
            )
            result = VisionForgeRunner(
                artifacts=artifacts,
                workspace=workspace,
                integrator=PatchIntegrator(workspace, ("src/**", "public/**")),
                analyst=RequirementAnalyst(analyst_model, artifacts, images),
                developer=VisionForgeDeveloper(developer_model, artifacts),
                browser_tester=PlaywrightBrowserTester(
                    project,
                    process_runner,
                    artifacts,
                    images,
                    root / "runtime",
                ),
                visual_reviewer=VisualReviewer(reviewer_model, artifacts, images),
            ).run(
                task_id="vf-real-chain",
                requirement="实现邮箱注册落地页",
                reference_image_artifact_ref=reference_ref,
            )
            self.assertTrue(result.browser_passed)
            self.assertFalse(result.needs_fix)
            self.assertEqual(
                artifacts.get(result.actual_screenshot_artifact_ref).kind,
                "actual_screenshot",
            )
            self.assertEqual(
                artifacts.get(result.visual_review_artifact_ref).kind,
                "visual_review",
            )
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", 4173)), 0)


if __name__ == "__main__":
    unittest.main()
