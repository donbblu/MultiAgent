from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Mapping

from ..artifacts import Artifact, ArtifactDraft, ArtifactStore
from ..harness import (
    LifecycleController,
    TaskGraph,
    TaskGraphExecutor,
    TaskRunRequest,
    TaskRunResult,
    TaskSpec,
    WorkerRegistry,
)
from ..integration import PatchIntegrator
from ..memory import MemoryManager, MemoryPolicy
from ..models import TaskContext
from ..roles import Capability, RoleRegistry, RoleSpec
from ..workspace import ProjectWorkspace
from .agents import (
    RequirementAnalyst,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisualReviewer,
)
from .contracts import UISpec, VisualReview
from .quality import VisionForgeQualityGate
from .runner import (
    BrowserTester,
    VisionForgeCycle,
    VisionForgeFeedbackPolicy,
    VisionForgeRunResult,
)


def _reference(artifact: Artifact) -> str:
    return f"artifact://{artifact.artifact_id}"


class _StagingArtifactStore:
    """读取权威 Store，所有新增 Artifact 只写入本次 Worker staging。"""

    def __init__(self, base: ArtifactStore, staging: ArtifactStore) -> None:
        self.base = base
        self.staging = staging

    def get(self, reference: str) -> Artifact:
        try:
            return self.staging.get(reference)
        except KeyError:
            return self.base.get(reference)

    def put(self, artifact: Artifact) -> str:
        return self.staging.put(artifact)


_STAGING_LOCK = RLock()


def _staged_call(component: object, callback):
    with _STAGING_LOCK:
        base = getattr(component, "artifacts", None)
        if not isinstance(base, ArtifactStore):
            raise TypeError("VisionForge Worker 组件必须暴露 ArtifactStore")
        staging = ArtifactStore()
        setattr(component, "artifacts", _StagingArtifactStore(base, staging))
        try:
            result = callback()
        finally:
            setattr(component, "artifacts", base)
    return result, staging


def _draft(staging: ArtifactStore, reference: str) -> ArtifactDraft:
    return ArtifactDraft.from_artifact(staging.get(reference))


class UIAnalystWorker:
    def __init__(self, analyst: RequirementAnalyst) -> None:
        self.analyst = analyst

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        result, staging = _staged_call(
            self.analyst,
            lambda: self.analyst.analyze(
                task_id=request.parent.task_id,
                requirement=request.parent.user_request or request.parent.objective,
                reference_image_artifact_ref=_reference(
                    request.inputs["reference_image"]
                ),
            ),
        )
        return TaskRunResult(
            True, "UI Spec 已生成",
            {"ui_spec": _draft(staging, result.artifact_ref)},
        )


class WebDeveloperWorker:
    def __init__(
        self,
        developer: VisionForgeDeveloper,
        workspace: ProjectWorkspace,
        allowed_paths: tuple[str, ...],
        acceptance_spec: UISpec | None,
    ) -> None:
        self.developer = developer
        self.workspace = workspace
        self.allowed_paths = allowed_paths
        self.acceptance_spec = acceptance_spec

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        result, staging = _staged_call(
            self.developer,
            lambda: self.developer.develop(
                task_id=request.parent.task_id,
                requirement=request.parent.user_request or request.parent.objective,
                ui_spec_artifact_ref=_reference(request.inputs["ui_spec"]),
                workspace=self.workspace,
                allowed_paths=self.allowed_paths,
                runtime_acceptance_spec=self.acceptance_spec,
            ),
        )
        return TaskRunResult(
            True,
            "Vue Patch 已生成",
            {"implementation_plan": _draft(staging, result.artifact_ref)},
        )


class PatchIntegrationWorker:
    def __init__(self, integrator: PatchIntegrator, round_index: int) -> None:
        self.integrator = integrator
        self.round_index = round_index

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        plan = request.inputs["implementation_plan"]
        result = self.integrator.integrate((plan,))
        return TaskRunResult(
            True,
            "Patch 已安全合并",
            {
                "integration_result": ArtifactDraft(
                    {
                        "round_index": self.round_index,
                        "implementation_artifact_ref": _reference(plan),
                        "changed_files": list(result.changed_files),
                    },
                    kind="integration_result",
                    metadata={"round_index": self.round_index},
                )
            },
        )


