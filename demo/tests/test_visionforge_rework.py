from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from coding_workflow.artifacts import (
    Artifact,
    ArtifactStore,
    ArtifactValidationState,
)
from coding_workflow.integration import PatchIntegrator
from coding_workflow.model import (
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ModelUsage,
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
    VisionForgeCheckpointStore,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisionForgeFeedbackPolicy,
    VisionForgeRecoveryError,
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


def review_data(
    *,
    passed: bool,
    score: float,
    severity: str | None = None,
) -> dict[str, object]:
    issues: list[dict[str, str]] = []
    if severity:
        issues.append({
            "severity": severity,
            "region": "hero",
            "category": "spacing",
            "expected": "主区域上下留白均衡",
            "actual": "顶部留白明显不足",
            "evidence": "实际截图中标题距离顶部过近",
            "suggestion": "增加 hero 顶部 padding",
        })
    return {
        "schema_version": "1.0",
        "passed": passed,
        "score": score,
        "summary": "视觉审查结果",
        "issues": issues,
    }


def plan_data(content: str) -> dict[str, object]:
    return {
        "summary": "修复视觉问题",
        "changes": [{
            "path": "src/App.vue",
            "content": content,
            "reason": "根据结构化视觉反馈修复",
        }],
        "suggested_checks": [],
    }


class QueueModelClient:
    def __init__(
        self,
        *responses: dict[str, object],
        model: str = "fake-model",
    ) -> None:
        self.responses = list(responses)
        self.model = model
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> frozenset[ModelCapability]:
        return ALL_CAPABILITIES

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"{self.model} 没有剩余响应")
        return ModelResponse(
            self.responses.pop(0),
            "fake",
            self.model,
            ModelUsage(12, 6, 18),
            8,
        )

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        raise AssertionError("VisionForge 必须使用结构化模型调用")


class ScriptedBrowserTester:
    def __init__(
        self,
        artifacts: ArtifactStore,
        images: ImageAssetStore,
        outcomes: list[bool | BaseException],
    ) -> None:
        self.artifacts = artifacts
        self.images = images
        self.outcomes = list(outcomes)
        self.calls = 0

    def run(
        self,
        *,
        task_id: str,
        ui_spec: UISpec,
        artifact_prefix: str = "browser",
        lifecycle: object | None = None,
    ) -> BrowserTestArtifacts:
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("Browser Tester 没有剩余结果")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        build_ref = self.artifacts.put(Artifact.create(
            f"{artifact_prefix}-build",
            task_id,
            {"passed": True, "exit_code": 0},
            kind="build_result",
        ))
        screenshot_ref, _ = self.images.create_artifact(
            self.artifacts,
            name=f"{artifact_prefix}-screenshot",
            task_id=task_id,
            data=minimal_png(ui_spec.viewport.width, ui_spec.viewport.height),
            kind="actual_screenshot",
        )
        assertions = []
        for index, item in enumerate(ui_spec.interactions):
            assertion_passed = bool(outcome) or index > 0
            assertions.append(BrowserAssertion(
                item.interaction_id,
                item.action,
                item.target,
                assertion_passed,
                "browser evidence" if assertion_passed else "",
                "断言失败" if not assertion_passed else "",
                1,
            ))
        result = BrowserRunResult(
            "1.0",
            bool(outcome),
            "http://127.0.0.1:4173/",
            ui_spec.viewport,
            tuple(assertions),
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
            metadata={"screenshot_artifact_ref": screenshot_ref},
        ))
        return BrowserTestArtifacts(build_ref, screenshot_ref, browser_ref, result)


