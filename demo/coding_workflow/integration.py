from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from .artifacts import Artifact
from .models import FileChange, ImplementationPlan
from .workspace import ProjectWorkspace


class IntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class IntegrationResult:
    changed_files: tuple[str, ...]


class PatchIntegrator:
    """共享 Workspace 的唯一写入者；先验证整批 Patch，再原子应用。"""

    def __init__(self, workspace: ProjectWorkspace, allowed_paths: Iterable[str]) -> None:
        self.workspace = workspace
        self.allowed_paths = tuple(allowed_paths)

    def integrate(self, artifacts: Iterable[Artifact]) -> IntegrationResult:
        changes: list[FileChange] = []
        owners: dict[str, str] = {}
        for artifact in artifacts:
            if not isinstance(artifact.content, ImplementationPlan):
                raise IntegrationError(f"Artifact {artifact.name} 不是 ImplementationPlan")
            for change in artifact.content.changes:
                self._validate_path(change.path)
                if change.path in owners:
                    raise IntegrationError(
                        f"Patch 冲突: {change.path} 同时由 {owners[change.path]} 和 {artifact.name} 修改"
                    )
                owners[change.path] = artifact.name
                changes.append(change)
        return IntegrationResult(tuple(self.workspace.apply_changes(changes)))

    def _validate_path(self, value: str) -> None:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise IntegrationError(f"不安全的 Patch 路径: {value}")
        if path.parts[0] in {".git", ".runs", ".verification"} or path.name.startswith(".env"):
            raise IntegrationError(f"禁止修改受保护路径: {value}")
        if not any(fnmatch.fnmatch(value, pattern) for pattern in self.allowed_paths):
            raise IntegrationError(f"Patch 路径不在允许范围: {value}")
