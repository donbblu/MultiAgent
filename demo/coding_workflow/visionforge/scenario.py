from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from ..artifacts import Artifact, ArtifactStore
from ..harness import (
    ConvergenceAction,
    ConvergenceDecision,
    LifecycleController,
    ScenarioRoundPlan,
    ScenarioRegistration,
    ScenarioRunState,
    ScenarioRuntime,
    SQLiteScenarioRunStore,
    WorkerRegistry,
)
from ..integration import PatchIntegrator
from ..memory import MemoryManager
from ..models import TaskContext
from ..runtime_sqlite import SQLiteRuntimeStore
from ..workspace import ProjectWorkspace
from .agents import (
    RequirementAnalyst,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisualReviewer,
)
from .artifact_types import REFERENCE_IMAGE, RUN
from .contracts import UISpec, VisualReview
from .dag import (
    BrowserDagWorker,
    PatchIntegrationWorker,
    QualityGateWorker,
    UIAnalystWorker,
    VisualReviewWorker,
    WebDeveloperWorker,
    WebFixerWorker,
    _fix_graph,
    _initial_graph,
    _memory,
    _roles,
)
from .quality import VisionForgeQualityGate
from .runner import (
    BrowserTester,
    VisionForgeCycle,
    VisionForgeFeedbackPolicy,
    VisionForgeRunResult,
)


