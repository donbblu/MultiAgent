from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import uuid4

from .coding_ablation import (
    AblationBudget,
    AblationStrategyProfile,
    CodingAblationReport,
    CodingAblationRunner,
    default_ablation_profiles,
)
from .coding_evaluation import FixedCodingSuite
from .coding_model_workers import (
    CODING_MODEL_PROTOCOL_VERSION,
    CODING_PROMPT_VERSION,
    ModelAblationWorker,
    build_model_ablation_registry,
)
from .model import (
    BudgetedModelClient,
    ModelCallBudget,
    ModelClient,
    ModelClientFactory,
    ModelConfig,
)


CORE_ABLATION_EXPERIMENT_VERSION = "1.0"
MODEL_ROLES = ("planner", "implementer", "tester", "fixer")


def _canonical(value: object) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class CoreAblationCallEstimate:
    task_count: int
    minimum_logical_calls: int
    maximum_logical_calls: int
    maximum_external_requests: int

    def to_dict(self) -> Mapping[str, int]:
        return MappingProxyType({
            "task_count": self.task_count,
            "minimum_logical_calls": self.minimum_logical_calls,
            "maximum_logical_calls": self.maximum_logical_calls,
            "maximum_external_requests": self.maximum_external_requests,
        })


def estimate_core_ablation_calls(
    task_count: int, *, max_retries: int = 0
) -> CoreAblationCallEstimate:
    if task_count <= 0 or max_retries < 0:
        raise ValueError("任务数必须为正数，重试次数不能为负数")
    # single=1; planner+developer=2; full=2 when first pass, 4 when fixing.
    minimum = task_count * 5
    maximum = task_count * 7
    return CoreAblationCallEstimate(
        task_count,
        minimum,
        maximum,
        maximum * (max_retries + 1),
    )


@dataclass(frozen=True)
class CoreAblationExperimentConfig:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    structured_output_mode: str
    capabilities: tuple[str, ...]
    temperature: float
    max_retries: int
    max_output_tokens: int
    max_tokens_per_call: int
    max_total_tokens: int
    max_model_calls: int
    max_context_chars: int = 60_000
    max_file_chars: int = 20_000
    prompt_version: str = CODING_PROMPT_VERSION
    protocol_version: str = CODING_MODEL_PROTOCOL_VERSION
    schema_version: str = CORE_ABLATION_EXPERIMENT_VERSION

    @classmethod
    def from_model_config(
        cls,
        model: ModelConfig,
        *,
        task_count: int,
        max_tokens_per_call: int,
        max_total_tokens: int,
    ) -> "CoreAblationExperimentConfig":
        estimate = estimate_core_ablation_calls(
            task_count, max_retries=model.max_retries
        )
        config = cls(
            model.provider,
            model.model,
            model.base_url,
            model.api_key_env,
            model.structured_output_mode.value,
            tuple(sorted(item.value for item in model.capabilities)),
            float(model.temperature),
            model.max_retries,
            model.max_tokens,
            max_tokens_per_call,
            max_total_tokens,
            estimate.maximum_external_requests,
        )
        config.validate(model)
        return config

    def validate(self, model: ModelConfig | None = None) -> None:
        if self.schema_version != CORE_ABLATION_EXPERIMENT_VERSION:
            raise ValueError("Core Ablation experiment schema_version 无效")
        if self.protocol_version != CODING_MODEL_PROTOCOL_VERSION:
            raise ValueError("Coding model protocol_version 无效")
        if min(
            self.max_output_tokens,
            self.max_tokens_per_call,
            self.max_total_tokens,
            self.max_model_calls,
            self.max_context_chars,
            self.max_file_chars,
        ) <= 0:
            raise ValueError("实验预算和披露上限必须为正数")
        if self.max_output_tokens >= self.max_tokens_per_call:
            raise ValueError("单次预算必须为输入和协议开销保留空间")
        if self.max_tokens_per_call > self.max_total_tokens:
            raise ValueError("单次 Token 上限不能超过全局上限")
        if self.max_retries != 0:
            raise ValueError("真实消融必须关闭供应商 HTTP 自动重试")
        required = {"text", "structured_output", "tool_calling"}
        if not required.issubset(self.capabilities):
            raise ValueError("实验模型缺少 text/structured_output/tool_calling")
        if model is not None and not model.include_max_tokens:
            raise ValueError("真实消融必须向供应商发送 max_tokens 上限")

    @property
    def digest(self) -> str:
        return sha256(
            _canonical(dict(self.to_dict())).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "structured_output_mode": self.structured_output_mode,
            "capabilities": self.capabilities,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "max_output_tokens": self.max_output_tokens,
            "max_tokens_per_call": self.max_tokens_per_call,
            "max_total_tokens": self.max_total_tokens,
            "max_model_calls": self.max_model_calls,
            "max_context_chars": self.max_context_chars,
            "max_file_chars": self.max_file_chars,
            "prompt_version": self.prompt_version,
            "protocol_version": self.protocol_version,
        })


