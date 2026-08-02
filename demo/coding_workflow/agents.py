from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from .models import (
    AgentResult,
    FileChange,
    ImplementationPlan,
    CriterionResult,
    ProjectFile,
    TaskContext,
    VerificationResult,
)
from .context import ProjectContextBuilder
from .policy import CommandPolicy, CommandPolicyError
from .validation import PlanValidator, SchemaValidationError
from .workspace import ProjectWorkspace, WorkspaceError


class CodingBackend(Protocol):
    """可由任意 LLM/规则引擎实现，只负责生成结构化计划。"""

    def create_plan(
        self, task: TaskContext, context_files: list[ProjectFile]
    ) -> ImplementationPlan: ...


class CodingAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> AgentResult:
        raise NotImplementedError


class VerificationAgent(ABC):
    @abstractmethod
    def run(self, task: TaskContext) -> VerificationResult:
        raise NotImplementedError


class WorkspaceCodingAgent(CodingAgent):
    """让后端做决策，让受限 Workspace 执行文件变更。"""

    def __init__(
        self,
        backend: CodingBackend,
        workspace: ProjectWorkspace,
        context_builder: ProjectContextBuilder | None = None,
        validator: PlanValidator | None = None,
    ) -> None:
        self.backend = backend
        self.workspace = workspace
        self.context_builder = context_builder or ProjectContextBuilder(workspace)
        self.validator = validator or PlanValidator()

    def run(self, task: TaskContext) -> AgentResult:
        try:
            context_files = self.context_builder.select(task)
            plan = self.backend.create_plan(task, context_files)
            self.validator.validate(plan, task)
            changed = self.workspace.apply_changes(plan.changes)
            return AgentResult(
                success=True,
                summary=plan.summary,
                changed_files=changed,
                evidence=[change.reason for change in plan.changes],
            )
        except (WorkspaceError, SchemaValidationError, OSError, ValueError) as exc:
            return AgentResult(False, "实现失败", error=str(exc))


class CommandVerificationAgent(VerificationAgent):
    """真实执行验收命令；只读项目结果，不修改文件。"""

    def __init__(
        self, workspace: ProjectWorkspace, command_policy: CommandPolicy | None = None
    ) -> None:
        self.workspace = workspace
        self.command_policy = command_policy or CommandPolicy()

    def run(self, task: TaskContext) -> VerificationResult:
        if not task.verification_commands:
            return VerificationResult(
                passed=False,
                summary="没有配置可执行的验证命令",
                feedback=["为任务配置 verification_commands"],
            )

        try:
            for command in task.verification_commands:
                self.command_policy.validate(command)
        except CommandPolicyError as exc:
            return VerificationResult(
                passed=False,
                summary="验证命令被安全策略拒绝",
                feedback=[str(exc)],
                criteria_results=[
                    CriterionResult(item, False, "验证命令未获准执行")
                    for item in task.acceptance_criteria
                ],
            )

        results = [self.workspace.run(command) for command in task.verification_commands]
        failures = [result for result in results if result.exit_code != 0]
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
                for item in task.acceptance_criteria
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
            for item in task.acceptance_criteria
        ]
        return VerificationResult(
            passed=True,
            summary="全部验证命令通过",
            evidence=evidence,
            command_results=results,
            criteria_results=criteria,
        )


class DemoProjectBackend:
    """离线示例后端；生产环境替换为真实模型后端。"""

    def create_plan(
        self, task: TaskContext, context_files: list[ProjectFile]
    ) -> ImplementationPlan:
        del context_files
        if task.attempt == 1:
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
            summary=f"根据需求生成问候项目（第 {task.attempt} 次实现）",
            changes=[
                FileChange("app.py", app, "实现问候功能并处理验证反馈"),
                FileChange("test_app.py", test, "覆盖正常输入和空输入"),
                FileChange("README.md", f"# Generated Project\n\n{task.objective}\n", "记录项目目标"),
            ],
            suggested_checks=[["python3", "-m", "unittest", "-v"]],
        )
