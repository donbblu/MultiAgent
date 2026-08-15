from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ..artifacts import ArtifactStore
from ..integration import PatchIntegrator
from ..model import ModelClient, ModelRequest, ModelResponse
from ..models import ImplementationPlan
from ..workspace import ProjectWorkspace
from .agents import (
    RequirementAnalyst,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisualReviewer,
)
from .assets import ImageAssetStore
from .browser import BrowserProcessRunner, BrowserProjectConfig, PlaywrightBrowserTester
from .evaluation import (
    EvaluationConfig,
    EvaluationTask,
    EvaluationTrialResult,
    EvaluationVariant,
    _safe_error,
)
from .runner import (
    VisionForgeFeedbackPolicy,
    VisionForgeRunner,
)


class EvaluationBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluationBudgetSnapshot:
    max_model_calls: int
    max_total_tokens: int
    attempted_model_calls: int
    observed_total_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_total_tokens": self.max_total_tokens,
            "attempted_model_calls": self.attempted_model_calls,
            "observed_total_tokens": self.observed_total_tokens,
        }


class EvaluationModelBudget:
    """在每次模型调用边界执行共享停止条件。"""

    def __init__(self, *, max_model_calls: int, max_total_tokens: int) -> None:
        if max_model_calls <= 0 or max_total_tokens <= 0:
            raise ValueError("模型调用和 Token 预算必须大于 0")
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens
        self._attempted_model_calls = 0
        self._observed_total_tokens = 0
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._attempted_model_calls >= self.max_model_calls:
                raise EvaluationBudgetExceeded("已达到模型调用次数上限")
            if self._observed_total_tokens >= self.max_total_tokens:
                raise EvaluationBudgetExceeded("已达到模型 Token 上限")
            self._attempted_model_calls += 1

    def after_call(self, response: ModelResponse) -> None:
        with self._lock:
            self._observed_total_tokens += response.usage.total_tokens

    def snapshot(self) -> EvaluationBudgetSnapshot:
        with self._lock:
            return EvaluationBudgetSnapshot(
                self.max_model_calls,
                self.max_total_tokens,
                self._attempted_model_calls,
                self._observed_total_tokens,
            )


class BudgetedModelClient:
    def __init__(self, client: ModelClient, budget: EvaluationModelBudget) -> None:
        self.client = client
        self.budget = budget

    @property
    def capabilities(self):
        return self.client.capabilities

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        self.budget.before_call()
        response = self.client.generate_structured(request)
        self.budget.after_call(response)
        return response

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        request = ModelRequest.from_text_messages(messages)
        return dict(self.generate_structured(request).data)