@dataclass(frozen=True)
class CoreAblationPreflight:
    suite_id: str
    suite_manifest_sha256: str
    experiment: CoreAblationExperimentConfig
    call_estimate: CoreAblationCallEstimate
    source_disclosures: tuple[Mapping[str, object], ...]
    generated_artifact_disclosure: tuple[str, ...]
    excluded_sources: tuple[str, ...]
    created_at: str

    @property
    def digest(self) -> str:
        stable = dict(self._payload())
        stable.pop("created_at", None)
        return sha256(_canonical(stable).encode("utf-8")).hexdigest()

    def _payload(self) -> Mapping[str, object]:
        payload = {
            "schema_version": CORE_ABLATION_EXPERIMENT_VERSION,
            "will_call_external_models": False,
            "suite": {
                "suite_id": self.suite_id,
                "manifest_sha256": self.suite_manifest_sha256,
                "task_count": self.call_estimate.task_count,
            },
            "experiment": dict(self.experiment.to_dict()),
            "experiment_sha256": self.experiment.digest,
            "call_estimate": dict(self.call_estimate.to_dict()),
            "source_disclosures": [dict(item) for item in self.source_disclosures],
            "generated_artifact_disclosure": self.generated_artifact_disclosure,
            "excluded_sources": self.excluded_sources,
            "created_at": self.created_at,
        }
        return MappingProxyType(dict(_jsonable(payload)))

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            **dict(self._payload()),
            "preflight_sha256": self.digest,
        })


def build_core_ablation_preflight(
    suite: FixedCodingSuite,
    experiment: CoreAblationExperimentConfig,
) -> CoreAblationPreflight:
    estimate = estimate_core_ablation_calls(
        len(suite.tasks), max_retries=experiment.max_retries
    )
    if experiment.max_model_calls != estimate.maximum_external_requests:
        raise ValueError("全局调用上限必须等于冻结实验的最坏外部请求数")
    disclosures: list[Mapping[str, object]] = []
    for task in suite.tasks:
        files: list[Mapping[str, object]] = []
        for item in task.starter_files:
            source = task.task_root / "starter" / item.path
            text = source.read_text(encoding="utf-8")
            files.append(MappingProxyType({
                "path": item.path,
                "sha256": item.sha256,
                "utf8_bytes": len(text.encode("utf-8")),
                "characters": len(text),
                "public_test": item.path.startswith("tests/"),
            }))
        disclosures.append(MappingProxyType({
            "task_id": task.task_id,
            "requirement_sha256": sha256(
                task.objective.encode("utf-8")
            ).hexdigest(),
            "requirement_characters": len(task.objective),
            "allowed_write_paths": task.allowed_write_paths,
            "starter_files": tuple(files),
            "starter_characters": sum(
                int(item["characters"]) for item in files
            ),
            "hidden_files_excluded": len(task.hidden_files),
            "solution_files_excluded": len(task.solution_files),
        }))
    return CoreAblationPreflight(
        suite.suite_id,
        suite.manifest_sha256,
        experiment,
        estimate,
        tuple(disclosures),
        (
            "Planner 生成的 core:plan",
            "Implementer/Fixer 生成后的当前源码快照",
            "Runtime 裁剪后的 Validator 失败摘要",
            "Tester 生成的 core:test_diagnosis",
        ),
        (
            ".env 与凭据",
            ".git、.runs、.runtime、.verification",
            ".harness-hidden-tests 与隐藏验收源码",
            "solution 参考答案",
        ),
        datetime.now(timezone.utc).isoformat(),
    )


