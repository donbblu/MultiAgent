from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .artifacts import Artifact
from .harness.registry import WorkerDescriptor
from .harness.task_graph import TaskSpec
from .roles import Capability, RoleSpec
from .runtime_domain.acceptance import (
    AcceptanceEvidence,
    AcceptanceSubjectType,
)
from .runtime_domain.common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
    canonical_digest,
    namespaced,
    nonempty,
    positive_int,
    require_fields,
    require_schema_version,
    sha256_digest,
    string_tuple,
)
from .runtime_domain.interaction import AgentProfile, AgentRole
from .runtime_domain.invocation import InvocationInputRef
from .truth import VerificationOutcome, VerificationRecord


CODING_RUNTIME_COMPAT_VERSION = RUNTIME_PROTOCOL_VERSION


def _coding_identifier(value: object, field_name: str) -> str:
    raw = nonempty(value, field_name)
    if ":" in raw:
        parsed = namespaced(raw, field_name)
        if not parsed.startswith("coding:"):
            raise RuntimeProtocolError(f"{field_name} 必须属于 coding namespace")
        return parsed
    return namespaced(f"coding:{raw}", field_name)


def _coding_or_existing_identifier(value: object, field_name: str) -> str:
    if isinstance(value, Enum):
        value = value.value
    raw = nonempty(value, field_name)
    return namespaced(raw if ":" in raw else f"coding:{raw}", field_name)


def _legacy_identifier(value: str, field_name: str) -> str:
    parsed = _coding_identifier(value, field_name)
    return parsed.removeprefix("coding:")


def _strict_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是字符串数组")
    return tuple(nonempty(item, field_name) for item in value)


def _strict_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeProtocolError(f"{field_name} 必须是整数")
    return value


def role_spec_to_agent_role(
    role: RoleSpec,
    *,
    scope_id: str,
    created_at: str,
    version: int = 1,
) -> AgentRole:
    """Map a Coding RoleSpec into a generic, provider-neutral AgentRole."""

    if not isinstance(role, RoleSpec):
        raise TypeError("role 必须是 RoleSpec")
    capabilities = tuple(sorted(
        _coding_or_existing_identifier(item, "capabilities")
        for item in role.capabilities
    ))
    return AgentRole(
        role_id=_coding_identifier(role.name, "role.name"),
        scope_id=scope_id,
        objective=role.objective,
        # RoleSpec has no separate responsibility field. Its objective is the
        # lossless responsibility statement; instructions remain constraints.
        responsibilities=(role.objective,),
        constraints=tuple(role.instructions),
        capability_ceiling=capabilities,
        version=version,
        created_at=created_at,
    )


def agent_role_to_role_spec(role: AgentRole) -> RoleSpec:
    """Reverse only AgentRole values produced by role_spec_to_agent_role."""

    if not isinstance(role, AgentRole):
        raise TypeError("role 必须是 AgentRole")
    if role.responsibilities != (role.objective,):
        raise RuntimeProtocolError("AgentRole 不是无损的 Coding RoleSpec 映射")
    capability_values: set[Capability] = set()
    for capability_id in role.capability_ceiling:
        legacy = _legacy_identifier(capability_id, "capability_ceiling")
        try:
            capability_values.add(Capability(legacy))
        except ValueError as exc:
            raise RuntimeProtocolError(
                f"未知 Coding Capability: {capability_id}"
            ) from exc
    return RoleSpec(
        _legacy_identifier(role.role_id, "role_id"),
        role.objective,
        frozenset(capability_values),
        role.constraints,
    )


