from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from ..artifacts import ArtifactStore
from .browser import BrowserProcessRunner
from .contracts import BrowserRunResult, UISpec, VisualReview
from .runner import VisionForgeCycle, VisionForgeRunResult


class VisionForgeEvaluationError(ValueError):
    pass


class EvaluationVariant(str, Enum):
    LLM_ONCE = "llm_once"
    LLM_BROWSER = "llm_browser_feedback"
    LLM_BROWSER_VLM = "llm_browser_vlm"

    @classmethod
    def ordered(cls) -> tuple["EvaluationVariant", ...]:
        return (cls.LLM_ONCE, cls.LLM_BROWSER, cls.LLM_BROWSER_VLM)


@dataclass(frozen=True)
class EvaluationTask:
    task_id: str
    title: str
    requirement: str
    reference_source: Path
    acceptance_spec: UISpec
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.title.strip() or not self.requirement.strip():
            raise VisionForgeEvaluationError("评测任务 ID、标题和需求不能为空")
        if not self.reference_source.is_file() or self.reference_source.suffix != ".html":
            raise VisionForgeEvaluationError("评测参考源必须是存在的本地 HTML 文件")
        if len(set(self.tags)) != len(self.tags) or any(not item.strip() for item in self.tags):
            raise VisionForgeEvaluationError("评测任务 tags 必须是非空且不重复的字符串")


