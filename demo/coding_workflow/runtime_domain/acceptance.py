from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
    canonical_digest,
    enum_value,
    namespaced,
    nonempty,
    optional_ref_from_dict,
    optional_ref_to_dict,
    positive_int,
    require_fields,
    require_schema_version,
    sha256_digest,
    timestamp,
)


class OutcomeStatus(str, Enum):
    UNKNOWN = "unknown"
    NEEDS_INPUT = "needs_input"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AcceptanceSubjectType(str, Enum):
    TURN = "core:turn"
    TASK = "core:task"
    SCENARIO_RUN = "core:scenario_run"
    EXTERNAL_ACTION = "core:external_action"


def _outcomes(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[OutcomeStatus, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是 Outcome 数组")
    parsed = tuple(enum_value(item, OutcomeStatus, field_name) for item in value)
    if not allow_empty and not parsed:
        raise RuntimeProtocolError(f"{field_name} 不能为空")
    if len(parsed) != len(set(parsed)):
        raise RuntimeProtocolError(f"{field_name} 不能重复")
    return parsed


@dataclass(frozen=True)
class AcceptanceEvidence:
    """Evidence produced by an evaluator principal; Runtime remains issuer."""

    evidence_ref: ScopedRef
    evidence_kind: str
    subject_ref: ScopedRef
    observed_at: str
    content_hash: str
    evaluator_principal_id: str
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "AcceptanceEvidence")
        if not isinstance(self.evidence_ref, ScopedRef):
            raise RuntimeProtocolError("evidence_ref 必须是 ScopedRef")
        if not isinstance(self.subject_ref, ScopedRef):
            raise RuntimeProtocolError("subject_ref 必须是 ScopedRef")
        self.subject_ref.assert_scope(self.evidence_ref.scope_id, "subject_ref")
        object.__setattr__(
            self, "evidence_kind", namespaced(self.evidence_kind, "evidence_kind")
        )
        object.__setattr__(
            self, "observed_at", timestamp(self.observed_at, "observed_at")
        )
        object.__setattr__(
            self, "content_hash", sha256_digest(self.content_hash, "content_hash")
        )
        object.__setattr__(
            self,
            "evaluator_principal_id",
            nonempty(self.evaluator_principal_id, "evaluator_principal_id"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "evidence_ref": dict(self.evidence_ref.to_dict()),
            "evidence_kind": self.evidence_kind,
            "subject_ref": dict(self.subject_ref.to_dict()),
            "observed_at": self.observed_at,
            "content_hash": self.content_hash,
            "evaluator_principal_id": self.evaluator_principal_id,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AcceptanceEvidence":
        root = require_fields(
            value,
            type_name="AcceptanceEvidence",
            required=frozenset({
                "schema_version", "evidence_ref", "evidence_kind", "subject_ref",
                "observed_at", "content_hash", "evaluator_principal_id",
            }),
        )
        evidence_ref = root["evidence_ref"]
        subject_ref = root["subject_ref"]
        if not isinstance(evidence_ref, Mapping) or not isinstance(subject_ref, Mapping):
            raise RuntimeProtocolError("AcceptanceEvidence 引用必须是对象")
        return cls(
            ScopedRef.from_dict(evidence_ref),
            root["evidence_kind"],
            ScopedRef.from_dict(subject_ref),
            root["observed_at"],
            root["content_hash"],
            root["evaluator_principal_id"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class EvidenceRequirement:
    evidence_kind: str
    min_count: int = 1
    max_age_seconds: int | None = None
    bind_subject_version: bool = True
    required_for: tuple[OutcomeStatus, ...] = (OutcomeStatus.ACCEPTED,)
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "EvidenceRequirement")
        object.__setattr__(
            self, "evidence_kind", namespaced(self.evidence_kind, "evidence_kind")
        )
        object.__setattr__(self, "min_count", positive_int(self.min_count, "min_count"))
        if self.max_age_seconds is not None:
            object.__setattr__(
                self,
                "max_age_seconds",
                positive_int(self.max_age_seconds, "max_age_seconds"),
            )
        if not isinstance(self.bind_subject_version, bool):
            raise RuntimeProtocolError("bind_subject_version 必须是布尔值")
        object.__setattr__(
            self,
            "required_for",
            _outcomes(self.required_for, "required_for"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "min_count": self.min_count,
            "max_age_seconds": self.max_age_seconds,
            "bind_subject_version": self.bind_subject_version,
            "required_for": [item.value for item in self.required_for],
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceRequirement":
        root = require_fields(
            value,
            type_name="EvidenceRequirement",
            required=frozenset({
                "schema_version", "evidence_kind", "min_count", "max_age_seconds",
                "bind_subject_version", "required_for",
            }),
        )
        return cls(
            root["evidence_kind"],
            root["min_count"],
            root["max_age_seconds"],
            root["bind_subject_version"],
            root["required_for"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class AcceptancePolicy:
    scope_id: str
    policy_id: str
    subject_type: AcceptanceSubjectType
    evidence_requirements: tuple[EvidenceRequirement, ...]
    independent_evaluator_required: bool
    allowed_outcomes: tuple[OutcomeStatus, ...]
    version: int = 1
    policy_hash: str = ""
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "AcceptancePolicy")
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(self, "policy_id", nonempty(self.policy_id, "policy_id"))
        object.__setattr__(
            self,
            "subject_type",
            enum_value(self.subject_type, AcceptanceSubjectType, "subject_type"),
        )
        if not isinstance(self.evidence_requirements, (tuple, list)):
            raise RuntimeProtocolError("evidence_requirements 必须是数组")
        requirements = tuple(self.evidence_requirements)
        if not all(isinstance(item, EvidenceRequirement) for item in requirements):
            raise RuntimeProtocolError(
                "evidence_requirements 必须包含 EvidenceRequirement"
            )
        requirement_keys = tuple(
            (item.evidence_kind, item.required_for) for item in requirements
        )
        if len(requirement_keys) != len(set(requirement_keys)):
            raise RuntimeProtocolError("evidence_requirements 不能重复")
        object.__setattr__(self, "evidence_requirements", requirements)
        if not isinstance(self.independent_evaluator_required, bool):
            raise RuntimeProtocolError("independent_evaluator_required 必须是布尔值")
        outcomes = _outcomes(self.allowed_outcomes, "allowed_outcomes")
        if (
            OutcomeStatus.ACCEPTED in outcomes
            and not any(
                OutcomeStatus.ACCEPTED in item.required_for
                for item in requirements
            )
        ):
            raise RuntimeProtocolError(
                "允许 accepted 的 Policy 必须声明 accepted EvidenceRequirement"
            )
        object.__setattr__(self, "allowed_outcomes", outcomes)
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self, "created_at", timestamp(self.created_at, "created_at", default_now=True)
        )
        expected_hash = canonical_digest(self._hash_payload())
        supplied_hash = self.policy_hash
        if supplied_hash:
            supplied_hash = sha256_digest(supplied_hash, "policy_hash")
            if supplied_hash != expected_hash:
                raise RuntimeProtocolError("AcceptancePolicy hash 与内容不匹配")
        object.__setattr__(self, "policy_hash", expected_hash)

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, "core:acceptance_policy", self.policy_id, self.version
        )

    def _hash_payload(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "policy_id": self.policy_id,
            "subject_type": self.subject_type.value,
            "evidence_requirements": [
                dict(item.to_dict()) for item in self.evidence_requirements
            ],
            "independent_evaluator_required": self.independent_evaluator_required,
            "allowed_outcomes": [item.value for item in self.allowed_outcomes],
            "version": self.version,
            "created_at": self.created_at,
        }

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            **self._hash_payload(),
            "policy_hash": self.policy_hash,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AcceptancePolicy":
        root = require_fields(
            value,
            type_name="AcceptancePolicy",
            required=frozenset({
                "schema_version", "scope_id", "policy_id", "subject_type",
                "evidence_requirements", "independent_evaluator_required",
                "allowed_outcomes", "version", "policy_hash", "created_at",
            }),
        )
        requirements = root["evidence_requirements"]
        if not isinstance(requirements, (tuple, list)):
            raise RuntimeProtocolError("evidence_requirements 必须是数组")
        if not all(isinstance(item, Mapping) for item in requirements):
            raise RuntimeProtocolError("evidence_requirements 必须包含对象")
        return cls(
            root["scope_id"],
            root["policy_id"],
            root["subject_type"],
            tuple(EvidenceRequirement.from_dict(item) for item in requirements),
            root["independent_evaluator_required"],
            root["allowed_outcomes"],
            root["version"],
            root["policy_hash"],
            root["created_at"],
            root["schema_version"],
        )


def _validate_policy_decision(
    policy: AcceptancePolicy,
    subject_ref: ScopedRef,
    outcome: OutcomeStatus,
    evidence: tuple[AcceptanceEvidence, ...],
    producer_principal_id: str,
    evaluated_at: str,
) -> tuple[str, ...]:
    subject_ref.assert_scope(policy.scope_id, "subject_ref")
    if subject_ref.entity_type != policy.subject_type.value:
        raise RuntimeProtocolError(
            "AcceptancePolicy subject_type 与 subject_ref 不匹配"
        )
    if outcome not in policy.allowed_outcomes:
        raise RuntimeProtocolError(f"Policy 不允许 Outcome: {outcome.value}")
    evaluated = datetime.fromisoformat(evaluated_at)
    if evaluated < datetime.fromisoformat(policy.created_at):
        raise RuntimeProtocolError("evaluated_at 不能早于 Policy created_at")
    for item in evidence:
        item.evidence_ref.assert_scope(policy.scope_id, "evidence_ref")
        item.subject_ref.assert_scope(policy.scope_id, "evidence subject_ref")
        observed = datetime.fromisoformat(item.observed_at)
        if observed > evaluated:
            raise RuntimeProtocolError("Evidence observed_at 不能晚于 evaluated_at")
    for requirement in policy.evidence_requirements:
        if outcome not in requirement.required_for:
            continue
        matches = tuple(
            item for item in evidence
            if item.evidence_kind == requirement.evidence_kind
            and item.subject_ref.scope_id == subject_ref.scope_id
            and item.subject_ref.entity_type == subject_ref.entity_type
            and item.subject_ref.entity_id == subject_ref.entity_id
            and (
                not requirement.bind_subject_version
                or item.subject_ref.version == subject_ref.version
            )
            and (
                requirement.max_age_seconds is None
                or (evaluated - datetime.fromisoformat(item.observed_at)).total_seconds()
                <= requirement.max_age_seconds
            )
        )
        if len(matches) < requirement.min_count:
            raise RuntimeProtocolError(
                f"Acceptance Evidence 不足: {requirement.evidence_kind}"
            )
    evaluator_ids = tuple(dict.fromkeys(
        item.evaluator_principal_id for item in evidence
    ))
    if (
        outcome is OutcomeStatus.ACCEPTED
        and policy.independent_evaluator_required
        and (
            not evaluator_ids
            or producer_principal_id in evaluator_ids
        )
    ):
        raise RuntimeProtocolError("accepted Outcome 缺少独立 Evaluator")
    return evaluator_ids


@dataclass(frozen=True)
class AcceptanceRecord:
    """Runtime decision value.

    This v1 value enforces shape and ``issued_by=runtime``.  Authorization of
    the writer is deliberately a PROD-01B Store boundary; parsing an
    Agent-supplied lookalike must never persist it as an accepted record.
    """

    scope_id: str
    record_id: str
    subject_ref: ScopedRef
    policy_ref: ScopedRef
    policy_hash: str
    outcome: OutcomeStatus
    evidence: tuple[AcceptanceEvidence, ...]
    producer_principal_id: str
    evaluator_principal_ids: tuple[str, ...]
    evaluated_at: str
    version: int = 1
    issued_by: str = field(default="runtime", init=False)
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "AcceptanceRecord")
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(self, "record_id", nonempty(self.record_id, "record_id"))
        if not isinstance(self.subject_ref, ScopedRef):
            raise RuntimeProtocolError("subject_ref 必须是 ScopedRef")
        self.subject_ref.assert_scope(self.scope_id, "subject_ref")
        if self.subject_ref.entity_type not in {
            item.value for item in AcceptanceSubjectType
        }:
            raise RuntimeProtocolError("Thread 等长期对象不能作为 Acceptance subject")
        if not isinstance(self.policy_ref, ScopedRef):
            raise RuntimeProtocolError("policy_ref 必须是 ScopedRef")
        self.policy_ref.assert_scope(self.scope_id, "policy_ref")
        self.policy_ref.assert_type("core:acceptance_policy")
        object.__setattr__(
            self, "policy_hash", sha256_digest(self.policy_hash, "policy_hash")
        )
        object.__setattr__(
            self, "outcome", enum_value(self.outcome, OutcomeStatus, "outcome")
        )
        if not isinstance(self.evidence, (tuple, list)):
            raise RuntimeProtocolError("evidence 必须是数组")
        evidence = tuple(self.evidence)
        if not all(isinstance(item, AcceptanceEvidence) for item in evidence):
            raise RuntimeProtocolError("evidence 必须包含 AcceptanceEvidence")
        for item in evidence:
            item.evidence_ref.assert_scope(self.scope_id, "evidence")
        evidence_identities = tuple(
            (
                item.evidence_ref.scope_id,
                item.evidence_ref.entity_type,
                item.evidence_ref.entity_id,
            )
            for item in evidence
        )
        if len(evidence_identities) != len(set(evidence_identities)):
            raise RuntimeProtocolError(
                "同一 evidence 实体的多个版本不能重复计入"
            )
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(
            self,
            "producer_principal_id",
            nonempty(self.producer_principal_id, "producer_principal_id"),
        )
        if not isinstance(self.evaluator_principal_ids, (tuple, list)):
            raise RuntimeProtocolError("evaluator_principal_ids 必须是数组")
        evaluator_ids = tuple(
            nonempty(item, "evaluator_principal_ids")
            for item in self.evaluator_principal_ids
        )
        if len(evaluator_ids) != len(set(evaluator_ids)):
            raise RuntimeProtocolError("evaluator_principal_ids 不能重复")
        if set(evaluator_ids) != {
            item.evaluator_principal_id for item in evidence
        }:
            raise RuntimeProtocolError(
                "evaluator_principal_ids 必须与 Evidence 提供者一致"
            )
        object.__setattr__(self, "evaluator_principal_ids", evaluator_ids)
        object.__setattr__(
            self, "evaluated_at", timestamp(self.evaluated_at, "evaluated_at")
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, "core:acceptance_record", self.record_id, self.version
        )

    @classmethod
    def issue(
        cls,
        policy: AcceptancePolicy,
        subject_ref: ScopedRef,
        outcome: OutcomeStatus,
        evidence: tuple[AcceptanceEvidence, ...],
        *,
        producer_principal_id: str,
        evaluated_at: str,
        record_id: str,
        version: int = 1,
    ) -> "AcceptanceRecord":
        if not isinstance(policy, AcceptancePolicy):
            raise RuntimeProtocolError("policy 必须是 AcceptancePolicy")
        parsed_outcome = enum_value(outcome, OutcomeStatus, "outcome")
        parsed_time = timestamp(evaluated_at, "evaluated_at")
        producer = nonempty(producer_principal_id, "producer_principal_id")
        evidence_items = tuple(evidence)
        evaluator_ids = _validate_policy_decision(
            policy,
            subject_ref,
            parsed_outcome,
            evidence_items,
            producer,
            parsed_time,
        )
        return cls(
            policy.scope_id,
            record_id,
            subject_ref,
            policy.reference,
            policy.policy_hash,
            parsed_outcome,
            evidence_items,
            producer,
            evaluator_ids,
            parsed_time,
            version,
        )

    def validate_against(self, policy: AcceptancePolicy) -> None:
        if self.policy_ref != policy.reference or self.policy_hash != policy.policy_hash:
            raise RuntimeProtocolError("AcceptanceRecord 与 Policy 版本/hash 不匹配")
        evaluator_ids = _validate_policy_decision(
            policy,
            self.subject_ref,
            self.outcome,
            self.evidence,
            self.producer_principal_id,
            self.evaluated_at,
        )
        if evaluator_ids != self.evaluator_principal_ids:
            raise RuntimeProtocolError("AcceptanceRecord evaluator 不匹配")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "record_id": self.record_id,
            "subject_ref": dict(self.subject_ref.to_dict()),
            "policy_ref": dict(self.policy_ref.to_dict()),
            "policy_hash": self.policy_hash,
            "outcome": self.outcome.value,
            "evidence": [dict(item.to_dict()) for item in self.evidence],
            "producer_principal_id": self.producer_principal_id,
            "evaluator_principal_ids": list(self.evaluator_principal_ids),
            "evaluated_at": self.evaluated_at,
            "version": self.version,
            "issued_by": self.issued_by,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AcceptanceRecord":
        root = require_fields(
            value,
            type_name="AcceptanceRecord",
            required=frozenset({
                "schema_version", "scope_id", "record_id", "subject_ref",
                "policy_ref", "policy_hash", "outcome", "evidence",
                "producer_principal_id", "evaluator_principal_ids",
                "evaluated_at", "version", "issued_by",
            }),
        )
        if root["issued_by"] != "runtime":
            raise RuntimeProtocolError("AcceptanceRecord 只能由 Runtime 签发")
        subject_ref = root["subject_ref"]
        policy_ref = root["policy_ref"]
        evidence = root["evidence"]
        if not isinstance(subject_ref, Mapping) or not isinstance(policy_ref, Mapping):
            raise RuntimeProtocolError("AcceptanceRecord 引用必须是对象")
        if not isinstance(evidence, (tuple, list)) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise RuntimeProtocolError("AcceptanceRecord evidence 必须是对象数组")
        return cls(
            root["scope_id"],
            root["record_id"],
            ScopedRef.from_dict(subject_ref),
            ScopedRef.from_dict(policy_ref),
            root["policy_hash"],
            root["outcome"],
            tuple(AcceptanceEvidence.from_dict(item) for item in evidence),
            root["producer_principal_id"],
            root["evaluator_principal_ids"],
            root["evaluated_at"],
            root["version"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class Outcome:
    scope_id: str
    outcome_id: str
    subject_ref: ScopedRef
    status: OutcomeStatus
    acceptance_record_ref: ScopedRef | None = None
    version: int = 1
    updated_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "Outcome")
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(self, "outcome_id", nonempty(self.outcome_id, "outcome_id"))
        if not isinstance(self.subject_ref, ScopedRef):
            raise RuntimeProtocolError("subject_ref 必须是 ScopedRef")
        self.subject_ref.assert_scope(self.scope_id, "subject_ref")
        if self.subject_ref.entity_type not in {
            item.value for item in AcceptanceSubjectType
        }:
            raise RuntimeProtocolError(
                "Thread 等长期对象不能作为 Outcome subject"
            )
        object.__setattr__(
            self, "status", enum_value(self.status, OutcomeStatus, "status")
        )
        if self.acceptance_record_ref is not None:
            if not isinstance(self.acceptance_record_ref, ScopedRef):
                raise RuntimeProtocolError(
                    "acceptance_record_ref 必须是 ScopedRef 或 null"
                )
            self.acceptance_record_ref.assert_scope(
                self.scope_id, "acceptance_record_ref"
            )
            self.acceptance_record_ref.assert_type("core:acceptance_record")
        if (
            self.status is not OutcomeStatus.UNKNOWN
            and self.acceptance_record_ref is None
        ):
            raise RuntimeProtocolError("非 unknown Outcome 必须引用 AcceptanceRecord")
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self, "updated_at", timestamp(self.updated_at, "updated_at", default_now=True)
        )

    @classmethod
    def from_record(
        cls,
        outcome_id: str,
        record: AcceptanceRecord,
        *,
        version: int = 1,
    ) -> "Outcome":
        return cls(
            record.scope_id,
            outcome_id,
            record.subject_ref,
            record.outcome,
            record.reference,
            version,
            record.evaluated_at,
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "outcome_id": self.outcome_id,
            "subject_ref": dict(self.subject_ref.to_dict()),
            "status": self.status.value,
            "acceptance_record_ref": optional_ref_to_dict(
                self.acceptance_record_ref
            ),
            "version": self.version,
            "updated_at": self.updated_at,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Outcome":
        root = require_fields(
            value,
            type_name="Outcome",
            required=frozenset({
                "schema_version", "scope_id", "outcome_id", "subject_ref",
                "status", "acceptance_record_ref", "version", "updated_at",
            }),
        )
        subject_ref = root["subject_ref"]
        if not isinstance(subject_ref, Mapping):
            raise RuntimeProtocolError("subject_ref 必须是引用对象")
        return cls(
            root["scope_id"],
            root["outcome_id"],
            ScopedRef.from_dict(subject_ref),
            root["status"],
            optional_ref_from_dict(
                root["acceptance_record_ref"], "acceptance_record_ref"
            ),
            root["version"],
            root["updated_at"],
            root["schema_version"],
        )
