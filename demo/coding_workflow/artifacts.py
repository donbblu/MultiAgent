from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


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
        self._lock = RLock()

    def put(self, artifact: Artifact) -> str:
        with self._lock:
            if artifact.artifact_id in self._by_id:
                raise ValueError(f"Artifact ID 已存在: {artifact.artifact_id}")
            self._by_id[artifact.artifact_id] = artifact
            self._latest_by_name[artifact.name] = artifact.artifact_id
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
