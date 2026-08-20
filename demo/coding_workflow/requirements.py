from __future__ import annotations

import fnmatch
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .truth import VerificationOutcome, VerificationRecord


class EvidenceModality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class EvidenceAccess(str, Enum):
    TASK = "task"
    PROJECT = "project"
    RESTRICTED = "restricted"


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_NAMESPACED = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value.strip()


def _strings(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{field_name} 必须是字符串数组")
    result = tuple(_nonempty(item, field_name) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{field_name} 不能为空")
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} 不能重复")
    return result


def _artifact_refs(
    value: object, field_name: str, *, allow_empty: bool = True
) -> tuple[str, ...]:
    result = _strings(value, field_name, allow_empty=allow_empty)
    if any(not item.startswith("artifact://") for item in result):
        raise ValueError(f"{field_name} 必须使用 artifact:// 引用")
    return result


def _freeze_json(value: object, field_name: str) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field_name} 不能包含 NaN 或 Infinity")
        return value
    if isinstance(value, Mapping):
        parsed: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{field_name} 的对象键必须是非空字符串")
            parsed[key] = _freeze_json(item, field_name)
        return MappingProxyType(parsed)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item, field_name) for item in value)
    raise ValueError(f"{field_name} 只能包含 JSON 值")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        _thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _enum(value: object, enum_type, field_name: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 无效") from exc


@dataclass(frozen=True)
class RequirementEvidence:
    artifact_ref: str
    modality: EvidenceModality
    mime_type: str
    size_bytes: int
    content_hash: str
    source: str
    derived_from: tuple[str, ...] = ()
    access: EvidenceAccess = EvidenceAccess.TASK
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        refs = _artifact_refs((self.artifact_ref,), "artifact_ref", allow_empty=False)
        object.__setattr__(self, "artifact_ref", refs[0])
        modality = _enum(self.modality, EvidenceModality, "modality")
        object.__setattr__(self, "modality", modality)
        mime = _nonempty(self.mime_type, "mime_type").lower()
        if not _MIME.fullmatch(mime):
            raise ValueError("mime_type 格式无效")
        allowed = {
            EvidenceModality.TEXT: mime.startswith("text/") or mime in {
                "application/json", "application/pdf", "application/xml"
            },
            EvidenceModality.IMAGE: mime.startswith("image/"),
            EvidenceModality.AUDIO: mime.startswith("audio/"),
            EvidenceModality.VIDEO: mime.startswith("video/"),
        }[modality]
        if not allowed:
            raise ValueError("mime_type 与 modality 不匹配")
        object.__setattr__(self, "mime_type", mime)
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise ValueError("size_bytes 必须是整数")
        if self.size_bytes <= 0:
            raise ValueError("size_bytes 必须大于 0")
        content_hash = _nonempty(self.content_hash, "content_hash").lower()
        if not _SHA256.fullmatch(content_hash):
            raise ValueError("content_hash 必须是 SHA-256")
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "source", _nonempty(self.source, "source"))
        derived = _artifact_refs(self.derived_from, "derived_from")
        if self.artifact_ref in derived:
            raise ValueError("Evidence 不能派生自自身")
        object.__setattr__(self, "derived_from", derived)
        object.__setattr__(
            self, "access", _enum(self.access, EvidenceAccess, "access")
        )
        if self.schema_version != "1.0":
            raise ValueError("RequirementEvidence 只支持 schema_version 1.0")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "artifact_ref": self.artifact_ref,
            "modality": self.modality.value,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "source": self.source,
            "derived_from": self.derived_from,
            "access": self.access.value,
        })

    def validate_artifact(self, artifact: object) -> None:
        artifact_id = getattr(artifact, "artifact_id", "")
        if f"artifact://{artifact_id}" != self.artifact_ref:
            raise ValueError("RequirementEvidence 与 Artifact 引用不匹配")
        metadata = getattr(artifact, "metadata", None)
        content = getattr(artifact, "content", None)
        if not isinstance(metadata, Mapping):
            raise ValueError("Evidence Artifact 缺少 metadata")
        mime_type = metadata.get("mime_type")
        content_hash = metadata.get("content_hash", metadata.get("sha256"))
        size_bytes = metadata.get("size_bytes")
        if size_bytes is None and isinstance(content, Mapping):
            size_bytes = content.get("size_bytes", content.get("byte_size"))
        if mime_type != self.mime_type:
            raise ValueError("Evidence Artifact MIME 不匹配")
        if content_hash != self.content_hash:
            raise ValueError("Evidence Artifact 内容哈希不匹配")
        if size_bytes != self.size_bytes:
            raise ValueError("Evidence Artifact 大小不匹配")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RequirementEvidence":
        return cls(
            value["artifact_ref"], value["modality"], value["mime_type"],
            value["size_bytes"], value["content_hash"], value["source"],
            value.get("derived_from", ()), value.get("access", "task"),
            value.get("schema_version", ""),
        )