class BrowserDagWorker:
    def __init__(
        self,
        browser_tester: BrowserTester,
        acceptance_spec: UISpec | None,
        round_index: int,
        lifecycle: LifecycleController,
    ) -> None:
        self.browser_tester = browser_tester
        self.acceptance_spec = acceptance_spec
        self.round_index = round_index
        self.lifecycle = lifecycle

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        generated_spec = UISpec.from_dict(request.inputs["ui_spec"].content)
        result, staging = _staged_call(
            self.browser_tester,
            lambda: self.browser_tester.run(
                task_id=request.parent.task_id,
                ui_spec=self.acceptance_spec or generated_spec,
                artifact_prefix=f"visionforge-dag-round-{self.round_index}",
                lifecycle=self.lifecycle,
            ),
        )
        return TaskRunResult(
            True,
            "浏览器验证完成",
            {
                "build_result": _draft(staging, result.build_artifact_ref),
                "actual_screenshot": _draft(
                    staging, result.screenshot_artifact_ref
                ),
                "browser_run": _draft(staging, result.browser_run_artifact_ref),
            },
        )


class VisualReviewWorker:
    def __init__(self, reviewer: VisualReviewer, round_index: int) -> None:
        self.reviewer = reviewer
        self.round_index = round_index

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        result, staging = _staged_call(
            self.reviewer,
            lambda: self.reviewer.review(
                task_id=request.parent.task_id,
                reference_image_artifact_ref=_reference(
                    request.inputs["reference_image"]
                ),
                actual_screenshot_artifact_ref=_reference(
                    request.inputs["actual_screenshot"]
                ),
                ui_spec_artifact_ref=_reference(request.inputs["ui_spec"]),
                browser_run_artifact_ref=_reference(request.inputs["browser_run"]),
                artifact_name=(
                    f"visionforge-dag-visual-review-{self.round_index}"
                ),
            ),
        )
        return TaskRunResult(
            True,
            "视觉审查完成",
            {"visual_review": _draft(staging, result.artifact_ref)},
        )


class QualityGateWorker:
    def __init__(self, quality_gate: VisionForgeQualityGate, round_index: int) -> None:
        self.quality_gate = quality_gate
        self.round_index = round_index

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        decision, staging = _staged_call(
            self.quality_gate,
            lambda: self.quality_gate.evaluate(
                task_id=request.parent.task_id,
                round_index=self.round_index,
                build_artifact_ref=_reference(request.inputs["build_result"]),
                browser_run_artifact_ref=_reference(request.inputs["browser_run"]),
                visual_review_artifact_ref=_reference(
                    request.inputs["visual_review"]
                ),
            ),
        )
        return TaskRunResult(
            True,
            "质量门禁通过" if decision.passed else "质量门禁未通过",
            {"quality_gate": _draft(staging, decision.artifact_ref)},
        )


class WebFixerWorker:
    def __init__(
        self,
        fixer: VisionForgeFixer,
        workspace: ProjectWorkspace,
        allowed_paths: tuple[str, ...],
        round_index: int,
        feedback_policy: VisionForgeFeedbackPolicy,
    ) -> None:
        self.fixer = fixer
        self.workspace = workspace
        self.allowed_paths = allowed_paths
        self.round_index = round_index
        self.feedback_policy = feedback_policy

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        result, staging = _staged_call(
            self.fixer,
            lambda: self.fixer.fix(
                task_id=request.parent.task_id,
                round_index=self.round_index,
                ui_spec_artifact_ref=_reference(request.inputs["ui_spec"]),
                browser_run_artifact_ref=_reference(
                    request.inputs["previous_browser_run"]
                ),
                visual_review_artifact_ref=(
                    _reference(request.inputs["previous_visual_review"])
                    if self.feedback_policy
                    is VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
                    else None
                ),
                current_implementation_artifact_ref=_reference(
                    request.inputs["previous_implementation_plan"]
                ),
                workspace=self.workspace,
                allowed_paths=self.allowed_paths,
            ),
        )
        return TaskRunResult(
            True,
            "局部视觉修复 Patch 已生成",
            {"implementation_plan": _draft(staging, result.artifact_ref)},
        )


