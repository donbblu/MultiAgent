from __future__ import annotations

import os
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath

from .local_execution import (
    PROFILE_LEGACY,
    SANDBOX_REQUIRED,
    LocalExecutionError,
    prepare_execution,
    run_prepared,
)
from .models import CommandResult, FileChange


_RESERVED_TOP_LEVEL_FILES = frozenset({".env", ".env.local"})
_RESERVED_TOP_LEVEL_DIRECTORIES = frozenset({
    ".git",
    ".runtime",
    ".runs",
    ".verification",
    ".harness-hidden-tests",
})
_RESERVED_EXACT_PATHS = frozenset({("solution", "reference.py")})
_LEGACY_COMMAND = ("python3", "-V")
_LEGACY_EXECUTABLE = "/usr/bin/python3"
_SPAWN_FAILURE_PREFIX = "local execution spawn failed: "


class WorkspaceError(RuntimeError):
    pass


class ProjectWorkspace:
    """只允许在指定项目根目录内读写和执行命令。"""

    def __init__(self, root: Path, command_timeout: int = 30) -> None:
        if (
            not isinstance(command_timeout, int)
            or isinstance(command_timeout, bool)
            or command_timeout <= 0
            or command_timeout > 60
        ):
            raise ValueError("command_timeout 必须是 1..60 秒的整数")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.command_timeout = command_timeout

    def _resolve(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or "\0" in relative_path:
            raise WorkspaceError(f"不安全的项目路径: {relative_path!r}")
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise WorkspaceError(f"不安全的项目路径: {relative_path}")

        parts = tuple(path.parts)
        if (
            parts[0] in _RESERVED_TOP_LEVEL_FILES
            or parts[0] in _RESERVED_TOP_LEVEL_DIRECTORIES
            or parts in _RESERVED_EXACT_PATHS
        ):
            raise WorkspaceError(f"保留的项目路径: {relative_path}")

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

    def content_hashes(
        self,
        *,
        exclude: set[str] | None = None,
        exclude_prefixes: tuple[str, ...] = (),
    ) -> dict[str, str]:
        excluded = exclude or set()
        return {
            path: sha256(self._resolve_for_hash(path).read_bytes()).hexdigest()
            for path in self.list_files()
            if path not in excluded
            and not any(
                path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
                for prefix in exclude_prefixes
            )
            and "__pycache__" not in PurePosixPath(path).parts
            and not path.endswith((".pyc", ".pyo"))
        }

    def _resolve_for_hash(self, relative_path: str) -> Path:
        """内部快照可见保留文件，但仍不得越过 Workspace。"""

        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise WorkspaceError(f"不安全的快照路径: {relative_path}")
        candidate = self.root / Path(*path.parts)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"快照路径越过项目边界: {relative_path}")
        if candidate.is_symlink():
            raise WorkspaceError(f"快照路径不能是符号链接: {relative_path}")
        return resolved

    def run(
        self,
        command: list[str],
        *,
        trusted_local: object = None,
    ) -> CommandResult | LocalExecutionError:
        if (
            not isinstance(command, (tuple, list))
            or tuple(command) != _LEGACY_COMMAND
        ):
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "legacy Workspace command is not registered",
            )
        requested_command = list(command)
        prepared = prepare_execution(
            profile_id=PROFILE_LEGACY,
            workspace_root=self.root,
            executable=_LEGACY_EXECUTABLE,
            command=_LEGACY_COMMAND,
            wall_deadline_seconds=self.command_timeout,
            output_limit_chars=10_000,
            python_profile=True,
        )
        try:
            outcome = run_prepared(
                prepared,
                trusted_local=trusted_local,
            )
        except LocalExecutionError as exc:
            if exc.code == SANDBOX_REQUIRED and not exc.reason.startswith(
                _SPAWN_FAILURE_PREFIX
            ):
                return exc
            if not (
                exc.code == SANDBOX_REQUIRED
                and exc.reason.startswith(_SPAWN_FAILURE_PREFIX)
            ):
                raise
            return CommandResult(
                requested_command,
                127,
                "",
                exc.reason.removeprefix(_SPAWN_FAILURE_PREFIX),
                profile_manifest=prepared.profile_manifest,
                cleanup_evidence=getattr(exc, "cleanup_evidence", None),
                cleanup_evidence_digest=getattr(
                    exc,
                    "cleanup_evidence_digest",
                    "",
                ),
            )
        except OSError as exc:
            return CommandResult(
                requested_command,
                127,
                "",
                str(exc),
                profile_manifest=prepared.profile_manifest,
                cleanup_evidence=getattr(exc, "cleanup_evidence", None),
                cleanup_evidence_digest=getattr(
                    exc,
                    "cleanup_evidence_digest",
                    "",
                ),
            )

        timed_out = outcome.timed_out or outcome.exit_code == 124
        result = CommandResult(
            command=requested_command,
            exit_code=124 if timed_out else outcome.exit_code,
            stdout=outcome.stdout.text,
            stderr=outcome.stderr.text,
            timed_out=timed_out,
            stdout_chars=outcome.stdout.raw_chars,
            stderr_chars=outcome.stderr.raw_chars,
            stdout_sha256=outcome.stdout.raw_sha256,
            stderr_sha256=outcome.stderr.raw_sha256,
            stdout_truncated=outcome.stdout.truncated,
            stderr_truncated=outcome.stderr.truncated,
            profile_manifest=outcome.profile_manifest,
            cleanup_evidence=outcome.cleanup_evidence,
            cleanup_evidence_digest=outcome.cleanup_evidence_digest,
        )
        # CommandResult also sanitizes direct construction.  Restore the
        # supervisor's original-stream metadata after passing only bounded text.
        for name, value in (
            ("stdout_chars", outcome.stdout.raw_chars),
            ("stderr_chars", outcome.stderr.raw_chars),
            ("stdout_sha256", outcome.stdout.raw_sha256),
            ("stderr_sha256", outcome.stderr.raw_sha256),
            ("stdout_truncated", outcome.stdout.truncated),
            ("stderr_truncated", outcome.stderr.truncated),
        ):
            object.__setattr__(result, name, value)
        return result
