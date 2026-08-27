from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol
from uuid import uuid4

from .artifacts import Artifact, ArtifactDraft, ArtifactStore
from .coding_evaluation import FixedCodingSuite, FixedCodingTask
from .harness.registry import (
    WorkerDescriptor,
    WorkerRegistry,
    WorkerSelectionRequest,
)
from .integration import IntegrationError, PatchIntegrator
from .local_execution_approval import LocalExecutionApprover
from .models import FileChange, ImplementationPlan
from .truth import VerificationOutcome
from .workspace import ProjectWorkspace


class AblationStrategy(str, Enum):
    SINGLE_AGENT = "single_agent"
    PLANNER_DEVELOPER = "planner_developer"
    TESTER_FIXER = "planner_developer_tester_fixer"


class UsageSource(str, Enum):
    SCRIPTED = "scripted"
    MODEL = "model"


class AblationBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class AblationBudget:
    max_worker_calls: int = 4
    max_accounted_tokens: int = 2000
    max_fix_rounds: int = 1
    max_human_interventions: int = 0

    def __post_init__(self) -> None:
        if min(
            self.max_worker_calls,
            self.max_accounted_tokens,
            self.max_fix_rounds,
            self.max_human_interventions,
        ) < 0:
            raise ValueError("AblationBudget 不能包含负数")
        if self.max_worker_calls == 0 or self.max_accounted_tokens == 0:
            raise ValueError("AblationBudget 必须允许至少一次 Worker 调用")

    @property
    def digest(self) -> str:
        return _mapping_digest(self.to_dict())

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "max_worker_calls": self.max_worker_calls,
            "max_accounted_tokens": self.max_accounted_tokens,
            "max_fix_rounds": self.max_fix_rounds,
            "max_human_interventions": self.max_human_interventions,
        })


@dataclass(frozen=True)
class AblationStagePolicy:
    stage_id: str
    role: str
    required_capability: str
    visible_kinds: frozenset[str]
    required_kinds: frozenset[str]
    output_kind: str
    token_limit: int = 300
    independent_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "visible_kinds", frozenset(self.visible_kinds))
        object.__setattr__(self, "required_kinds", frozenset(self.required_kinds))
        object.__setattr__(self, "independent_from", tuple(self.independent_from))
        if not all((
            self.stage_id.strip(), self.role.strip(),
            self.required_capability.strip(), self.output_kind.strip(),
        )):
            raise ValueError("AblationStagePolicy 字段不能为空")
        if not self.required_kinds.issubset(self.visible_kinds):
            raise ValueError("required_kinds 必须包含在 visible_kinds 中")
        if self.token_limit <= 0:
            raise ValueError("stage token_limit 必须大于 0")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "stage_id": self.stage_id,
            "role": self.role,
            "required_capability": self.required_capability,
            "visible_kinds": sorted(self.visible_kinds),
            "required_kinds": sorted(self.required_kinds),
            "output_kind": self.output_kind,
            "token_limit": self.token_limit,
            "independent_from": self.independent_from,
        })