@dataclass(frozen=True)
class EvaluationSuite:
    suite_id: str
    version: str
    tasks: tuple[EvaluationTask, ...]
    content_sha256: str

    SCHEMA_VERSION = "1.0"

    @classmethod
    def load(cls, manifest_path: Path) -> "EvaluationSuite":
        manifest = manifest_path.resolve()
        try:
            raw = manifest.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise VisionForgeEvaluationError(f"无法读取评测任务集: {exc}") from exc
        if not isinstance(value, dict):
            raise VisionForgeEvaluationError("评测任务集必须是 JSON 对象")
        allowed = {"schema_version", "suite_id", "version", "tasks"}
        unknown = set(value) - allowed
        if unknown:
            raise VisionForgeEvaluationError(f"评测任务集包含未知字段: {sorted(unknown)}")
        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise VisionForgeEvaluationError("不支持的评测任务集版本")
        suite_id = _required_text(value.get("suite_id"), "suite_id")
        version = _required_text(value.get("version"), "version")
        task_values = value.get("tasks")
        if not isinstance(task_values, list) or len(task_values) < 3:
            raise VisionForgeEvaluationError("固定评测任务集至少需要 3 个页面任务")
        root = manifest.parent
        tasks = tuple(cls._load_task(root, item) for item in task_values)
        ids = [item.task_id for item in tasks]
        if len(ids) != len(set(ids)):
            raise VisionForgeEvaluationError("评测任务 ID 不能重复")
        viewports = {item.acceptance_spec.viewport for item in tasks}
        if len(viewports) != 1:
            raise VisionForgeEvaluationError("同一评测任务集必须固定统一 viewport")
        digest = hashlib.sha256(raw)
        for task in sorted(tasks, key=lambda item: item.task_id):
            digest.update(task.task_id.encode("utf-8"))
            digest.update(task.reference_source.read_bytes())
            digest.update(json.dumps(
                task.acceptance_spec.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"))
        return cls(suite_id, version, tasks, digest.hexdigest())

    @classmethod
    def _load_task(cls, root: Path, value: object) -> EvaluationTask:
        if not isinstance(value, dict):
            raise VisionForgeEvaluationError("评测任务必须是对象")
        allowed = {
            "task_id", "title", "requirement", "reference_source",
            "acceptance_spec", "tags",
        }
        unknown = set(value) - allowed
        if unknown:
            raise VisionForgeEvaluationError(f"评测任务包含未知字段: {sorted(unknown)}")
        reference = _safe_child(root, _required_text(
            value.get("reference_source"), "reference_source"
        ))
        spec_path = _safe_child(root, _required_text(
            value.get("acceptance_spec"), "acceptance_spec"
        ))
        try:
            spec = UISpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise VisionForgeEvaluationError(f"固定验收 Spec 无效: {exc}") from exc
        tags = value.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
            raise VisionForgeEvaluationError("评测任务 tags 必须是字符串数组")
        return EvaluationTask(
            _required_text(value.get("task_id"), "task_id"),
            _required_text(value.get("title"), "title"),
            _required_text(value.get("requirement"), "requirement"),
            reference,
            spec,
            tuple(tags),
        )


@dataclass(frozen=True)
class EvaluationConfig:
    model_provider: str
    model_name: str
    prompt_version: str
    repetitions: int = 1
    max_fix_attempts: int = 2
    minimum_visual_score: float = 85
    browser_engine: str = "chromium"
    browser_version: str = "runtime-managed"
    playwright_version: str = "1.62.0"
    temperature: float = 0.0
    max_output_tokens: int = 12000
    schema_version: str = "1.0"
    vision_model_provider: str = ""
    vision_model_name: str = ""
    max_model_calls: int = 51
    max_total_tokens: int = 600_000
    vision_max_output_tokens: int = 8_000

    def __post_init__(self) -> None:
        for field_name in (
            "model_provider", "model_name", "prompt_version", "browser_engine",
            "browser_version", "playwright_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise VisionForgeEvaluationError(f"{field_name} 不能为空")
        if self.schema_version != "1.0":
            raise VisionForgeEvaluationError("不支持的评测配置版本")
        if not 1 <= self.repetitions <= 20:
            raise VisionForgeEvaluationError("repetitions 必须在 1 到 20 之间")
        if not 0 <= self.max_fix_attempts <= 2:
            raise VisionForgeEvaluationError("max_fix_attempts 必须在 0 到 2 之间")
        if not 0 <= self.minimum_visual_score <= 100:
            raise VisionForgeEvaluationError("minimum_visual_score 必须在 0 到 100 之间")
        if not 0 <= self.temperature <= 2:
            raise VisionForgeEvaluationError("temperature 必须在 0 到 2 之间")
        if not 1 <= self.max_output_tokens <= 100_000:
            raise VisionForgeEvaluationError("max_output_tokens 必须在 1 到 100000 之间")
        if self.max_model_calls <= 0 or self.max_total_tokens <= 0:
            raise VisionForgeEvaluationError("模型调用和总 Token 预算必须大于 0")
        if not 1 <= self.vision_max_output_tokens <= 100_000:
            raise VisionForgeEvaluationError(
                "vision_max_output_tokens 必须在 1 到 100000 之间"
            )
        object.__setattr__(
            self,
            "vision_model_provider",
            self.vision_model_provider.strip() or self.model_provider,
        )
        object.__setattr__(
            self,
            "vision_model_name",
            self.vision_model_name.strip() or self.model_name,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "models": {
                "text": {
                    "provider": self.model_provider,
                    "model": self.model_name,
                },
                "vision": {
                    "provider": self.vision_model_provider,
                    "model": self.vision_model_name,
                },
            },
            "prompt_version": self.prompt_version,
            "repetitions": self.repetitions,
            "max_fix_attempts": self.max_fix_attempts,
            "minimum_visual_score": self.minimum_visual_score,
            "browser_engine": self.browser_engine,
            "browser_version": self.browser_version,
            "playwright_version": self.playwright_version,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "max_model_calls": self.max_model_calls,
            "max_total_tokens": self.max_total_tokens,
            "vision_max_output_tokens": self.vision_max_output_tokens,
            "ui_spec_schema_version": UISpec.CURRENT_VERSION,
            "visual_review_schema_version": VisualReview.CURRENT_VERSION,
        }


@dataclass(frozen=True)
class EvaluationCallEstimate:
    task_count: int
    repetitions: int
    max_fix_attempts: int
    text_calls: int
    vision_calls: int
    total_calls: int
    by_variant: Mapping[str, Mapping[str, int]]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_count": self.task_count,
            "repetitions": self.repetitions,
            "max_fix_attempts": self.max_fix_attempts,
            "text_calls": self.text_calls,
            "vision_calls": self.vision_calls,
            "total_calls": self.total_calls,
            "by_variant": {
                key: dict(value) for key, value in self.by_variant.items()
            },
        }


def estimate_model_calls(
    *,
    task_count: int,
    repetitions: int,
    max_fix_attempts: int,
) -> EvaluationCallEstimate:
    if task_count <= 0 or repetitions <= 0:
        raise VisionForgeEvaluationError("任务数和重复次数必须大于 0")
    if not 0 <= max_fix_attempts <= 2:
        raise VisionForgeEvaluationError("max_fix_attempts 必须在 0 到 2 之间")
    per_trial = {
        EvaluationVariant.LLM_ONCE.value: {"text": 1, "vision": 2},
        EvaluationVariant.LLM_BROWSER.value: {
            "text": 1 + max_fix_attempts,
            "vision": 2 + max_fix_attempts,
        },
        EvaluationVariant.LLM_BROWSER_VLM.value: {
            "text": 1 + max_fix_attempts,
            "vision": 2 + max_fix_attempts,
        },
    }
    multiplier = task_count * repetitions
    text_calls = multiplier * sum(item["text"] for item in per_trial.values())
    vision_calls = multiplier * sum(item["vision"] for item in per_trial.values())
    return EvaluationCallEstimate(
        task_count,
        repetitions,
        max_fix_attempts,
        text_calls,
        vision_calls,
        text_calls + vision_calls,
        MappingProxyType({
            key: MappingProxyType(dict(value)) for key, value in per_trial.items()
        }),
    )


class ReferenceImageRenderer:
    """把版本化 HTML 参考源渲染为模型实际接收的固定 PNG。"""

    def __init__(
        self,
        process_runner: BrowserProcessRunner,
        renderer_script: Path,
    ) -> None:
        self.process_runner = process_runner
        self.renderer_script = renderer_script.resolve()
        if not self.renderer_script.is_file():
            raise VisionForgeEvaluationError("参考图渲染脚本不存在")

    def render(self, task: EvaluationTask, output_path: Path) -> Path:
        output = output_path.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        viewport = task.acceptance_spec.viewport
        execution = self.process_runner.run(
            (
                "node", str(self.renderer_script),
                "--source", str(task.reference_source),
                "--output", str(output),
                "--width", str(viewport.width),
                "--height", str(viewport.height),
                "--scale", str(viewport.device_scale_factor),
            ),
            cwd=self.renderer_script.parent,
            timeout_seconds=30,
        )
        if not execution.passed:
            raise VisionForgeEvaluationError(
                "参考图渲染失败: " + (execution.stderr or execution.stdout)[-2000:]
            )
        try:
            header = output.read_bytes()[:8]
        except OSError as exc:
            raise VisionForgeEvaluationError(f"参考图渲染结果不存在: {exc}") from exc
        if header != b"\x89PNG\r\n\x1a\n":
            raise VisionForgeEvaluationError("参考图渲染结果不是 PNG")
        return output


@dataclass(frozen=True)
class EvaluationTrialResult:
    task_id: str
    variant: EvaluationVariant
    repetition: int
    build_passed: bool
    dom_interaction_passed: bool
    visual_passed: bool
    first_passed: bool
    auto_fix_succeeded: bool
    fix_rounds: int
    visual_score: float
    total_tokens: int
    duration_ms: int
    human_interventions: int = 0
    status: str = "completed"
    error: str = ""
    artifact_refs: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.task_id.strip() or self.repetition < 0:
            raise VisionForgeEvaluationError("评测记录的任务 ID 或重复序号无效")
        if not 0 <= self.fix_rounds <= 2:
            raise VisionForgeEvaluationError("fix_rounds 必须在 0 到 2 之间")
        if not 0 <= self.visual_score <= 100:
            raise VisionForgeEvaluationError("visual_score 必须在 0 到 100 之间")
        if min(self.total_tokens, self.duration_ms, self.human_interventions) < 0:
            raise VisionForgeEvaluationError("Token、耗时和人工介入不能为负数")
        if self.auto_fix_succeeded and (self.first_passed or self.fix_rounds == 0):
            raise VisionForgeEvaluationError("自动修复成功必须发生在首次失败且执行过修复后")
        if self.status not in {"completed", "failed"}:
            raise VisionForgeEvaluationError("评测记录状态无效")
        object.__setattr__(self, "artifact_refs", MappingProxyType(dict(self.artifact_refs)))

    @property
    def delivery_passed(self) -> bool:
        return self.build_passed and self.dom_interaction_passed and self.visual_passed

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "variant": self.variant.value,
            "repetition": self.repetition,
            "status": self.status,
            "build_passed": self.build_passed,
            "dom_interaction_passed": self.dom_interaction_passed,
            "visual_passed": self.visual_passed,
            "delivery_passed": self.delivery_passed,
            "first_passed": self.first_passed,
            "auto_fix_succeeded": self.auto_fix_succeeded,
            "fix_rounds": self.fix_rounds,
            "visual_score": self.visual_score,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "human_interventions": self.human_interventions,
            "error": self.error,
            "artifact_refs": dict(self.artifact_refs),
        }

    @classmethod
    def from_visionforge_run(
        cls,
        *,
        task_id: str,
        variant: EvaluationVariant,
        repetition: int,
        result: VisionForgeRunResult,
        artifacts: ArtifactStore,
        minimum_visual_score: float,
        duration_ms: int,
    ) -> "EvaluationTrialResult":
        if result.task_id != task_id or not result.cycles:
            raise VisionForgeEvaluationError("VisionForge Run 与评测任务不匹配")
        first = _cycle_score(result.cycles[0], artifacts, minimum_visual_score)
        final = _cycle_score(result.cycles[-1], artifacts, minimum_visual_score)
        run = artifacts.get(result.run_artifact_ref)
        if not isinstance(run.content, dict):
            raise VisionForgeEvaluationError("VisionForge Run Artifact 内容无效")
        return cls(
            task_id,
            variant,
            repetition,
            final[0],
            final[1],
            final[2],
            all(first),
            not all(first) and all(final) and result.fix_attempts > 0,
            result.fix_attempts,
            result.visual_score,
            int(run.content.get("total_tokens", 0)),
            duration_ms,
            artifact_refs={
                "run": result.run_artifact_ref,
                "quality_gate": result.quality_gate_artifact_ref,
                "browser_run": result.browser_run_artifact_ref,
                "visual_review": result.visual_review_artifact_ref,
                "actual_screenshot": result.actual_screenshot_artifact_ref,
            },
        )


class EvaluationTrialExecutor(Protocol):
    def execute(
        self,
        *,
        task: EvaluationTask,
        variant: EvaluationVariant,
        repetition: int,
        reference_image_path: Path,
        config: EvaluationConfig,
    ) -> EvaluationTrialResult: ...


@dataclass(frozen=True)
class EvaluationReport:
    schema_version: str
    suite_id: str
    suite_version: str
    suite_content_sha256: str
    config: Mapping[str, object]
    trials: tuple[EvaluationTrialResult, ...]
    variants: Mapping[str, Mapping[str, object]]
    created_at_epoch_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suite": {
                "suite_id": self.suite_id,
                "version": self.suite_version,
                "content_sha256": self.suite_content_sha256,
            },
            "config": dict(self.config),
            "variants": {key: dict(value) for key, value in self.variants.items()},
            "trials": [item.to_dict() for item in self.trials],
            "created_at_epoch_ms": self.created_at_epoch_ms,
        }

    def write(self, output_path: Path) -> None:
        output = output_path.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)