@dataclass(frozen=True)
class CodingWorkerBinding:
    """A routable Worker binding, deliberately not an AgentInstance."""

    scope_id: str
    worker_id: str
    principal_id: str
    role_ref: ScopedRef
    profile_ref: ScopedRef
    capabilities: tuple[str, ...] = ()
    input_protocols: tuple[str, ...] = ()
    output_protocols: tuple[str, ...] = ()
    policy_tags: tuple[str, ...] = ()
    priority: int = 0
    enabled: bool = True
    version: int = 1
    schema_version: str = CODING_RUNTIME_COMPAT_VERSION

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "worker_id", nonempty(self.worker_id, "worker_id"))
        object.__setattr__(
            self, "principal_id", nonempty(self.principal_id, "principal_id")
        )
        for field_name, entity_type in (
            ("role_ref", "core:agent_role"),
            ("profile_ref", "core:agent_profile"),
        ):
            reference = getattr(self, field_name)
            if not isinstance(reference, ScopedRef):
                raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef")
            reference.assert_scope(scope_id, field_name)
            reference.assert_type(entity_type)
        _coding_identifier(self.role_ref.entity_id, "role_ref.entity_id")
        for field_name in (
            "capabilities", "input_protocols", "output_protocols", "policy_tags"
        ):
            values = string_tuple(
                getattr(self, field_name),
                field_name,
                require_namespaced=True,
            )
            object.__setattr__(self, field_name, tuple(sorted(values)))
        object.__setattr__(self, "priority", _strict_int(self.priority, "priority"))
        if not isinstance(self.enabled, bool):
            raise RuntimeProtocolError("enabled 必须是布尔值")
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "CodingWorkerBinding"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, "coding:worker_binding", self.worker_id, self.version
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "worker_id": self.worker_id,
            "principal_id": self.principal_id,
            "role_ref": dict(self.role_ref.to_dict()),
            "profile_ref": dict(self.profile_ref.to_dict()),
            "capabilities": list(self.capabilities),
            "input_protocols": list(self.input_protocols),
            "output_protocols": list(self.output_protocols),
            "policy_tags": list(self.policy_tags),
            "priority": self.priority,
            "enabled": self.enabled,
            "version": self.version,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CodingWorkerBinding":
        root = require_fields(
            value,
            type_name="CodingWorkerBinding",
            required=frozenset({
                "schema_version", "scope_id", "worker_id", "principal_id",
                "role_ref", "profile_ref", "capabilities", "input_protocols",
                "output_protocols", "policy_tags", "priority", "enabled",
                "version",
            }),
        )
        role_ref = root["role_ref"]
        profile_ref = root["profile_ref"]
        if not isinstance(role_ref, Mapping) or not isinstance(profile_ref, Mapping):
            raise RuntimeProtocolError("role_ref/profile_ref 必须是引用对象")
        return cls(
            scope_id=root["scope_id"],
            worker_id=root["worker_id"],
            principal_id=root["principal_id"],
            role_ref=ScopedRef.from_dict(role_ref),
            profile_ref=ScopedRef.from_dict(profile_ref),
            capabilities=root["capabilities"],
            input_protocols=root["input_protocols"],
            output_protocols=root["output_protocols"],
            policy_tags=root["policy_tags"],
            priority=root["priority"],
            enabled=root["enabled"],
            version=root["version"],
            schema_version=root["schema_version"],
        )


def worker_descriptor_to_binding(
    descriptor: WorkerDescriptor,
    *,
    scope_id: str,
    profile: AgentProfile,
    version: int = 1,
) -> CodingWorkerBinding:
    if not isinstance(descriptor, WorkerDescriptor):
        raise TypeError("descriptor 必须是 WorkerDescriptor")
    if not isinstance(profile, AgentProfile):
        raise TypeError("profile 必须是 AgentProfile")
    normalized_scope = nonempty(scope_id, "scope_id")
    if profile.scope_id != normalized_scope:
        raise ScopeBoundaryError("AgentProfile 与 WorkerDescriptor 绑定跨 Scope")
    expected_role_id = _coding_identifier(descriptor.role, "descriptor.role")
    if profile.role_ref.entity_id != expected_role_id:
        raise RuntimeProtocolError("WorkerDescriptor role 与 AgentProfile 不匹配")
    return CodingWorkerBinding(
        scope_id=normalized_scope,
        worker_id=descriptor.worker_id,
        principal_id=descriptor.principal_id,
        role_ref=profile.role_ref,
        profile_ref=profile.reference,
        capabilities=tuple(
            _coding_or_existing_identifier(item, "capabilities")
            for item in descriptor.capabilities
        ),
        input_protocols=tuple(
            _coding_or_existing_identifier(item, "input_protocols")
            for item in descriptor.input_protocols
        ),
        output_protocols=tuple(
            _coding_or_existing_identifier(item, "output_protocols")
            for item in descriptor.output_protocols
        ),
        policy_tags=tuple(
            _coding_or_existing_identifier(item, "policy_tags")
            for item in descriptor.policy_tags
        ),
        priority=descriptor.priority,
        enabled=descriptor.enabled,
        version=version,
    )