class ReworkFixture:
    def __init__(
        self,
        root: Path,
        *,
        reviews: list[dict[str, object]],
        fixes: list[str],
        browser_outcomes: list[bool | BaseException],
        checkpoint_store: VisionForgeCheckpointStore | None = None,
        artifacts: ArtifactStore | None = None,
        images: ImageAssetStore | None = None,
        create_project: bool = True,
        feedback_policy: VisionForgeFeedbackPolicy = (
            VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
        ),
        acceptance_spec: UISpec | None = None,
    ) -> None:
        self.project = root / "project"
        if create_project:
            (self.project / "src").mkdir(parents=True)
            (self.project / "src" / "App.vue").write_text(
                "<template><main>before</main></template>", encoding="utf-8"
            )
        self.workspace = ProjectWorkspace(self.project)
        self.artifacts = artifacts or ArtifactStore()
        self.images = images or ImageAssetStore(root / "assets")
        self.analyst_model = QueueModelClient(ui_spec_data(), model="analyst")
        self.developer_model = QueueModelClient(
            plan_data("<template><main>initial</main></template>"), model="developer"
        )
        self.reviewer_model = QueueModelClient(*reviews, model="reviewer")
        self.fixer_model = QueueModelClient(
            *(plan_data(content) for content in fixes), model="fixer"
        )
        self.browser = ScriptedBrowserTester(
            self.artifacts, self.images, browser_outcomes
        )
        self.runner = VisionForgeRunner(
            artifacts=self.artifacts,
            workspace=self.workspace,
            integrator=PatchIntegrator(
                self.workspace, ("src/**", "public/**")
            ),
            analyst=RequirementAnalyst(
                self.analyst_model, self.artifacts, self.images
            ),
            developer=VisionForgeDeveloper(
                self.developer_model, self.artifacts
            ),
            browser_tester=self.browser,
            visual_reviewer=VisualReviewer(
                self.reviewer_model, self.artifacts, self.images
            ),
            fixer=VisionForgeFixer(self.fixer_model, self.artifacts),
            checkpoint_store=checkpoint_store,
            max_fix_attempts=2,
            feedback_policy=feedback_policy,
            acceptance_spec=acceptance_spec,
        )

    def reference(self, task_id: str) -> str:
        reference, _ = self.images.create_artifact(
            self.artifacts,
            name="reference-image",
            task_id=task_id,
            data=minimal_png(1440, 900),
        )
        return reference