def real_model_ablation_profiles(
    experiment: CoreAblationExperimentConfig,
) -> tuple[AblationStrategyProfile, ...]:
    budget = AblationBudget(
        max_worker_calls=4,
        max_accounted_tokens=experiment.max_tokens_per_call * 4,
        max_fix_rounds=1,
    )
    profiles = default_ablation_profiles(
        budget, worker_policy_tag="model-eval"
    )
    return tuple(replace(
        profile,
        stages=tuple(
            replace(stage, token_limit=experiment.max_tokens_per_call)
            for stage in profile.stages
        ),
    ) for profile in profiles)


@dataclass(frozen=True)
class CoreAblationExecutionResult:
    report: CodingAblationReport
    budget: Mapping[str, int]
    model_audits: Mapping[str, tuple[Mapping[str, object], ...]]


def run_real_model_ablation(
    suite: FixedCodingSuite,
    model_config: ModelConfig,
    experiment: CoreAblationExperimentConfig,
    *,
    create_client: Callable[[ModelConfig], ModelClient] | None = None,
    trusted_local_execution: bool = False,
) -> CoreAblationExecutionResult:
    if type(trusted_local_execution) is not bool:
        raise TypeError("trusted_local_execution 必须是真正的 bool")
    experiment.validate(model_config)
    budget = ModelCallBudget(
        max_model_calls=experiment.max_model_calls,
        max_total_tokens=experiment.max_total_tokens,
        max_tokens_per_call=experiment.max_tokens_per_call,
    )
    factory = create_client or (lambda config: ModelClientFactory.create(config))
    clients: dict[str, ModelClient] = {}
    for role in MODEL_ROLES:
        clients[role] = BudgetedModelClient(
            factory(model_config),
            budget,
            max_output_tokens=experiment.max_output_tokens,
        )
    registry, workers = build_model_ablation_registry(
        clients,
        prompt_version=experiment.prompt_version,
        max_context_chars=experiment.max_context_chars,
        max_file_chars=experiment.max_file_chars,
    )
    report = CodingAblationRunner(
        suite,
        registry,
        real_model_ablation_profiles(experiment),
        allow_model_usage=True,
        trusted_local_execution=trusted_local_execution,
    ).run()
    audits = {
        role: tuple(
            MappingProxyType(dict(item.audit_dict()))
            for item in worker.prepared_invocations
        )
        for role, worker in workers.items()
        if isinstance(worker, ModelAblationWorker)
    }
    return CoreAblationExecutionResult(
        report,
        MappingProxyType(budget.snapshot().to_dict()),
        MappingProxyType(audits),
    )


def write_core_ablation_run_bundle(
    run_root: Path,
    *,
    preflight: CoreAblationPreflight,
    execution: CoreAblationExecutionResult,
) -> Mapping[str, str]:
    root = run_root.resolve()
    if root.exists():
        raise ValueError(f"评测 Run 已存在: {root}")
    root.mkdir(parents=True)
    report_path = execution.report.write_json(root / "report.json")
    payloads = {
        "preflight": dict(preflight.to_dict()),
        "budget": dict(execution.budget),
        "model_audits": {
            role: [dict(item) for item in items]
            for role, items in execution.model_audits.items()
        },
    }
    paths: dict[str, str] = {"report": str(report_path)}
    for name, payload in payloads.items():
        path = root / f"{name}.json"
        temporary = root / f".{name}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, indent=2
                ) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        paths[name] = str(path)
    return MappingProxyType(paths)
