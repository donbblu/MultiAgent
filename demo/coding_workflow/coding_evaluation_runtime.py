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
from typing import Callable, Mapping
from uuid import uuid4

from .artifacts import Artifact, ArtifactStore
from .coding_evaluation import FixedCodingSuite, FixedCodingTask
from .local_execution_approval import LocalExecutionApprover
from .truth import VerificationOutcome


class FixedRevision(str, Enum):
    STARTER = "starter"
    REFERENCE_SOLUTION = "reference_solution"


@dataclass(frozen=True)
class FixedValidatorObservation:
    validator_kind: str
    outcome: VerificationOutcome
    summary: str
    evidence_count: int

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "validator_kind": self.validator_kind,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "evidence_count": self.evidence_count,
        })


@dataclass(frozen=True)
class FixedEvaluationTrial:
    task_id: str
    revision: FixedRevision
    outcome: VerificationOutcome
    delivered: bool
    duration_ms: int
    unauthorized_attempts: int
    workspace_hash: str
    validators: tuple[FixedValidatorObservation, ...]
    failure_reasons: tuple[str, ...]

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "task_id": self.task_id,
            "revision": self.revision.value,
            "outcome": self.outcome.value,
            "delivered": self.delivered,
            "duration_ms": self.duration_ms,
            "unauthorized_attempts": self.unauthorized_attempts,
            "workspace_hash": self.workspace_hash,
            "validators": [dict(item.to_dict()) for item in self.validators],
            "failure_reasons": self.failure_reasons,
        })


@dataclass(frozen=True)
class FixedEvaluationReport:
    suite_id: str
    suite_manifest_sha256: str
    started_at: str
    completed_at: str
    trials: tuple[FixedEvaluationTrial, ...]
    calibration_by_task: Mapping[str, bool]
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "calibration_by_task",
            MappingProxyType(dict(self.calibration_by_task)),
        )

    @property
    def calibration_passed(self) -> bool:
        return bool(self.calibration_by_task) and all(
            self.calibration_by_task.values()
        )

    @property
    def digest(self) -> str:
        payload = json.dumps(
            dict(self.to_dict()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def summary(self) -> Mapping[str, object]:
        result: dict[str, object] = {
            "task_count": len(self.calibration_by_task),
            "trial_count": len(self.trials),
            "calibrated_tasks": sum(self.calibration_by_task.values()),
            "calibration_passed": self.calibration_passed,
        }
        for revision in FixedRevision:
            trials = tuple(
                item for item in self.trials if item.revision is revision
            )
            delivered = sum(item.delivered for item in trials)
            result[revision.value] = {
                "trials": len(trials),
                "delivered": delivered,
                "delivery_rate": delivered / len(trials) if trials else 0.0,
            }
        return MappingProxyType(result)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "suite_manifest_sha256": self.suite_manifest_sha256,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "calibration_passed": self.calibration_passed,
            "calibration_by_task": dict(self.calibration_by_task),
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
                    dict(self.to_dict()),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output


class FixedCodingEvaluationRunner:
    """离线复位每个固定任务，并验证 starter/参考答案的题目有效性。"""

    def __init__(
        self,
        suite: FixedCodingSuite,
        *,
        trusted_local_execution: bool = False,
    ) -> None:
        if type(trusted_local_execution) is not bool:
            raise TypeError("trusted_local_execution 必须是真正的 bool")
        self.suite = suite
        approved = trusted_local_execution
        self._approver_factory: Callable[[], LocalExecutionApprover] = (
            lambda: LocalExecutionApprover(approved)
        )

    def run(self) -> FixedEvaluationReport:
        started_at = datetime.now(timezone.utc).isoformat()
        trials: list[FixedEvaluationTrial] = []
        with tempfile.TemporaryDirectory(prefix="core-coding-eval-") as temp:
            root = Path(temp)
            for task in self.suite.tasks:
                for revision in FixedRevision:
                    trials.append(self._run_trial(task, revision, root))
        completed_at = datetime.now(timezone.utc).isoformat()
        by_task: dict[str, bool] = {}
        for task in self.suite.tasks:
            starter = next(
                item for item in trials
                if item.task_id == task.task_id
                and item.revision is FixedRevision.STARTER
            )
            solution = next(
                item for item in trials
                if item.task_id == task.task_id
                and item.revision is FixedRevision.REFERENCE_SOLUTION
            )
            by_task[task.task_id] = (
                starter.outcome is VerificationOutcome.FAILED
                and solution.outcome is VerificationOutcome.PASSED
            )
        return FixedEvaluationReport(
            self.suite.suite_id,
            self.suite.manifest_sha256,
            started_at,
            completed_at,
            tuple(trials),
            by_task,
        )

    def _run_trial(
        self,
        task: FixedCodingTask,
        revision: FixedRevision,
        root: Path,
    ) -> FixedEvaluationTrial:
        trial_root = root / task.task_id / revision.value
        workspace = task.prepare_workspace(trial_root / "agent-workspace")
        if revision is FixedRevision.REFERENCE_SOLUTION:
            task.apply_reference_solution(workspace)
        artifacts = ArtifactStore()
        task_run_id = f"{task.task_id}-{revision.value}"
        subject_ref = artifacts.put(Artifact.create(
            "candidate",
            task_run_id,
            {
                "suite_id": self.suite.suite_id,
                "task_id": task.task_id,
                "revision": revision.value,
            },
            kind="core:candidate",
        ))
        started = time.monotonic()
        try:
            result = task.validate_candidate(
                workspace=workspace,
                validation_workspace=trial_root / "validation-workspace",
                artifacts=artifacts,
                subject_refs=(subject_ref,),
                task_id=task_run_id,
                approver_factory=self._approver_factory,
            )
        except PermissionError as exc:
            return self._exception_trial(
                task,
                revision,
                VerificationOutcome.FAILED,
                started,
                str(exc),
                unauthorized_attempts=1,
            )
        except Exception as exc:
            return self._exception_trial(
                task,
                revision,
                VerificationOutcome.UNKNOWN,
                started,
                f"Runtime exception: {type(exc).__name__}: {exc}",
            )

        gate = artifacts.verification(result.verification_ref)
        observations = tuple(FixedValidatorObservation(
            record.validator_kind,
            record.outcome,
            record.summary,
            len(record.evidence_refs),
        ) for record in result.validator_records)
        failures = tuple(
            item.summary for item in observations
            if item.outcome is not VerificationOutcome.PASSED
        )
        return FixedEvaluationTrial(
            task.task_id,
            revision,
            result.outcome,
            result.outcome is VerificationOutcome.PASSED,
            int((time.monotonic() - started) * 1000),
            0,
            gate.workspace_hash,
            observations,
            failures,
        )

    @staticmethod
    def _exception_trial(
        task: FixedCodingTask,
        revision: FixedRevision,
        outcome: VerificationOutcome,
        started: float,
        reason: str,
        *,
        unauthorized_attempts: int = 0,
    ) -> FixedEvaluationTrial:
        return FixedEvaluationTrial(
            task.task_id,
            revision,
            outcome,
            False,
            int((time.monotonic() - started) * 1000),
            unauthorized_attempts,
            "",
            (),
            (reason,),
        )