class VisionForgeReworkTests(unittest.TestCase):
    def test_browser_only_policy_does_not_use_visual_failure_for_fixing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[review_data(passed=False, score=70, severity="P2")],
                fixes=["<template><main>must-not-run</main></template>"],
                browser_outcomes=[True],
                feedback_policy=VisionForgeFeedbackPolicy.BROWSER_ONLY,
            )
            result = fixture.runner.run(
                task_id="vf-browser-no-visual",
                requirement="只允许浏览器反馈",
                reference_image_artifact_ref=fixture.reference(
                    "vf-browser-no-visual"
                ),
            )
            self.assertEqual(result.status, "needs_fix")
            self.assertEqual(result.fix_attempts, 0)
            self.assertFalse(fixture.fixer_model.requests)

    def test_browser_only_fixer_request_omits_visual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[
                    review_data(passed=True, score=95),
                    review_data(passed=True, score=96),
                ],
                fixes=["<template><main>browser-fixed</main></template>"],
                browser_outcomes=[False, True],
                feedback_policy=VisionForgeFeedbackPolicy.BROWSER_ONLY,
            )
            result = fixture.runner.run(
                task_id="vf-browser-feedback",
                requirement="使用浏览器反馈修复",
                reference_image_artifact_ref=fixture.reference(
                    "vf-browser-feedback"
                ),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.fix_attempts, 1)
            request_data = json.loads(
                fixture.fixer_model.requests[0].messages[-1].content[0].text
            )
            self.assertIn("browser_run", request_data)
            self.assertNotIn("visual_review", request_data)
            fix_artifact = fixture.artifacts.get(
                result.implementation_artifact_ref
            )
            self.assertEqual(
                fix_artifact.metadata["feedback_sources"], ["browser_run"]
            )

    def test_runtime_fixed_acceptance_spec_replaces_model_interactions(self) -> None:
        fixed = UISpec.from_dict(ui_spec_data())
        generated = ui_spec_data()
        generated["interactions"] = []
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[review_data(passed=True, score=95)],
                fixes=[],
                browser_outcomes=[True],
                feedback_policy=VisionForgeFeedbackPolicy.NONE,
                acceptance_spec=fixed,
            )
            fixture.analyst_model.responses[0] = generated
            result = fixture.runner.run(
                task_id="vf-fixed-acceptance",
                requirement="使用固定验收",
                reference_image_artifact_ref=fixture.reference(
                    "vf-fixed-acceptance"
                ),
            )
            browser = fixture.artifacts.get(result.browser_run_artifact_ref)
            self.assertEqual(
                len(browser.content["assertions"]), len(fixed.interactions)
            )
            run = fixture.artifacts.get(result.run_artifact_ref)
            self.assertEqual(
                run.content["acceptance_spec_source"], "runtime_fixed"
            )
            developer_input = json.loads(
                fixture.developer_model.requests[0].messages[-1].content[0].text
            )
            self.assertTrue(developer_input["runtime_acceptance"]["immutable"])
            self.assertEqual(
                len(developer_input["runtime_acceptance"]["interactions"]),
                len(fixed.interactions),
            )

    def test_p2_is_fixed_once_then_runtime_marks_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[
                    review_data(passed=False, score=72, severity="P2"),
                    review_data(passed=True, score=94),
                ],
                fixes=["<template><main>fixed</main></template>"],
                browser_outcomes=[True, True],
            )
            result = fixture.runner.run(
                task_id="vf-fixed",
                requirement="实现并修复页面",
                reference_image_artifact_ref=fixture.reference("vf-fixed"),
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.fix_attempts, 1)
            self.assertEqual(len(result.cycles), 2)
            initial = result.cycles[0].implementation_artifact_ref
            self.assertEqual(
                fixture.artifacts.validation(initial).state,
                ArtifactValidationState.SUPERSEDED,
            )
            self.assertEqual(
                fixture.artifacts.validation(initial).superseded_by,
                result.implementation_artifact_ref,
            )
            self.assertEqual(
                fixture.artifacts.validation(result.implementation_artifact_ref).state,
                ArtifactValidationState.VERIFIED,
            )
            self.assertEqual(
                fixture.artifacts.validation(result.run_artifact_ref).state,
                ArtifactValidationState.VERIFIED,
            )
            self.assertIn(
                "fixed",
                (fixture.project / "src" / "App.vue").read_text(encoding="utf-8"),
            )

    def test_runtime_fails_deterministically_after_two_fix_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[
                    review_data(passed=False, score=60, severity="P2"),
                    review_data(passed=False, score=65, severity="P2"),
                    review_data(passed=False, score=70, severity="P2"),
                ],
                fixes=[
                    "<template><main>fix-one</main></template>",
                    "<template><main>fix-two</main></template>",
                ],
                browser_outcomes=[True, True, True],
            )
            result = fixture.runner.run(
                task_id="vf-limit",
                requirement="反复修复页面",
                reference_image_artifact_ref=fixture.reference("vf-limit"),
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.fix_attempts, 2)
            self.assertEqual(len(result.cycles), 3)
            self.assertEqual(len(fixture.fixer_model.requests), 2)
            self.assertEqual(len(fixture.reviewer_model.requests), 3)
            self.assertEqual(
                fixture.artifacts.validation(result.implementation_artifact_ref).state,
                ArtifactValidationState.FAILED,
            )

    def test_model_passed_cannot_override_p2_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = ReworkFixture(
                Path(temp),
                reviews=[review_data(passed=True, score=95, severity="P2")],
                fixes=[],
                browser_outcomes=[True],
            )
            fixture.runner.fixer = None
            result = fixture.runner.run(
                task_id="vf-gate",
                requirement="检查门禁",
                reference_image_artifact_ref=fixture.reference("vf-gate"),
            )
            self.assertEqual(result.status, "needs_fix")
            gate = fixture.artifacts.get(result.quality_gate_artifact_ref)
            self.assertFalse(gate.content["passed"])
            self.assertFalse(gate.content["checks"]["no_blocking_issues"])

    def test_resume_after_fix_integration_does_not_repeat_fixer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkpoints = VisionForgeCheckpointStore(root / "runtime.sqlite3")
            first = ReworkFixture(
                root,
                reviews=[review_data(passed=False, score=70, severity="P2")],
                fixes=["<template><main>fixed-before-crash</main></template>"],
                browser_outcomes=[True, RuntimeError("模拟浏览器阶段中断")],
                checkpoint_store=checkpoints,
            )
            reference = first.reference("vf-resume")
            with self.assertRaisesRegex(RuntimeError, "模拟浏览器阶段中断"):
                first.runner.run(
                    task_id="vf-resume",
                    requirement="支持恢复",
                    reference_image_artifact_ref=reference,
                    checkpoint_id="vf-resume-checkpoint",
                )
            checkpoint = checkpoints.load("vf-resume-checkpoint")
            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint.phase, "verifying")
            self.assertEqual(checkpoint.fix_attempts, 1)
            self.assertEqual(len(checkpoint.cycles), 1)
            self.assertIn(
                "fixed-before-crash",
                (first.project / "src" / "App.vue").read_text(encoding="utf-8"),
            )

            restored_artifacts = ArtifactStore()
            restored_images = ImageAssetStore(root / "assets")
            resumed = ReworkFixture(
                root,
                reviews=[review_data(passed=True, score=96)],
                fixes=[],
                browser_outcomes=[True],
                checkpoint_store=checkpoints,
                artifacts=restored_artifacts,
                images=restored_images,
                create_project=False,
            )
            result = resumed.runner.resume("vf-resume-checkpoint")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.fix_attempts, 1)
            self.assertEqual(len(result.cycles), 2)
            self.assertFalse(resumed.analyst_model.requests)
            self.assertFalse(resumed.developer_model.requests)
            self.assertFalse(resumed.fixer_model.requests)
            self.assertIsNone(checkpoints.load("vf-resume-checkpoint"))

    def test_resume_rejects_workspace_changed_after_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkpoints = VisionForgeCheckpointStore(root / "runtime.sqlite3")
            first = ReworkFixture(
                root,
                reviews=[],
                fixes=[],
                browser_outcomes=[RuntimeError("中断")],
                checkpoint_store=checkpoints,
            )
            with self.assertRaisesRegex(RuntimeError, "中断"):
                first.runner.run(
                    task_id="vf-drift",
                    requirement="检查漂移",
                    reference_image_artifact_ref=first.reference("vf-drift"),
                    checkpoint_id="vf-drift-checkpoint",
                )
            (first.project / "src" / "App.vue").write_text(
                "<template>manual edit</template>", encoding="utf-8"
            )
            resumed = ReworkFixture(
                root,
                reviews=[review_data(passed=True, score=96)],
                fixes=[],
                browser_outcomes=[True],
                checkpoint_store=checkpoints,
                artifacts=ArtifactStore(),
                images=ImageAssetStore(root / "assets"),
                create_project=False,
            )
            with self.assertRaisesRegex(
                VisionForgeRecoveryError, "Workspace.*发生变化"
            ):
                resumed.runner.resume("vf-drift-checkpoint")


