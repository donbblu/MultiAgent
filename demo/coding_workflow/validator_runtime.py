from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol

from .artifacts import Artifact, ArtifactDraft, ArtifactStore
from .requirements import ValidatorProfile, ValidatorSpec
from .truth import (
    VerificationOutcome,
    VerificationRecord,
    workspace_digest,
)


_VALIDATOR_KIND = re.compile(
    r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$"
)


@dataclass(frozen=True)
class ValidatorRunRequest:
    task_id: str
    spec: ValidatorSpec
    subjects: Mapping[str, Artifact]
    workspace_hashes: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id 不能为空")
        object.__setattr__(self, "subjects", MappingProxyType(dict(self.subjects)))
        object.__setattr__(
            self, "workspace_hashes", MappingProxyType(dict(self.workspace_hashes))
        )


@dataclass(frozen=True)
class ValidatorRunResult:
    outcome: VerificationOutcome
    summary: str
    evidence: tuple[ArtifactDraft, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, VerificationOutcome):
            object.__setattr__(
                self, "outcome", VerificationOutcome(self.outcome)
            )
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("ValidatorRunResult summary 不能为空")
        if not isinstance(self.evidence, (tuple, list)) or not all(
            isinstance(item, ArtifactDraft) for item in self.evidence
        ):
            raise ValueError("Validator evidence 必须是 ArtifactDraft 数组")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if self.outcome is not VerificationOutcome.UNKNOWN and not self.evidence:
            raise ValueError("passed/failed Validator 必须返回证据 Artifact")


class RuntimeValidator(Protocol):
    def validate(self, request: ValidatorRunRequest) -> ValidatorRunResult: ...


@dataclass(frozen=True)
class RegisteredValidator:
    validator_kind: str
    principal_id: str
    validator: RuntimeValidator


class ValidatorRegistry:
    """Runtime 拥有的 Validator 注册表；模型不能注册或替换实现。"""

    def __init__(self) -> None:
        self._validators: dict[str, RegisteredValidator] = {}

    def register(
        self,
        validator_kind: str,
        validator: RuntimeValidator,
        *,
        principal_id: str = "",
        replace: bool = False,
    ) -> None:
        if not isinstance(validator_kind, str) or not _VALIDATOR_KIND.fullmatch(
            validator_kind
        ):
            raise ValueError("validator_kind 必须使用 namespace:name")
        if not hasattr(validator, "validate"):
            raise TypeError("Validator 必须实现 validate")
        principal = principal_id or f"validator:{validator_kind}"
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("Validator principal_id 不能为空")
        if validator_kind in self._validators and not replace:
            raise ValueError(f"Validator 已注册: {validator_kind}")
        self._validators[validator_kind] = RegisteredValidator(
            validator_kind, principal, validator
        )

    def resolve(self, validator_kind: str) -> RuntimeValidator | None:
        registration = self._validators.get(validator_kind)
        return registration.validator if registration else None

    def registration(self, validator_kind: str) -> RegisteredValidator | None:
        return self._validators.get(validator_kind)

    def snapshot(self) -> tuple[str, ...]:
        return tuple(sorted(self._validators))


@dataclass(frozen=True)
class ProfileVerificationResult:
    outcome: VerificationOutcome
    verification_ref: str
    report_artifact_ref: str
    validator_records: tuple[VerificationRecord, ...]


class ValidatorProfileRunner:
    """按冻结 Profile 执行可信 Validator，并用一个组合门禁更新事实状态。"""

    def __init__(
        self,
        profile: ValidatorProfile,
        registry: ValidatorRegistry,
        artifacts: ArtifactStore,
    ) -> None:
        self.profile = profile
        self.registry = registry
        self.artifacts = artifacts

    def run(
        self,
        *,
        task_id: str,
        subject_refs: tuple[str, ...],
        workspace_hashes: Mapping[str, str] | None = None,
    ) -> ProfileVerificationResult:
        subjects = MappingProxyType({
            reference: self.artifacts.get(reference)
            for reference in subject_refs
        })
        subject_hashes = MappingProxyType({
            reference: artifact.content_hash
            for reference, artifact in subjects.items()
        })
        hashes = MappingProxyType(dict(workspace_hashes or {}))
        records: list[VerificationRecord] = []
        raw_evidence_refs: list[str] = []

        for spec in self.profile.validators:
            registration = self.registry.registration(spec.validator_kind)
            validator = registration.validator if registration else None
            producer_principals = {
                provenance.get("principal_id")
                for artifact in subjects.values()
                for provenance in (artifact.metadata.get("runtime_provenance", {}),)
                if isinstance(provenance, Mapping)
                and isinstance(provenance.get("principal_id"), str)
                and provenance.get("principal_id")
            }
            if spec.bind_workspace and not hashes:
                result = ValidatorRunResult(
                    VerificationOutcome.UNKNOWN,
                    f"Validator 缺少 Workspace hash: {spec.validator_kind}",
                )
            elif validator is None:
                result = ValidatorRunResult(
                    VerificationOutcome.UNKNOWN,
                    f"Validator 不可用: {spec.validator_kind}",
                )
            elif registration.principal_id in producer_principals:
                result = ValidatorRunResult(
                    VerificationOutcome.UNKNOWN,
                    "Validator 与被验证 Artifact 的生产者属于同一 principal，"
                    f"拒绝自证: {registration.principal_id}",
                )
            else:
                try:
                    result = validator.validate(ValidatorRunRequest(
                        task_id, spec, subjects, hashes
                    ))
                    if not isinstance(result, ValidatorRunResult):
                        raise TypeError("Validator 必须返回 ValidatorRunResult")
                except Exception as exc:
                    result = ValidatorRunResult(
                        VerificationOutcome.UNKNOWN,
                        f"Validator 执行异常: {type(exc).__name__}: {exc}",
                    )

            evidence_refs = tuple(
                self.artifacts.put(draft.materialize(
                    f"validator-evidence:{spec.validator_id}:{index}", task_id
                ))
                for index, draft in enumerate(result.evidence, start=1)
            )
            raw_evidence_refs.extend(evidence_refs)
            records.append(VerificationRecord.create(
                spec.validator_kind,
                result.outcome,
                subject_refs,
                evidence_refs,
                result.summary,
                subject_hashes=subject_hashes,
                workspace_hash=(
                    workspace_digest(hashes)
                    if spec.bind_workspace and hashes else ""
                ),
            ))

        outcome = self.profile.decide(tuple(records))
        report_ref = self.artifacts.put(Artifact.create(
            "validator-profile-report",
            task_id,
            {
                "profile_ref": self.profile.reference,
                "outcome": outcome.value,
                "validators": [dict(record.to_dict()) for record in records],
                "evidence_refs": raw_evidence_refs,
            },
            kind="validator_profile_report",
            metadata={"profile_id": self.profile.profile_id},
        ))
        gate = VerificationRecord.create(
            "core:profile_gate",
            outcome,
            subject_refs,
            (report_ref,),
            f"ValidatorProfile {self.profile.profile_id}: {outcome.value}",
            subject_hashes=subject_hashes,
            workspace_hash=(
                workspace_digest(hashes)
                if hashes and any(
                    item.bind_workspace for item in self.profile.validators
                )
                else ""
            ),
        )
        verification_ref = self.artifacts.record_verification(gate)
        return ProfileVerificationResult(
            outcome,
            verification_ref,
            report_ref,
            tuple(records),
        )