@dataclass(frozen=True)
class AblationStrategyProfile:
    strategy: AblationStrategy
    stages: tuple[AblationStagePolicy, ...]
    budget: AblationBudget
    worker_policy_tag: str = "offline-eval"
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", AblationStrategy(self.strategy))
        object.__setattr__(self, "stages", tuple(self.stages))
        if not isinstance(self.worker_policy_tag, str) or not (
            self.worker_policy_tag.strip()
        ):
            raise ValueError("worker_policy_tag 不能为空")
        ids = tuple(item.stage_id for item in self.stages)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Ablation stage 必须非空且 ID 唯一")
        required = {
            AblationStrategy.SINGLE_AGENT: {"implement"},
            AblationStrategy.PLANNER_DEVELOPER: {"plan", "implement"},
            AblationStrategy.TESTER_FIXER: {
                "plan", "implement", "diagnose", "fix",
            },
        }[self.strategy]
        if set(ids) != required:
            raise ValueError("Ablation strategy 的 stage 组成无效")
        requirement = "core:coding_requirement"
        source = "core:source_snapshot"
        plan = "core:plan"
        feedback = "core:validator_feedback"
        diagnosis = "core:test_diagnosis"
        expected = {
            "plan": (
                "planner", "task_planning",
                frozenset({requirement, source}),
                frozenset({requirement, source}),
                plan, (),
            ),
            "implement": (
                "implementer", "code_generation",
                (
                    frozenset({requirement, source})
                    if self.strategy is AblationStrategy.SINGLE_AGENT
                    else frozenset({requirement, source, plan})
                ),
                (
                    frozenset({requirement, source})
                    if self.strategy is AblationStrategy.SINGLE_AGENT
                    else frozenset({requirement, source, plan})
                ),
                "core:patch", (),
            ),
            "diagnose": (
                "tester", "failure_analysis",
                frozenset({requirement, source, plan, feedback}),
                frozenset({requirement, source, plan, feedback}),
                diagnosis, ("implement",),
            ),
            "fix": (
                "fixer", "code_repair",
                frozenset({requirement, source, plan, feedback, diagnosis}),
                frozenset({requirement, source, plan, feedback, diagnosis}),
                "core:patch", (),
            ),
        }
        for stage in self.stages:
            actual = (
                stage.role,
                stage.required_capability,
                stage.visible_kinds,
                stage.required_kinds,
                stage.output_kind,
                stage.independent_from,
            )
            if actual != expected[stage.stage_id]:
                raise ValueError(
                    f"Ablation stage 边界被修改: {stage.stage_id}"
                )
        if self.schema_version != "1.0":
            raise ValueError("只支持 AblationStrategyProfile 1.0")

    @property
    def digest(self) -> str:
        return _mapping_digest(self.to_dict())

    def stage(self, stage_id: str) -> AblationStagePolicy:
        for item in self.stages:
            if item.stage_id == stage_id:
                return item
        raise KeyError(f"策略不包含 stage: {stage_id}")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "strategy": self.strategy.value,
            "budget": dict(self.budget.to_dict()),
            "worker_policy_tag": self.worker_policy_tag,
            "stages": [dict(item.to_dict()) for item in self.stages],
        })


def default_ablation_profiles(
    budget: AblationBudget | None = None,
    *,
    worker_policy_tag: str = "offline-eval",
) -> tuple[AblationStrategyProfile, ...]:
    shared = budget or AblationBudget()
    requirement = "core:coding_requirement"
    source = "core:source_snapshot"
    plan = "core:plan"
    feedback = "core:validator_feedback"
    diagnosis = "core:test_diagnosis"
    implement = AblationStagePolicy(
        "implement", "implementer", "code_generation",
        frozenset({requirement, source, plan}),
        frozenset({requirement, source, plan}),
        "core:patch",
    )
    return (
        AblationStrategyProfile(
            AblationStrategy.SINGLE_AGENT,
            (AblationStagePolicy(
                "implement", "implementer", "code_generation",
                frozenset({requirement, source}),
                frozenset({requirement, source}),
                "core:patch",
            ),),
            shared,
            worker_policy_tag,
        ),
        AblationStrategyProfile(
            AblationStrategy.PLANNER_DEVELOPER,
            (
                AblationStagePolicy(
                    "plan", "planner", "task_planning",
                    frozenset({requirement, source}),
                    frozenset({requirement, source}),
                    plan,
                ),
                implement,
            ),
            shared,
            worker_policy_tag,
        ),
        AblationStrategyProfile(
            AblationStrategy.TESTER_FIXER,
            (
                AblationStagePolicy(
                    "plan", "planner", "task_planning",
                    frozenset({requirement, source}),
                    frozenset({requirement, source}),
                    plan,
                ),
                implement,
                AblationStagePolicy(
                    "diagnose", "tester", "failure_analysis",
                    frozenset({requirement, source, plan, feedback}),
                    frozenset({requirement, source, plan, feedback}),
                    diagnosis,
                    independent_from=("implement",),
                ),
                AblationStagePolicy(
                    "fix", "fixer", "code_repair",
                    frozenset({
                        requirement, source, plan, feedback, diagnosis,
                    }),
                    frozenset({
                        requirement, source, plan, feedback, diagnosis,
                    }),
                    "core:patch",
                ),
            ),
            shared,
            worker_policy_tag,
        ),
    )


