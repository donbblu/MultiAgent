from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .models import ProjectFile, TaskContext
from .roles import FIXER, IMPLEMENTER, PLANNER, REVIEWER, TESTER, RoleSpec


@dataclass(frozen=True)
class MemoryPolicy:
    """控制某个角色可以读取的作用域和单次上下文预算。"""

    readable_scopes: frozenset[str]
    writable_scopes: frozenset[str]
    max_context_chars: int
    include_project_files: bool = False
    include_feedback: bool = False
    include_verification_commands: bool = False
    secret_access: bool = False

    def __post_init__(self) -> None:
        if self.max_context_chars < 0:
            raise ValueError("max_context_chars 不能小于 0")
        if self.secret_access:
            raise ValueError("当前框架不允许任何角色访问密钥")


@dataclass(frozen=True)
class RoleMemoryView:
    """为单次角色执行生成的不可变、最小化 Memory View。"""

    task_id: str
    role: RoleSpec
    objective: str
    user_request: str
    acceptance_criteria: tuple[str, ...]
    tech_stack: Mapping[str, str]
    constraints: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    assumptions: tuple[str, ...]
    attempt: int
    feedback: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    project_files: tuple[ProjectFile, ...]
    policy: MemoryPolicy

    def model_input(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "user_request": self.user_request or self.objective,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "tech_stack": dict(self.tech_stack),
            "constraints": list(self.constraints),
            "allowed_paths": list(self.allowed_paths),
            "prohibited_actions": list(self.prohibited_actions),
            "assumptions": list(self.assumptions),
            "attempt": self.attempt,
            "feedback": list(self.feedback),
            "role": self.role.model_input(),
            "memory_policy": {
                "readable_scopes": sorted(self.policy.readable_scopes),
                "writable_scopes": sorted(self.policy.writable_scopes),
                "max_context_chars": self.policy.max_context_chars,
                "secret_access": False,
            },
        }


DEFAULT_MEMORY_POLICIES: Mapping[str, MemoryPolicy] = MappingProxyType(
    {
        PLANNER.name: MemoryPolicy(
            frozenset({"task", "project_summary"}),
            frozenset({"planning_result"}),
            15_000,
        ),
        IMPLEMENTER.name: MemoryPolicy(
            frozenset({"task", "project_files", "planning_result"}),
            frozenset({"implementation_result"}),
            40_000,
            include_project_files=True,
        ),
        TESTER.name: MemoryPolicy(
            frozenset({"task", "acceptance_criteria", "verification_commands"}),
            frozenset({"verification_result"}),
            0,
            include_verification_commands=True,
        ),
        FIXER.name: MemoryPolicy(
            frozenset({"task", "project_files", "verification_feedback"}),
            frozenset({"implementation_result"}),
            30_000,
            include_project_files=True,
            include_feedback=True,
        ),
        REVIEWER.name: MemoryPolicy(
            frozenset({"task", "project_files", "implementation_result"}),
            frozenset({"review_result"}),
            25_000,
            include_project_files=True,
        ),
    }
)


class MemoryManager:
    def __init__(
        self, policies: Mapping[str, MemoryPolicy] = DEFAULT_MEMORY_POLICIES
    ) -> None:
        self.policies = MappingProxyType(dict(policies))

    def policy_for(self, role: RoleSpec) -> MemoryPolicy:
        try:
            return self.policies[role.name]
        except KeyError as exc:
            raise KeyError(f"角色没有 MemoryPolicy: {role.name}") from exc

    def build(
        self,
        task: TaskContext,
        role: RoleSpec,
        project_files: list[ProjectFile] | tuple[ProjectFile, ...] = (),
    ) -> RoleMemoryView:
        policy = self.policy_for(role)
        selected = self._limit_files(project_files, policy)
        return RoleMemoryView(
            task_id=task.task_id,
            role=role,
            objective=task.objective,
            user_request=task.user_request,
            acceptance_criteria=tuple(task.acceptance_criteria),
            tech_stack=MappingProxyType(dict(task.tech_stack)),
            constraints=tuple(task.constraints),
            allowed_paths=tuple(task.allowed_paths),
            prohibited_actions=tuple(task.prohibited_actions),
            assumptions=tuple(task.assumptions),
            attempt=task.attempt,
            feedback=tuple(task.feedback) if policy.include_feedback else (),
            verification_commands=(
                tuple(tuple(command) for command in task.verification_commands)
                if policy.include_verification_commands
                else ()
            ),
            project_files=selected,
            policy=policy,
        )

    @staticmethod
    def _limit_files(
        project_files: list[ProjectFile] | tuple[ProjectFile, ...],
        policy: MemoryPolicy,
    ) -> tuple[ProjectFile, ...]:
        if not policy.include_project_files or policy.max_context_chars == 0:
            return ()
        remaining = policy.max_context_chars
        selected: list[ProjectFile] = []
        for item in project_files:
            if remaining <= 0:
                break
            content = item.content[:remaining]
            selected.append(
                ProjectFile(
                    item.path,
                    content,
                    item.truncated or len(content) < len(item.content),
                )
            )
            remaining -= len(content)
        return tuple(selected)
