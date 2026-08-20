from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from ..artifacts import Artifact, ArtifactStore
from ..integration import PatchIntegrator
from ..model import ModelResponse
from ..workspace import ProjectWorkspace
from .agents import (
    RequirementAnalyst,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisualReviewer,
)
from .artifact_types import RUN
from .browser import BrowserTestArtifacts
from .contracts import UISpec, VisualReview
from .quality import QualityGateDecision, VisionForgeQualityGate
from .recovery import (
    VisionForgeCheckpoint,
    VisionForgeCheckpointStore,
    VisionForgeRecoveryError,
)


class BrowserTester(Protocol):
    def run(
        self,
        *,
        task_id: str,
        ui_spec: UISpec,
        artifact_prefix: str = "browser",
        lifecycle: object | None = None,
    ) -> BrowserTestArtifacts: ...


class VisionForgeFeedbackPolicy(str, Enum):
    """决定失败证据是否、以及以何种边界返回给 Fixer。"""

    NONE = "none"
    BROWSER_ONLY = "browser_only"
    BROWSER_AND_VISUAL = "browser_and_visual"


@dataclass(frozen=True)
class VisionForgeCycle:
    round_index: int
    implementation_artifact_ref: str
    integration_artifact_ref: str
    build_artifact_ref: str
    actual_screenshot_artifact_ref: str
    browser_run_artifact_ref: str
    visual_review_artifact_ref: str
    quality_gate_artifact_ref: str
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "round_index": self.round_index,
            "implementation_artifact_ref": self.implementation_artifact_ref,
            "integration_artifact_ref": self.integration_artifact_ref,
            "build_artifact_ref": self.build_artifact_ref,
            "actual_screenshot_artifact_ref": self.actual_screenshot_artifact_ref,
            "browser_run_artifact_ref": self.browser_run_artifact_ref,
            "visual_review_artifact_ref": self.visual_review_artifact_ref,
            "quality_gate_artifact_ref": self.quality_gate_artifact_ref,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VisionForgeCycle":
        return cls(
            int(value["round_index"]),
            str(value["implementation_artifact_ref"]),
            str(value["integration_artifact_ref"]),
            str(value["build_artifact_ref"]),
            str(value["actual_screenshot_artifact_ref"]),
            str(value["browser_run_artifact_ref"]),
            str(value["visual_review_artifact_ref"]),
            str(value["quality_gate_artifact_ref"]),
            bool(value["passed"]),
        )


@dataclass(frozen=True)
class VisionForgeRunResult:
    task_id: str
    reference_image_artifact_ref: str
    ui_spec_artifact_ref: str
    implementation_artifact_ref: str
    integration_artifact_ref: str
    build_artifact_ref: str
    actual_screenshot_artifact_ref: str
    browser_run_artifact_ref: str
    visual_review_artifact_ref: str
    run_artifact_ref: str
    changed_files: tuple[str, ...]
    browser_passed: bool
    visual_score: float
    needs_fix: bool
    status: str
    fix_attempts: int
    quality_gate_artifact_ref: str
    cycles: tuple[VisionForgeCycle, ...]


@dataclass
class _ExecutionState:
    checkpoint_id: str
    task_id: str
    requirement: str
    reference_image_artifact_ref: str
    ui_spec_artifact_ref: str
    current_implementation_artifact_ref: str
    current_integration_artifact_ref: str
    fix_attempts: int
    cycles: list[VisionForgeCycle]
    model_calls: list[dict[str, object]]