@dataclass(frozen=True)
class AblationUsage:
    source: UsageSource
    input_tokens: int
    output_tokens: int
    calls: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", UsageSource(self.source))
        if min(self.input_tokens, self.output_tokens) < 0 or self.calls != 1:
            raise ValueError("AblationUsage 必须是一次非负用量")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AblationWorkerRequest:
    task_id: str
    strategy: AblationStrategy
    stage: AblationStagePolicy
    visible_artifacts: Mapping[str, Artifact]
    allowed_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", AblationStrategy(self.strategy))
        object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        object.__setattr__(
            self,
            "visible_artifacts",
            MappingProxyType(dict(self.visible_artifacts)),
        )


@dataclass(frozen=True)
class AblationWorkerResponse:
    artifact: ArtifactDraft
    summary: str
    usage: AblationUsage


class AblationWorker(Protocol):
    def run_experiment(
        self, request: AblationWorkerRequest
    ) -> AblationWorkerResponse: ...


class AblationBudgetLedger:
    def __init__(self, budget: AblationBudget) -> None:
        self.budget = budget
        self.worker_calls = 0
        self.accounted_tokens = 0
        self.scripted_calls = 0
        self.scripted_tokens = 0
        self.model_calls = 0
        self.model_tokens = 0

    def reserve(self, token_limit: int) -> None:
        if self.worker_calls + 1 > self.budget.max_worker_calls:
            raise AblationBudgetExceeded("Worker 调用预算耗尽")
        if self.accounted_tokens + token_limit > self.budget.max_accounted_tokens:
            raise AblationBudgetExceeded("Token 预算不足以安全发起下一次调用")
        self.worker_calls += 1

    def record(self, usage: AblationUsage, token_limit: int) -> None:
        if usage.total_tokens > token_limit:
            raise AblationBudgetExceeded("Worker 返回的 Token 用量超过单次上限")
        if self.accounted_tokens + usage.total_tokens > (
            self.budget.max_accounted_tokens
        ):
            raise AblationBudgetExceeded("累计 Token 用量超过预算")
        self.accounted_tokens += usage.total_tokens
        if usage.source is UsageSource.SCRIPTED:
            self.scripted_calls += 1
            self.scripted_tokens += usage.total_tokens
        else:
            self.model_calls += 1
            self.model_tokens += usage.total_tokens


@dataclass(frozen=True)
class AblationStageAudit:
    stage_id: str
    role: str
    worker_id: str
    principal_id: str
    visible_artifacts: Mapping[str, str]
    visible_kinds: tuple[str, ...]
    output_kind: str
    usage_source: UsageSource
    tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "visible_artifacts",
            MappingProxyType(dict(self.visible_artifacts)),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "stage_id": self.stage_id,
            "role": self.role,
            "worker_id": self.worker_id,
            "principal_id": self.principal_id,
            "visible_artifacts": dict(self.visible_artifacts),
            "visible_kinds": self.visible_kinds,
            "output_kind": self.output_kind,
            "usage_source": self.usage_source.value,
            "tokens": self.tokens,
        })