class VisionForgeEvaluator:
    """以相同任务、配置和最终评分口径顺序比较三种交付策略。"""

    def __init__(
        self,
        suite: EvaluationSuite,
        config: EvaluationConfig,
        renderer: ReferenceImageRenderer,
        executor: EvaluationTrialExecutor,
        runtime_root: Path,
    ) -> None:
        self.suite = suite
        self.config = config
        self.renderer = renderer
        self.executor = executor
        self.runtime_root = runtime_root.resolve()

    def run(self) -> EvaluationReport:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        references = {
            task.task_id: self.renderer.render(
                task, self.runtime_root / "references" / f"{task.task_id}.png"
            )
            for task in self.suite.tasks
        }
        trials: list[EvaluationTrialResult] = []
        for repetition in range(self.config.repetitions):
            for task in self.suite.tasks:
                for variant in EvaluationVariant.ordered():
                    started = time.monotonic()
                    try:
                        trial = self.executor.execute(
                            task=task,
                            variant=variant,
                            repetition=repetition,
                            reference_image_path=references[task.task_id],
                            config=self.config,
                        )
                        if (
                            trial.task_id != task.task_id
                            or trial.variant is not variant
                            or trial.repetition != repetition
                        ):
                            raise VisionForgeEvaluationError("评测执行器返回了错误的任务身份")
                    except Exception as exc:
                        trial = EvaluationTrialResult(
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
                            0,
                            int((time.monotonic() - started) * 1000),
                            1,
                            "failed",
                            _safe_error(exc),
                        )
                    trials.append(trial)
        variants = MappingProxyType({
            variant.value: MappingProxyType(_aggregate(
                [item for item in trials if item.variant is variant]
            ))
            for variant in EvaluationVariant.ordered()
        })
        config_snapshot = self.config.to_dict()
        config_snapshot["viewport"] = (
            self.suite.tasks[0].acceptance_spec.viewport.to_dict()
        )
        return EvaluationReport(
            "1.0",
            self.suite.suite_id,
            self.suite.version,
            self.suite.content_sha256,
            MappingProxyType(config_snapshot),
            tuple(trials),
            variants,
            int(time.time() * 1000),
        )