_ROLE_SPECS = (
    RoleSpec(
        "ui_analyst",
        "结合需求和参考图生成可验证 UI Spec",
        frozenset({Capability.READ_PROJECT}),
        ("不得生成代码", "不得改变 Runtime 验收条件"),
    ),
    RoleSpec(
        "web_developer",
        "根据 UI Spec 生成受限 Vue 文件变更",
        frozenset({Capability.READ_PROJECT, Capability.PROPOSE_CHANGES}),
        ("只修改允许路径",),
    ),
    RoleSpec(
        "patch_integrator",
        "确定性检查并应用 Patch",
        frozenset({Capability.WRITE_PROJECT}),
    ),
    RoleSpec(
        "browser_tester",
        "执行构建、DOM、交互和截图验证",
        frozenset({Capability.READ_PROJECT, Capability.RUN_VERIFICATION}),
        ("只执行 Runtime 白名单动作",),
    ),
    RoleSpec(
        "visual_reviewer",
        "比较参考图与实际截图并输出结构化视觉问题",
        frozenset({Capability.REVIEW_CHANGES}),
        ("功能事实以 Browser Run 为准",),
    ),
    RoleSpec(
        "quality_gate",
        "根据 Runtime 证据确定是否通过",
        frozenset({Capability.REVIEW_CHANGES}),
        ("模型不能自行宣告完成",),
    ),
    RoleSpec(
        "web_fixer",
        "根据浏览器和视觉失败证据生成最小修复",
        frozenset({Capability.READ_PROJECT, Capability.PROPOSE_CHANGES}),
        ("只修复已报告问题",),
    ),
)


def _roles() -> RoleRegistry:
    return RoleRegistry(_ROLE_SPECS)


def _memory(roles: RoleRegistry) -> MemoryManager:
    policies = {
        name: MemoryPolicy(
            frozenset({"task"}),
            frozenset({"task"}),
            0,
            include_feedback=name == "web_fixer",
            max_context_tokens=2_000,
        )
        for name in roles.names()
    }
    return MemoryManager(policies)


def _initial_graph() -> TaskGraph:
    criteria = ("Runtime 的构建、浏览器和视觉质量门禁完成",)
    return TaskGraph(
        (
            TaskSpec(
                "ui-analysis", "分析参考页面", "生成 UI Spec", "ui_analyst",
                acceptance_criteria=criteria,
                input_artifacts=("reference_image",),
                output_artifacts=("ui_spec",), retry_limit=0,
            ),
            TaskSpec(
                "web-implementation", "实现 Vue 页面", "生成页面 Patch",
                "web_developer", dependencies=("ui-analysis",),
                acceptance_criteria=criteria, input_artifacts=("ui_spec",),
                output_artifacts=("implementation_plan",), retry_limit=0,
            ),
            TaskSpec(
                "patch-integration", "合并页面 Patch", "安全应用页面变更",
                "patch_integrator", dependencies=("web-implementation",),
                acceptance_criteria=criteria,
                input_artifacts=("implementation_plan",),
                output_artifacts=("integration_result",), retry_limit=0,
            ),
            TaskSpec(
                "browser-test", "浏览器验收", "构建页面并执行受控交互",
                "browser_tester", dependencies=("patch-integration",),
                acceptance_criteria=criteria,
                input_artifacts=("ui_spec", "integration_result"),
                output_artifacts=(
                    "build_result", "actual_screenshot", "browser_run",
                ), retry_limit=0,
            ),
            TaskSpec(
                "visual-review", "视觉审查", "比较参考图和实际截图",
                "visual_reviewer", dependencies=("browser-test",),
                acceptance_criteria=criteria,
                input_artifacts=(
                    "reference_image", "ui_spec", "actual_screenshot", "browser_run",
                ),
                output_artifacts=("visual_review",), retry_limit=0,
            ),
            TaskSpec(
                "quality-gate", "质量门禁", "根据全部证据作出确定性判断",
                "quality_gate", dependencies=("browser-test", "visual-review"),
                acceptance_criteria=criteria,
                input_artifacts=("build_result", "browser_run", "visual_review"),
                output_artifacts=("quality_gate",), retry_limit=0,
            ),
        ),
        external_artifacts=("reference_image",),
    )