@dataclass(frozen=True)
class AblationTrialResult:
    task_id: str
    strategy: AblationStrategy
    outcome: VerificationOutcome
    initial_outcome: VerificationOutcome
    delivered: bool
    first_passed: bool
    fix_attempted: bool
    fix_succeeded: bool
    fix_rounds: int
    duration_ms: int
    worker_calls: int
    accounted_tokens: int
    scripted_calls: int
    scripted_tokens: int
    model_calls: int
    model_tokens: int
    unauthorized_attempts: int
    human_interventions: int
    validator_outcomes: Mapping[str, str]
    stage_audits: tuple[AblationStageAudit, ...]
    failure_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validator_outcomes",
            MappingProxyType(dict(self.validator_outcomes)),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "task_id": self.task_id,
            "strategy": self.strategy.value,
            "outcome": self.outcome.value,
            "initial_outcome": self.initial_outcome.value,
            "delivered": self.delivered,
            "first_passed": self.first_passed,
            "fix_attempted": self.fix_attempted,
            "fix_succeeded": self.fix_succeeded,
            "fix_rounds": self.fix_rounds,
            "duration_ms": self.duration_ms,
            "worker_calls": self.worker_calls,
            "accounted_tokens": self.accounted_tokens,
            "scripted_calls": self.scripted_calls,
            "scripted_tokens": self.scripted_tokens,
            "model_calls": self.model_calls,
            "model_tokens": self.model_tokens,
            "unauthorized_attempts": self.unauthorized_attempts,
            "human_interventions": self.human_interventions,
            "validator_outcomes": dict(self.validator_outcomes),
            "stage_audits": [dict(item.to_dict()) for item in self.stage_audits],
            "failure_reasons": self.failure_reasons,
        })


@dataclass(frozen=True)
class CodingAblationReport:
    suite_id: str
    suite_manifest_sha256: str
    budget_digest: str
    profile_digests: Mapping[str, str]
    started_at: str
    completed_at: str
    trials: tuple[AblationTrialResult, ...]
    dry_run: bool = True
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "profile_digests", MappingProxyType(dict(self.profile_digests))
        )

    def summary(self) -> Mapping[str, object]:
        summaries: dict[str, object] = {}
        for strategy in AblationStrategy:
            trials = tuple(item for item in self.trials if item.strategy is strategy)
            delivered = sum(item.delivered for item in trials)
            first = sum(item.first_passed for item in trials)
            fix_attempted = sum(item.fix_attempted for item in trials)
            fixed = sum(item.fix_succeeded for item in trials)
            summaries[strategy.value] = {
                "trials": len(trials),
                "delivered": delivered,
                "delivery_rate": delivered / len(trials) if trials else 0.0,
                "first_passed": first,
                "first_pass_rate": first / len(trials) if trials else 0.0,
                "fix_attempted": fix_attempted,
                "fix_succeeded": fixed,
                "fix_success_rate": fixed / fix_attempted if fix_attempted else 0.0,
                "average_fix_rounds": (
                    sum(item.fix_rounds for item in trials) / len(trials)
                    if trials else 0.0
                ),
                "worker_calls": sum(item.worker_calls for item in trials),
                "accounted_tokens": sum(item.accounted_tokens for item in trials),
                "scripted_calls": sum(item.scripted_calls for item in trials),
                "scripted_tokens": sum(item.scripted_tokens for item in trials),
                "model_calls": sum(item.model_calls for item in trials),
                "model_tokens": sum(item.model_tokens for item in trials),
                "average_duration_ms": (
                    sum(item.duration_ms for item in trials) / len(trials)
                    if trials else 0.0
                ),
                "unauthorized_attempts": sum(
                    item.unauthorized_attempts for item in trials
                ),
                "human_interventions": sum(
                    item.human_interventions for item in trials
                ),
            }
        return MappingProxyType(summaries)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "dry_run": self.dry_run,
            "suite_id": self.suite_id,
            "suite_manifest_sha256": self.suite_manifest_sha256,
            "budget_digest": self.budget_digest,
            "profile_digests": dict(self.profile_digests),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": dict(self.summary()),
            "trials": [dict(item.to_dict()) for item in self.trials],
        })

    def write_json(self, output_path: Path) -> Path:
        output = output_path.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    dict(self.to_dict()), ensure_ascii=False,
                    sort_keys=True, indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output