def _aggregate(trials: list[EvaluationTrialResult]) -> dict[str, object]:
    count = len(trials)
    if not count:
        raise VisionForgeEvaluationError("每种评测方案都必须有试验记录")
    attempted_fixes = [item for item in trials if item.fix_rounds > 0]
    return {
        "trial_count": count,
        "build_success_rate": _rate(item.build_passed for item in trials),
        "dom_interaction_success_rate": _rate(
            item.dom_interaction_passed for item in trials
        ),
        "visual_acceptance_rate": _rate(item.visual_passed for item in trials),
        "delivery_success_rate": _rate(item.delivery_passed for item in trials),
        "first_pass_rate": _rate(item.first_passed for item in trials),
        "auto_fix_success_rate": (
            _rate(item.auto_fix_succeeded for item in attempted_fixes)
            if attempted_fixes else None
        ),
        "average_fix_rounds": round(sum(item.fix_rounds for item in trials) / count, 4),
        "average_visual_score": round(sum(item.visual_score for item in trials) / count, 4),
        "average_tokens": round(sum(item.total_tokens for item in trials) / count, 4),
        "average_duration_ms": round(sum(item.duration_ms for item in trials) / count, 4),
        "human_interventions": sum(item.human_interventions for item in trials),
    }


def _cycle_score(
    cycle: VisionForgeCycle,
    artifacts: ArtifactStore,
    minimum_visual_score: float,
) -> tuple[bool, bool, bool]:
    build = artifacts.get(cycle.build_artifact_ref)
    browser_artifact = artifacts.get(cycle.browser_run_artifact_ref)
    review_artifact = artifacts.get(cycle.visual_review_artifact_ref)
    if not isinstance(build.content, dict) or not isinstance(browser_artifact.content, dict):
        raise VisionForgeEvaluationError("构建或 Browser Run Artifact 内容无效")
    screenshot_ref = browser_artifact.content.get("screenshot_artifact_ref")
    if not isinstance(screenshot_ref, str):
        raise VisionForgeEvaluationError("Browser Run 缺少截图引用")
    browser = BrowserRunResult.from_runner_payload(
        browser_artifact.content, screenshot_ref
    )
    review = VisualReview.from_dict(review_artifact.content)
    build_passed = build.content.get("passed") is True
    dom_passed = (
        browser.passed
        and all(item.passed for item in browser.assertions)
        and not any(item.level == "error" for item in browser.console_messages)
        and not browser.page_errors
        and not browser.network_errors
    )
    visual_passed = (
        review.passed
        and review.score >= minimum_visual_score
        and not review.blocking_issues
    )
    return build_passed, dom_passed, visual_passed


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise VisionForgeEvaluationError("评测任务路径越过任务集目录")
    if not candidate.is_file():
        raise VisionForgeEvaluationError(f"评测任务文件不存在: {relative}")
    return candidate


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisionForgeEvaluationError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _rate(values: object) -> float:
    items = tuple(bool(item) for item in values)
    if not items:
        return 0.0
    return round(sum(items) / len(items), 4)


def _safe_error(error: Exception) -> str:
    value = str(error)[:4000]
    value = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value[:2000]
