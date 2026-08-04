from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from .models import (
    AgentResult,
    FileChange,
    ImplementationPlan,
    CriterionResult,
    ProjectFile,
    ReviewResult,
    TaskContext,
    VerificationResult,
)
from .context import ProjectContextBuilder
from .policy import CommandPolicy, CommandPolicyError
from .validation import PlanValidator, SchemaValidationError
from .workspace import ProjectWorkspace, WorkspaceError
from .roles import Capability, IMPLEMENTER, REVIEWER, RoleSpec, TESTER
from .memory import MemoryManager, RoleMemoryView


class CodingBackend(Protocol):
    """可由任意 LLM/规则引擎实现，只负责生成结构化计划。"""

    def create_plan(self, memory: RoleMemoryView) -> ImplementationPlan: ...


class CodingAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> AgentResult:
        raise NotImplementedError


class VerificationAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> VerificationResult:
        raise NotImplementedError


class ReviewBackend(Protocol):
    def review(self, memory: RoleMemoryView) -> ReviewResult: ...


class ReviewAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> ReviewResult:
        raise NotImplementedError


class WorkspaceCodingAgent(CodingAgent):
    """让后端做决策，让受限 Workspace 执行文件变更。"""

    def __init__(
        self,
        backend: CodingBackend,
        workspace: ProjectWorkspace,
        context_builder: ProjectContextBuilder | None = None,
        validator: PlanValidator | None = None,
        role: RoleSpec = IMPLEMENTER,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.backend = backend
        self.workspace = workspace
        self.context_builder = context_builder or ProjectContextBuilder(workspace)
        self.validator = validator or PlanValidator()
        self.role = role
        self.memory_manager = memory_manager or MemoryManager()

    def run(self, task: TaskContext) -> AgentResult:
        try:
            role = task.active_role or self.role
            if not role.allows(Capability.WRITE_PROJECT):
                return AgentResult(False, "角色权限不足", error=f"角色 {role.name} 无写入能力")
            context_files = self.context_builder.select(task)
            memory = self.memory_manager.build(task, role, context_files)
            plan = self.backend.create_plan(memory)
            self.validator.validate(plan, task)
            changed = self.workspace.apply_changes(plan.changes)
            return AgentResult(
                success=True,
                summary=plan.summary,
                changed_files=changed,
                evidence=[change.reason for change in plan.changes],
            )
        except (WorkspaceError, SchemaValidationError, OSError, ValueError, RuntimeError) as exc:
            return AgentResult(False, "实现失败", error=str(exc))


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


class WorkspaceReviewAgent(ReviewAgent):
    """只读审查项目；不应用文件变更，也不运行命令。"""

    def __init__(
        self,
        backend: ReviewBackend,
        workspace: ProjectWorkspace,
        context_builder: ProjectContextBuilder | None = None,
        memory_manager: MemoryManager | None = None,
        role: RoleSpec = REVIEWER,
    ) -> None:
        self.backend = backend
        self.context_builder = context_builder or ProjectContextBuilder(workspace)
        self.memory_manager = memory_manager or MemoryManager()
        self.role = role

    def run(self, task: TaskContext) -> ReviewResult:
        if not self.role.allows(Capability.REVIEW_CHANGES):
            return ReviewResult(
                False,
                "角色权限不足",
                feedback=[f"角色 {self.role.name} 无审查能力"],
            )
        try:
            files = self.context_builder.select(task)
            memory = self.memory_manager.build(task, self.role, files)
            return self.backend.review(memory)
        except (OSError, ValueError, RuntimeError) as exc:
            return ReviewResult(False, "审查失败", feedback=[str(exc)])


class DemoProjectBackend:
    """离线示例后端；生产环境替换为真实模型后端。"""

    def create_plan(self, memory: RoleMemoryView) -> ImplementationPlan:
        if memory.attempt == 1:
            app = 'def greet(name):\n    return f"Hello, {name}!"\n'
        else:
            app = (
                'def greet(name):\n'
                '    if not name:\n'
                '        return "Hello, stranger!"\n'
                '    return f"Hello, {name}!"\n'
            )
        test = (
            "import unittest\n"
            "from app import greet\n\n"
            "class GreetTests(unittest.TestCase):\n"
            "    def test_name(self):\n"
            "        self.assertEqual(greet('Codex'), 'Hello, Codex!')\n\n"
            "    def test_empty_name(self):\n"
            "        self.assertEqual(greet(''), 'Hello, stranger!')\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
        return ImplementationPlan(
            summary=f"根据需求生成问候项目（第 {memory.attempt} 次实现）",
            changes=[
                FileChange("app.py", app, "实现问候功能并处理验证反馈"),
                FileChange("test_app.py", test, "覆盖正常输入和空输入"),
                FileChange("README.md", f"# Generated Project\n\n{memory.objective}\n", "记录项目目标"),
            ],
            suggested_checks=[["python3", "-m", "unittest", "-v"]],
        )
