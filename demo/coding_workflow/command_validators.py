from __future__ import annotations

import shutil
from dataclasses import InitVar, dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from .artifacts import ArtifactDraft
from .local_execution import (
    FROZEN_PATH,
    PROFILE_CORE,
    SANDBOX_REQUIRED,
    ExecutionOutcome,
    LocalExecutionError,
    prepare_execution,
    redact_text,
    run_prepared,
    sanitize_output,
)
from .local_execution_approval import LocalExecutionApprover
from .policy import CommandPolicy, CommandPolicyError
from .truth import VerificationOutcome
from .validator_runtime import ValidatorRunRequest, ValidatorRunResult


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
    # Retain the historical positional/keyword constructor slots without
    # retaining a second, potentially unredacted copy of process output.
    _assertion_stdout: InitVar[str] = ""
    _assertion_stderr: InitVar[str] = ""
    profile_manifest: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    cleanup_evidence: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    cleanup_evidence_digest: str = ""
    assertion_results: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def __post_init__(
        self,
        _assertion_stdout: str,
        _assertion_stderr: str,
    ) -> None:
        del _assertion_stdout, _assertion_stderr
        assertions = self.assertion_results
        if not isinstance(assertions, Mapping):
            raise TypeError("assertion_results 必须是映射")
        if not assertions:
            normalized: Mapping[str, object] = MappingProxyType({})
        else:
            if set(assertions) != {
                "stdout_contains", "stderr_contains", "zero_tests_absent",
            }:
                raise ValueError("assertion_results 字段无效")
            stdout_contains = assertions["stdout_contains"]
            stderr_contains = assertions["stderr_contains"]
            zero_tests_absent = assertions["zero_tests_absent"]
            if (
                not isinstance(stdout_contains, (tuple, list))
                or not all(isinstance(item, bool) for item in stdout_contains)
                or not isinstance(stderr_contains, (tuple, list))
                or not all(isinstance(item, bool) for item in stderr_contains)
                or not isinstance(zero_tests_absent, bool)
            ):
                raise ValueError("assertion_results 必须只包含布尔证据")
            normalized = MappingProxyType({
                "stdout_contains": tuple(stdout_contains),
                "stderr_contains": tuple(stderr_contains),
                "zero_tests_absent": zero_tests_absent,
            })
        object.__setattr__(self, "assertion_results", normalized)

    def evidence(self) -> Mapping[str, object]:
        return MappingProxyType({
            "command": [redact_text(part) for part in self.command],
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
            "profile_manifest": self.profile_manifest,
            "cleanup_evidence": self.cleanup_evidence,
            "cleanup_evidence_digest": self.cleanup_evidence_digest,
            "assertion_results": self.assertion_results,
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
        if output_limit_chars > 10_000:
            raise ValueError("output_limit_chars 不能超过 Profile 上限 10000")
        if max_timeout_seconds > 30:
            raise ValueError("max_timeout_seconds 不能超过 Profile 上限 30")
        if environment:
            raise ValueError("Core Validator 不接受调用方环境扩展")
        self.policy = policy
        self.max_timeout_seconds = float(max_timeout_seconds)
        self.output_limit_chars = output_limit_chars
        self.environment = MappingProxyType({
            "PATH": FROZEN_PATH,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        })

    def run(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        trusted_local: object = None,
        stdout_contains: tuple[str, ...] = (),
        stderr_contains: tuple[str, ...] = (),
        reject_zero_tests: bool = False,
    ) -> ControlledCommandResult:
        try:
            self.policy.validate(list(command))
        except (TypeError, ValueError, CommandPolicyError) as exc:
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                f"Core Validator command rejected: {exc}",
            ) from None
        if timeout_seconds <= 0 or timeout_seconds > self.max_timeout_seconds:
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Core Validator timeout 超出 Runtime Profile 上限",
            )
        executable = shutil.which(command[0], path=FROZEN_PATH)
        if executable is None:
            return self._local_failure_result(
                command=command,
                stderr=f"Validator executable not found: {command[0]}",
                tool_missing=True,
            )
        executable_path = Path(executable)
        if not executable_path.is_absolute() or executable_path.resolve(
        ).is_relative_to(self.workspace_root):
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Core Validator executable 未由冻结 Runtime PATH 解析",
            )
        prepared = prepare_execution(
            profile_id=PROFILE_CORE,
            workspace_root=self.workspace_root,
            executable=str(executable_path),
            command=command,
            wall_deadline_seconds=timeout_seconds,
            output_limit_chars=self.output_limit_chars,
            python_profile=True,
        )
        try:
            outcome = run_prepared(
                prepared,
                trusted_local=trusted_local,
                stdout_contains=stdout_contains,
                stderr_contains=stderr_contains,
                reject_zero_tests=reject_zero_tests,
            )
        except LocalExecutionError as exc:
            if (
                exc.code == SANDBOX_REQUIRED
                and exc.reason.startswith("local execution spawn failed:")
            ):
                return self._local_failure_result(
                    command=command,
                    stderr=exc.reason,
                    tool_missing="No such file" in exc.reason,
                )
            raise
        return self._result_from_outcome(command, outcome)

    def _local_failure_result(
        self,
        *,
        command: tuple[str, ...],
        stderr: str,
        tool_missing: bool = False,
    ) -> ControlledCommandResult:
        stdout = sanitize_output(
            "",
            limit_chars=self.output_limit_chars,
        )
        bounded_error = sanitize_output(
            stderr,
            limit_chars=self.output_limit_chars,
        )
        return ControlledCommandResult(
            command=command,
            exit_code=None,
            stdout=stdout.text,
            stderr=bounded_error.text,
            duration_ms=0,
            tool_missing=tool_missing,
            stdout_truncated=stdout.truncated,
            stderr_truncated=bounded_error.truncated,
            stdout_chars=stdout.raw_chars,
            stderr_chars=bounded_error.raw_chars,
            stdout_sha256=stdout.raw_sha256,
            stderr_sha256=bounded_error.raw_sha256,
        )

    @staticmethod
    def _result_from_outcome(
        command: tuple[str, ...],
        outcome: ExecutionOutcome,
    ) -> ControlledCommandResult:
        return ControlledCommandResult(
            command=command,
            exit_code=None if outcome.timed_out else outcome.exit_code,
            stdout=outcome.stdout.text,
            stderr=outcome.stderr.text,
            duration_ms=outcome.duration_ms,
            timed_out=outcome.timed_out,
            stdout_truncated=outcome.stdout.truncated,
            stderr_truncated=outcome.stderr.truncated,
            stdout_chars=outcome.stdout.raw_chars,
            stderr_chars=outcome.stderr.raw_chars,
            stdout_sha256=outcome.stdout.raw_sha256,
            stderr_sha256=outcome.stderr.raw_sha256,
            profile_manifest=outcome.profile_manifest,
            cleanup_evidence=outcome.cleanup_evidence,
            cleanup_evidence_digest=outcome.cleanup_evidence_digest,
            assertion_results=outcome.assertion_results,
        )


