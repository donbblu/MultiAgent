from __future__ import annotations

from abc import ABC, abstractmethod
from .models import (
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

    def __init__(
        self,
        workspace: ProjectWorkspace,
        command_policy: CommandPolicy | None = None,
        role: RoleSpec = TESTER,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.workspace = workspace
        self.command_policy = command_policy or CommandPolicy()
        self.role = role
        self.memory_manager = memory_manager or MemoryManager()

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

        results = [self.workspace.run(list(command)) for command in memory.verification_commands]
        failures = [
            result
            for result in results
            if result.exit_code != 0
            or "Ran 0 tests" in result.stdout
            or "Ran 0 tests" in result.stderr
        ]
        evidence = [
            f"{' '.join(result.command)} -> exit {result.exit_code}"
            for result in results
        ]
        if failures:
            feedback = []
            for result in failures:
                detail = (result.stderr or result.stdout).strip()
                feedback.append(
                    f"命令 {' '.join(result.command)} 失败: {detail[-1000:]}"
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
