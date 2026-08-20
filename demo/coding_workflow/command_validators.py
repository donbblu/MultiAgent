from __future__ import annotations

import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .artifacts import ArtifactDraft
from .policy import CommandPolicy, CommandPolicyError
from .truth import VerificationOutcome
from .validator_runtime import ValidatorRunRequest, ValidatorRunResult


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*(['\"]?)[^\s,'\";]+\2"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*")


def _redact(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", value
    )


def _clip(value: str, limit: int) -> tuple[str, bool, int, str]:
    digest = sha256(value.encode("utf-8", errors="replace")).hexdigest()
    length = len(value)
    redacted = _redact(value)
    if len(redacted) <= limit:
        return redacted, False, length, digest
    head = limit // 2
    tail = limit - head
    return (
        redacted[:head]
        + f"\n... [TRUNCATED {len(redacted) - limit} CHARS] ...\n"
        + redacted[-tail:],
        True,
        length,
        digest,
    )


@dataclass(frozen=True)
class ControlledCommandResult:
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    tool_missing: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_chars: int = 0
    stderr_chars: int = 0
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    _assertion_stdout: str = ""
    _assertion_stderr: str = ""

    def evidence(self) -> Mapping[str, object]:
        return MappingProxyType({
            "command": [_redact(part) for part in self.command],
            "command_sha256": sha256(
                "\0".join(self.command).encode("utf-8", errors="replace")
            ).hexdigest(),
            "cwd": ".",
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "tool_missing": self.tool_missing,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "stdout_chars": self.stdout_chars,
            "stderr_chars": self.stderr_chars,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
        })


class ControlledCommandRunner:
    """在固定 Workspace 中以清理后的环境运行无 shell 命令。"""

    def __init__(
        self,
        workspace_root: Path,
        policy: CommandPolicy,
        *,
        max_timeout_seconds: float = 30,
        output_limit_chars: int = 8000,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        if not self.workspace_root.is_dir():
            raise ValueError("Validator Workspace 不存在")
        if max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds 必须大于 0")
        if output_limit_chars < 200:
            raise ValueError("output_limit_chars 不能小于 200")
        self.policy = policy
        self.max_timeout_seconds = float(max_timeout_seconds)
        self.output_limit_chars = output_limit_chars
        base = {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        base.update(dict(environment or {}))
        self.environment = MappingProxyType(base)

    def run(
        self, command: tuple[str, ...], *, timeout_seconds: float
    ) -> ControlledCommandResult:
        self.policy.validate(list(command))
        if timeout_seconds <= 0 or timeout_seconds > self.max_timeout_seconds:
            raise ValueError("命令 timeout 超出 Runtime 上限")
        started = time.monotonic()
        executable = shutil.which(command[0], path=self.environment["PATH"])
        if executable is None:
            return self._result(
                command,
                None,
                "",
                f"Validator executable not found: {command[0]}",
                started,
                tool_missing=True,
            )
        execution_command = (executable, *command[1:])
        try:
            process = subprocess.Popen(
                execution_command,
                cwd=self.workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                shell=False,
                env=dict(self.environment),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return self._result(
                command, None, "", str(exc), started, tool_missing=True
            )
        except OSError as exc:
            return self._result(command, None, "", str(exc), started)

        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return self._result(
                command, process.returncode, stdout, stderr, started
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate(process)
            stdout, stderr = process.communicate()
            if exc.stdout:
                stdout = self._text(exc.stdout) + stdout
            if exc.stderr:
                stderr = self._text(exc.stderr) + stderr
            return self._result(
                command, None, stdout, stderr, started, timed_out=True
            )

    @staticmethod
    def _text(value: str | bytes) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(
            value, bytes
        ) else value

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    def _result(
        self,
        command: tuple[str, ...],
        exit_code: int | None,
        stdout: str,
        stderr: str,
        started: float,
        *,
        timed_out: bool = False,
        tool_missing: bool = False,
    ) -> ControlledCommandResult:
        out, out_cut, out_chars, out_hash = _clip(
            stdout or "", self.output_limit_chars
        )
        err, err_cut, err_chars, err_hash = _clip(
            stderr or "", self.output_limit_chars
        )
        return ControlledCommandResult(
            command,
            exit_code,
            out,
            err,
            int((time.monotonic() - started) * 1000),
            timed_out,
            tool_missing,
            out_cut,
            err_cut,
            out_chars,
            err_chars,
            out_hash,
            err_hash,
            stdout or "",
            stderr or "",
        )


class CommandValidator:
    """执行冻结 ValidatorSpec 中的命令和确定性输出断言。"""

    SUPPORTED_KINDS = frozenset({"core:build", "core:test", "core:cli"})

    def __init__(self, validator_kind: str, runner: ControlledCommandRunner) -> None:
        if validator_kind not in self.SUPPORTED_KINDS:
            raise ValueError("CommandValidator kind 无效")
        self.validator_kind = validator_kind
        self.runner = runner

    def validate(self, request: ValidatorRunRequest) -> ValidatorRunResult:
        if request.spec.validator_kind != self.validator_kind:
            return ValidatorRunResult(
                VerificationOutcome.UNKNOWN,
                "Validator kind 与注册实现不匹配",
            )
        try:
            commands, timeout = self._config(request.spec.config)
        except (TypeError, ValueError, CommandPolicyError) as exc:
            return ValidatorRunResult(
                VerificationOutcome.UNKNOWN,
                f"Validator 配置未获 Runtime 接纳: {exc}",
            )

        results: list[tuple[ControlledCommandResult, Mapping[str, object]]] = []
        for item in commands:
            argv = tuple(item["argv"])
            try:
                execution = self.runner.run(argv, timeout_seconds=timeout)
            except (ValueError, CommandPolicyError) as exc:
                return ValidatorRunResult(
                    VerificationOutcome.UNKNOWN,
                    f"Validator 命令未获 Runtime 接纳: {exc}",
                )
            results.append((execution, item))

        evidence = tuple(ArtifactDraft(
            {
                "validator_kind": self.validator_kind,
                "result": dict(execution.evidence()),
                "assertions": {
                    "expected_exit_code": item["expected_exit_code"],
                    "stdout_contains": list(item["stdout_contains"]),
                    "stderr_contains": list(item["stderr_contains"]),
                    "reject_zero_tests": item["reject_zero_tests"],
                },
            },
            kind="core:command_evidence",
        ) for execution, item in results)

        definite_failures: list[str] = []
        unknowns: list[str] = []
        for index, (execution, item) in enumerate(results, start=1):
            if execution.tool_missing:
                unknowns.append(f"命令 {index} 的工具不存在")
                continue
            if execution.timed_out or execution.exit_code is None:
                unknowns.append(f"命令 {index} 超时或无法执行")
                continue
            if execution.exit_code != item["expected_exit_code"]:
                definite_failures.append(
                    f"命令 {index} 退出码 {execution.exit_code}，"
                    f"预期 {item['expected_exit_code']}"
                )
            for expected in item["stdout_contains"]:
                if expected not in execution._assertion_stdout:
                    definite_failures.append(
                        f"命令 {index} stdout 缺少预期文本"
                    )
            for expected in item["stderr_contains"]:
                if expected not in execution._assertion_stderr:
                    definite_failures.append(
                        f"命令 {index} stderr 缺少预期文本"
                    )
            if item["reject_zero_tests"] and (
                "Ran 0 tests" in execution._assertion_stdout
                or "Ran 0 tests" in execution._assertion_stderr
            ):
                definite_failures.append(f"命令 {index} 未执行任何测试")

        if definite_failures:
            return ValidatorRunResult(
                VerificationOutcome.FAILED,
                "; ".join(definite_failures),
                evidence,
            )
        if unknowns:
            return ValidatorRunResult(
                VerificationOutcome.UNKNOWN,
                "; ".join(unknowns),
                evidence,
            )
        return ValidatorRunResult(
            VerificationOutcome.PASSED,
            f"{self.validator_kind} 的 {len(results)} 个命令全部通过",
            evidence,
        )

    def _config(
        self, config: Mapping[str, object]
    ) -> tuple[tuple[Mapping[str, object], ...], float]:
        allowed = {"commands", "timeout_seconds"}
        unknown = set(config) - allowed
        if unknown:
            raise ValueError(f"未知配置字段: {sorted(unknown)}")
        timeout = config.get("timeout_seconds", self.runner.max_timeout_seconds)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ValueError("timeout_seconds 必须是数字")
        raw_commands = config.get("commands")
        if not isinstance(raw_commands, (tuple, list)) or not raw_commands:
            raise ValueError("commands 必须是非空数组")
        parsed = tuple(self._command(item) for item in raw_commands)
        for item in parsed:
            self.runner.policy.validate(list(item["argv"]))
        return parsed, float(timeout)

    @staticmethod
    def _command(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("command 必须是对象")
        allowed = {
            "argv", "expected_exit_code", "stdout_contains",
            "stderr_contains", "reject_zero_tests",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"command 包含未知字段: {sorted(unknown)}")
        argv = value.get("argv")
        if not isinstance(argv, (tuple, list)) or not argv or not all(
            isinstance(part, str) and part for part in argv
        ):
            raise ValueError("argv 必须是非空字符串数组")
        exit_code = value.get("expected_exit_code", 0)
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ValueError("expected_exit_code 必须是整数")

        def strings(name: str) -> tuple[str, ...]:
            items = value.get(name, ())
            if not isinstance(items, (tuple, list)) or not all(
                isinstance(item, str) and item for item in items
            ):
                raise ValueError(f"{name} 必须是字符串数组")
            return tuple(items)

        reject_zero = value.get("reject_zero_tests", False)
        if not isinstance(reject_zero, bool):
            raise ValueError("reject_zero_tests 必须是布尔值")
        return MappingProxyType({
            "argv": tuple(argv),
            "expected_exit_code": exit_code,
            "stdout_contains": strings("stdout_contains"),
            "stderr_contains": strings("stderr_contains"),
            "reject_zero_tests": reject_zero,
        })


def register_core_command_validators(
    registry,
    workspace_root: Path,
    commands_by_kind: Mapping[str, tuple[tuple[str, ...], ...]],
    *,
    max_timeout_seconds: float = 30,
) -> None:
    """Composition Root 显式注册；不提供开放命令执行器。"""
    from .validator_runtime import ValidatorRegistry

    if not isinstance(registry, ValidatorRegistry):
        raise TypeError("registry 必须是 ValidatorRegistry")
    for kind, commands in sorted(commands_by_kind.items()):
        if kind not in CommandValidator.SUPPORTED_KINDS:
            raise ValueError(f"不支持的 Core command validator: {kind}")
        if not commands:
            raise ValueError(f"Validator 命令白名单不能为空: {kind}")
        policy = CommandPolicy(
            allowed_executables={command[0] for command in commands},
            allowed_commands=[list(command) for command in commands],
        )
        runner = ControlledCommandRunner(
            workspace_root,
            policy,
            max_timeout_seconds=max_timeout_seconds,
        )
        registry.register(kind, CommandValidator(kind, runner))