def _mapping_digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class CodingAblationRunner:
    """用相同任务、Validator 和预算运行三种协作方案。"""

    def __init__(
        self,
        suite: FixedCodingSuite,
        workers: WorkerRegistry,
        profiles: tuple[AblationStrategyProfile, ...] | None = None,
        *,
        allow_model_usage: bool = False,
        trusted_local_execution: bool = False,
    ) -> None:
        self.suite = suite
        self.workers = workers
        self.profiles = profiles or default_ablation_profiles()
        self.allow_model_usage = allow_model_usage
        if not isinstance(allow_model_usage, bool):
            raise ValueError("allow_model_usage 必须是布尔值")
        if type(trusted_local_execution) is not bool:
            raise TypeError("trusted_local_execution 必须是真正的 bool")
        approved = trusted_local_execution
        self._approver_factory: Callable[[], LocalExecutionApprover] = (
            lambda: LocalExecutionApprover(approved)
        )
        if {item.strategy for item in self.profiles} != set(AblationStrategy):
            raise ValueError("Ablation Runner 必须包含三种策略")
        budget_digests = {item.budget.digest for item in self.profiles}
        if len(budget_digests) != 1:
            raise ValueError("三种策略必须使用相同预算")

    def run(self) -> CodingAblationReport:
        started_at = datetime.now(timezone.utc).isoformat()
        trials: list[AblationTrialResult] = []
        with tempfile.TemporaryDirectory(prefix="core-ablation-") as temp:
            root = Path(temp)
            for task in self.suite.tasks:
                for profile in self.profiles:
                    trials.append(self.run_trial(task, profile, root))
        completed_at = datetime.now(timezone.utc).isoformat()
        return CodingAblationReport(
            self.suite.suite_id,
            self.suite.manifest_sha256,
            self.profiles[0].budget.digest,
            {
                item.strategy.value: item.digest for item in self.profiles
            },
            started_at,
            completed_at,
            tuple(trials),
            dry_run=not self.allow_model_usage,
        )

    def run_trial(
        self,
        task: FixedCodingTask,
        profile: AblationStrategyProfile,
        root: Path,
    ) -> AblationTrialResult:
        started = time.monotonic()
        trial_root = root / task.task_id / profile.strategy.value
        workspace_path = task.prepare_workspace(trial_root / "agent-workspace")
        workspace = ProjectWorkspace(workspace_path)
        artifacts = ArtifactStore()
        available: dict[str, str] = {}
        principals: dict[str, str] = {}
        audits: list[AblationStageAudit] = []
        ledger = AblationBudgetLedger(profile.budget)
        unauthorized_attempts = 0
        human_interventions = 0
        failure_reasons: list[str] = []
        initial_outcome = VerificationOutcome.UNKNOWN
        final_outcome = VerificationOutcome.UNKNOWN
        final_validators: Mapping[str, str] = MappingProxyType({})
        fix_rounds = 0
        fix_attempted = False

        available["requirement"] = artifacts.put(Artifact.create(
            "requirement",
            task.task_id,
            {
                "objective": task.objective,
                "allowed_write_paths": task.allowed_write_paths,
                "validator_kinds": tuple(sorted(task.validator_configs)),
            },
            kind="core:coding_requirement",
        ))
        available["source"] = self._source_snapshot(
            task, workspace, artifacts, "source"
        )

        try:
            if profile.strategy is not AblationStrategy.SINGLE_AGENT:
                available["plan"] = self._call_stage(
                    task, profile, "plan", available, artifacts,
                    principals, audits, ledger,
                )
            patch_ref = self._call_stage(
                task, profile, "implement", available, artifacts,
                principals, audits, ledger,
            )
            self._integrate(task, workspace, artifacts, patch_ref)
            available["source"] = self._source_snapshot(
                task, workspace, artifacts, "source-after-implement"
            )
            initial = self._validate(
                task, workspace_path, trial_root / "validation-0",
                artifacts, patch_ref, f"{task.task_id}-{profile.strategy.value}-0",
            )
            initial_outcome = initial[0]
            final_outcome, final_validators = initial[0], initial[1]
            failure_reasons.extend(initial[2])

            if (
                profile.strategy is AblationStrategy.TESTER_FIXER
                and initial_outcome is VerificationOutcome.FAILED
                and profile.budget.max_fix_rounds > 0
            ):
                fix_attempted = True
                available["validator_feedback"] = artifacts.put(Artifact.create(
                    "validator-feedback",
                    task.task_id,
                    {
                        "outcome": initial_outcome.value,
                        "validator_outcomes": dict(initial[1]),
                        "failure_summaries": initial[2],
                    },
                    kind="core:validator_feedback",
                ))
                available["diagnosis"] = self._call_stage(
                    task, profile, "diagnose", available, artifacts,
                    principals, audits, ledger,
                )
                fix_ref = self._call_stage(
                    task, profile, "fix", available, artifacts,
                    principals, audits, ledger,
                )
                self._integrate(task, workspace, artifacts, fix_ref)
                fix_rounds = 1
                available["source"] = self._source_snapshot(
                    task, workspace, artifacts, "source-after-fix"
                )
                fixed = self._validate(
                    task, workspace_path, trial_root / "validation-1",
                    artifacts, fix_ref,
                    f"{task.task_id}-{profile.strategy.value}-1",
                )
                final_outcome, final_validators = fixed[0], fixed[1]
                if final_outcome is not VerificationOutcome.PASSED:
                    failure_reasons.extend(fixed[2])
            elif final_outcome is VerificationOutcome.PASSED:
                failure_reasons.clear()
        except IntegrationError as exc:
            unauthorized_attempts += 1
            final_outcome = VerificationOutcome.FAILED
            failure_reasons.append(str(exc))
        except (AblationBudgetExceeded, PermissionError) as exc:
            final_outcome = VerificationOutcome.UNKNOWN
            failure_reasons.append(str(exc))
            if isinstance(exc, PermissionError):
                unauthorized_attempts += 1
        except Exception as exc:
            final_outcome = VerificationOutcome.UNKNOWN
            failure_reasons.append(
                f"Runtime exception: {type(exc).__name__}: {exc}"
            )

        delivered = final_outcome is VerificationOutcome.PASSED
        return AblationTrialResult(
            task.task_id,
            profile.strategy,
            final_outcome,
            initial_outcome,
            delivered,
            initial_outcome is VerificationOutcome.PASSED,
            fix_attempted,
            fix_attempted and delivered,
            fix_rounds,
            int((time.monotonic() - started) * 1000),
            ledger.worker_calls,
            ledger.accounted_tokens,
            ledger.scripted_calls,
            ledger.scripted_tokens,
            ledger.model_calls,
            ledger.model_tokens,
            unauthorized_attempts,
            human_interventions,
            final_validators,
            tuple(audits),
            tuple(failure_reasons),
        )

    def _call_stage(
        self,
        task: FixedCodingTask,
        profile: AblationStrategyProfile,
        stage_id: str,
        available: Mapping[str, str],
        artifacts: ArtifactStore,
        principals: dict[str, str],
        audits: list[AblationStageAudit],
        ledger: AblationBudgetLedger,
    ) -> str:
        stage = profile.stage(stage_id)
        ledger.reserve(stage.token_limit)
        visible = {
            name: artifacts.get(reference)
            for name, reference in available.items()
            if artifacts.get(reference).kind in stage.visible_kinds
        }
        kinds = {item.kind for item in visible.values()}
        if not stage.required_kinds.issubset(kinds):
            raise PermissionError(
                f"stage {stage_id} 缺少必需 Artifact: "
                f"{sorted(stage.required_kinds - kinds)}"
            )
        if any(
            "hidden" in name.lower()
            or "solution" in name.lower()
            or "hidden" in artifact.kind.lower()
            or "solution" in artifact.kind.lower()
            for name, artifact in visible.items()
        ):
            raise PermissionError("Worker 可见输入包含隐藏验收或参考答案")
        excluded = frozenset(
            principals[item] for item in stage.independent_from
            if item in principals
        )
        selection = self.workers.select(WorkerSelectionRequest(
            f"{task.task_id}:{profile.strategy.value}:{stage_id}",
            stage.role,
            frozenset({stage.required_capability}),
            frozenset(stage.required_kinds),
            frozenset({stage.output_kind}),
            frozenset({profile.worker_policy_tag}),
            excluded,
        ))
        worker = selection.worker
        if not hasattr(worker, "run_experiment"):
            raise TypeError("Ablation Worker 必须实现 run_experiment")
        response = worker.run_experiment(AblationWorkerRequest(
            task.task_id,
            profile.strategy,
            stage,
            visible,
            task.allowed_write_paths,
        ))
        if not isinstance(response, AblationWorkerResponse):
            raise TypeError("Ablation Worker 返回类型无效")
        if response.artifact.kind != stage.output_kind:
            raise PermissionError(
                f"stage {stage_id} 输出协议不匹配: {response.artifact.kind}"
            )
        if (
            response.usage.source is UsageSource.MODEL
            and not self.allow_model_usage
        ):
            raise PermissionError("离线 dry-run 禁止登记真实模型用量")
        if stage.output_kind == "core:patch" and not isinstance(
            response.artifact.content, ImplementationPlan
        ):
            raise TypeError("core:patch 必须包含 ImplementationPlan")
        ledger.record(response.usage, stage.token_limit)
        materialized = response.artifact.materialize(stage_id, task.task_id)
        reference = artifacts.put(Artifact(
            materialized.artifact_id,
            materialized.name,
            materialized.task_id,
            materialized.kind,
            materialized.content,
            MappingProxyType({
                **dict(materialized.metadata),
                "runtime_provenance": {
                    "worker_id": selection.descriptor.worker_id,
                    "principal_id": selection.descriptor.principal_id,
                    "role": stage.role,
                    "stage_id": stage_id,
                },
            }),
            materialized.created_at,
        ))
        principals[stage_id] = selection.descriptor.principal_id
        audits.append(AblationStageAudit(
            stage_id,
            stage.role,
            selection.descriptor.worker_id,
            selection.descriptor.principal_id,
            {
                name: artifact.content_hash
                for name, artifact in visible.items()
            },
            tuple(sorted(kinds)),
            stage.output_kind,
            response.usage.source,
            response.usage.total_tokens,
        ))
        return reference

    @staticmethod
    def _source_snapshot(
        task: FixedCodingTask,
        workspace: ProjectWorkspace,
        artifacts: ArtifactStore,
        name: str,
    ) -> str:
        task.assert_candidate_scope(workspace.root)
        return artifacts.put(Artifact.create(
            name,
            task.task_id,
            {
                path: workspace.read_text(path)
                for path in workspace.list_files()
                if "__pycache__" not in Path(path).parts
                and not path.endswith((".pyc", ".pyo"))
            },
            kind="core:source_snapshot",
        ))

    @staticmethod
    def _integrate(
        task: FixedCodingTask,
        workspace: ProjectWorkspace,
        artifacts: ArtifactStore,
        patch_ref: str,
    ) -> None:
        PatchIntegrator(workspace, task.allowed_write_paths).integrate((
            artifacts.get(patch_ref),
        ))

    def _validate(
        self,
        task: FixedCodingTask,
        workspace: Path,
        validation_workspace: Path,
        artifacts: ArtifactStore,
        subject_ref: str,
        task_id: str,
    ) -> tuple[VerificationOutcome, Mapping[str, str], tuple[str, ...]]:
        result = task.validate_candidate(
            workspace=workspace,
            validation_workspace=validation_workspace,
            artifacts=artifacts,
            subject_refs=(subject_ref,),
            task_id=task_id,
            approver_factory=self._approver_factory,
        )
        outcomes = MappingProxyType({
            item.validator_kind: item.outcome.value
            for item in result.validator_records
        })
        failures = tuple(
            item.summary for item in result.validator_records
            if item.outcome is not VerificationOutcome.PASSED
        )
        return result.outcome, outcomes, failures