def _fix_graph(round_index: int) -> TaskGraph:
    prefix = f"fix-{round_index}"
    criteria = ("修复后重新通过完整浏览器与视觉质量门禁",)
    external = (
        "reference_image", "ui_spec", "previous_implementation_plan",
        "previous_browser_run", "previous_visual_review",
    )
    return TaskGraph(
        (
            TaskSpec(
                prefix, "修复页面", "根据结构化失败证据生成局部 Patch",
                "web_fixer", acceptance_criteria=criteria,
                input_artifacts=(
                    "ui_spec", "previous_implementation_plan",
                    "previous_browser_run", "previous_visual_review",
                ), output_artifacts=("implementation_plan",), retry_limit=0,
            ),
            TaskSpec(
                f"{prefix}-integration", "合并修复 Patch", "安全应用修复",
                "patch_integrator", dependencies=(prefix,),
                acceptance_criteria=criteria,
                input_artifacts=("implementation_plan",),
                output_artifacts=("integration_result",), retry_limit=0,
            ),
            TaskSpec(
                f"{prefix}-browser", "重新执行浏览器验收", "验证修复后的页面",
                "browser_tester", dependencies=(f"{prefix}-integration",),
                acceptance_criteria=criteria,
                input_artifacts=("ui_spec", "integration_result"),
                output_artifacts=(
                    "build_result", "actual_screenshot", "browser_run",
                ), retry_limit=0,
            ),
            TaskSpec(
                f"{prefix}-review", "重新执行视觉审查", "比较修复后的截图",
                "visual_reviewer", dependencies=(f"{prefix}-browser",),
                acceptance_criteria=criteria,
                input_artifacts=(
                    "reference_image", "ui_spec", "actual_screenshot",
                    "browser_run",
                ), output_artifacts=("visual_review",), retry_limit=0,
            ),
            TaskSpec(
                f"{prefix}-gate", "重新执行质量门禁", "判断修复是否通过",
                "quality_gate",
                dependencies=(f"{prefix}-browser", f"{prefix}-review"),
                acceptance_criteria=criteria,
                input_artifacts=(
                    "build_result", "browser_run", "visual_review",
                ), output_artifacts=("quality_gate",), retry_limit=0,
            ),
        ),
        external_artifacts=external,
    )