class CommandValidator:
    """执行冻结 ValidatorSpec 中的命令和确定性输出断言。"""

    SUPPORTED_KINDS = frozenset({"core:build", "core:test", "core:cli"})

    def __init__(
        self,
        validator_kind: str,
        runner: ControlledCommandRunner,
        *,
        approver_factory: Callable[[], LocalExecutionApprover] | None = None,
    ) -> None:
        if validator_kind not in self.SUPPORTED_KINDS:
            raise ValueError("CommandValidator kind 无效")
        if approver_factory is not None and not callable(approver_factory):
            raise TypeError("approver_factory 必须可调用")
        self.validator_kind = validator_kind
        self.runner = runner
        self.approver_factory = approver_factory

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
                if self.approver_factory is None:
                    execution = self.runner.run(
                        argv,
                        timeout_seconds=timeout,
                        stdout_contains=item["stdout_contains"],
                        stderr_contains=item["stderr_contains"],
                        reject_zero_tests=item["reject_zero_tests"],
                    )
                else:
                    approver = self.approver_factory()
                    if type(approver) is not LocalExecutionApprover:
                        raise TypeError(
                            "approver_factory 必须返回 LocalExecutionApprover"
                        )
                    execution = approver.run_controlled(
                        self.runner,
                        argv,
                        timeout_seconds=timeout,
                        stdout_contains=item["stdout_contains"],
                        stderr_contains=item["stderr_contains"],
                        reject_zero_tests=item["reject_zero_tests"],
                    )
            except (
                ValueError,
                CommandPolicyError,
                LocalExecutionError,
            ) as exc:
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
            assertion_results = execution.assertion_results
            stdout_matches = assertion_results.get("stdout_contains")
            stderr_matches = assertion_results.get("stderr_contains")
            zero_tests_absent = assertion_results.get("zero_tests_absent")
            if (
                not isinstance(stdout_matches, tuple)
                or len(stdout_matches) != len(item["stdout_contains"])
                or not isinstance(stderr_matches, tuple)
                or len(stderr_matches) != len(item["stderr_contains"])
                or not isinstance(zero_tests_absent, bool)
            ):
                unknowns.append(f"命令 {index} 缺少 Runtime 输出断言证据")
                continue
            for matched in stdout_matches:
                if not matched:
                    definite_failures.append(
                        f"命令 {index} stdout 缺少预期文本"
                    )
            for matched in stderr_matches:
                if not matched:
                    definite_failures.append(
                        f"命令 {index} stderr 缺少预期文本"
                    )
            if item["reject_zero_tests"] and not zero_tests_absent:
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
    approver_factory: Callable[[], LocalExecutionApprover] | None = None,
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
        registry.register(
            kind,
            CommandValidator(
                kind,
                runner,
                approver_factory=approver_factory,
            ),
        )