@dataclass
class WebVisualScenario:
    artifacts: ArtifactStore
    workspace: ProjectWorkspace
    integrator: PatchIntegrator
    analyst: RequirementAnalyst
    developer: VisionForgeDeveloper
    browser_tester: BrowserTester
    visual_reviewer: VisualReviewer
    reference_image_artifact_ref: str
    fixer: VisionForgeFixer | None = None
    quality_gate: VisionForgeQualityGate | None = None
    minimum_visual_score: float = 85
    max_rework_rounds: int = 2
    feedback_policy: VisionForgeFeedbackPolicy = (
        VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
    )
    acceptance_spec: UISpec | None = None
    name: str = "web_visual"
    roles: object = field(init=False)
    memory: MemoryManager = field(init=False)

    def __post_init__(self) -> None:
        if not 0 <= self.max_rework_rounds <= 2:
            raise ValueError("max_rework_rounds 必须在 0 到 2 之间")
        if not isinstance(self.feedback_policy, VisionForgeFeedbackPolicy):
            raise ValueError("feedback_policy 无效")
        if self.integrator.workspace.root != self.workspace.root:
            raise ValueError("场景与 PatchIntegrator 必须使用同一 Workspace")
        for component in (
            self.analyst, self.developer, self.browser_tester,
            self.visual_reviewer, self.fixer,
        ):
            component_store = getattr(component, "artifacts", self.artifacts)
            if component is not None and component_store is not self.artifacts:
                raise ValueError("VisionForge Worker 必须共享同一个 ArtifactStore")
        reference = self.artifacts.get(self.reference_image_artifact_ref)
        if reference.kind != REFERENCE_IMAGE:
            raise ValueError("场景输入必须是 reference_image Artifact")
        if self.quality_gate is None:
            self.quality_gate = VisionForgeQualityGate(
                self.artifacts, minimum_visual_score=self.minimum_visual_score
            )
        if self.quality_gate.artifacts is not self.artifacts:
            raise ValueError("Quality Gate 必须共享同一个 ArtifactStore")
        self.roles = _roles()
        self.memory = _memory(self.roles)

    def build_round(
        self, state: ScenarioRunState, lifecycle: LifecycleController
    ) -> ScenarioRoundPlan:
        if state.current_round == 0:
            graph = _initial_graph()
            initial = {"reference_image": self.reference_image_artifact_ref}
        else:
            previous = state.active_artifacts
            required = {
                "ui_spec", "implementation_plan", "browser_run", "visual_review",
            }
            missing = required - set(previous)
            if missing:
                raise RuntimeError(
                    f"Fix DAG 缺少上一轮 Artifact: {sorted(missing)}"
                )
            graph = _fix_graph(state.current_round)
            initial = {
                "reference_image": self.reference_image_artifact_ref,
                "ui_spec": previous["ui_spec"],
                "previous_implementation_plan": previous["implementation_plan"],
                "previous_browser_run": previous["browser_run"],
                "previous_visual_review": previous["visual_review"],
            }
        return ScenarioRoundPlan(
            graph,
            self._workers(state.current_round, lifecycle),
            self.roles,
            self.memory,
            initial,
        )

    def decide(
        self, state: ScenarioRunState, execution, artifacts: ArtifactStore
    ) -> ConvergenceDecision:
        current = execution.snapshot.artifacts
        gate_ref = current["quality_gate"]
        gate = artifacts.get(gate_ref).content
        passed = isinstance(gate, dict) and gate.get("passed") is True
        if state.current_round > 0 and state.round_artifacts:
            previous_plan = state.round_artifacts[-1]["implementation_plan"]
            current_plan = current["implementation_plan"]
            artifacts.supersede((previous_plan,), current_plan)
        cycle = self._cycle(state.current_round, current, passed)
        if passed:
            self._mark_verified(cycle, current["ui_spec"])
            return ConvergenceDecision(
                ConvergenceAction.COMPLETE, "VisionForge 质量门禁通过", gate_ref
            )
        self._mark_failed(cycle)
        if self.fixer is None or not self._should_fix(gate_ref):
            return ConvergenceDecision(
                ConvergenceAction.NEEDS_INPUT,
                "质量门禁未通过且当前反馈策略不允许自动修复",
                gate_ref,
            )
        return ConvergenceDecision(
            ConvergenceAction.REWORK, "质量门禁未通过，创建 Fix DAG", gate_ref
        )

    def finalize(
        self,
        state: ScenarioRunState,
        artifacts: ArtifactStore,
        decision: ConvergenceDecision,
    ) -> tuple[str, VisionForgeRunResult]:
        cycles = tuple(
            self._cycle(
                index,
                round_artifacts,
                self._gate_passed(round_artifacts["quality_gate"]),
            )
            for index, round_artifacts in enumerate(state.round_artifacts)
        )
        final = cycles[-1]
        latest = state.round_artifacts[-1]
        status = {
            ConvergenceAction.COMPLETE: "completed",
            ConvergenceAction.FAIL: "failed",
            ConvergenceAction.NEEDS_INPUT: "needs_fix",
        }[decision.action]
        review = VisualReview.from_dict(
            artifacts.get(final.visual_review_artifact_ref).content
        )
        changed_files = self._changed_files(cycles)
        model_calls = self._model_calls(cycles, latest["ui_spec"])
        content = {
            "task_id": state.task_id,
            "stage": status,
            "status": status,
            "engine": "scenario_dag",
            "scenario": self.name,
            "artifact_chain": {
                "reference_image": self.reference_image_artifact_ref,
                "ui_spec": latest["ui_spec"],
                "implementation_plan": latest["implementation_plan"],
                "integration_result": latest["integration_result"],
                "build_result": final.build_artifact_ref,
                "actual_screenshot": final.actual_screenshot_artifact_ref,
                "browser_run": final.browser_run_artifact_ref,
                "visual_review": final.visual_review_artifact_ref,
                "quality_gate": final.quality_gate_artifact_ref,
            },
            "cycles": [item.to_dict() for item in cycles],
            "changed_files": list(changed_files),
            "browser_passed": bool(
                artifacts.get(final.browser_run_artifact_ref).content["passed"]
            ),
            "visual_score": review.score,
            "needs_fix": status != "completed",
            "fix_attempts": state.current_round,
            "minimum_visual_score": self.quality_gate.minimum_visual_score,
            "model_calls": model_calls,
            "total_tokens": sum(int(item["total_tokens"]) for item in model_calls),
            "total_model_latency_ms": sum(
                int(item["latency_ms"]) for item in model_calls
            ),
        }
        run_ref = artifacts.put(Artifact.create(
            "visionforge-scenario-run",
            state.task_id,
            content,
            kind=RUN,
            metadata={
                "engine": "scenario_dag", "scenario": self.name,
                "stage": status, "fix_attempts": state.current_round,
            },
        ))
        if status == "completed":
            artifacts.mark_verified((run_ref,), (final.quality_gate_artifact_ref,))
        else:
            artifacts.mark_failed((run_ref,), (final.quality_gate_artifact_ref,))
        return run_ref, self._result(run_ref, content)

    def restore_result(
        self, result_artifact_ref: str, artifacts: ArtifactStore
    ) -> VisionForgeRunResult:
        artifact = artifacts.get(result_artifact_ref)
        if artifact.kind != RUN or not isinstance(
            artifact.content, dict
        ):
            raise RuntimeError("场景结果 Artifact 无效")
        return self._result(result_artifact_ref, artifact.content)

    def _workers(
        self, round_index: int, lifecycle: LifecycleController
    ) -> WorkerRegistry:
        registry = WorkerRegistry()
        registry.register("ui_analyst", UIAnalystWorker(self.analyst))
        registry.register(
            "web_developer",
            WebDeveloperWorker(
                self.developer, self.workspace,
                tuple(self.integrator.allowed_paths), self.acceptance_spec,
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
        registry.register(
            "quality_gate", QualityGateWorker(self.quality_gate, round_index)
        )
        if self.fixer is not None:
            registry.register(
                "web_fixer",
                WebFixerWorker(
                    self.fixer, self.workspace, tuple(self.integrator.allowed_paths),
                    round_index, self.feedback_policy,
                ),
            )
        return registry

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

    def _gate_passed(self, gate_ref: str) -> bool:
        gate = self.artifacts.get(gate_ref).content
        return isinstance(gate, dict) and gate.get("passed") is True

    @staticmethod
    def _cycle(
        round_index: int,
        refs: Mapping[str, str],
        passed: bool,
    ) -> VisionForgeCycle:
        return VisionForgeCycle(
            round_index, refs["implementation_plan"], refs["integration_result"],
            refs["build_result"], refs["actual_screenshot"], refs["browser_run"],
            refs["visual_review"], refs["quality_gate"], passed,
        )

    def _mark_failed(self, cycle: VisionForgeCycle) -> None:
        self.artifacts.mark_failed(
            (
                cycle.implementation_artifact_ref,
                cycle.integration_artifact_ref,
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

    def _mark_verified(self, cycle: VisionForgeCycle, ui_spec_ref: str) -> None:
        self.artifacts.mark_verified(
            (
                ui_spec_ref, cycle.implementation_artifact_ref,
                cycle.integration_artifact_ref, cycle.build_artifact_ref,
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

    def _changed_files(
        self, cycles: tuple[VisionForgeCycle, ...]
    ) -> tuple[str, ...]:
        result: list[str] = []
        for cycle in cycles:
            content = self.artifacts.get(cycle.integration_artifact_ref).content
            if isinstance(content, dict):
                for path in content.get("changed_files", []):
                    if isinstance(path, str) and path not in result:
                        result.append(path)
        return tuple(result)

    def _model_calls(
        self, cycles: tuple[VisionForgeCycle, ...], ui_spec_ref: str
    ) -> list[dict[str, object]]:
        references = [("ui_analyst", ui_spec_ref)]
        for index, cycle in enumerate(cycles):
            references.append((
                "developer" if index == 0 else "fixer",
                cycle.implementation_artifact_ref,
            ))
            references.append(("visual_reviewer", cycle.visual_review_artifact_ref))
        result = []
        for role, reference in references:
            metadata = self.artifacts.get(reference).metadata
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

    @staticmethod
    def _result(
        run_ref: str, content: Mapping[str, object]
    ) -> VisionForgeRunResult:
        chain = content["artifact_chain"]
        cycles = tuple(
            VisionForgeCycle.from_dict(item) for item in content["cycles"]
        )
        return VisionForgeRunResult(
            str(content["task_id"]),
            str(chain["reference_image"]), str(chain["ui_spec"]),
            str(chain["implementation_plan"]), str(chain["integration_result"]),
            str(chain["build_result"]), str(chain["actual_screenshot"]),
            str(chain["browser_run"]), str(chain["visual_review"]), run_ref,
            tuple(str(item) for item in content["changed_files"]),
            bool(content["browser_passed"]), float(content["visual_score"]),
            bool(content["needs_fix"]), str(content["status"]),
            int(content["fix_attempts"]), str(chain["quality_gate"]), cycles,
        )


@dataclass
class VisionForgeScenarioRunner:
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
    runtime_path: Path | None = None
    checkpoint_hook: Callable[[str, ScenarioRunState], None] | None = None
    scenario_registration: ScenarioRegistration | None = None

    def run(
        self,
        *,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
        run_id: str | None = None,
    ) -> VisionForgeRunResult:
        reference = self.artifacts.get(reference_image_artifact_ref)
        if reference.task_id != task_id:
            raise ValueError("参考图 Artifact 与任务不匹配")
        if (
            self.scenario_registration is not None
            and self.scenario_registration.reference
            != "visionforge:web_visual"
        ):
            raise ValueError(
                "VisionForgeScenarioRunner 只接受 visionforge:web_visual"
            )
        path = self.runtime_path or (
            self.workspace.root / ".runtime" / "visionforge-scenario.sqlite3"
        )
        task = TaskContext(
            task_id, requirement, ["通过浏览器和视觉质量门禁"],
            user_request=requirement, project_root=str(self.workspace.root),
            allowed_paths=list(self.integrator.allowed_paths),
        )
        profile_args = (
            self.artifacts, self.workspace, self.integrator,
            self.analyst, self.developer, self.browser_tester,
            self.visual_reviewer, reference_image_artifact_ref,
        )
        profile_options = {
            "fixer": self.fixer,
            "quality_gate": self.quality_gate,
            "minimum_visual_score": self.minimum_visual_score,
            "max_rework_rounds": self.max_fix_attempts,
            "feedback_policy": self.feedback_policy,
            "acceptance_spec": self.acceptance_spec,
        }
        profile = (
            self.scenario_registration.create(
                *profile_args, **profile_options
            )
            if self.scenario_registration is not None
            else WebVisualScenario(*profile_args, **profile_options)
        )
        runtime = ScenarioRuntime(
            runtime_store=SQLiteRuntimeStore(path),
            scenario_store=SQLiteScenarioRunStore(path),
            workspace_hashes_provider=lambda: self.workspace.content_hashes(
                exclude_prefixes=(".runtime", "node_modules", "dist")
            ),
            max_workers=3,
            checkpoint_hook=self.checkpoint_hook,
        )
        result = runtime.run(
            run_id=run_id or f"visionforge:{task_id}",
            task=task,
            profile=profile,
            artifacts=self.artifacts,
        )
        if not isinstance(result, VisionForgeRunResult):
            raise TypeError("WebVisualScenario 返回了错误的结果类型")
        return result
