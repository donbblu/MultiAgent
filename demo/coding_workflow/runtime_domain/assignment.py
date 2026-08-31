from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Mapping

from .common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopedRef,
    enum_value,
    namespaced,
    nonempty,
    optional_ref_from_dict,
    optional_ref_to_dict,
    positive_int,
    refs_from_dict,
    refs_to_dict,
    require_fields,
    require_schema_version,
    scoped_refs,
    string_tuple,
    timestamp,
)
from .interaction import AgentSessionState


class AssignmentRisk(str, Enum):
    NORMAL = "normal"
    HIGH = "high"


class AgentAvailability(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"


class AssignmentDecision(str, Enum):
    ASSIGNED = "assigned"
    WAITING = "waiting"
    NEEDS_INPUT = "needs_input"


def _ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_types: tuple[str, ...],
) -> ScopedRef:
    if not isinstance(value, ScopedRef):
        raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef")
    value.assert_scope(scope_id, field_name)
    value.assert_type(*entity_types)
    return value


def _optional_ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_types: tuple[str, ...],
) -> ScopedRef | None:
    if value is None:
        return None
    return _ref(value, field_name, scope_id=scope_id, entity_types=entity_types)


def _score(value: object, field_name: str) -> int:
    parsed = positive_int(value, field_name, allow_zero=True)
    if parsed > 100:
        raise RuntimeProtocolError(f"{field_name} 必须小于等于 100")
    return parsed


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeProtocolError(f"{field_name} 必须是布尔值")
    return value