class ScriptedAblationWorker:
    """只用于验证实验编排；不代表任何模型效果。"""

    _USAGE = {
        "plan": (24, 16),
        "implement": (40, 30),
        "diagnose": (20, 12),
        "fix": (38, 28),
    }

    def __init__(
        self,
        role: str,
        solution_plans: Mapping[str, ImplementationPlan],
    ) -> None:
        self.role = role
        self.solution_plans = MappingProxyType(dict(solution_plans))
        self.requests: list[AblationWorkerRequest] = []

    def run_experiment(
        self, request: AblationWorkerRequest
    ) -> AblationWorkerResponse:
        if request.stage.role != self.role:
            raise PermissionError("脚本 Worker 收到错误 Role 请求")
        self.requests.append(request)
        input_tokens, output_tokens = self._USAGE[request.stage.stage_id]
        usage = AblationUsage(
            UsageSource.SCRIPTED, input_tokens, output_tokens
        )
        if request.stage.stage_id == "plan":
            draft = ArtifactDraft(
                {
                    "summary": "scripted plan",
                    "allowed_paths": request.allowed_paths,
                },
                kind="core:plan",
            )
        elif request.stage.stage_id == "diagnose":
            feedback = next(
                item.content for item in request.visible_artifacts.values()
                if item.kind == "core:validator_feedback"
            )
            draft = ArtifactDraft(
                {
                    "summary": "scripted diagnosis",
                    "validator_outcomes": feedback["validator_outcomes"],
                },
                kind="core:test_diagnosis",
            )
        else:
            use_solution = (
                request.strategy is AblationStrategy.PLANNER_DEVELOPER
                and request.stage.stage_id == "implement"
            ) or request.stage.stage_id == "fix"
            plan = (
                self.solution_plans[request.task_id]
                if use_solution
                else ImplementationPlan("scripted no-op", [])
            )
            draft = ArtifactDraft(plan, kind="core:patch")
        return AblationWorkerResponse(
            draft,
            f"scripted {request.stage.stage_id}",
            usage,
        )