def _scope_path(value: str, field_name: str) -> str:
    path = _nonempty(value, field_name).replace("\\", "/")
    if path.startswith(("/", "~")) or "\x00" in path:
        raise ValueError(f"{field_name} 必须是相对路径或 glob")
    if ".." in PurePosixPath(path).parts:
        raise ValueError(f"{field_name} 不能包含 ..")
    return path


@dataclass(frozen=True)
class RepositoryScope:
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    prohibited_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        reads = tuple(_scope_path(item, "read_paths") for item in _strings(
            self.read_paths, "read_paths", allow_empty=False
        ))
        writes = tuple(_scope_path(item, "write_paths") for item in _strings(
            self.write_paths, "write_paths", allow_empty=False
        ))
        object.__setattr__(self, "read_paths", reads)
        object.__setattr__(self, "write_paths", writes)
        object.__setattr__(
            self,
            "prohibited_actions",
            _strings(self.prohibited_actions, "prohibited_actions"),
        )

    def assert_within(self, runtime_scope: "RepositoryScope") -> None:
        def allowed(candidate: str, boundaries: tuple[str, ...]) -> bool:
            return any(
                boundary == "**"
                or candidate == boundary
                or (
                    not any(token in candidate for token in "*?[")
                    and fnmatch.fnmatch(candidate, boundary)
                )
                for boundary in boundaries
            )

        for candidate in self.read_paths:
            if not allowed(candidate, runtime_scope.read_paths):
                raise PermissionError(f"需求扩大读取范围: {candidate}")
        for candidate in self.write_paths:
            if not allowed(candidate, runtime_scope.write_paths):
                raise PermissionError(f"需求扩大写入范围: {candidate}")
        missing = set(runtime_scope.prohibited_actions) - set(
            self.prohibited_actions
        )
        if missing:
            raise PermissionError(f"需求删除了禁止操作: {sorted(missing)}")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "read_paths": self.read_paths,
            "write_paths": self.write_paths,
            "prohibited_actions": self.prohibited_actions,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RepositoryScope":
        return cls(
            value.get("read_paths", ()),
            value.get("write_paths", ()),
            value.get("prohibited_actions", ()),
        )


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    validator_kind: str
    expected_result: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    required: bool = True
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        criterion_id = _nonempty(self.criterion_id, "criterion_id")
        if not _IDENTIFIER.fullmatch(criterion_id):
            raise ValueError("criterion_id 格式无效")
        object.__setattr__(self, "criterion_id", criterion_id)
        object.__setattr__(
            self, "description", _nonempty(self.description, "description")
        )
        validator_kind = _nonempty(self.validator_kind, "validator_kind")
        if not _NAMESPACED.fullmatch(validator_kind):
            raise ValueError("validator_kind 必须使用 namespace:name")
        object.__setattr__(self, "validator_kind", validator_kind)
        if not isinstance(self.required, bool):
            raise ValueError("required 必须是布尔值")
        frozen = _freeze_json(self.expected_result, "expected_result")
        if not isinstance(frozen, Mapping):
            raise ValueError("expected_result 必须是对象")
        object.__setattr__(self, "expected_result", frozen)
        object.__setattr__(
            self,
            "evidence_refs",
            _artifact_refs(self.evidence_refs, "evidence_refs"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "criterion_id": self.criterion_id,
            "description": self.description,
            "validator_kind": self.validator_kind,
            "expected_result": _thaw_json(self.expected_result),
            "required": self.required,
            "evidence_refs": self.evidence_refs,
        })

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AcceptanceCriterion":
        return cls(
            value["criterion_id"], value["description"],
            value["validator_kind"], value.get("expected_result", {}),
            value.get("required", True), value.get("evidence_refs", ()),
        )