@dataclass(frozen=True)
class CodingTaskSnapshot:
    """A strict, content-addressed snapshot of the legacy Coding TaskSpec."""

    scope_id: str
    task_id: str
    title: str
    objective: str
    role: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    context_queries: tuple[str, ...] = ()
    risk_level: str = "low"
    timeout_seconds: int = 120
    retry_limit: int = 1
    priority: int = 0
    required_verified_inputs: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    input_protocols: tuple[str, ...] = ()
    output_protocols: tuple[str, ...] = ()
    required_policy_tags: tuple[str, ...] = ()
    independent_from_tasks: tuple[str, ...] = ()
    version: int = 1
    snapshot_hash: str = ""
    schema_version: str = CODING_RUNTIME_COMPAT_VERSION

    _ARRAY_FIELDS = (
        "dependencies", "acceptance_criteria", "read_scopes", "write_scopes",
        "input_artifacts", "output_artifacts", "context_queries",
        "required_verified_inputs", "required_capabilities", "input_protocols",
        "output_protocols", "required_policy_tags", "independent_from_tasks",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        for field_name in ("task_id", "title", "objective", "role", "risk_level"):
            object.__setattr__(
                self, field_name, nonempty(getattr(self, field_name), field_name)
            )
        _coding_identifier(self.role, "role")
        for field_name in self._ARRAY_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _strict_strings(getattr(self, field_name), field_name),
            )
        for field_name in ("timeout_seconds", "retry_limit", "priority"):
            object.__setattr__(
                self, field_name, _strict_int(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "CodingTaskSnapshot"),
        )
        # Reuse all current TaskSpec semantic validation without changing it.
        self.to_task_spec()
        expected_hash = canonical_digest(self._hash_payload())
        if self.snapshot_hash:
            supplied = sha256_digest(self.snapshot_hash, "snapshot_hash")
            if supplied != expected_hash:
                raise RuntimeProtocolError("CodingTaskSnapshot hash 与内容不匹配")
        object.__setattr__(self, "snapshot_hash", expected_hash)

    @property
    def role_id(self) -> str:
        return _coding_identifier(self.role, "role")

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(self.scope_id, "core:task", self.task_id, self.version)

    @property
    def invocation_input(self) -> InvocationInputRef:
        return InvocationInputRef(self.reference, self.snapshot_hash)

    @classmethod
    def from_task_spec(
        cls,
        task: TaskSpec,
        *,
        scope_id: str,
        version: int = 1,
    ) -> "CodingTaskSnapshot":
        if not isinstance(task, TaskSpec):
            raise TypeError("task 必须是 TaskSpec")
        return cls(
            scope_id=scope_id,
            task_id=task.task_id,
            title=task.title,
            objective=task.objective,
            role=task.role,
            dependencies=tuple(task.dependencies),
            acceptance_criteria=tuple(task.acceptance_criteria),
            read_scopes=tuple(task.read_scopes),
            write_scopes=tuple(task.write_scopes),
            input_artifacts=tuple(task.input_artifacts),
            output_artifacts=tuple(task.output_artifacts),
            context_queries=tuple(task.context_queries),
            risk_level=task.risk_level,
            timeout_seconds=task.timeout_seconds,
            retry_limit=task.retry_limit,
            priority=task.priority,
            required_verified_inputs=tuple(task.required_verified_inputs),
            required_capabilities=tuple(task.required_capabilities),
            input_protocols=tuple(task.input_protocols),
            output_protocols=tuple(task.output_protocols),
            required_policy_tags=tuple(task.required_policy_tags),
            independent_from_tasks=tuple(task.independent_from_tasks),
            version=version,
        )

    def to_task_spec(self) -> TaskSpec:
        return TaskSpec(
            task_id=self.task_id,
            title=self.title,
            objective=self.objective,
            role=self.role,
            dependencies=self.dependencies,
            acceptance_criteria=self.acceptance_criteria,
            read_scopes=self.read_scopes,
            write_scopes=self.write_scopes,
            input_artifacts=self.input_artifacts,
            output_artifacts=self.output_artifacts,
            context_queries=self.context_queries,
            risk_level=self.risk_level,
            timeout_seconds=self.timeout_seconds,
            retry_limit=self.retry_limit,
            priority=self.priority,
            required_verified_inputs=self.required_verified_inputs,
            required_capabilities=self.required_capabilities,
            input_protocols=self.input_protocols,
            output_protocols=self.output_protocols,
            required_policy_tags=self.required_policy_tags,
            independent_from_tasks=self.independent_from_tasks,
        )

    def _hash_payload(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "task_id": self.task_id,
            "title": self.title,
            "objective": self.objective,
            "role": self.role,
            "dependencies": list(self.dependencies),
            "acceptance_criteria": list(self.acceptance_criteria),
            "read_scopes": list(self.read_scopes),
            "write_scopes": list(self.write_scopes),
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "context_queries": list(self.context_queries),
            "risk_level": self.risk_level,
            "timeout_seconds": self.timeout_seconds,
            "retry_limit": self.retry_limit,
            "priority": self.priority,
            "required_verified_inputs": list(self.required_verified_inputs),
            "required_capabilities": list(self.required_capabilities),
            "input_protocols": list(self.input_protocols),
            "output_protocols": list(self.output_protocols),
            "required_policy_tags": list(self.required_policy_tags),
            "independent_from_tasks": list(self.independent_from_tasks),
            "version": self.version,
        }

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({**self._hash_payload(), "snapshot_hash": self.snapshot_hash})

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CodingTaskSnapshot":
        required = frozenset({
            "schema_version", "scope_id", "task_id", "title", "objective",
            "role", "dependencies", "acceptance_criteria", "read_scopes",
            "write_scopes", "input_artifacts", "output_artifacts",
            "context_queries", "risk_level", "timeout_seconds", "retry_limit",
            "priority", "required_verified_inputs", "required_capabilities",
            "input_protocols", "output_protocols", "required_policy_tags",
            "independent_from_tasks", "version", "snapshot_hash",
        })
        root = require_fields(
            value, type_name="CodingTaskSnapshot", required=required
        )
        return cls(
            scope_id=root["scope_id"], task_id=root["task_id"],
            title=root["title"], objective=root["objective"], role=root["role"],
            dependencies=root["dependencies"],
            acceptance_criteria=root["acceptance_criteria"],
            read_scopes=root["read_scopes"], write_scopes=root["write_scopes"],
            input_artifacts=root["input_artifacts"],
            output_artifacts=root["output_artifacts"],
            context_queries=root["context_queries"],
            risk_level=root["risk_level"], timeout_seconds=root["timeout_seconds"],
            retry_limit=root["retry_limit"], priority=root["priority"],
            required_verified_inputs=root["required_verified_inputs"],
            required_capabilities=root["required_capabilities"],
            input_protocols=root["input_protocols"],
            output_protocols=root["output_protocols"],
            required_policy_tags=root["required_policy_tags"],
            independent_from_tasks=root["independent_from_tasks"],
            version=root["version"], snapshot_hash=root["snapshot_hash"],
            schema_version=root["schema_version"],
        )


