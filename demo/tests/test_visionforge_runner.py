from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from coding_workflow.artifacts import Artifact, ArtifactStore
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
from coding_workflow.visionforge import (
    BrowserAssertion,
    BrowserProcessRunner,
    BrowserRunResult,
    BrowserTestArtifacts,
    ImageAssetStore,
    PlaywrightBrowserTester,
    RequirementAnalyst,
    UISpec,
    VisionForgeDeveloper,
    VisionForgeRunner,
    VisualReviewer,
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
            kind="actual_screenshot",
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
            kind="browser_run",
            metadata={"screenshot_artifact_ref": screenshot_ref, "passed": True},
        ))
        return BrowserTestArtifacts(build_ref, screenshot_ref, browser_ref, result)


class VisionForgeRunnerTests(unittest.TestCase):
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
            self.assertEqual(run.kind, "visionforge_run")
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
