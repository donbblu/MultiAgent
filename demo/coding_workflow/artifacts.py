from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from threading import RLock
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from .truth import VerificationOutcome, VerificationRecord


class ArtifactValidationState(str, Enum):
    UNVERIFIED = "unverified"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    VERIFIED = "verified"


@dataclass(frozen=True)
class ArtifactValidation:
    state: ArtifactValidationState = ArtifactValidationState.UNVERIFIED
    verification_refs: tuple[str, ...] = ()
    superseded_by: str | None = None
    verification_records: tuple[VerificationRecord, ...] = ()


_RESERVED_VALIDATION_FIELDS = frozenset({
    "artifact_validation",
    "validation_state",
    "verification_refs",
    "runtime_provenance",
})


def _forged_validation_field(value: object) -> str:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in _RESERVED_VALIDATION_FIELDS:
                return str(key)
            found = _forged_validation_field(nested)
            if found:
                return found
    elif isinstance(value, (tuple, list)):
        for nested in value:
            found = _forged_validation_field(nested)
            if found:
                return found
    return ""


def _stable_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _stable_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_stable_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_stable_value(item) for item in value), key=repr)
    return {"type": type(value).__qualname__, "repr": repr(value)}


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    name: str
    task_id: str
    kind: str
    content: object
    metadata: Mapping[str, object]
    created_at: str

    @property
    def content_hash(self) -> str:
        payload = json.dumps(
            _stable_value({
                "artifact_id": self.artifact_id,
                "name": self.name,
                "task_id": self.task_id,
                "kind": self.kind,
                "content": self.content,
                "metadata": self.metadata,
            }),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def create(
        cls,
        name: str,
        task_id: str,
        content: object,
        *,
        kind: str = "result",
        metadata: Mapping[str, object] | None = None,
    ) -> "Artifact":
        if not name.strip() or not task_id.strip():
            raise ValueError("Artifact 名称和任务 ID 不能为空")
        return cls(
            str(uuid4()), name, task_id, kind, content,
            MappingProxyType(dict(metadata or {})),
            datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True)
class ArtifactDraft:
    """Worker 提交给 Harness 的不可变 Artifact 草稿。"""

    content: object
    kind: str = "result"
    metadata: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    artifact_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if not self.kind.strip():
            raise ValueError("ArtifactDraft kind 不能为空")
        forged = _forged_validation_field(self.metadata)
        if forged:
            raise ValueError(
                f"Worker Artifact metadata 不能声明 Runtime 保留字段: {forged}"
            )

    @classmethod
    def from_artifact(cls, artifact: Artifact) -> "ArtifactDraft":
        # Runtime provenance belongs to the accepted Artifact and cannot be
        # carried back into a Worker-controlled draft.
        metadata = dict(artifact.metadata)
        metadata.pop("runtime_provenance", None)
        return cls(
            artifact.content,
            artifact.kind,
            metadata,
            artifact.artifact_id,
            artifact.created_at,
        )

    @property
    def reference(self) -> str:
        if not self.artifact_id:
            raise ValueError("未分配 ID 的 ArtifactDraft 没有引用")
        return f"artifact://{self.artifact_id}"

    def materialize(self, name: str, task_id: str) -> Artifact:
        if not self.artifact_id:
            return Artifact.create(
                name, task_id, self.content,
                kind=self.kind, metadata=self.metadata,
            )
        return Artifact(
            self.artifact_id,
            name,
            task_id,
            self.kind,
            self.content,
            self.metadata,
            self.created_at or datetime.now(timezone.utc).isoformat(),
        )


class ArtifactStore:
    """Artifact 是节点之间唯一的结果交接方式。"""

    def __init__(self) -> None:
        self._by_id: dict[str, Artifact] = {}
        self._latest_by_name: dict[str, str] = {}
        self._validation: dict[str, ArtifactValidation] = {}
        self._lock = RLock()

    def put(self, artifact: Artifact) -> str:
        with self._lock:
            if artifact.artifact_id in self._by_id:
                raise ValueError(f"Artifact ID 已存在: {artifact.artifact_id}")
            self._by_id[artifact.artifact_id] = artifact
            self._latest_by_name[artifact.name] = artifact.artifact_id
            self._validation[artifact.artifact_id] = ArtifactValidation()
        return f"artifact://{artifact.artifact_id}"

    def get(self, reference: str) -> Artifact:
        artifact_id = reference.removeprefix("artifact://")
        with self._lock:
            try:
                return self._by_id[artifact_id]
            except KeyError as exc:
                raise KeyError(f"Artifact 不存在: {reference}") from exc

    def latest(self, name: str) -> Artifact:
        with self._lock:
            try:
                artifact_id = self._latest_by_name[name]
            except KeyError as exc:
                raise KeyError(f"Artifact 名称不存在: {name}") from exc
            return self._by_id[artifact_id]

    def resolve(self, references: Mapping[str, str]) -> Mapping[str, Artifact]:
        return MappingProxyType({name: self.get(ref) for name, ref in references.items()})

    def snapshot(self) -> tuple[tuple[Artifact, ArtifactValidation], ...]:
        with self._lock:
            return tuple(
                (artifact, self._validation[artifact_id])
                for artifact_id, artifact in self._by_id.items()
            )

    @classmethod
    def restore(
        cls, items: tuple[tuple[Artifact, ArtifactValidation], ...]
    ) -> "ArtifactStore":
        store = cls()
        with store._lock:
            for artifact, validation in items:
                if artifact.artifact_id in store._by_id:
                    raise ValueError(f"Artifact ID 重复: {artifact.artifact_id}")
                store._by_id[artifact.artifact_id] = artifact
                store._latest_by_name[artifact.name] = artifact.artifact_id
                store._validation[artifact.artifact_id] = validation
        return store

    def replace_with(self, other: "ArtifactStore") -> None:
        """原地恢复快照，使已注入同一 Store 的组件继续引用恢复后的状态。"""
        items = other.snapshot()
        with self._lock:
            self._by_id.clear()
            self._latest_by_name.clear()
            self._validation.clear()
            for artifact, validation in items:
                self._by_id[artifact.artifact_id] = artifact
                self._latest_by_name[artifact.name] = artifact.artifact_id
                self._validation[artifact.artifact_id] = validation

    def validation(self, reference: str) -> ArtifactValidation:
        artifact_id = reference.removeprefix("artifact://")
        with self._lock:
            try:
                return self._validation[artifact_id]
            except KeyError as exc:
                raise KeyError(f"Artifact 不存在: {reference}") from exc

    def mark_failed(
        self,
        references: tuple[str, ...],
        verification_refs: tuple[str, ...],
        *,
        validator_kind: str = "core:runtime",
        summary: str = "Runtime 验证失败",
        workspace_hash: str = "",
    ) -> str:
        return self.record_verification(VerificationRecord.create(
            validator_kind,
            VerificationOutcome.FAILED,
            references,
            verification_refs,
            summary,
            subject_hashes=self._subject_hashes(references),
            workspace_hash=workspace_hash,
        ))

    def mark_verified(
        self,
        references: tuple[str, ...],
        verification_refs: tuple[str, ...],
        *,
        validator_kind: str = "core:runtime",
        summary: str = "Runtime 验证通过",
        workspace_hash: str = "",
    ) -> str:
        return self.record_verification(VerificationRecord.create(
            validator_kind,
            VerificationOutcome.PASSED,
            references,
            verification_refs,
            summary,
            subject_hashes=self._subject_hashes(references),
            workspace_hash=workspace_hash,
        ))

    def _subject_hashes(self, references: tuple[str, ...]) -> Mapping[str, str]:
        return MappingProxyType({
            reference: self.get(reference).content_hash
            for reference in references
        })

    def record_verification(self, record: VerificationRecord) -> str:
        if not isinstance(record, VerificationRecord):
            raise TypeError("验证结果必须是 VerificationRecord")
        with self._lock:
            subject_ids = tuple(
                reference.removeprefix("artifact://")
                for reference in record.subject_refs
            )
            for reference, artifact_id in zip(record.subject_refs, subject_ids):
                if artifact_id not in self._by_id:
                    raise KeyError(f"Artifact 不存在: {reference}")
                if (
                    self._validation[artifact_id].state
                    is ArtifactValidationState.SUPERSEDED
                ):
                    raise ValueError(
                        f"已被替代的 Artifact 不能改变验证状态: {reference}"
                    )
            for evidence_ref in record.evidence_refs:
                if evidence_ref.startswith("artifact://"):
                    self.get(evidence_ref)
            if any(
                record.reference in validation.verification_refs
                for validation in self._validation.values()
            ):
                raise ValueError(f"VerificationRecord 已存在: {record.reference}")
            current_hashes = {
                reference: self._by_id[artifact_id].content_hash
                for reference, artifact_id in zip(record.subject_refs, subject_ids)
            }
            if dict(record.subject_hashes) != current_hashes:
                raise ValueError("VerificationRecord 的 subject_hashes 不匹配")

            state = {
                VerificationOutcome.PASSED: ArtifactValidationState.VERIFIED,
                VerificationOutcome.FAILED: ArtifactValidationState.FAILED,
                VerificationOutcome.UNKNOWN: ArtifactValidationState.UNVERIFIED,
            }[record.outcome]
            for artifact_id in subject_ids:
                current = self._validation[artifact_id]
                self._validation[artifact_id] = ArtifactValidation(
                    state,
                    (*current.verification_refs, record.reference),
                    current.superseded_by,
                    (*current.verification_records, record),
                )
        return record.reference

    def verification(self, reference: str) -> VerificationRecord:
        with self._lock:
            for validation in self._validation.values():
                for record in validation.verification_records:
                    if record.reference == reference:
                        return record
        raise KeyError(f"VerificationRecord 不存在: {reference}")

    def is_verified(
        self, reference: str, *, workspace_hash: str = ""
    ) -> bool:
        validation = self.validation(reference)
        if validation.state is not ArtifactValidationState.VERIFIED:
            return False
        for record in reversed(validation.verification_records):
            if record.outcome is VerificationOutcome.PASSED:
                relevant_hashes = {
                    item: self.get(item).content_hash
                    for item in record.subject_refs
                }
                return record.is_fresh(
                    relevant_hashes, workspace_hash=workspace_hash
                )
        return False

    def mark_unknown(
        self,
        references: tuple[str, ...],
        *,
        validator_kind: str,
        summary: str,
        evidence_refs: tuple[str, ...] = (),
        workspace_hash: str = "",
    ) -> str:
        return self.record_verification(VerificationRecord.create(
            validator_kind,
            VerificationOutcome.UNKNOWN,
            references,
            evidence_refs,
            summary,
            subject_hashes=self._subject_hashes(references),
            workspace_hash=workspace_hash,
        ))

    def supersede(self, references: tuple[str, ...], superseded_by: str) -> None:
        with self._lock:
            self.get(superseded_by)
            for reference in references:
                artifact_id = reference.removeprefix("artifact://")
                if artifact_id not in self._by_id:
                    raise KeyError(f"Artifact 不存在: {reference}")
                current = self._validation[artifact_id]
                self._validation[artifact_id] = ArtifactValidation(
                    ArtifactValidationState.SUPERSEDED,
                    current.verification_refs,
                    superseded_by,
                    current.verification_records,
                )