def artifact_to_scoped_ref(
    artifact: Artifact,
    *,
    scope_id: str,
    version: int = 1,
) -> ScopedRef:
    if not isinstance(artifact, Artifact):
        raise TypeError("artifact 必须是 Artifact")
    return ScopedRef(scope_id, "core:artifact", artifact.artifact_id, version)


def artifact_to_invocation_input(
    artifact: Artifact,
    *,
    scope_id: str,
    version: int = 1,
) -> InvocationInputRef:
    return InvocationInputRef(
        artifact_to_scoped_ref(artifact, scope_id=scope_id, version=version),
        artifact.content_hash,
    )


def validate_artifact_input_binding(
    artifact: Artifact,
    input_ref: InvocationInputRef,
    *,
    scope_id: str,
) -> None:
    if not isinstance(artifact, Artifact):
        raise TypeError("artifact 必须是 Artifact")
    if not isinstance(input_ref, InvocationInputRef):
        raise TypeError("input_ref 必须是 InvocationInputRef")
    normalized_scope = nonempty(scope_id, "scope_id")
    input_ref.ref.assert_scope(normalized_scope, "input_ref")
    input_ref.ref.assert_type("core:artifact")
    if input_ref.ref.entity_id != artifact.artifact_id:
        raise RuntimeProtocolError("InvocationInputRef 绑定了错误 Artifact")
    if input_ref.content_hash != artifact.content_hash:
        raise RuntimeProtocolError("InvocationInputRef 的 Artifact 内容哈希已过期")


