from __future__ import annotations

import json
import shutil
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
import weakref
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterator, Mapping, NoReturn
from urllib.parse import urlparse

from ..artifacts import Artifact, ArtifactStore
from ..harness.lifecycle import LifecycleController
from ..local_execution import (
    FROZEN_PATH,
    PROFILE_VISIONFORGE_BROWSER,
    PROFILE_VISIONFORGE_BUILD,
    PROFILE_VISIONFORGE_DEV,
    SANDBOX_REQUIRED,
    ExecutionOutcome,
    LocalExecutionError,
    SupervisedBackground,
    prepare_execution,
    redact_text,
    run_prepared,
    sanitize_output,
    start_prepared_background,
)
from ..local_execution_approval import (
    LocalExecutionApprover,
    LocalExecutionManagedResult,
)
from .assets import ImageAssetStore
from .artifact_types import ACTUAL_SCREENSHOT, BROWSER_RUN
from .contracts import (
    BrowserAssertion,
    BrowserRunResult,
    InteractionAction,
    UISpec,
    VisionForgeSchemaError,
    ViewportSpec,
)


class BrowserRuntimeError(RuntimeError):
    def __init__(self, reason: object) -> None:
        super().__init__(redact_text(str(reason)))


_PUBLIC_OUTPUT_LIMIT = 10_000


def _bounded_public_text(
    value: object,
    *,
    limit: int = _PUBLIC_OUTPUT_LIMIT,
) -> tuple[str, bool, int, str]:
    public_value = (
        value
        if isinstance(value, (str, bytes)) or value is None
        else str(value)
    )
    bounded = sanitize_output(public_value, limit_chars=limit)
    return (
        bounded.text,
        bounded.truncated,
        bounded.raw_chars,
        bounded.raw_sha256,
    )


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _public_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({
        redact_text(str(key)): _public_value(item)
        for key, item in value.items()
    })


def _public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _public_mapping(value)
    if isinstance(value, tuple):
        return tuple(_public_value(item) for item in value)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, (str, bytes)):
        return _bounded_public_text(value)[0]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_public_text(value)[0]


def _plain_public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_public_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_plain_public_value(item) for item in value]
    return value


def _validated_workspace_path(
    value: Path,
    *,
    workspace_root: Path,
    label: str,
    must_exist: bool,
    require_directory: bool = False,
    require_file: bool = False,
) -> Path:
    root_input = Path(workspace_root)
    candidate_input = Path(value)
    if not root_input.parts or root_input.is_symlink():
        raise BrowserRuntimeError(f"{label} 的 Workspace 根目录不安全")
    if not candidate_input.parts or ".." in candidate_input.parts:
        raise BrowserRuntimeError(f"{label} 路径不安全")

    root = root_input.resolve()
    if not root.is_dir():
        raise BrowserRuntimeError(f"{label} 的 Workspace 根目录不存在")
    candidate = (
        candidate_input
        if candidate_input.is_absolute()
        else root / candidate_input
    )
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise BrowserRuntimeError(f"{label} 路径越过 Workspace 边界")

    cursor = candidate
    while True:
        if cursor.is_symlink():
            raise BrowserRuntimeError(f"{label} 路径不允许使用符号链接")
        if cursor.resolve(strict=False) == root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise BrowserRuntimeError(f"{label} 路径越过 Workspace 边界")
        cursor = parent

    if must_exist and not resolved.exists():
        raise BrowserRuntimeError(f"{label} 路径不存在")
    if require_directory and not resolved.is_dir():
        raise BrowserRuntimeError(f"{label} 必须是目录")
    if require_file and not resolved.is_file():
        raise BrowserRuntimeError(f"{label} 必须是文件")
    if not must_exist and resolved.exists() and not resolved.is_file():
        raise BrowserRuntimeError(f"{label} 必须是文件路径")
    return resolved


def validate_browser_cwd(cwd: Path, *, workspace_root: Path) -> Path:
    """验证前台或后台进程 cwd 位于已登记 Workspace 内。"""

    return _validated_workspace_path(
        cwd,
        workspace_root=workspace_root,
        label="browser cwd",
        must_exist=True,
        require_directory=True,
    )


def validate_browser_log_path(log_path: Path, *, workspace_root: Path) -> Path:
    """验证后台日志目标位于 Workspace 内且没有符号链接分量。"""

    return _validated_workspace_path(
        log_path,
        workspace_root=workspace_root,
        label="browser log",
        must_exist=False,
    )


def validate_loopback_browser_url(value: str) -> str:
    """只接纳无凭据、带端口的 loopback HTTP URL。"""

    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise BrowserRuntimeError("Browser URL 无效") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BrowserRuntimeError("Browser URL 必须是无凭据、带端口的本地 HTTP URL")
    return value


@dataclass(frozen=True)
class BrowserCommandPaths:
    cwd: Path
    runner: Path
    url: str
    spec: Path
    screenshot: Path
    result: Path


def validate_browser_command_paths(
    command: tuple[str, ...] | list[str],
    *,
    workspace_root: Path,
    cwd: Path,
) -> BrowserCommandPaths:
    """解析固定 Browser Runner argv，并验证所有文件路径和 URL。"""

    if not isinstance(command, (tuple, list)) or not all(
        isinstance(item, str) and item for item in command
    ):
        raise BrowserRuntimeError("Browser Runner 命令必须是非空字符串数组")
    expected_switches = ("--url", "--spec", "--screenshot", "--result")
    if (
        len(command) != 10
        or command[0] != "node"
        or tuple(command[index] for index in (2, 4, 6, 8))
        != expected_switches
    ):
        raise BrowserRuntimeError("Browser Runner argv 不符合冻结协议")

    validated_cwd = validate_browser_cwd(cwd, workspace_root=workspace_root)
    runner = _validated_workspace_path(
        Path(command[1]),
        workspace_root=workspace_root,
        label="browser runner",
        must_exist=True,
        require_file=True,
    )
    if not runner.is_relative_to(validated_cwd):
        raise BrowserRuntimeError("browser runner 不在执行 cwd 内")
    url = validate_loopback_browser_url(command[3])
    spec = _validated_workspace_path(
        Path(command[5]),
        workspace_root=workspace_root,
        label="browser spec",
        must_exist=True,
        require_file=True,
    )
    screenshot = _validated_workspace_path(
        Path(command[7]),
        workspace_root=workspace_root,
        label="browser screenshot",
        must_exist=False,
    )
    result = _validated_workspace_path(
        Path(command[9]),
        workspace_root=workspace_root,
        label="browser result",
        must_exist=False,
    )
    if len({runner, spec, screenshot, result}) != 4:
        raise BrowserRuntimeError("Browser Runner 输入和输出路径必须互不相同")
    return BrowserCommandPaths(
        validated_cwd,
        runner,
        url,
        spec,
        screenshot,
        result,
    )


@dataclass(frozen=True)
class ProcessExecution:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    stdout_chars: int = 0
    stderr_chars: int = 0
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    profile_manifest: Mapping[str, object] = field(default_factory=dict)
    cleanup_evidence: Mapping[str, object] = field(default_factory=dict)
    cleanup_evidence_digest: str = ""

    def __post_init__(self) -> None:
        sanitized_command = tuple(
            _bounded_public_text(part)[0] for part in self.command
        )
        object.__setattr__(self, "command", sanitized_command)
        for stream in ("stdout", "stderr"):
            raw = getattr(self, stream)
            sanitized, truncated, chars, digest = _bounded_public_text(raw)
            supplied_digest = getattr(self, f"{stream}_sha256")
            supplied_chars = getattr(self, f"{stream}_chars")
            supplied_truncated = getattr(self, f"{stream}_truncated")
            metadata_supplied = _valid_digest(supplied_digest)
            object.__setattr__(self, stream, sanitized)
            object.__setattr__(
                self,
                f"{stream}_chars",
                supplied_chars
                if metadata_supplied
                and isinstance(supplied_chars, int)
                and not isinstance(supplied_chars, bool)
                and supplied_chars >= 0
                else chars,
            )
            object.__setattr__(
                self,
                f"{stream}_sha256",
                supplied_digest if metadata_supplied else digest,
            )
            object.__setattr__(
                self,
                f"{stream}_truncated",
                supplied_truncated if metadata_supplied else truncated,
            )
        object.__setattr__(
            self,
            "profile_manifest",
            _public_mapping(self.profile_manifest),
        )
        object.__setattr__(
            self,
            "cleanup_evidence",
            _public_mapping(self.cleanup_evidence),
        )
        object.__setattr__(
            self,
            "cleanup_evidence_digest",
            _bounded_public_text(self.cleanup_evidence_digest)[0],
        )

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "passed": self.passed,
            "stdout_chars": self.stdout_chars,
            "stderr_chars": self.stderr_chars,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "profile_manifest": _plain_public_value(self.profile_manifest),
            "cleanup_evidence": _plain_public_value(self.cleanup_evidence),
            "cleanup_evidence_digest": self.cleanup_evidence_digest,
        }


@dataclass(frozen=True)
class BrowserProjectConfig:
    origin: str
    entry_route: str
    viewport: ViewportSpec
    build_command: tuple[str, ...]
    dev_command: tuple[str, ...]
    browser_runner: str

    FORBIDDEN_ARGUMENTS = frozenset({
        "install", "uninstall", "publish", "deploy", "push",
    })

    @property
    def page_url(self) -> str:
        return self.origin.rstrip("/") + "/" + self.entry_route.lstrip("/")

    @classmethod
    def load(cls, project_root: Path) -> "BrowserProjectConfig":
        config_path = project_root / "visionforge.template.json"
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeError(f"无法读取 VisionForge 项目配置: {exc}") from exc
        if not isinstance(data, dict):
            raise BrowserRuntimeError("VisionForge 项目配置必须是对象")
        commands = data.get("commands")
        if not isinstance(commands, dict):
            raise BrowserRuntimeError("VisionForge 项目配置缺少 commands")
        build = cls._command(commands.get("build"), "build")
        dev = cls._command(commands.get("dev"), "dev")
        origin = str(data.get("origin", ""))
        parsed = urlparse(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None
            or parsed.path not in {"", "/"}
        ):
            raise BrowserRuntimeError("VisionForge origin 必须是带端口的本地 HTTP 地址")
        entry_route = str(data.get("entry_route", ""))
        if not entry_route.startswith("/") or ".." in PurePosixPath(entry_route).parts:
            raise BrowserRuntimeError("VisionForge entry_route 必须是安全的绝对路由")
        browser_runner = str(data.get("browser_runner", ""))
        runner_path = PurePosixPath(browser_runner)
        if runner_path.is_absolute() or ".." in runner_path.parts or not runner_path.parts:
            raise BrowserRuntimeError("browser_runner 路径不安全")
        _validated_workspace_path(
            Path(*runner_path.parts),
            workspace_root=project_root,
            label="browser_runner",
            must_exist=True,
            require_file=True,
        )
        return cls(
            origin,
            entry_route,
            ViewportSpec.from_dict(data.get("viewport")),
            build,
            dev,
            browser_runner,
        )

    @classmethod
    def _command(cls, value: object, name: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item for item in value
        ):
            raise BrowserRuntimeError(f"{name} 命令必须是非空字符串数组")
        if value[0] != "pnpm":
            raise BrowserRuntimeError(f"{name} 命令只允许使用 pnpm")
        forbidden = cls.FORBIDDEN_ARGUMENTS.intersection(
            item.lower() for item in value[1:]
        )
        if forbidden:
            raise BrowserRuntimeError(
                f"{name} 命令包含禁止参数: {', '.join(sorted(forbidden))}"
            )
        return tuple(value)


class BrowserProcessRunner:
    """运行受信任的数组命令，并在超时或取消时终止整个进程组。"""

    BUILD_COMMAND = ("pnpm", "run", "build")
    DEV_COMMAND = ("pnpm", "run", "dev", "--port", "4173")
    BUILD_DEADLINE_SECONDS = 60.0
    BROWSER_DEADLINE_SECONDS = 45.0
    DEV_DEADLINE_SECONDS = 60.0

    def __init__(
        self,
        *,
        allowed_executables: frozenset[str] = frozenset({"node", "pnpm"}),
        executable_overrides: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        poll_interval: float = 0.05,
        workspace_root: Path | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval 必须大于 0")
        if environment:
            raise ValueError("Browser Runtime 不接受调用方环境扩展")
        self.allowed_executables = frozenset(allowed_executables)
        self.executable_overrides = MappingProxyType(dict(executable_overrides or {}))
        self.environment = MappingProxyType({
            "PATH": FROZEN_PATH,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        })
        self.poll_interval = poll_interval
        if workspace_root is None:
            self.workspace_root: Path | None = None
        else:
            try:
                self.workspace_root = validate_browser_cwd(
                    workspace_root,
                    workspace_root=workspace_root,
                )
            except BrowserRuntimeError as exc:
                raise ValueError(
                    f"Browser Runtime workspace_root 无效: {exc}"
                ) from exc

    def resolve(self, command: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if not command or command[0] not in self.allowed_executables:
            raise BrowserRuntimeError(f"浏览器 Runtime 禁止命令: {command[0] if command else ''}")
        executable = self.executable_overrides.get(command[0]) or shutil.which(
            command[0], path=FROZEN_PATH
        )
        if not executable:
            raise BrowserRuntimeError(f"找不到可执行文件: {command[0]}")
        return (executable, *command[1:])

    def run(
        self,
        command: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        lifecycle: LifecycleController | None = None,
        trusted_local: object = None,
    ) -> ProcessExecution:
        requested = self._requested_command(command)
        workspace_root = self._workspace_root(
            cwd,
            trusted_local=trusted_local,
        )
        if requested == self.BUILD_COMMAND:
            profile_id = PROFILE_VISIONFORGE_BUILD
            maximum_deadline = self.BUILD_DEADLINE_SECONDS
        elif requested[0] == "node":
            try:
                validate_browser_command_paths(
                    requested,
                    workspace_root=workspace_root,
                    cwd=workspace_root,
                )
            except BrowserRuntimeError as exc:
                self._reject(f"Browser Runner preflight rejected: {exc}")
            profile_id = PROFILE_VISIONFORGE_BROWSER
            maximum_deadline = self.BROWSER_DEADLINE_SECONDS
        else:
            self._reject("Browser Runtime command is not registered")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
            or timeout_seconds > maximum_deadline
        ):
            self._reject("Browser Runtime timeout 超出冻结 Profile 上限")
        executable = self._profile_executable(requested, workspace_root)
        prepared = prepare_execution(
            profile_id=profile_id,
            workspace_root=workspace_root,
            executable=executable,
            command=requested,
            wall_deadline_seconds=timeout_seconds,
            output_limit_chars=_PUBLIC_OUTPUT_LIMIT,
        )
        outcome = run_prepared(
            prepared,
            trusted_local=trusted_local,
            poll_interval=self.poll_interval,
            lifecycle=lifecycle,
        )
        return self._execution_from_outcome(requested, outcome)

    def start_background(
        self,
        command: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        log_path: Path,
        trusted_local: object = None,
    ) -> "ManagedProcess":
        requested = self._requested_command(command)
        if requested != self.DEV_COMMAND:
            self._reject("VisionForge dev command is not registered")
        workspace_root = self._workspace_root(
            cwd,
            trusted_local=trusted_local,
        )
        try:
            validated_log = validate_browser_log_path(
                log_path, workspace_root=workspace_root
            )
        except BrowserRuntimeError as exc:
            self._reject(f"Browser server log preflight rejected: {exc}")
        executable = self._profile_executable(requested, workspace_root)
        prepared = prepare_execution(
            profile_id=PROFILE_VISIONFORGE_DEV,
            workspace_root=workspace_root,
            executable=executable,
            command=requested,
            wall_deadline_seconds=self.DEV_DEADLINE_SECONDS,
            output_limit_chars=_PUBLIC_OUTPUT_LIMIT,
            output_kind="server_log",
        )
        supervised = start_prepared_background(
            prepared,
            trusted_local=trusted_local,
            log_path=validated_log,
        )
        return ManagedProcess(supervised, self)

    def _environment(self) -> dict[str, str]:
        return dict(self.environment)

    @staticmethod
    def _requested_command(
        command: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        if (
            not isinstance(command, (tuple, list))
            or not command
            or not all(
                isinstance(part, str) and part and "\0" not in part
                for part in command
            )
        ):
            BrowserProcessRunner._reject("Browser Runtime argv 无效")
        return tuple(command)

    def _workspace_root(
        self,
        cwd: Path,
        *,
        trusted_local: object,
    ) -> Path:
        if self.workspace_root is None and trusted_local is None:
            self._reject(
                "Browser Runtime requires a registered workspace_root "
                "before issuing a confirmation challenge"
            )
        registered_root = self.workspace_root or cwd
        try:
            return validate_browser_cwd(
                cwd,
                workspace_root=registered_root,
            )
        except (BrowserRuntimeError, TypeError, ValueError) as exc:
            self._reject(
                f"Browser Runtime cwd preflight rejected: {exc}"
            )

    def _profile_executable(
        self,
        command: tuple[str, ...],
        workspace_root: Path,
    ) -> str:
        try:
            resolved = self.resolve(command)
        except BrowserRuntimeError as exc:
            self._reject(str(exc))
        executable = Path(resolved[0])
        if (
            not executable.is_absolute()
            or executable.resolve(strict=False).is_relative_to(workspace_root)
        ):
            self._reject("Browser Runtime executable 未由冻结 Runtime PATH 解析")
        return str(executable)

    @staticmethod
    def _execution_from_outcome(
        command: tuple[str, ...],
        outcome: ExecutionOutcome,
    ) -> ProcessExecution:
        result = ProcessExecution(
            command,
            124 if outcome.timed_out else (
                outcome.exit_code if outcome.exit_code is not None else 1
            ),
            "",
            "",
            outcome.duration_ms,
            outcome.timed_out,
            profile_manifest=outcome.profile_manifest,
            cleanup_evidence=outcome.cleanup_evidence,
            cleanup_evidence_digest=outcome.cleanup_evidence_digest,
        )
        for stream, bounded in (
            ("stdout", outcome.stdout),
            ("stderr", outcome.stderr),
        ):
            object.__setattr__(result, stream, bounded.text)
            object.__setattr__(result, f"{stream}_chars", bounded.raw_chars)
            object.__setattr__(result, f"{stream}_sha256", bounded.raw_sha256)
            object.__setattr__(result, f"{stream}_truncated", bounded.truncated)
        return result

    @staticmethod
    def _reject(reason: str) -> NoReturn:
        raise LocalExecutionError(SANDBOX_REQUIRED, reason)


@dataclass(frozen=True)
class _ManagedProcessSnapshot:
    profile_manifest: Mapping[str, object]
    cleanup_evidence: Mapping[str, object]
    cleanup_evidence_digest: str
    server_log: Mapping[str, object]


_MANAGED_PROCESS_LOCK = threading.RLock()
_MANAGED_PROCESS_STATES: weakref.WeakKeyDictionary[
    object, SupervisedBackground | _ManagedProcessSnapshot
] = weakref.WeakKeyDictionary()
_MANAGED_PROCESS_FINALIZERS: weakref.WeakKeyDictionary[
    object, weakref.finalize
] = weakref.WeakKeyDictionary()


def _managed_process_snapshot(
    supervised: SupervisedBackground,
) -> _ManagedProcessSnapshot:
    return _ManagedProcessSnapshot(
        MappingProxyType(dict(supervised.profile_manifest)),
        MappingProxyType(dict(supervised.cleanup_evidence)),
        str(supervised.cleanup_evidence_digest),
        MappingProxyType(dict(supervised.server_log)),
    )


class ManagedProcess(LocalExecutionManagedResult):
    __slots__ = ("_handle", "_snapshot", "log_path", "__weakref__")

    def __init__(
        self,
        supervised: SupervisedBackground,
        runner: BrowserProcessRunner,
    ) -> None:
        if type(supervised) is not SupervisedBackground:
            raise TypeError("managed process requires a Runtime supervisor")
        if type(runner) is not BrowserProcessRunner:
            raise TypeError("managed process requires a Browser Runtime")
        self._handle = uuid.uuid4().hex
        self._snapshot: _ManagedProcessSnapshot | None = None
        self.log_path = supervised.log_path
        with _MANAGED_PROCESS_LOCK:
            _MANAGED_PROCESS_STATES[self] = supervised
            finalizer = weakref.finalize(
                self,
                supervised.request_stop,
                "abandoned",
            )
            finalizer.atexit = False
            _MANAGED_PROCESS_FINALIZERS[self] = finalizer

    def _state(self) -> SupervisedBackground | _ManagedProcessSnapshot:
        with _MANAGED_PROCESS_LOCK:
            if self._snapshot is not None:
                return self._snapshot
            state = _MANAGED_PROCESS_STATES.get(self)
        if state is None:
            raise BrowserRuntimeError("managed process handle is no longer valid")
        return state

    def local_execution_approval_state(self) -> Mapping[str, object]:
        """Return only authority-free state for the Composition approval gate."""

        if (
            type(self._handle) is not str
            or not isinstance(self.log_path, Path)
            or self._snapshot is not None
        ):
            raise BrowserRuntimeError("managed process public handle is invalid")
        return MappingProxyType({
            "handle": self._handle,
            "log_path": str(self.log_path),
        })

    def discard_local_execution_result(self) -> None:
        self.stop()

    def _snapshot_terminal_state(self, state: SupervisedBackground) -> bool:
        if state.cleanup_terminal is not True:
            return False
        snapshot = _managed_process_snapshot(state)
        with _MANAGED_PROCESS_LOCK:
            if _MANAGED_PROCESS_STATES.get(self) is not state:
                return self._snapshot is not None
            _MANAGED_PROCESS_STATES.pop(self, None)
            self._snapshot = snapshot
            finalizer = _MANAGED_PROCESS_FINALIZERS.pop(self, None)
            if finalizer is not None:
                finalizer.detach()
        return True

    def stop(self) -> None:
        state = self._state()
        if isinstance(state, _ManagedProcessSnapshot):
            return
        try:
            state.stop()
        except BaseException:
            self._snapshot_terminal_state(state)
            raise
        if not self._snapshot_terminal_state(state):
            raise BrowserRuntimeError(
                "Runtime cleanup returned before reaching a terminal state"
            )

    @property
    def running(self) -> bool:
        state = self._state()
        return (
            state.running
            if isinstance(state, SupervisedBackground)
            else False
        )

    def log_tail(self, limit: int = 4000) -> str:
        state = self._state()
        if isinstance(state, SupervisedBackground):
            return state.log_tail(limit)
        try:
            return self.log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-limit:]
        except OSError:
            return ""

    @property
    def profile_manifest(self) -> Mapping[str, object]:
        return self._state().profile_manifest

    @property
    def cleanup_evidence(self) -> Mapping[str, object]:
        return self._state().cleanup_evidence

    @property
    def cleanup_evidence_digest(self) -> str:
        return self._state().cleanup_evidence_digest

    @property
    def server_log(self) -> Mapping[str, object]:
        return self._state().server_log

    @property
    def server_log_chars(self) -> int:
        return int(self.server_log["chars"])

    @property
    def server_log_sha256(self) -> str:
        return str(self.server_log["sha256"])

    @property
    def server_log_truncated(self) -> bool:
        return bool(self.server_log["truncated"])

    def evidence(self) -> Mapping[str, object]:
        return MappingProxyType({
            "profile_manifest": self.profile_manifest,
            "cleanup_evidence": self.cleanup_evidence,
            "cleanup_evidence_digest": self.cleanup_evidence_digest,
            "server_log": self.server_log,
        })


class VisionForgeLocalExecutionApprover(LocalExecutionApprover):
    """VisionForge-owned typed adapter over the Core approval state machine."""

    def run_browser(
        self,
        runner: object,
        command: tuple[str, ...] | list[str],
        *,
        cwd: object,
        timeout_seconds: float,
        lifecycle: object | None = None,
    ) -> ProcessExecution:
        if type(runner) is not BrowserProcessRunner:
            raise TypeError("runner is not a Browser Runtime entrypoint")
        frozen_command = tuple(command)
        return self._invoke_fixed(lambda trusted_local: runner.run(
            frozen_command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            lifecycle=lifecycle,
            trusted_local=trusted_local,
        ), expected_type=ProcessExecution)

    def start_browser(
        self,
        runner: object,
        command: tuple[str, ...] | list[str],
        *,
        cwd: object,
        log_path: object,
    ) -> ManagedProcess:
        if type(runner) is not BrowserProcessRunner:
            raise TypeError("runner is not a Browser Runtime entrypoint")
        frozen_command = tuple(command)
        return self._invoke_fixed(lambda trusted_local: runner.start_background(
            frozen_command,
            cwd=cwd,
            log_path=log_path,
            trusted_local=trusted_local,
        ), expected_type=ManagedProcess)


class BrowserProjectRuntime:
    def __init__(
        self,
        project_root: Path,
        process_runner: BrowserProcessRunner,
        *,
        approver_factory: Callable[
            [], VisionForgeLocalExecutionApprover
        ] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = BrowserProjectConfig.load(self.project_root)
        self.process_runner = process_runner
        if approver_factory is not None and not callable(approver_factory):
            raise TypeError("approver_factory 必须可调用")
        self.approver_factory = approver_factory

    def _approver(self) -> VisionForgeLocalExecutionApprover | None:
        if self.approver_factory is None:
            return None
        approver = self.approver_factory()
        if type(approver) is not VisionForgeLocalExecutionApprover:
            raise TypeError(
                "approver_factory 必须返回 VisionForgeLocalExecutionApprover"
            )
        return approver

    def build(
        self,
        *,
        timeout_seconds: float = 60,
        lifecycle: LifecycleController | None = None,
    ) -> ProcessExecution:
        approver = self._approver()
        if approver is None:
            return self.process_runner.run(
                self.config.build_command,
                cwd=self.project_root,
                timeout_seconds=timeout_seconds,
                lifecycle=lifecycle,
            )
        return approver.run_browser(
            self.process_runner,
            self.config.build_command,
            cwd=self.project_root,
            timeout_seconds=timeout_seconds,
            lifecycle=lifecycle,
        )

    @contextmanager
    def running_server(
        self,
        *,
        log_path: Path,
        readiness_timeout_seconds: float = 15,
        lifecycle: LifecycleController | None = None,
    ) -> Iterator[str]:
        approver = self._approver()
        if approver is None:
            managed = self.process_runner.start_background(
                self.config.dev_command,
                cwd=self.project_root,
                log_path=log_path,
            )
        else:
            managed = approver.start_browser(
                self.process_runner,
                self.config.dev_command,
                cwd=self.project_root,
                log_path=log_path,
            )
        pending_error: BaseException | None = None
        try:
            self._wait_until_ready(
                managed,
                readiness_timeout_seconds,
                lifecycle,
            )
            yield self.config.page_url
        except BaseException as exc:
            pending_error = exc
            raise
        finally:
            managed.stop()
            if pending_error is not None:
                for name in (
                    "cleanup_evidence",
                    "cleanup_evidence_digest",
                    "profile_manifest",
                ):
                    if not hasattr(managed, name):
                        continue
                    try:
                        setattr(pending_error, name, getattr(managed, name))
                    except (AttributeError, TypeError):
                        pass

    def _wait_until_ready(
        self,
        managed: ManagedProcess,
        timeout_seconds: float,
        lifecycle: LifecycleController | None,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        last_error = ""
        while time.monotonic() < deadline:
            if lifecycle:
                lifecycle.checkpoint()
            if not managed.running:
                raise BrowserRuntimeError(
                    "Vue 开发服务器提前退出: " + managed.log_tail()
                )
            try:
                with opener.open(self.config.page_url, timeout=0.5) as response:
                    if 200 <= response.status < 400:
                        return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.05)
        raise BrowserRuntimeError(
            f"Vue 开发服务器 readiness 超时: {last_error}\n{managed.log_tail()}"
        )


@dataclass(frozen=True)
class BrowserTestArtifacts:
    build_artifact_ref: str
    screenshot_artifact_ref: str
    browser_run_artifact_ref: str
    result: BrowserRunResult


class PlaywrightBrowserTester:
    """Runtime 控制的 Browser Tester；模型不能执行命令或自行宣告通过。"""

    def __init__(
        self,
        project_root: Path,
        process_runner: BrowserProcessRunner,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
        runtime_dir: Path,
        *,
        approver_factory: Callable[
            [], VisionForgeLocalExecutionApprover
        ] | None = None,
    ) -> None:
        self.project = BrowserProjectRuntime(
            project_root,
            process_runner,
            approver_factory=approver_factory,
        )
        self.process_runner = process_runner
        self.artifacts = artifacts
        self.image_assets = image_assets
        self.approver_factory = approver_factory
        self.runtime_dir = runtime_dir.resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        *,
        task_id: str,
        ui_spec: UISpec,
        artifact_prefix: str = "browser",
        lifecycle: LifecycleController | None = None,
    ) -> BrowserTestArtifacts:
        if (
            not isinstance(artifact_prefix, str)
            or not artifact_prefix
            or artifact_prefix in {".", ".."}
            or "\0" in artifact_prefix
            or "/" in artifact_prefix
            or "\\" in artifact_prefix
            or Path(artifact_prefix).is_absolute()
        ):
            raise BrowserRuntimeError("artifact_prefix 必须是安全的单一路径组件")
        derived_paths = {
            "spec": self.runtime_dir / f"{artifact_prefix}-ui-spec.json",
            "screenshot": self.runtime_dir / f"{artifact_prefix}-actual.png",
            "result": self.runtime_dir / f"{artifact_prefix}-result.json",
            "server_log": self.runtime_dir / f"{artifact_prefix}-server.log",
        }
        for label, candidate in derived_paths.items():
            _validated_workspace_path(
                candidate,
                workspace_root=self.runtime_dir,
                label=f"browser {label}",
                must_exist=False,
            )
        if ui_spec.viewport != self.project.config.viewport:
            raise BrowserRuntimeError("UI Spec viewport 与固定项目配置不一致")
        build = self.project.build(lifecycle=lifecycle)
        build_ref = self.artifacts.put(Artifact.create(
            f"{artifact_prefix}-build",
            task_id,
            build.to_dict(),
            kind="build_result",
        ))
        if not build.passed:
            message = "Vue 构建失败: " + (build.stderr or build.stdout)[-2000:]
            screenshot_ref, _ = self.image_assets.create_artifact(
                self.artifacts,
                name=f"{artifact_prefix}-build-failure-screenshot",
                task_id=task_id,
                data=self._placeholder_png(
                    ui_spec.viewport.width, ui_spec.viewport.height
                ),
                kind=ACTUAL_SCREENSHOT,
            )
            result = BrowserRunResult(
                BrowserRunResult.CURRENT_VERSION,
                False,
                self.project.config.page_url,
                ui_spec.viewport,
                (BrowserAssertion(
                    "runtime-build",
                    InteractionAction.EXPECT_VISIBLE,
                    "[data-testid=page-shell]",
                    False,
                    "",
                    message,
                    build.duration_ms,
                ),),
                (),
                (message,),
                (),
                screenshot_ref,
                build.duration_ms,
            )
            browser_run_ref = self.artifacts.put(Artifact.create(
                f"{artifact_prefix}-run",
                task_id,
                result.to_dict(),
                kind=BROWSER_RUN,
                metadata={
                    "screenshot_artifact_ref": screenshot_ref,
                    "passed": False,
                    "build_failed": True,
                },
            ))
            return BrowserTestArtifacts(
                build_ref, screenshot_ref, browser_run_ref, result
            )
        spec_path = derived_paths["spec"]
        screenshot_path = derived_paths["screenshot"]
        result_path = derived_paths["result"]
        server_log = derived_paths["server_log"]
        spec_path.write_text(
            json.dumps(ui_spec.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        runner_path = self.project.project_root / self.project.config.browser_runner
        with self.project.running_server(
            log_path=server_log,
            lifecycle=lifecycle,
        ) as page_url:
            browser_command = (
                "node", str(runner_path),
                "--url", page_url,
                "--spec", str(spec_path),
                "--screenshot", str(screenshot_path),
                "--result", str(result_path),
            )
            approver = self.project._approver()
            if approver is None:
                execution = self.process_runner.run(
                    browser_command,
                    cwd=self.project.project_root,
                    timeout_seconds=45,
                    lifecycle=lifecycle,
                )
            else:
                execution = approver.run_browser(
                    self.process_runner,
                    browser_command,
                    cwd=self.project.project_root,
                    timeout_seconds=45,
                    lifecycle=lifecycle,
                )
        if not execution.passed:
            raise BrowserRuntimeError(
                "Playwright Browser Tester 失败: "
                + (execution.stderr or execution.stdout)[-3000:]
            )
        try:
            raw_result = json.loads(result_path.read_text(encoding="utf-8"))
            screenshot_data = screenshot_path.read_bytes()
        except (OSError, json.JSONDecodeError) as exc:
            raise BrowserRuntimeError(f"Browser Tester 产物无效: {exc}") from exc
        screenshot_ref, _ = self.image_assets.create_artifact(
            self.artifacts,
            name=f"{artifact_prefix}-actual-screenshot",
            task_id=task_id,
            data=screenshot_data,
            kind=ACTUAL_SCREENSHOT,
        )
        try:
            result = BrowserRunResult.from_runner_payload(raw_result, screenshot_ref)
        except VisionForgeSchemaError as exc:
            raise BrowserRuntimeError(f"Browser Run 协议无效: {exc}") from exc
        browser_run_ref = self.artifacts.put(Artifact.create(
            f"{artifact_prefix}-run",
            task_id,
            result.to_dict(),
            kind=BROWSER_RUN,
            metadata={
                "screenshot_artifact_ref": screenshot_ref,
                "passed": result.passed,
            },
        ))
        return BrowserTestArtifacts(build_ref, screenshot_ref, browser_run_ref, result)

    @staticmethod
    def _placeholder_png(width: int, height: int) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        row = b"\xff\xff\xff\xff" * width
        pixels = b"".join(b"\x00" + row for _ in range(height))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
            )
            + chunk(b"IDAT", zlib.compress(pixels, 9))
            + chunk(b"IEND", b"")
        )