@dataclass(frozen=True)
class EvidenceGrant:
    grant_id: str
    task_id: str
    role: str
    evidence_refs: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    purpose: str
    expires_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("grant_id", "task_id", "role", "purpose"):
            object.__setattr__(
                self, field_name, _nonempty(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "evidence_refs",
            _artifact_refs(self.evidence_refs, "evidence_refs", allow_empty=False),
        )
        operations = _strings(
            self.allowed_operations, "allowed_operations", allow_empty=False
        )
        if any(
            not (_IDENTIFIER.fullmatch(item) or _NAMESPACED.fullmatch(item))
            for item in operations
        ):
            raise ValueError("allowed_operations 格式无效")
        object.__setattr__(self, "allowed_operations", operations)
        if self.expires_at:
            try:
                parsed = datetime.fromisoformat(self.expires_at)
            except ValueError as exc:
                raise ValueError("expires_at 必须是 ISO-8601") from exc
            if parsed.tzinfo is None:
                raise ValueError("expires_at 必须包含时区")

    def allows(
        self,
        *,
        task_id: str,
        role: str,
        evidence_ref: str,
        operation: str = "read",
        now: datetime | None = None,
    ) -> bool:
        if task_id != self.task_id or role != self.role:
            return False
        if evidence_ref not in self.evidence_refs:
            return False
        if operation not in self.allowed_operations:
            return False
        if self.expires_at:
            current = now or datetime.now(timezone.utc)
            if current >= datetime.fromisoformat(self.expires_at):
                return False
        return True

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "grant_id": self.grant_id,
            "task_id": self.task_id,
            "role": self.role,
            "evidence_refs": self.evidence_refs,
            "allowed_operations": self.allowed_operations,
            "purpose": self.purpose,
            "expires_at": self.expires_at,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceGrant":
        return cls(
            value["grant_id"], value["task_id"], value["role"],
            value["evidence_refs"], value["allowed_operations"],
            value["purpose"], value.get("expires_at", ""),
        )


@dataclass(frozen=True)
class ValidatorSpec:
    validator_id: str
    validator_kind: str
    criterion_ids: tuple[str, ...]
    config: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    required: bool = True
    bind_workspace: bool = False

    def __post_init__(self) -> None:
        validator_id = _nonempty(self.validator_id, "validator_id")
        if not _IDENTIFIER.fullmatch(validator_id):
            raise ValueError("validator_id 格式无效")
        object.__setattr__(self, "validator_id", validator_id)
        validator_kind = _nonempty(self.validator_kind, "validator_kind")
        if not _NAMESPACED.fullmatch(validator_kind):
            raise ValueError("validator_kind 必须使用 namespace:name")
        object.__setattr__(self, "validator_kind", validator_kind)
        object.__setattr__(
            self,
            "criterion_ids",
            _strings(self.criterion_ids, "criterion_ids", allow_empty=False),
        )
        frozen = _freeze_json(self.config, "config")
        if not isinstance(frozen, Mapping):
            raise ValueError("config 必须是对象")
        object.__setattr__(self, "config", frozen)
        if not isinstance(self.required, bool) or not isinstance(
            self.bind_workspace, bool
        ):
            raise ValueError("required/bind_workspace 必须是布尔值")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "validator_id": self.validator_id,
            "validator_kind": self.validator_kind,
            "criterion_ids": self.criterion_ids,
            "config": _thaw_json(self.config),
            "required": self.required,
            "bind_workspace": self.bind_workspace,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ValidatorSpec":
        return cls(
            value["validator_id"], value["validator_kind"],
            value["criterion_ids"], value.get("config", {}),
            value.get("required", True), value.get("bind_workspace", False),
        )


@dataclass(frozen=True)
class ValidatorProfile:
    profile_id: str
    validators: tuple[ValidatorSpec, ...]
    criterion_hashes: Mapping[str, str]
    completion_policy: str = "all_required"
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        profile_id = _nonempty(self.profile_id, "profile_id")
        if not _IDENTIFIER.fullmatch(profile_id):
            raise ValueError("profile_id 格式无效")
        object.__setattr__(self, "profile_id", profile_id)
        if not isinstance(self.validators, (tuple, list)) or not self.validators:
            raise ValueError("ValidatorProfile 必须包含 validators")
        validators = tuple(self.validators)
        if not all(isinstance(item, ValidatorSpec) for item in validators):
            raise ValueError("validators 必须是 ValidatorSpec")
        ids = tuple(item.validator_id for item in validators)
        kinds = tuple(item.validator_kind for item in validators)
        if len(ids) != len(set(ids)) or len(kinds) != len(set(kinds)):
            raise ValueError("validator_id 和 validator_kind 不能重复")
        object.__setattr__(self, "validators", validators)
        if not isinstance(self.criterion_hashes, Mapping):
            raise ValueError("criterion_hashes 必须是对象")
        criterion_hashes: dict[str, str] = {}
        for criterion_id, digest in self.criterion_hashes.items():
            if not isinstance(criterion_id, str) or not _IDENTIFIER.fullmatch(
                criterion_id
            ):
                raise ValueError("criterion_hashes 的 ID 无效")
            if not isinstance(digest, str) or not _SHA256.fullmatch(
                digest.lower()
            ):
                raise ValueError("criterion_hashes 必须是 SHA-256")
            criterion_hashes[criterion_id] = digest.lower()
        referenced_ids = {
            criterion_id
            for validator in validators
            for criterion_id in validator.criterion_ids
        }
        if set(criterion_hashes) != referenced_ids:
            raise ValueError("criterion_hashes 必须覆盖且只覆盖 Validator 验收项")
        object.__setattr__(
            self, "criterion_hashes", MappingProxyType(criterion_hashes)
        )
        if self.completion_policy != "all_required":
            raise ValueError("当前只支持 all_required 完成策略")
        if self.schema_version != "1.0":
            raise ValueError("ValidatorProfile 只支持 schema_version 1.0")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def reference(self) -> str:
        return f"validator-profile://{self.profile_id}@{self.digest}"

    def validate_criteria(
        self, criteria: tuple[AcceptanceCriterion, ...]
    ) -> None:
        criterion_ids = {item.criterion_id for item in criteria}
        kinds = {item.validator_kind for item in self.validators}
        for criterion in criteria:
            if criterion.validator_kind not in kinds:
                raise ValueError(
                    f"验收项缺少 Validator: {criterion.criterion_id}"
                )
        referenced = {
            criterion_id
            for validator in self.validators
            for criterion_id in validator.criterion_ids
        }
        unknown = referenced - criterion_ids
        if unknown:
            raise ValueError(f"Validator 引用了未知验收项: {sorted(unknown)}")
        actual_hashes = {item.criterion_id: item.digest for item in criteria}
        if actual_hashes != dict(self.criterion_hashes):
            raise PermissionError("AcceptanceCriterion 已被修改或降低")
        required_ids = {
            item.criterion_id for item in criteria if item.required
        }
        covered_by_required = {
            criterion_id
            for validator in self.validators if validator.required
            for criterion_id in validator.criterion_ids
        }
        missing = required_ids - covered_by_required
        if missing:
            raise ValueError(f"必需验收项未进入必需门禁: {sorted(missing)}")

    def assert_frozen(self, expected_reference: str) -> None:
        if self.reference != expected_reference:
            raise PermissionError("ValidatorProfile 已被修改或降低")

    def decide(
        self, records: tuple[VerificationRecord, ...]
    ) -> VerificationOutcome:
        by_kind = {item.validator_kind: item for item in self.validators}
        latest = {
            record.validator_kind: record
            for record in records
            if record.validator_kind in by_kind
            and (
                not by_kind[record.validator_kind].bind_workspace
                or bool(record.workspace_hash)
            )
        }
        required = tuple(item for item in self.validators if item.required)
        if any(
            item.validator_kind in latest
            and latest[item.validator_kind].outcome is VerificationOutcome.FAILED
            for item in required
        ):
            return VerificationOutcome.FAILED
        if all(
            item.validator_kind in latest
            and latest[item.validator_kind].outcome is VerificationOutcome.PASSED
            for item in required
        ):
            return VerificationOutcome.PASSED
        return VerificationOutcome.UNKNOWN

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "validators": [dict(item.to_dict()) for item in self.validators],
            "criterion_hashes": dict(self.criterion_hashes),
            "completion_policy": self.completion_policy,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ValidatorProfile":
        validators = value.get("validators", ())
        if not isinstance(validators, (tuple, list)):
            raise ValueError("validators 必须是数组")
        return cls(
            value["profile_id"],
            tuple(ValidatorSpec.from_dict(item) for item in validators),
            value.get("criterion_hashes", {}),
            value.get("completion_policy", ""),
            value.get("schema_version", ""),
        )


@dataclass(frozen=True)
class CodingRequirement:
    requirement_id: str
    objective: str
    deliverables: tuple[str, ...]
    constraints: tuple[str, ...]
    repository_scope: RepositoryScope
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    evidence_refs: tuple[str, ...]
    validator_profile_ref: str
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    extension_refs: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        requirement_id = _nonempty(self.requirement_id, "requirement_id")
        if not _IDENTIFIER.fullmatch(requirement_id):
            raise ValueError("requirement_id 格式无效")
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "objective", _nonempty(self.objective, "objective"))
        object.__setattr__(
            self, "deliverables",
            _strings(self.deliverables, "deliverables", allow_empty=False),
        )
        object.__setattr__(
            self, "constraints", _strings(self.constraints, "constraints")
        )
        if not isinstance(self.repository_scope, RepositoryScope):
            raise ValueError("repository_scope 必须是 RepositoryScope")
        if not isinstance(self.acceptance_criteria, (tuple, list)):
            raise ValueError("acceptance_criteria 必须是数组")
        criteria = tuple(self.acceptance_criteria)
        if not criteria or not all(
            isinstance(item, AcceptanceCriterion) for item in criteria
        ):
            raise ValueError("acceptance_criteria 必须包含 AcceptanceCriterion")
        ids = tuple(item.criterion_id for item in criteria)
        if len(ids) != len(set(ids)):
            raise ValueError("criterion_id 不能重复")
        object.__setattr__(self, "acceptance_criteria", criteria)
        evidence_refs = _artifact_refs(self.evidence_refs, "evidence_refs")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        profile_ref = _nonempty(
            self.validator_profile_ref, "validator_profile_ref"
        )
        if not profile_ref.startswith("validator-profile://"):
            raise ValueError("validator_profile_ref 格式无效")
        object.__setattr__(self, "validator_profile_ref", profile_ref)
        object.__setattr__(
            self, "assumptions", _strings(self.assumptions, "assumptions")
        )
        object.__setattr__(
            self, "open_questions", _strings(self.open_questions, "open_questions")
        )
        object.__setattr__(
            self, "extension_refs",
            _artifact_refs(self.extension_refs, "extension_refs"),
        )
        criterion_evidence = {
            ref for item in criteria for ref in item.evidence_refs
        }
        unknown = criterion_evidence - set(evidence_refs)
        if unknown:
            raise ValueError(f"验收项引用了未声明证据: {sorted(unknown)}")
        if self.schema_version != "1.0":
            raise ValueError("CodingRequirement 只支持 schema_version 1.0")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def enforce_runtime_boundaries(
        self,
        *,
        runtime_scope: RepositoryScope,
        validator_profile: ValidatorProfile,
        available_evidence: tuple[RequirementEvidence, ...],
    ) -> None:
        self.repository_scope.assert_within(runtime_scope)
        validator_profile.assert_frozen(self.validator_profile_ref)
        validator_profile.validate_criteria(self.acceptance_criteria)
        available = {item.artifact_ref for item in available_evidence}
        missing = set(self.evidence_refs) - available
        if missing:
            raise PermissionError(f"需求引用了未授权证据: {sorted(missing)}")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "requirement_id": self.requirement_id,
            "objective": self.objective,
            "deliverables": self.deliverables,
            "constraints": self.constraints,
            "repository_scope": dict(self.repository_scope.to_dict()),
            "acceptance_criteria": [
                dict(item.to_dict()) for item in self.acceptance_criteria
            ],
            "evidence_refs": self.evidence_refs,
            "validator_profile_ref": self.validator_profile_ref,
            "assumptions": self.assumptions,
            "open_questions": self.open_questions,
            "extension_refs": self.extension_refs,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CodingRequirement":
        criteria = value.get("acceptance_criteria", ())
        if not isinstance(criteria, (tuple, list)):
            raise ValueError("acceptance_criteria 必须是数组")
        return cls(
            value["requirement_id"], value["objective"],
            value["deliverables"], value.get("constraints", ()),
            RepositoryScope.from_dict(value["repository_scope"]),
            tuple(AcceptanceCriterion.from_dict(item) for item in criteria),
            value.get("evidence_refs", ()), value["validator_profile_ref"],
            value.get("assumptions", ()), value.get("open_questions", ()),
            value.get("extension_refs", ()), value.get("schema_version", ""),
        )