def _verification_evidence_kind(record: VerificationRecord) -> str:
    namespace, name = record.validator_kind.split(":", 1)
    return namespaced(
        f"{namespace}:{name}_{record.outcome.value}", "evidence_kind"
    )


def verification_record_to_acceptance_evidence(
    record: VerificationRecord,
    *,
    scope_id: str,
    acceptance_subject_ref: ScopedRef,
    subject_inputs: tuple[InvocationInputRef, ...],
    evaluator_principal_id: str,
    current_workspace_hash: str = "",
    version: int = 1,
) -> AcceptanceEvidence:
    """Expose a VerificationRecord as evidence, never as an Outcome."""

    if not isinstance(record, VerificationRecord):
        raise TypeError("record 必须是 VerificationRecord")
    normalized_scope = nonempty(scope_id, "scope_id")
    if not isinstance(acceptance_subject_ref, ScopedRef):
        raise RuntimeProtocolError("acceptance_subject_ref 必须是 ScopedRef")
    acceptance_subject_ref.assert_scope(
        normalized_scope, "acceptance_subject_ref"
    )
    if acceptance_subject_ref.entity_type not in {
        item.value for item in AcceptanceSubjectType
    }:
        raise RuntimeProtocolError("Acceptance Evidence subject 类型无效")
    if not isinstance(subject_inputs, (tuple, list)):
        raise RuntimeProtocolError("subject_inputs 必须是输入快照引用数组")
    inputs = tuple(subject_inputs)
    if not inputs or not all(isinstance(item, InvocationInputRef) for item in inputs):
        raise RuntimeProtocolError("subject_inputs 必须包含 InvocationInputRef")
    current_hashes: dict[str, str] = {}
    for item in inputs:
        item.ref.assert_scope(normalized_scope, "subject_inputs")
        item.ref.assert_type("core:artifact")
        legacy_ref = f"artifact://{item.ref.entity_id}"
        if legacy_ref in current_hashes:
            raise RuntimeProtocolError("subject_inputs 不能重复")
        current_hashes[legacy_ref] = item.content_hash
    if set(current_hashes) != set(record.subject_refs):
        raise RuntimeProtocolError("VerificationRecord 绑定了错误 Artifact 集合")
    if dict(record.subject_hashes) != current_hashes:
        raise RuntimeProtocolError("VerificationRecord subject_hashes 已过期或缺失")
    if record.outcome is VerificationOutcome.PASSED and (
        not record.workspace_hash or not current_workspace_hash
    ):
        raise RuntimeProtocolError(
            "passed Coding Evidence 必须绑定当前 Workspace hash"
        )
    workspace_hash = current_workspace_hash
    if workspace_hash:
        workspace_hash = sha256_digest(workspace_hash, "current_workspace_hash")
    if not record.is_fresh(current_hashes, workspace_hash=workspace_hash):
        raise RuntimeProtocolError("VerificationRecord Workspace 证据已过期")
    evidence_ref = ScopedRef(
        normalized_scope,
        "core:verification_record",
        record.verification_id,
        version,
    )
    return AcceptanceEvidence(
        evidence_ref=evidence_ref,
        evidence_kind=_verification_evidence_kind(record),
        subject_ref=acceptance_subject_ref,
        observed_at=record.created_at,
        content_hash=canonical_digest(dict(record.to_dict())),
        evaluator_principal_id=evaluator_principal_id,
    )


__all__ = [
    "CODING_RUNTIME_COMPAT_VERSION",
    "CodingTaskSnapshot",
    "CodingWorkerBinding",
    "agent_role_to_role_spec",
    "artifact_to_invocation_input",
    "artifact_to_scoped_ref",
    "role_spec_to_agent_role",
    "validate_artifact_input_binding",
    "verification_record_to_acceptance_evidence",
    "worker_descriptor_to_binding",
]
