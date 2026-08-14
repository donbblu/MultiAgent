from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


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


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    name: str
    task_id: str
    kind: str
    content: object
    metadata: Mapping[str, object]
    created_at: str

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

    def validation(self, reference: str) -> ArtifactValidation:
        artifact_id = reference.removeprefix("artifact://")
        with self._lock:
            try:
                return self._validation[artifact_id]
            except KeyError as exc:
                raise KeyError(f"Artifact 不存在: {reference}") from exc

    def mark_failed(
        self, references: tuple[str, ...], verification_refs: tuple[str, ...]
    ) -> None:
        self._set_validation(
            references, ArtifactValidationState.FAILED, verification_refs
        )

    def mark_verified(
        self, references: tuple[str, ...], verification_refs: tuple[str, ...]
    ) -> None:
        self._set_validation(
            references, ArtifactValidationState.VERIFIED, verification_refs
        )

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
                )

    def _set_validation(
        self,
        references: tuple[str, ...],
        state: ArtifactValidationState,
        verification_refs: tuple[str, ...],
    ) -> None:
        with self._lock:
            for reference in references:
                artifact_id = reference.removeprefix("artifact://")
                if artifact_id not in self._by_id:
                    raise KeyError(f"Artifact 不存在: {reference}")
                current = self._validation[artifact_id]
                if current.state is ArtifactValidationState.SUPERSEDED:
                    raise ValueError(f"已被替代的 Artifact 不能改变验证状态: {reference}")
                self._validation[artifact_id] = ArtifactValidation(
                    state, verification_refs
                )