@dataclass(frozen=True)
class RoleRequirement:
    requirement_id: str
    scope_id: str
    thread_ref: ScopedRef
    work_ref: ScopedRef
    role_ref: ScopedRef
    required_capabilities: tuple[str, ...] = ()
    risk: AssignmentRisk = AssignmentRisk.NORMAL
    continuity_required: bool = False
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:role_requirement"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "requirement_id", nonempty(self.requirement_id, "requirement_id"))
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "thread_ref",
            _ref(self.thread_ref, "thread_ref", scope_id=scope_id, entity_types=("core:thread",)),
        )
        object.__setattr__(
            self,
            "work_ref",
            _ref(
                self.work_ref,
                "work_ref",
                scope_id=scope_id,
                entity_types=("core:invocation", "core:task", "core:turn"),
            ),
        )
        object.__setattr__(
            self,
            "role_ref",
            _ref(self.role_ref, "role_ref", scope_id=scope_id, entity_types=("core:agent_role",)),
        )
        object.__setattr__(
            self,
            "required_capabilities",
            string_tuple(
                self.required_capabilities,
                "required_capabilities",
                require_namespaced=True,
            ),
        )
        object.__setattr__(self, "risk", enum_value(self.risk, AssignmentRisk, "risk"))
        object.__setattr__(
            self,
            "continuity_required",
            _bool(self.continuity_required, "continuity_required"),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", timestamp(self.created_at, "created_at", default_now=True))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "RoleRequirement"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(self.scope_id, self.REFERENCE_TYPE, self.requirement_id, self.version)

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "requirement_id": self.requirement_id,
            "scope_id": self.scope_id,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "work_ref": dict(self.work_ref.to_dict()),
            "role_ref": dict(self.role_ref.to_dict()),
            "required_capabilities": list(self.required_capabilities),
            "risk": self.risk.value,
            "continuity_required": self.continuity_required,
            "version": self.version,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RoleRequirement":
        root = require_fields(
            value,
            type_name="RoleRequirement",
            required=frozenset({
                "schema_version", "requirement_id", "scope_id", "thread_ref",
                "work_ref", "role_ref", "required_capabilities", "risk",
                "continuity_required", "version", "created_at",
            }),
        )
        refs = {}
        for field_name in ("thread_ref", "work_ref", "role_ref"):
            raw = root[field_name]
            if not isinstance(raw, Mapping):
                raise RuntimeProtocolError(f"{field_name} 必须是引用对象")
            refs[field_name] = ScopedRef.from_dict(raw)
        return cls(
            requirement_id=root["requirement_id"],
            scope_id=root["scope_id"],
            thread_ref=refs["thread_ref"],
            work_ref=refs["work_ref"],
            role_ref=refs["role_ref"],
            required_capabilities=root["required_capabilities"],
            risk=root["risk"],
            continuity_required=root["continuity_required"],
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class AgentCandidate:
    agent_instance_ref: ScopedRef
    agent_session_ref: ScopedRef
    profile_ref: ScopedRef
    capabilities: tuple[str, ...] = ()
    session_state: AgentSessionState = AgentSessionState.ACTIVE
    availability: AgentAvailability = AgentAvailability.AVAILABLE
    estimated_wait_seconds: int = 0
    affinity_score: int = 0
    quality_score: int = 0
    cost_rank: int = 0
    latency_rank: int = 0
    permissions_granted: bool = True
    tools_available: bool = True
    context_available: bool = True
    provider_healthy: bool = True
    budget_available: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.agent_instance_ref, ScopedRef):
            raise RuntimeProtocolError("agent_instance_ref 必须是 ScopedRef")
        scope_id = self.agent_instance_ref.scope_id
        self.agent_instance_ref.assert_type("core:agent_instance")
        for field_name, reference, entity_type in (
            ("agent_session_ref", self.agent_session_ref, "core:agent_session"),
            ("profile_ref", self.profile_ref, "core:agent_profile"),
        ):
            object.__setattr__(
                self,
                field_name,
                _ref(reference, field_name, scope_id=scope_id, entity_types=(entity_type,)),
            )
        object.__setattr__(
            self,
            "capabilities",
            string_tuple(self.capabilities, "capabilities", require_namespaced=True),
        )
        object.__setattr__(
            self,
            "session_state",
            enum_value(self.session_state, AgentSessionState, "session_state"),
        )
        object.__setattr__(
            self,
            "availability",
            enum_value(self.availability, AgentAvailability, "availability"),
        )
        object.__setattr__(
            self,
            "estimated_wait_seconds",
            positive_int(self.estimated_wait_seconds, "estimated_wait_seconds", allow_zero=True),
        )
        object.__setattr__(self, "affinity_score", _score(self.affinity_score, "affinity_score"))
        object.__setattr__(self, "quality_score", _score(self.quality_score, "quality_score"))
        object.__setattr__(self, "cost_rank", positive_int(self.cost_rank, "cost_rank", allow_zero=True))
        object.__setattr__(self, "latency_rank", positive_int(self.latency_rank, "latency_rank", allow_zero=True))
        for field_name in (
            "permissions_granted", "tools_available", "context_available",
            "provider_healthy", "budget_available",
        ):
            object.__setattr__(self, field_name, _bool(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class CandidateEvaluation:
    agent_instance_ref: ScopedRef
    agent_session_ref: ScopedRef
    profile_ref: ScopedRef
    capabilities: tuple[str, ...]
    session_state: AgentSessionState
    availability: AgentAvailability
    estimated_wait_seconds: int
    affinity_score: int
    quality_score: int
    cost_rank: int
    latency_rank: int
    eligible: bool
    rejection_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        candidate = AgentCandidate(
            agent_instance_ref=self.agent_instance_ref,
            agent_session_ref=self.agent_session_ref,
            profile_ref=self.profile_ref,
            capabilities=self.capabilities,
            session_state=self.session_state,
            availability=self.availability,
            estimated_wait_seconds=self.estimated_wait_seconds,
            affinity_score=self.affinity_score,
            quality_score=self.quality_score,
            cost_rank=self.cost_rank,
            latency_rank=self.latency_rank,
        )
        for field_name in (
            "agent_instance_ref", "agent_session_ref", "profile_ref", "capabilities",
            "session_state", "availability", "estimated_wait_seconds",
            "affinity_score", "quality_score", "cost_rank", "latency_rank",
        ):
            object.__setattr__(self, field_name, getattr(candidate, field_name))
        object.__setattr__(self, "eligible", _bool(self.eligible, "eligible"))
        object.__setattr__(
            self,
            "rejection_codes",
            string_tuple(self.rejection_codes, "rejection_codes"),
        )
        if self.eligible and self.rejection_codes:
            raise RuntimeProtocolError("eligible 候选不能包含 rejection_codes")
        if self.eligible and self.session_state is not AgentSessionState.ACTIVE:
            raise RuntimeProtocolError("eligible 候选的 Session 必须是 active")
        if not self.eligible and not self.rejection_codes:
            raise RuntimeProtocolError("不合格候选必须包含 rejection_codes")

    @property
    def rank_key(self) -> tuple[int, int, int, int, str]:
        return (
            -self.affinity_score,
            -self.quality_score,
            self.cost_rank,
            self.latency_rank,
            self.agent_instance_ref.entity_id,
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "agent_instance_ref": dict(self.agent_instance_ref.to_dict()),
            "agent_session_ref": dict(self.agent_session_ref.to_dict()),
            "profile_ref": dict(self.profile_ref.to_dict()),
            "capabilities": list(self.capabilities),
            "session_state": self.session_state.value,
            "availability": self.availability.value,
            "estimated_wait_seconds": self.estimated_wait_seconds,
            "affinity_score": self.affinity_score,
            "quality_score": self.quality_score,
            "cost_rank": self.cost_rank,
            "latency_rank": self.latency_rank,
            "eligible": self.eligible,
            "rejection_codes": list(self.rejection_codes),
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateEvaluation":
        root = require_fields(
            value,
            type_name="CandidateEvaluation",
            required=frozenset({
                "agent_instance_ref", "agent_session_ref", "profile_ref", "capabilities",
                "session_state", "availability", "estimated_wait_seconds",
                "affinity_score", "quality_score", "cost_rank", "latency_rank",
                "eligible", "rejection_codes",
            }),
        )
        refs = {}
        for field_name in ("agent_instance_ref", "agent_session_ref", "profile_ref"):
            raw = root[field_name]
            if not isinstance(raw, Mapping):
                raise RuntimeProtocolError(f"{field_name} 必须是引用对象")
            refs[field_name] = ScopedRef.from_dict(raw)
        return cls(
            agent_instance_ref=refs["agent_instance_ref"],
            agent_session_ref=refs["agent_session_ref"],
            profile_ref=refs["profile_ref"],
            capabilities=root["capabilities"],
            session_state=root["session_state"],
            availability=root["availability"],
            estimated_wait_seconds=root["estimated_wait_seconds"],
            affinity_score=root["affinity_score"],
            quality_score=root["quality_score"],
            cost_rank=root["cost_rank"],
            latency_rank=root["latency_rank"],
            eligible=root["eligible"],
            rejection_codes=root["rejection_codes"],
        )


@dataclass(frozen=True)
class RoleAssignmentPolicy:
    policy_version: str
    max_wait_for_best_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", nonempty(self.policy_version, "policy_version"))
        object.__setattr__(
            self,
            "max_wait_for_best_seconds",
            positive_int(self.max_wait_for_best_seconds, "max_wait_for_best_seconds", allow_zero=True),
        )


@dataclass(frozen=True)
class RoleAssignment:
    assignment_id: str
    requirement: RoleRequirement
    decision: AssignmentDecision
    reason_code: str
    policy_version: str
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    selected_agent_instance_ref: ScopedRef | None = None
    selected_agent_session_ref: ScopedRef | None = None
    selected_profile_ref: ScopedRef | None = None
    generation: int = 1
    supersedes_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:role_assignment"

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, RoleRequirement):
            raise RuntimeProtocolError("requirement 必须是 RoleRequirement")
        scope_id = self.requirement.scope_id
        object.__setattr__(self, "assignment_id", nonempty(self.assignment_id, "assignment_id"))
        object.__setattr__(self, "decision", enum_value(self.decision, AssignmentDecision, "decision"))
        object.__setattr__(self, "reason_code", nonempty(self.reason_code, "reason_code"))
        object.__setattr__(self, "policy_version", nonempty(self.policy_version, "policy_version"))
        if not isinstance(self.candidate_evaluations, (tuple, list)):
            raise RuntimeProtocolError("candidate_evaluations 必须是数组")
        evaluations = tuple(self.candidate_evaluations)
        if not all(isinstance(item, CandidateEvaluation) for item in evaluations):
            raise RuntimeProtocolError("candidate_evaluations 类型无效")
        agent_ids = tuple(item.agent_instance_ref.entity_id for item in evaluations)
        if len(agent_ids) != len(set(agent_ids)):
            raise RuntimeProtocolError("candidate_evaluations 不能重复 Agent")
        for item in evaluations:
            item.agent_instance_ref.assert_scope(scope_id, "candidate_evaluations")
        object.__setattr__(self, "candidate_evaluations", evaluations)
        selected = (
            _optional_ref(
                self.selected_agent_instance_ref,
                "selected_agent_instance_ref",
                scope_id=scope_id,
                entity_types=("core:agent_instance",),
            ),
            _optional_ref(
                self.selected_agent_session_ref,
                "selected_agent_session_ref",
                scope_id=scope_id,
                entity_types=("core:agent_session",),
            ),
            _optional_ref(
                self.selected_profile_ref,
                "selected_profile_ref",
                scope_id=scope_id,
                entity_types=("core:agent_profile",),
            ),
        )
        if self.decision is AssignmentDecision.ASSIGNED:
            if any(item is None for item in selected):
                raise RuntimeProtocolError("assigned 决策必须包含完整 selected refs")
            selected_id = selected[0].entity_id  # type: ignore[union-attr]
            matching = [item for item in evaluations if item.agent_instance_ref.entity_id == selected_id]
            if len(matching) != 1 or not matching[0].eligible:
                raise RuntimeProtocolError("selected Agent 必须是唯一合格候选")
        elif any(item is not None for item in selected):
            raise RuntimeProtocolError("非 assigned 决策不能包含 selected refs")
        object.__setattr__(self, "selected_agent_instance_ref", selected[0])
        object.__setattr__(self, "selected_agent_session_ref", selected[1])
        object.__setattr__(self, "selected_profile_ref", selected[2])
        generation = positive_int(self.generation, "generation")
        supersedes = _optional_ref(
            self.supersedes_ref,
            "supersedes_ref",
            scope_id=scope_id,
            entity_types=(self.REFERENCE_TYPE,),
        )
        if generation == 1 and supersedes is not None:
            raise RuntimeProtocolError("首个 Assignment 不能 supersede")
        if generation > 1 and supersedes is None:
            raise RuntimeProtocolError("后续 Assignment 必须包含 supersedes_ref")
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "supersedes_ref", supersedes)
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", timestamp(self.created_at, "created_at", default_now=True))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "RoleAssignment"),
        )

    @property
    def scope_id(self) -> str:
        return self.requirement.scope_id

    @property
    def thread_id(self) -> str:
        return self.requirement.thread_id

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(self.scope_id, self.REFERENCE_TYPE, self.assignment_id, self.version)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "assignment_id": self.assignment_id,
            "requirement": dict(self.requirement.to_dict()),
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "candidate_evaluations": [dict(item.to_dict()) for item in self.candidate_evaluations],
            "selected_agent_instance_ref": optional_ref_to_dict(self.selected_agent_instance_ref),
            "selected_agent_session_ref": optional_ref_to_dict(self.selected_agent_session_ref),
            "selected_profile_ref": optional_ref_to_dict(self.selected_profile_ref),
            "generation": self.generation,
            "supersedes_ref": optional_ref_to_dict(self.supersedes_ref),
            "version": self.version,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RoleAssignment":
        root = require_fields(
            value,
            type_name="RoleAssignment",
            required=frozenset({
                "schema_version", "assignment_id", "requirement", "decision",
                "reason_code", "policy_version", "candidate_evaluations",
                "selected_agent_instance_ref", "selected_agent_session_ref",
                "selected_profile_ref", "generation", "supersedes_ref", "version",
                "created_at",
            }),
        )
        requirement = root["requirement"]
        evaluations = root["candidate_evaluations"]
        if not isinstance(requirement, Mapping):
            raise RuntimeProtocolError("requirement 必须是对象")
        if not isinstance(evaluations, (tuple, list)) or not all(isinstance(item, Mapping) for item in evaluations):
            raise RuntimeProtocolError("candidate_evaluations 必须是对象数组")
        return cls(
            assignment_id=root["assignment_id"],
            requirement=RoleRequirement.from_dict(requirement),
            decision=root["decision"],
            reason_code=root["reason_code"],
            policy_version=root["policy_version"],
            candidate_evaluations=tuple(CandidateEvaluation.from_dict(item) for item in evaluations),
            selected_agent_instance_ref=optional_ref_from_dict(root["selected_agent_instance_ref"], "selected_agent_instance_ref"),
            selected_agent_session_ref=optional_ref_from_dict(root["selected_agent_session_ref"], "selected_agent_session_ref"),
            selected_profile_ref=optional_ref_from_dict(root["selected_profile_ref"], "selected_profile_ref"),
            generation=root["generation"],
            supersedes_ref=optional_ref_from_dict(root["supersedes_ref"], "supersedes_ref"),
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )
