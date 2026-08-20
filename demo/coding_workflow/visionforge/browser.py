from __future__ import annotations

import json
import os
import shutil
import signal
import struct
import subprocess
import time
import urllib.error
import urllib.request
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterator, Mapping
from urllib.parse import urlparse

from ..artifacts import Artifact, ArtifactStore
from ..harness.lifecycle import LifecycleController, TaskCancelledError
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
    pass


@dataclass(frozen=True)
class ProcessExecution:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout[-10_000:],
            "stderr": self.stderr[-10_000:],
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "passed": self.passed,
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
        if not (project_root / Path(*runner_path.parts)).is_file():
            raise BrowserRuntimeError("browser_runner 文件不存在")
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

    def __init__(
        self,
        *,
        allowed_executables: frozenset[str] = frozenset({"node", "pnpm"}),
        executable_overrides: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval 必须大于 0")
        self.allowed_executables = allowed_executables
        self.executable_overrides = MappingProxyType(dict(executable_overrides or {}))
        self.environment = MappingProxyType(dict(environment or {}))
        self.poll_interval = poll_interval

    def resolve(self, command: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if not command or command[0] not in self.allowed_executables:
            raise BrowserRuntimeError(f"浏览器 Runtime 禁止命令: {command[0] if command else ''}")
        executable = self.executable_overrides.get(command[0]) or shutil.which(command[0])
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
    ) -> ProcessExecution:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        resolved = self.resolve(command)
        started = time.monotonic()
        process = subprocess.Popen(
            resolved,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=True,
            env=self._environment(),
        )
        try:
            while True:
                if lifecycle:
                    lifecycle.checkpoint()
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    self._terminate_group(process)
                    stdout, stderr = process.communicate()
                    return ProcessExecution(
                        tuple(command),
                        124,
                        stdout,
                        stderr,
                        int((time.monotonic() - started) * 1000),
                        True,
                    )
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(self.poll_interval, remaining)
                    )
                    return ProcessExecution(
                        tuple(command),
                        process.returncode,
                        stdout,
                        stderr,
                        int((time.monotonic() - started) * 1000),
                    )
                except subprocess.TimeoutExpired:
                    continue
        except TaskCancelledError:
            self._terminate_group(process)
            process.communicate()
            raise
        except BaseException:
            self._terminate_group(process)
            process.communicate()
            raise

    def start_background(
        self,
        command: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        log_path: Path,
    ) -> "ManagedProcess":
        resolved = self.resolve(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                resolved,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                start_new_session=True,
                env=self._environment(),
            )
        except BaseException:
            stream.close()
            raise
        return ManagedProcess(process, stream, log_path, self)

    def _environment(self) -> dict[str, str]:
        result = dict(os.environ)
        result.update(self.environment)
        return result

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str], grace_seconds: float = 1.0) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        process.wait(timeout=grace_seconds)


class ManagedProcess:
    def __init__(
        self,
        process: subprocess.Popen[str],
        stream: object,
        log_path: Path,
        runner: BrowserProcessRunner,
    ) -> None:
        self.process = process
        self.stream = stream
        self.log_path = log_path
        self.runner = runner

    def stop(self) -> None:
        self.runner._terminate_group(self.process)
        close = getattr(self.stream, "close", None)
        if close:
            close()

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def log_tail(self, limit: int = 4000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""


class BrowserProjectRuntime:
    def __init__(
        self,
        project_root: Path,
        process_runner: BrowserProcessRunner,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = BrowserProjectConfig.load(self.project_root)
        self.process_runner = process_runner

    def build(
        self,
        *,
        timeout_seconds: float = 60,
        lifecycle: LifecycleController | None = None,
    ) -> ProcessExecution:
        return self.process_runner.run(
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
        managed = self.process_runner.start_background(
            self.config.dev_command,
            cwd=self.project_root,
            log_path=log_path,
        )
        try:
            self._wait_until_ready(
                managed,
                readiness_timeout_seconds,
                lifecycle,
            )
            yield self.config.page_url
        finally:
            managed.stop()

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
    ) -> None:
        self.project = BrowserProjectRuntime(project_root, process_runner)
        self.process_runner = process_runner
        self.artifacts = artifacts
        self.image_assets = image_assets
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
        spec_path = self.runtime_dir / f"{artifact_prefix}-ui-spec.json"
        screenshot_path = self.runtime_dir / f"{artifact_prefix}-actual.png"
        result_path = self.runtime_dir / f"{artifact_prefix}-result.json"
        server_log = self.runtime_dir / f"{artifact_prefix}-server.log"
        spec_path.write_text(
            json.dumps(ui_spec.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        runner_path = self.project.project_root / self.project.config.browser_runner
        with self.project.running_server(
            log_path=server_log,
            lifecycle=lifecycle,
        ) as page_url:
            execution = self.process_runner.run(
                (
                    "node", str(runner_path),
                    "--url", page_url,
                    "--spec", str(spec_path),
                    "--screenshot", str(screenshot_path),
                    "--result", str(result_path),
                ),
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