@unittest.skipUnless(
    os.environ.get("VISIONFORGE_E2E") == "1",
    "设置 VISIONFORGE_E2E=1 后运行真实视觉修复浏览器测试",
)
class VisionForgeReworkIntegrationTests(unittest.TestCase):
    def test_p2_fix_rechecks_real_browser_before_completed(self) -> None:
        node = Path(os.environ.get("VISIONFORGE_NODE", ""))
        pnpm = Path(os.environ.get("VISIONFORGE_PNPM", ""))
        browser = Path(os.environ.get("VISIONFORGE_BROWSER_EXECUTABLE", ""))
        if not node.is_file() or not pnpm.is_file() or not browser.is_file():
            self.fail("真实修复测试缺少 Node、pnpm 或 Chrome 路径")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            shutil.copytree(TEMPLATE, project, symlinks=True)
            workspace = ProjectWorkspace(project)
            artifacts = ArtifactStore()
            images = ImageAssetStore(root / "assets")
            reference, _ = images.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-real-fix",
                data=minimal_png(1440, 900),
            )
            app = (TEMPLATE / "src" / "App.vue").read_text(encoding="utf-8")
            analyst_model = QueueModelClient(ui_spec_data(), model="analyst")
            developer_model = QueueModelClient(plan_data(app), model="developer")
            reviewer_model = QueueModelClient(
                review_data(passed=False, score=75, severity="P2"),
                review_data(passed=True, score=95),
                model="reviewer",
            )
            fixer_model = QueueModelClient(plan_data(app), model="fixer")
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
                    project, process_runner, artifacts, images, root / "browser-runtime"
                ),
                visual_reviewer=VisualReviewer(
                    reviewer_model, artifacts, images
                ),
                fixer=VisionForgeFixer(fixer_model, artifacts),
            ).run(
                task_id="vf-real-fix",
                requirement="实现并视觉修复邮箱注册页",
                reference_image_artifact_ref=reference,
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.fix_attempts, 1)
            self.assertEqual(len(result.cycles), 2)
            self.assertTrue(all(item.passed for item in result.cycles[1:]))
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", 4173)), 0)


if __name__ == "__main__":
    unittest.main()
