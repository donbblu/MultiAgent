from __future__ import annotations

import subprocess
import os
import tempfile
from pathlib import Path, PurePosixPath

from .models import CommandResult, FileChange


class WorkspaceError(RuntimeError):
    pass


class ProjectWorkspace:
    """只允许在指定项目根目录内读写和执行命令。"""

    def __init__(self, root: Path, command_timeout: int = 30) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.command_timeout = command_timeout

    def _resolve(self, relative_path: str) -> Path:
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise WorkspaceError(f"不安全的项目路径: {relative_path}")
        resolved = (self.root / Path(*path.parts)).resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"路径越过项目边界: {relative_path}")
        return resolved

    def apply_changes(self, changes: list[FileChange]) -> list[str]:
        targets = [(change, self._resolve(change.path)) for change in changes]
        originals: dict[Path, bytes | None] = {
            target: target.read_bytes() if target.exists() else None
            for _, target in targets
        }
        applied: list[Path] = []
        try:
            with tempfile.TemporaryDirectory(prefix=".runtime-", dir=self.root) as temp:
                staged: list[tuple[Path, Path]] = []
                for index, (change, target) in enumerate(targets):
                    staged_path = Path(temp) / str(index)
                    staged_path.write_text(change.content, encoding="utf-8")
                    staged.append((staged_path, target))
                for staged_path, target in staged:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_path, target)
                    applied.append(target)
        except OSError as exc:
            for target in reversed(applied):
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(original)
            raise WorkspaceError(f"文件变更失败并已回滚: {exc}") from exc
        return [change.path for change, _ in targets]

    def read_text(self, relative_path: str, max_chars: int | None = None) -> str:
        content = self._resolve(relative_path).read_text(encoding="utf-8", errors="replace")
        return content if max_chars is None else content[:max_chars]

    def list_files(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )

    def run(self, command: list[str]) -> CommandResult:
        if not command or any(not isinstance(part, str) for part in command):
            raise WorkspaceError("验证命令必须是非空字符串列表")
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                shell=False,
            )
            return CommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(command, 127, "", str(exc))
