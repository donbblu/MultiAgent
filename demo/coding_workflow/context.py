from __future__ import annotations

import re
from pathlib import Path

from .models import ProjectFile, TaskContext
from .workspace import ProjectWorkspace


class ProjectContextBuilder:
    """根据需求关键词选择相关文件，避免把整个代码库发送给模型。"""

    PRIORITY_FILES = {
        "README.md", "pyproject.toml", "requirements.txt", "package.json",
        "tsconfig.json", "Cargo.toml", "go.mod", "AGENTS.md",
    }
    SENSITIVE_NAMES = {".env", "credentials", "credentials.json", "secrets.json"}
    SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}

    def __init__(
        self, workspace: ProjectWorkspace, max_files: int = 20, max_total_chars: int = 80_000
    ) -> None:
        self.workspace = workspace
        self.max_files = max_files
        self.max_total_chars = max_total_chars

    def select(self, task: TaskContext) -> list[ProjectFile]:
        terms = {
            term.lower()
            for term in re.findall(r"[A-Za-z0-9_./-]{3,}", " ".join([
                task.objective, task.user_request, *task.feedback
            ]))
        }
        ranked: list[tuple[int, str]] = []
        for path in self.workspace.list_files():
            name = Path(path).name
            if name in self.SENSITIVE_NAMES or Path(path).suffix.lower() in self.SENSITIVE_SUFFIXES:
                continue
            lower = path.lower()
            score = 100 if name in self.PRIORITY_FILES else 0
            score += sum(10 for term in terms if term in lower)
            if name.startswith("test_") or "/test" in lower:
                score += 5
            ranked.append((score, path))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        selected: list[ProjectFile] = []
        remaining = self.max_total_chars
        for _, path in ranked[: self.max_files]:
            if remaining <= 0:
                break
            content = self.workspace.read_text(path, max_chars=remaining)
            selected.append(ProjectFile(path, content, len(content) >= remaining))
            remaining -= len(content)
        return selected