def build_scripted_ablation_registry(
    suite: FixedCodingSuite,
) -> tuple[WorkerRegistry, Mapping[str, ScriptedAblationWorker]]:
    plans: dict[str, ImplementationPlan] = {}
    for task in suite.tasks:
        changes = [
            FileChange(
                item.path,
                (task.task_root / "solution" / item.path).read_text(
                    encoding="utf-8"
                ),
                "scripted reference repair for Runtime dry-run",
            )
            for item in task.solution_files
        ]
        plans[task.task_id] = ImplementationPlan(
            "scripted reference repair", changes
        )
    registry = WorkerRegistry()
    workers: dict[str, ScriptedAblationWorker] = {}
    definitions = (
        (
            "planner", "scripted-planner", "scripted-planner-principal",
            {"task_planning"},
            {"core:coding_requirement", "core:source_snapshot"},
            {"core:plan"},
        ),
        (
            "implementer", "scripted-implementer",
            "scripted-implementer-principal", {"code_generation"},
            {"core:coding_requirement", "core:source_snapshot", "core:plan"},
            {"core:patch"},
        ),
        (
            "tester", "scripted-tester", "scripted-tester-principal",
            {"failure_analysis"},
            {
                "core:coding_requirement", "core:source_snapshot", "core:plan",
                "core:validator_feedback",
            },
            {"core:test_diagnosis"},
        ),
        (
            "fixer", "scripted-fixer", "scripted-fixer-principal",
            {"code_repair"},
            {
                "core:coding_requirement", "core:source_snapshot", "core:plan",
                "core:validator_feedback", "core:test_diagnosis",
            },
            {"core:patch"},
        ),
    )
    for role, worker_id, principal, capabilities, inputs, outputs in definitions:
        worker = ScriptedAblationWorker(role, plans)
        registry.register_worker(
            WorkerDescriptor(
                worker_id,
                role,
                frozenset(capabilities),
                frozenset(inputs),
                frozenset(outputs),
                frozenset({"offline-eval"}),
                principal_id=principal,
            ),
            worker,
        )
        workers[role] = worker
    return registry, MappingProxyType(workers)