@dataclass
class _LegacyVisionForgeDagRunner:
    artifacts: ArtifactStore
    workspace: ProjectWorkspace
    integrator: PatchIntegrator
    analyst: RequirementAnalyst
    developer: VisionForgeDeveloper
    browser_tester: BrowserTester
    visual_reviewer: VisualReviewer
    fixer: VisionForgeFixer | None = None
    quality_gate: VisionForgeQualityGate | None = None
    minimum_visual_score: float = 85
    max_fix_attempts: int = 2
    feedback_policy: VisionForgeFeedbackPolicy = (
        VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
    )
    acceptance_spec: UISpec | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.max_fix_attempts <= 2:
            raise ValueError("max_fix_attempts 必须在 0 到 2 之间")
        if not isinstance(self.feedback_policy, VisionForgeFeedbackPolicy):
            raise ValueError("feedback_policy 无效")
        if self.integrator.workspace.root != self.workspace.root:
            raise ValueError("DAG Runner 与 PatchIntegrator 必须使用同一 Workspace")
        for component in (
            self.analyst, self.developer, self.browser_tester,
            self.visual_reviewer, self.fixer,
        ):
            component_store = getattr(component, "artifacts", self.artifacts)
            if component is not None and component_store is not self.artifacts:
                raise ValueError("VisionForge DAG Worker 必须共享同一个 ArtifactStore")
        if self.quality_gate is None:
            self.quality_gate = VisionForgeQualityGate(
                self.artifacts, minimum_visual_score=self.minimum_visual_score
            )
        if self.quality_gate.artifacts is not self.artifacts:
            raise ValueError("Quality Gate 必须共享同一个 ArtifactStore")

    def run(
        self,
        *,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
    ) -> VisionForgeRunResult:
        reference = self.artifacts.get(reference_image_artifact_ref)
        if reference.task_id != task_id or reference.kind != "reference_image":
            raise ValueError("参考图 Artifact 与任务不匹配")
        roles = _roles()
        memory = _memory(roles)
        lifecycle = LifecycleController()
        task = TaskContext(
            task_id,
            requirement,
            ["通过 Runtime 浏览器和视觉质量门禁"],
            user_request=requirement,
            project_root=str(self.workspace.root),
            allowed_paths=list(self.integrator.allowed_paths),
        )
        workers = self._workers(0, lifecycle)
        initial = TaskGraphExecutor(
            _initial_graph(), workers, roles, memory,
            artifacts=self.artifacts, lifecycle=lifecycle,
            finalize_lifecycle=False,
            initial_artifacts={"reference_image": reference_image_artifact_ref},
        ).run(task)
        if not initial.succeeded:
            reason = "; ".join(initial.snapshot.failures.values()) or (
                "VisionForge 初始 DAG 执行失败"
            )
            lifecycle.mark_failed(reason)
            raise RuntimeError(reason)
        current = dict(initial.snapshot.artifacts)
        cycles: list[VisionForgeCycle] = []
        implementation_ref = current["implementation_plan"]
        integration_ref = current["integration_result"]
        fix_attempts = 0

        while True:
            cycle = self._cycle(
                fix_attempts,
                implementation_ref,
                integration_ref,
                current["build_result"],
                current["actual_screenshot"],
                current["browser_run"],
                current["visual_review"],
                current["quality_gate"],
            )
            cycles.append(cycle)
            if cycle.passed:
                self._mark_verified(cycle, current["ui_spec"])
                lifecycle.mark_completed()
                return self._finalize(
                    task_id, reference_image_artifact_ref, current["ui_spec"],
                    implementation_ref, integration_ref, cycles, "completed",
                    fix_attempts,
                )
            self._mark_failed(cycle, implementation_ref, integration_ref)
            if self.fixer is None or not self._should_fix(
                cycle.quality_gate_artifact_ref
            ):
                lifecycle.mark_failed("质量门禁未通过且当前反馈策略不允许修复")
                return self._finalize(
                    task_id, reference_image_artifact_ref, current["ui_spec"],
                    implementation_ref, integration_ref, cycles, "needs_fix",
                    fix_attempts,
                )
            if fix_attempts >= self.max_fix_attempts:
                lifecycle.mark_failed("VisionForge 修复次数已耗尽")
                return self._finalize(
                    task_id, reference_image_artifact_ref, current["ui_spec"],
                    implementation_ref, integration_ref, cycles, "failed",
                    fix_attempts,
                )
            fix_attempts += 1
            external = {
                "reference_image": reference_image_artifact_ref,
                "ui_spec": current["ui_spec"],
                "previous_implementation_plan": implementation_ref,
                "previous_browser_run": cycle.browser_run_artifact_ref,
                "previous_visual_review": cycle.visual_review_artifact_ref,
            }
            fixed = TaskGraphExecutor(
                _fix_graph(fix_attempts),
                self._workers(fix_attempts, lifecycle),
                roles,
                memory,
                artifacts=self.artifacts,
                lifecycle=lifecycle,
                finalize_lifecycle=False,
                initial_artifacts=external,
            ).run(task)
            if not fixed.succeeded:
                reason = "; ".join(fixed.snapshot.failures.values()) or (
                    "VisionForge Fix DAG 执行失败"
                )
                lifecycle.mark_failed(reason)
                raise RuntimeError(reason)
            previous = implementation_ref
            current = dict(fixed.snapshot.artifacts)
            implementation_ref = current["implementation_plan"]
            integration_ref = current["integration_result"]
            self.artifacts.supersede((previous,), implementation_ref)

    def _should_fix(self, gate_ref: str) -> bool:
        if self.feedback_policy is VisionForgeFeedbackPolicy.NONE:
            return False
        if self.feedback_policy is VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL:
            return True
        gate = self.artifacts.get(gate_ref).content
        if not isinstance(gate, dict) or not isinstance(gate.get("checks"), dict):
            return False
        checks = gate["checks"]
        return not all(bool(checks.get(name)) for name in (
            "build_passed", "assertions_passed", "console_passed",
            "page_errors_passed", "network_passed", "browser_run_passed",
        ))

    def _workers(
        self, round_index: int, lifecycle: LifecycleController
    ) -> WorkerRegistry:
        registry = WorkerRegistry()
        registry.register("ui_analyst", UIAnalystWorker(self.analyst))
        registry.register(
            "web_developer",
            WebDeveloperWorker(
                self.developer,
                self.workspace,
                tuple(self.integrator.allowed_paths),
                self.acceptance_spec,
            ),
        )
        registry.register(
            "patch_integrator", PatchIntegrationWorker(self.integrator, round_index)
        )
        registry.register(
            "browser_tester",
            BrowserDagWorker(
                self.browser_tester, self.acceptance_spec, round_index, lifecycle
            ),
        )
        registry.register(
            "visual_reviewer", VisualReviewWorker(self.visual_reviewer, round_index)
        )
        assert self.quality_gate is not None
        registry.register(
            "quality_gate", QualityGateWorker(self.quality_gate, round_index)
        )
        if self.fixer is not None:
            registry.register(
                "web_fixer",
                WebFixerWorker(
                    self.fixer,
                    self.workspace,
                    tuple(self.integrator.allowed_paths),
                    round_index,
                    self.feedback_policy,
                ),
            )
        return registry

    def _cycle(
        self,
        round_index: int,
        implementation_ref: str,
        integration_ref: str,
        build_ref: str,
        screenshot_ref: str,
        browser_ref: str,
        review_ref: str,
        gate_ref: str,
    ) -> VisionForgeCycle:
        gate = self.artifacts.get(gate_ref)
        passed = isinstance(gate.content, dict) and gate.content.get("passed") is True
        return VisionForgeCycle(
            round_index, implementation_ref, integration_ref, build_ref,
            screenshot_ref, browser_ref, review_ref, gate_ref, passed,
        )

    def _mark_failed(
        self,
        cycle: VisionForgeCycle,
        implementation_ref: str,
        integration_ref: str,
    ) -> None:
        self.artifacts.mark_failed(
            (
                implementation_ref, integration_ref, cycle.build_artifact_ref,
                cycle.actual_screenshot_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
                cycle.quality_gate_artifact_ref,
            ),
            (cycle.quality_gate_artifact_ref,),
        )

    def _mark_verified(self, cycle: VisionForgeCycle, ui_spec_ref: str) -> None:
        self.artifacts.mark_verified(
            (
                ui_spec_ref, cycle.implementation_artifact_ref,
                cycle.integration_artifact_ref, cycle.build_artifact_ref,
                cycle.actual_screenshot_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
                cycle.quality_gate_artifact_ref,
            ),
            (cycle.quality_gate_artifact_ref,),
        )

    def _finalize(
        self,
        task_id: str,
        reference_ref: str,
        ui_spec_ref: str,
        implementation_ref: str,
        integration_ref: str,
        cycles: list[VisionForgeCycle],
        status: str,
        fix_attempts: int,
    ) -> VisionForgeRunResult:
        final = cycles[-1]
        review = VisualReview.from_dict(
            self.artifacts.get(final.visual_review_artifact_ref).content
        )
        changed_files: list[str] = []
        for cycle in cycles:
            content = self.artifacts.get(cycle.integration_artifact_ref).content
            if isinstance(content, dict):
                for path in content.get("changed_files", []):
                    if isinstance(path, str) and path not in changed_files:
                        changed_files.append(path)
        model_calls = self._model_calls(cycles, ui_spec_ref)
        run_ref = self.artifacts.put(Artifact.create(
            "visionforge-dag-run",
            task_id,
            {
                "stage": status,
                "status": status,
                "engine": "dag",
                "artifact_chain": {
                    "reference_image": reference_ref,
                    "ui_spec": ui_spec_ref,
                    "implementation_plan": implementation_ref,
                    "integration_result": integration_ref,
                    "build_result": final.build_artifact_ref,
                    "actual_screenshot": final.actual_screenshot_artifact_ref,
                    "browser_run": final.browser_run_artifact_ref,
                    "visual_review": final.visual_review_artifact_ref,
                    "quality_gate": final.quality_gate_artifact_ref,
                },
                "cycles": [item.to_dict() for item in cycles],
                "changed_files": changed_files,
                "browser_passed": bool(
                    self.artifacts.get(final.browser_run_artifact_ref).content["passed"]
                ),
                "visual_model_passed": review.passed,
                "visual_score": review.score,
                "minimum_visual_score": self.quality_gate.minimum_visual_score,
                "blocking_issue_count": len(review.blocking_issues),
                "fix_attempts": fix_attempts,
                "max_fix_attempts": self.max_fix_attempts,
                "feedback_policy": self.feedback_policy.value,
                "needs_fix": status != "completed",
                "model_calls": model_calls,
                "total_tokens": sum(int(item["total_tokens"]) for item in model_calls),
                "total_model_latency_ms": sum(
                    int(item["latency_ms"]) for item in model_calls
                ),
            },
            kind="visionforge_run",
            metadata={
                "engine": "dag", "stage": status,
                "needs_fix": status != "completed", "fix_attempts": fix_attempts,
            },
        ))
        if status == "completed":
            self.artifacts.mark_verified((run_ref,), (final.quality_gate_artifact_ref,))
        else:
            self.artifacts.mark_failed((run_ref,), (final.quality_gate_artifact_ref,))
        browser_passed = bool(
            self.artifacts.get(final.browser_run_artifact_ref).content["passed"]
        )
        return VisionForgeRunResult(
            task_id, reference_ref, ui_spec_ref, implementation_ref,
            integration_ref, final.build_artifact_ref,
            final.actual_screenshot_artifact_ref,
            final.browser_run_artifact_ref, final.visual_review_artifact_ref,
            run_ref, tuple(changed_files), browser_passed, review.score,
            status != "completed", status, fix_attempts,
            final.quality_gate_artifact_ref, tuple(cycles),
        )

    def _model_calls(
        self, cycles: list[VisionForgeCycle], ui_spec_ref: str
    ) -> list[dict[str, object]]:
        references = [("ui_analyst", ui_spec_ref)]
        for index, cycle in enumerate(cycles):
            references.append((
                "developer" if index == 0 else "fixer",
                cycle.implementation_artifact_ref,
            ))
            references.append(("visual_reviewer", cycle.visual_review_artifact_ref))
        result: list[dict[str, object]] = []
        for role, reference in references:
            metadata: Mapping[str, object] = self.artifacts.get(reference).metadata
            result.append({
                "role": role,
                "provider": metadata.get("provider", ""),
                "model": metadata.get("model", ""),
                "input_tokens": int(metadata.get("input_tokens", 0)),
                "output_tokens": int(metadata.get("output_tokens", 0)),
                "total_tokens": int(metadata.get("total_tokens", 0)),
                "latency_ms": int(metadata.get("latency_ms", 0)),
            })
        return result