class VisionForgeRunner:
    """Runtime 驱动的视觉修复闭环；模型不能决定 completed。"""

    TERMINAL_STATUSES = frozenset({"completed", "failed", "needs_fix"})
    HASH_EXCLUDES = ("node_modules", "dist", ".runtime")

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        workspace: ProjectWorkspace,
        integrator: PatchIntegrator,
        analyst: RequirementAnalyst,
        developer: VisionForgeDeveloper,
        browser_tester: BrowserTester,
        visual_reviewer: VisualReviewer,
        fixer: VisionForgeFixer | None = None,
        quality_gate: VisionForgeQualityGate | None = None,
        checkpoint_store: VisionForgeCheckpointStore | None = None,
        minimum_visual_score: float = 85,
        max_fix_attempts: int = 2,
        feedback_policy: VisionForgeFeedbackPolicy = (
            VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
        ),
        acceptance_spec: UISpec | None = None,
    ) -> None:
        if not 0 <= minimum_visual_score <= 100:
            raise ValueError("minimum_visual_score 必须在 0 到 100 之间")
        if not 0 <= max_fix_attempts <= 2:
            raise ValueError("max_fix_attempts 必须在 0 到 2 之间")
        if not isinstance(feedback_policy, VisionForgeFeedbackPolicy):
            raise ValueError("feedback_policy 无效")
        if integrator.workspace.root != workspace.root:
            raise ValueError("VisionForgeRunner 与 PatchIntegrator 必须使用同一 Workspace")
        for component in (analyst, developer, visual_reviewer, fixer):
            if component is not None and component.artifacts is not artifacts:
                raise ValueError("VisionForge 角色必须共享同一个 ArtifactStore")
        self.artifacts = artifacts
        self.workspace = workspace
        self.integrator = integrator
        self.analyst = analyst
        self.developer = developer
        self.browser_tester = browser_tester
        self.visual_reviewer = visual_reviewer
        self.fixer = fixer
        self.quality_gate = quality_gate or VisionForgeQualityGate(
            artifacts, minimum_visual_score=minimum_visual_score
        )
        if self.quality_gate.artifacts is not artifacts:
            raise ValueError("Quality Gate 必须共享同一个 ArtifactStore")
        self.checkpoint_store = checkpoint_store
        self.minimum_visual_score = self.quality_gate.minimum_visual_score
        self.max_fix_attempts = max_fix_attempts
        self.feedback_policy = feedback_policy
        self.acceptance_spec = acceptance_spec

    def run(
        self,
        *,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
        checkpoint_id: str | None = None,
    ) -> VisionForgeRunResult:
        if not task_id.strip() or not requirement.strip():
            raise ValueError("task_id 和页面需求不能为空")
        analyst_result = self.analyst.analyze(
            task_id=task_id,
            requirement=requirement,
            reference_image_artifact_ref=reference_image_artifact_ref,
        )
        ui_spec = UISpec.from_dict(
            self.artifacts.get(analyst_result.artifact_ref).content
        )
        developer_result = self.developer.develop(
            task_id=task_id,
            requirement=requirement,
            ui_spec_artifact_ref=analyst_result.artifact_ref,
            workspace=self.workspace,
            allowed_paths=self.integrator.allowed_paths,
            runtime_acceptance_spec=self.acceptance_spec,
        )
        integration_ref = self._integrate(
            task_id, 0, developer_result.artifact_ref
        )
        state = _ExecutionState(
            checkpoint_id or f"visionforge:{task_id}",
            task_id,
            requirement.strip(),
            reference_image_artifact_ref,
            analyst_result.artifact_ref,
            developer_result.artifact_ref,
            integration_ref,
            0,
            [],
            [
                self._model_call("requirement_analyst", analyst_result.response),
                self._model_call("developer", developer_result.response),
            ],
        )
        self._save_checkpoint(state, "verifying")
        return self._advance(state, "verifying", ui_spec)

    def resume(self, checkpoint_id: str) -> VisionForgeRunResult:
        if self.checkpoint_store is None:
            raise VisionForgeRecoveryError("VisionForgeRunner 未配置 Checkpoint Store")
        checkpoint = self.checkpoint_store.load(checkpoint_id)
        if checkpoint is None:
            raise VisionForgeRecoveryError(f"Checkpoint 不存在: {checkpoint_id}")
        if checkpoint.phase == "failed":
            raise VisionForgeRecoveryError("失败终态的 Checkpoint 不能继续恢复")
        if checkpoint.max_fix_attempts != self.max_fix_attempts:
            raise VisionForgeRecoveryError("恢复时的最大修复轮数与 Checkpoint 不一致")
        self.checkpoint_store.validate_workspace(
            checkpoint, self._workspace_hashes()
        )
        self.artifacts.replace_with(checkpoint.artifacts)
        state = _ExecutionState(
            checkpoint.checkpoint_id,
            checkpoint.task_id,
            checkpoint.requirement,
            checkpoint.reference_image_artifact_ref,
            checkpoint.ui_spec_artifact_ref,
            checkpoint.current_implementation_artifact_ref,
            checkpoint.current_integration_artifact_ref,
            checkpoint.fix_attempts,
            [VisionForgeCycle.from_dict(item) for item in checkpoint.cycles],
            [dict(item) for item in checkpoint.model_calls],
        )
        ui_spec = UISpec.from_dict(
            self.artifacts.get(state.ui_spec_artifact_ref).content
        )
        return self._advance(state, checkpoint.phase, ui_spec)

    def _advance(
        self,
        state: _ExecutionState,
        phase: str,
        ui_spec: UISpec,
    ) -> VisionForgeRunResult:
        while True:
            if phase == "verifying":
                cycle, review, gate, review_response = self._verify(
                    state, ui_spec, self.acceptance_spec or ui_spec
                )
                state.cycles.append(cycle)
                state.model_calls.append(self._model_call(
                    "visual_reviewer", review_response
                ))
                if gate.passed:
                    self._mark_cycle_verified(state, cycle)
                    return self._finalize(state, review, "completed")
                self._mark_cycle_failed(state, cycle)
                if self.fixer is None or not self._should_fix(gate):
                    return self._finalize(state, review, "needs_fix")
                if state.fix_attempts >= self.max_fix_attempts:
                    return self._finalize(state, review, "failed")
                phase = "needs_fix"
                self._save_checkpoint(state, phase)

            if phase == "needs_fix":
                if self.fixer is None:
                    raise VisionForgeRecoveryError(
                        "Checkpoint 需要 Fixer，但当前 Runner 未配置"
                    )
                latest = state.cycles[-1]
                next_round = state.fix_attempts + 1
                fixer_result = self.fixer.fix(
                    task_id=state.task_id,
                    round_index=next_round,
                    ui_spec_artifact_ref=state.ui_spec_artifact_ref,
                    browser_run_artifact_ref=latest.browser_run_artifact_ref,
                    visual_review_artifact_ref=(
                        latest.visual_review_artifact_ref
                        if self.feedback_policy
                        is VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
                        else None
                    ),
                    current_implementation_artifact_ref=(
                        state.current_implementation_artifact_ref
                    ),
                    workspace=self.workspace,
                    allowed_paths=self.integrator.allowed_paths,
                )
                integration_ref = self._integrate(
                    state.task_id, next_round, fixer_result.artifact_ref
                )
                self.artifacts.supersede(
                    (state.current_implementation_artifact_ref,),
                    fixer_result.artifact_ref,
                )
                state.current_implementation_artifact_ref = fixer_result.artifact_ref
                state.current_integration_artifact_ref = integration_ref
                state.fix_attempts = next_round
                state.model_calls.append(self._model_call(
                    "fixer", fixer_result.response
                ))
                phase = "verifying"
                self._save_checkpoint(state, phase)

    def _verify(
        self,
        state: _ExecutionState,
        ui_spec: UISpec,
        acceptance_spec: UISpec,
    ) -> tuple[VisionForgeCycle, VisualReview, QualityGateDecision, ModelResponse]:
        round_index = state.fix_attempts
        browser = self.browser_tester.run(
            task_id=state.task_id,
            ui_spec=acceptance_spec,
            artifact_prefix=f"visionforge-round-{round_index}",
        )
        reviewer_result = self.visual_reviewer.review(
            task_id=state.task_id,
            reference_image_artifact_ref=state.reference_image_artifact_ref,
            actual_screenshot_artifact_ref=browser.screenshot_artifact_ref,
            ui_spec_artifact_ref=state.ui_spec_artifact_ref,
            browser_run_artifact_ref=browser.browser_run_artifact_ref,
            artifact_name=f"visionforge-visual-review-{round_index}",
        )
        review = VisualReview.from_dict(
            self.artifacts.get(reviewer_result.artifact_ref).content
        )
        gate = self.quality_gate.evaluate(
            task_id=state.task_id,
            round_index=round_index,
            build_artifact_ref=browser.build_artifact_ref,
            browser_run_artifact_ref=browser.browser_run_artifact_ref,
            visual_review_artifact_ref=reviewer_result.artifact_ref,
        )
        cycle = VisionForgeCycle(
            round_index,
            state.current_implementation_artifact_ref,
            state.current_integration_artifact_ref,
            browser.build_artifact_ref,
            browser.screenshot_artifact_ref,
            browser.browser_run_artifact_ref,
            reviewer_result.artifact_ref,
            gate.artifact_ref,
            gate.passed,
        )
        return cycle, review, gate, reviewer_result.response

    def _should_fix(self, gate: QualityGateDecision) -> bool:
        if self.feedback_policy is VisionForgeFeedbackPolicy.NONE:
            return False
        if self.feedback_policy is VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL:
            return not gate.passed
        return not all((
            gate.build_passed,
            gate.assertions_passed,
            gate.console_passed,
            gate.page_errors_passed,
            gate.network_passed,
            gate.browser_run_passed,
        ))

    def _integrate(
        self,
        task_id: str,
        round_index: int,
        implementation_artifact_ref: str,
    ) -> str:
        implementation = self.artifacts.get(implementation_artifact_ref)
        integrated = self.integrator.integrate((implementation,))
        return self.artifacts.put(Artifact.create(
            f"visionforge-integration-result-{round_index}",
            task_id,
            {
                "round_index": round_index,
                "implementation_artifact_ref": implementation_artifact_ref,
                "changed_files": list(integrated.changed_files),
            },
            kind="integration_result",
            metadata={
                "round_index": round_index,
                "implementation_artifact_ref": implementation_artifact_ref,
            },
        ))

    def _mark_cycle_failed(
        self, state: _ExecutionState, cycle: VisionForgeCycle
    ) -> None:
        self.artifacts.mark_failed(
            (
                state.current_implementation_artifact_ref,
                state.current_integration_artifact_ref,
                cycle.build_artifact_ref,
                cycle.actual_screenshot_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
            ),
            (cycle.quality_gate_artifact_ref,),
            validator_kind="visionforge:quality_gate",
        )
        self.artifacts.mark_failed(
            (cycle.quality_gate_artifact_ref,),
            (
                cycle.build_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
            ),
            validator_kind="visionforge:quality_gate",
        )

    def _mark_cycle_verified(
        self, state: _ExecutionState, cycle: VisionForgeCycle
    ) -> None:
        self.artifacts.mark_verified(
            (
                state.ui_spec_artifact_ref,
                state.current_implementation_artifact_ref,
                state.current_integration_artifact_ref,
                cycle.build_artifact_ref,
                cycle.actual_screenshot_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
            ),
            (cycle.quality_gate_artifact_ref,),
            validator_kind="visionforge:quality_gate",
        )
        self.artifacts.mark_verified(
            (cycle.quality_gate_artifact_ref,),
            (
                cycle.build_artifact_ref,
                cycle.browser_run_artifact_ref,
                cycle.visual_review_artifact_ref,
            ),
            validator_kind="visionforge:quality_gate",
        )

    def _finalize(
        self,
        state: _ExecutionState,
        review: VisualReview,
        status: str,
    ) -> VisionForgeRunResult:
        if status not in self.TERMINAL_STATUSES:
            raise ValueError(f"未知 VisionForge 终态: {status}")
        final = state.cycles[-1]
        changed_files = self._changed_files(state.cycles)
        chain = {
            "reference_image": state.reference_image_artifact_ref,
            "ui_spec": state.ui_spec_artifact_ref,
            "implementation_plan": state.current_implementation_artifact_ref,
            "integration_result": state.current_integration_artifact_ref,
            "build_result": final.build_artifact_ref,
            "actual_screenshot": final.actual_screenshot_artifact_ref,
            "browser_run": final.browser_run_artifact_ref,
            "visual_review": final.visual_review_artifact_ref,
            "quality_gate": final.quality_gate_artifact_ref,
        }
        run_ref = self.artifacts.put(Artifact.create(
            "visionforge-run",
            state.task_id,
            {
                "stage": status,
                "status": status,
                "artifact_chain": chain,
                "cycles": [item.to_dict() for item in state.cycles],
                "changed_files": list(changed_files),
                "browser_passed": self.artifacts.get(
                    final.browser_run_artifact_ref
                ).content["passed"],
                "visual_model_passed": review.passed,
                "visual_score": review.score,
                "minimum_visual_score": self.minimum_visual_score,
                "blocking_issue_count": len(review.blocking_issues),
                "fix_attempts": state.fix_attempts,
                "max_fix_attempts": self.max_fix_attempts,
                "feedback_policy": self.feedback_policy.value,
                "acceptance_spec_source": (
                    "runtime_fixed" if self.acceptance_spec is not None else "ui_spec"
                ),
                "needs_fix": status != "completed",
                "model_calls": state.model_calls,
                "total_tokens": sum(
                    int(item["total_tokens"]) for item in state.model_calls
                ),
                "total_model_latency_ms": sum(
                    int(item["latency_ms"]) for item in state.model_calls
                ),
            },
            kind=RUN,
            metadata={
                "stage": status,
                "needs_fix": status != "completed",
                "fix_attempts": state.fix_attempts,
            },
        ))
        if status == "completed":
            self.artifacts.mark_verified(
                (run_ref,), (final.quality_gate_artifact_ref,)
            )
            if self.checkpoint_store:
                self.checkpoint_store.delete(state.checkpoint_id)
        else:
            self.artifacts.mark_failed(
                (run_ref,), (final.quality_gate_artifact_ref,)
            )
            self._save_checkpoint(
                state, "failed" if status == "failed" else "needs_fix"
            )
        browser_passed = bool(
            self.artifacts.get(final.browser_run_artifact_ref).content["passed"]
        )
        return VisionForgeRunResult(
            state.task_id,
            state.reference_image_artifact_ref,
            state.ui_spec_artifact_ref,
            state.current_implementation_artifact_ref,
            state.current_integration_artifact_ref,
            final.build_artifact_ref,
            final.actual_screenshot_artifact_ref,
            final.browser_run_artifact_ref,
            final.visual_review_artifact_ref,
            run_ref,
            changed_files,
            browser_passed,
            review.score,
            status != "completed",
            status,
            state.fix_attempts,
            final.quality_gate_artifact_ref,
            tuple(state.cycles),
        )

    def _save_checkpoint(self, state: _ExecutionState, phase: str) -> None:
        if self.checkpoint_store is None:
            return
        self.checkpoint_store.save(VisionForgeCheckpoint(
            state.checkpoint_id,
            state.task_id,
            phase,
            state.requirement,
            state.reference_image_artifact_ref,
            state.ui_spec_artifact_ref,
            state.current_implementation_artifact_ref,
            state.current_integration_artifact_ref,
            state.fix_attempts,
            self.max_fix_attempts,
            tuple(MappingProxyType(item.to_dict()) for item in state.cycles),
            tuple(MappingProxyType(dict(item)) for item in state.model_calls),
            self.artifacts,
            MappingProxyType(self._workspace_hashes()),
        ))

    def _workspace_hashes(self) -> dict[str, str]:
        return self.workspace.content_hashes(exclude_prefixes=self.HASH_EXCLUDES)

    def _changed_files(self, cycles: list[VisionForgeCycle]) -> tuple[str, ...]:
        result: list[str] = []
        for cycle in cycles:
            artifact = self.artifacts.get(cycle.integration_artifact_ref)
            if not isinstance(artifact.content, dict):
                continue
            for path in artifact.content.get("changed_files", []):
                if isinstance(path, str) and path not in result:
                    result.append(path)
        return tuple(result)

    @staticmethod
    def _model_call(role: str, response: ModelResponse) -> dict[str, object]:
        return {
            "role": role,
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
            "latency_ms": response.latency_ms,
        }
