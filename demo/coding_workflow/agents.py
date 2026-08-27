from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .command_validators import (
    ControlledCommandResult,
    ControlledCommandRunner,
)
from .local_execution import SANDBOX_REQUIRED, LocalExecutionError
from .local_execution_approval import LocalExecutionApprover
from .models import (
    CommandResult,
    CriterionResult,
    TaskContext,
    VerificationResult,
)
from .policy import CommandPolicy, CommandPolicyError
from .workspace import ProjectWorkspace
from .roles import Capability, RoleSpec, TESTER
from .memory import MemoryManager


class VerificationAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> VerificationResult:
        raise NotImplementedError


class CommandVerificationAgent(VerificationAgent):
    """真实执行验收命令；只读项目结果，不修改文件。"""

    LOCAL_EXECUTION_REJECTED = "验证命令未获本地执行准入"
    LOCAL_EXECUTION_FAILED = "本地执行未能安全完成"

    @classmethod
    def is_local_execution_failure(cls, result: VerificationResult) -> bool:
        return result.summary in {
            cls.LOCAL_EXECUTION_REJECTED,
            cls.LOCAL_EXECUTION_FAILED,
        }

    def __init__(
        self,
        workspace: ProjectWorkspace,
        command_policy: CommandPolicy | None = None,
        role: RoleSpec = TESTER,
        memory_manager: MemoryManager | None = None,
        *,
        approver_factory: Callable[[], LocalExecutionApprover] | None = None,
    ) -> None:
        self.workspace = workspace
        self.command_policy = command_policy or CommandPolicy(
            allowed_executables=set(),
            allowed_commands=[],
        )
        self.role = role
        self.memory_manager = memory_manager or MemoryManager()
        if approver_factory is not None and not callable(approver_factory):
            raise TypeError("approver_factory 必须可调用")
        self.approver_factory = approver_factory
        self.command_runner = ControlledCommandRunner(
            self.workspace.root,
            self.command_policy,
            max_timeout_seconds=min(self.workspace.command_timeout, 30),
            output_limit_chars=10_000,
        )

    def run(self, task: TaskContext) -> VerificationResult:
        role = task.active_role or self.role
        if not role.allows(Capability.RUN_VERIFICATION):
            return VerificationResult(
                passed=False,
                summary="角色权限不足",
                feedback=[f"角色 {role.name} 无验证执行能力"],
            )
        memory = self.memory_manager.build(task, role)
        if not memory.verification_commands:
            return VerificationResult(
                passed=False,
                summary="没有配置可执行的验证命令",
                feedback=["为任务配置 verification_commands"],
            )

        try:
            for command in memory.verification_commands:
                self.command_policy.validate(list(command))
        except CommandPolicyError as exc:
            return VerificationResult(
                passed=False,
                summary="验证命令被安全策略拒绝",
                feedback=[str(exc)],
                criteria_results=[
                    CriterionResult(item, False, "验证命令未获准执行")
                    for item in memory.acceptance_criteria
                ],
            )

        if self.command_policy.allowed_commands is None:
            return VerificationResult(
                passed=False,
                summary="验证命令缺少精确登记策略",
                feedback=["CommandPolicy 必须显式登记完整 argv"],
                criteria_results=[
                    CriterionResult(item, False, "验证命令未精确登记")
                    for item in memory.acceptance_criteria
                ],
            )

        controlled_results: list[ControlledCommandResult] = []
        for command in memory.verification_commands:
            argv = tuple(command)
            try:
                if self.approver_factory is None:
                    execution = self.command_runner.run(
                        argv,
                        timeout_seconds=self.command_runner.max_timeout_seconds,
                        reject_zero_tests=True,
                    )
                else:
                    approver = self.approver_factory()
                    if type(approver) is not LocalExecutionApprover:
                        raise TypeError(
                            "approver_factory 必须返回 LocalExecutionApprover"
                        )
                    execution = approver.run_controlled(
                        self.command_runner,
                        argv,
                        timeout_seconds=self.command_runner.max_timeout_seconds,
                        reject_zero_tests=True,
                    )
            except LocalExecutionError as exc:
                summary = (
                    self.LOCAL_EXECUTION_REJECTED
                    if exc.code == SANDBOX_REQUIRED
                    else self.LOCAL_EXECUTION_FAILED
                )
                return VerificationResult(
                    passed=False,
                    summary=summary,
                    feedback=[f"{exc.code}: {exc.reason}"],
                    evidence=[
                        f"{' '.join(argv)} -> local_execution {exc.code}"
                    ],
                    criteria_results=[
                        CriterionResult(item, False, summary)
                        for item in memory.acceptance_criteria
                    ],
                )
            if not isinstance(execution, ControlledCommandResult):
                raise TypeError("Core Validator 必须返回 ControlledCommandResult")
            controlled_results.append(execution)

        failures: list[tuple[ControlledCommandResult, str]] = []
        for result in controlled_results:
            if result.exit_code != 0:
                failures.append((result, "命令退出码非零或无法执行"))
                continue
            zero_tests_absent = result.assertion_results.get(
                "zero_tests_absent"
            )
            if zero_tests_absent is not True:
                failures.append((result, "命令未执行测试或缺少断言证据"))

        results = [self._command_result(item) for item in controlled_results]
        evidence = [
            f"{' '.join(result.command)} -> exit {result.exit_code}"
            for result in results
        ]
        if failures:
            feedback = []
            for result, reason in failures:
                detail = (result.stderr or result.stdout).strip()
                feedback.append(
                    f"命令 {' '.join(result.command)} 失败: "
                    f"{reason}; {detail[-1000:]}"
                )
            criteria = [
                CriterionResult(item, False, f"验证失败：{evidence[0]}")
                for item in memory.acceptance_criteria
            ]
            return VerificationResult(
                passed=False,
                summary=f"{len(failures)} 个验证命令失败",
                feedback=feedback,
                evidence=evidence,
                command_results=results,
                criteria_results=criteria,
            )
        criteria = [
            CriterionResult(item, True, "; ".join(evidence))
            for item in memory.acceptance_criteria
        ]
        return VerificationResult(
            passed=True,
            summary="全部验证命令通过",
            evidence=evidence,
            command_results=results,
            criteria_results=criteria,
        )

    @staticmethod
    def _command_result(result: ControlledCommandResult) -> CommandResult:
        exit_code = result.exit_code
        if exit_code is None:
            exit_code = 124 if result.timed_out else 127
        return CommandResult(
            command=list(result.command),
            exit_code=exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            stdout_chars=result.stdout_chars,
            stderr_chars=result.stderr_chars,
            stdout_sha256=result.stdout_sha256,
            stderr_sha256=result.stderr_sha256,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            profile_manifest=result.profile_manifest,
            cleanup_evidence=result.cleanup_evidence,
            cleanup_evidence_digest=result.cleanup_evidence_digest,
        )