class RuntimeEvaluationTrialExecutor:
    """用隔离 Vue 副本执行一个真实评测试验并持久化证据。"""

    POLICY_BY_VARIANT = MappingProxyType({
        EvaluationVariant.LLM_ONCE: VisionForgeFeedbackPolicy.NONE,
        EvaluationVariant.LLM_BROWSER: VisionForgeFeedbackPolicy.BROWSER_ONLY,
        EvaluationVariant.LLM_BROWSER_VLM: (
            VisionForgeFeedbackPolicy.BROWSER_AND_VISUAL
        ),
    })

    def __init__(
        self,
        *,
        template_root: Path,
        runtime_root: Path,
        process_runner: BrowserProcessRunner,
        text_client: ModelClient,
        vision_client: ModelClient,
        budget: EvaluationModelBudget,
    ) -> None:
        self.template_root = template_root.resolve()
        self.runtime_root = runtime_root.resolve()
        if not self.template_root.is_dir():
            raise ValueError("VisionForge Vue 模板不存在")
        BrowserProjectConfig.load(self.template_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.process_runner = process_runner
        self.text_client = BudgetedModelClient(text_client, budget)
        self.vision_client = BudgetedModelClient(vision_client, budget)
        self.budget = budget

    def execute(
        self,
        *,
        task: EvaluationTask,
        variant: EvaluationVariant,
        repetition: int,
        reference_image_path: Path,
        config: EvaluationConfig,
    ) -> EvaluationTrialResult:
        trial_name = self._safe_name(
            f"r{repetition + 1}-{task.task_id}-{variant.value}"
        )
        trial_root = (self.runtime_root / "trials" / trial_name).resolve()
        trials_root = (self.runtime_root / "trials").resolve()
        if not trial_root.is_relative_to(trials_root):
            raise ValueError("评测试验目录越过 Runtime 边界")
        if trial_root.exists():
            raise ValueError(f"评测试验目录已存在: {trial_name}")
        project_root = trial_root / "project"
        self._prepare_project(project_root)
        artifacts = ArtifactStore()
        images = ImageAssetStore(trial_root / "assets")
        reference_ref, _ = images.create_artifact(
            artifacts,
            name="reference-image",
            task_id=task.task_id,
            data=reference_image_path.read_bytes(),
        )
        allowed_paths = self._allowed_paths(project_root)
        workspace = ProjectWorkspace(project_root)
        policy = self.POLICY_BY_VARIANT[variant]
        fixer = (
            None
            if policy is VisionForgeFeedbackPolicy.NONE
            else VisionForgeFixer(self.text_client, artifacts)
        )
        runner = VisionForgeRunner(
            artifacts=artifacts,
            workspace=workspace,
            integrator=PatchIntegrator(workspace, allowed_paths),
            analyst=RequirementAnalyst(self.vision_client, artifacts, images),
            developer=VisionForgeDeveloper(self.text_client, artifacts),
            browser_tester=PlaywrightBrowserTester(
                project_root,
                self.process_runner,
                artifacts,
                images,
                trial_root / "browser-runtime",
            ),
            visual_reviewer=VisualReviewer(
                self.vision_client, artifacts, images
            ),
            fixer=fixer,
            minimum_visual_score=config.minimum_visual_score,
            max_fix_attempts=(
                0
                if policy is VisionForgeFeedbackPolicy.NONE
                else config.max_fix_attempts
            ),
            feedback_policy=policy,
            acceptance_spec=task.acceptance_spec,
        )
        started = time.monotonic()
        budget_before = self.budget.snapshot()
        bundle_path = trial_root / "artifacts.json"
        try:
            result = runner.run(
                task_id=task.task_id,
                requirement=task.requirement,
                reference_image_artifact_ref=reference_ref,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            trial = EvaluationTrialResult.from_visionforge_run(
                task_id=task.task_id,
                variant=variant,
                repetition=repetition,
                result=result,
                artifacts=artifacts,
                minimum_visual_score=config.minimum_visual_score,
                duration_ms=duration_ms,
            )
            refs = dict(trial.artifact_refs)
            refs["artifact_bundle"] = str(bundle_path)
            refs["project_root"] = str(project_root)
            return EvaluationTrialResult(
                trial.task_id,
                trial.variant,
                trial.repetition,
                trial.build_passed,
                trial.dom_interaction_passed,
                trial.visual_passed,
                trial.first_passed,
                trial.auto_fix_succeeded,
                trial.fix_rounds,
                trial.visual_score,
                trial.total_tokens,
                trial.duration_ms,
                trial.human_interventions,
                trial.status,
                trial.error,
                refs,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            budget_after = self.budget.snapshot()
            return EvaluationTrialResult(
                task.task_id,
                variant,
                repetition,
                False,
                False,
                False,
                False,
                False,
                0,
                0,
                max(
                    0,
                    budget_after.observed_total_tokens
                    - budget_before.observed_total_tokens,
                ),
                duration_ms,
                1,
                "failed",
                _safe_error(exc),
                {
                    "artifact_bundle": str(bundle_path),
                    "project_root": str(project_root),
                },
            )
        finally:
            self._write_artifact_bundle(
                bundle_path,
                artifacts,
                task=task,
                variant=variant,
                repetition=repetition,
                budget=self.budget.snapshot(),
            )

    @staticmethod
    def _safe_name(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
        if not normalized:
            raise ValueError("评测试验名称无效")
        return normalized[:160]

    @staticmethod
    def _allowed_paths(project_root: Path) -> tuple[str, ...]:
        raw = json.loads(
            (project_root / "visionforge.template.json").read_text(encoding="utf-8")
        )
        allowed = raw.get("allowed_paths")
        if not isinstance(allowed, list) or not allowed or not all(
            isinstance(item, str) and item for item in allowed
        ):
            raise ValueError("Vue 模板 allowed_paths 无效")
        return tuple(allowed)

    def _prepare_project(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            self.template_root,
            destination,
            symlinks=True,
            copy_function=self._link_or_copy,
        )

    @staticmethod
    def _link_or_copy(source: str, destination: str) -> str:
        try:
            os.link(source, destination)
            return destination
        except OSError:
            return shutil.copy2(source, destination)

    @classmethod
    def _write_artifact_bundle(
        cls,
        output: Path,
        artifacts: ArtifactStore,
        *,
        task: EvaluationTask,
        variant: EvaluationVariant,
        repetition: int,
        budget: EvaluationBudgetSnapshot,
    ) -> None:
        items = []
        for artifact, validation in artifacts.snapshot():
            items.append({
                "reference": f"artifact://{artifact.artifact_id}",
                "name": artifact.name,
                "task_id": artifact.task_id,
                "kind": artifact.kind,
                "content": cls._json_content(artifact.content),
                "metadata": dict(artifact.metadata),
                "created_at": artifact.created_at,
                "validation": {
                    "state": validation.state.value,
                    "verification_refs": list(validation.verification_refs),
                    "superseded_by": validation.superseded_by,
                },
            })
        payload = {
            "schema_version": "1.0",
            "task_id": task.task_id,
            "variant": variant.value,
            "repetition": repetition,
            "budget": budget.to_dict(),
            "artifacts": items,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)

    @staticmethod
    def _json_content(content: object) -> object:
        if isinstance(content, ImplementationPlan):
            return {
                "summary": content.summary,
                "changes": [
                    {
                        "path": item.path,
                        "content": item.content,
                        "reason": item.reason,
                    }
                    for item in content.changes
                ],
                "suggested_checks": content.suggested_checks,
            }
        json.dumps(content, ensure_ascii=False)
        return content
