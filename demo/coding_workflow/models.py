from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .roles import RoleSpec


class TaskState(str, Enum):
    RECEIVED = "received"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REWORK = "rework"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidTaskTransition(ValueError):
    pass


TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.RECEIVED: frozenset({TaskState.PLANNING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.PLANNING: frozenset({TaskState.IMPLEMENTING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.IMPLEMENTING: frozenset({TaskState.VERIFYING, TaskState.REWORK, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.VERIFYING: frozenset({TaskState.COMPLETED, TaskState.REWORK, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.REWORK: frozenset({TaskState.IMPLEMENTING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class FileChange:
    """Coding Agent 请求工作区执行的一次完整文件写入。"""

    path: str
    content: str
    reason: str


@dataclass(frozen=True)
class ProjectFile:
    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class ImplementationPlan:
    summary: str
    changes: list[FileChange]
    suggested_checks: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class AgentResult:
    success: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class VerificationResult:
    passed: bool
    summary: str
    feedback: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    command_results: list[CommandResult] = field(default_factory=list)
    criteria_results: list[CriterionResult] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    message: str
    path: str = ""


@dataclass
class ReviewResult:
    passed: bool
    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CriterionResult:
    criterion: str
    passed: bool
    evidence: str


@dataclass
class TaskContext:
    task_id: str
    objective: str
    acceptance_criteria: list[str]
    verification_commands: list[list[str]] = field(default_factory=list)
    user_request: str = ""
    project_root: str = ""
    tech_stack: dict[str, str] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=lambda: ["**"])
    prohibited_actions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    state: TaskState = TaskState.RECEIVED
    attempt: int = 0
    feedback: list[str] = field(default_factory=list)
    implementation: AgentResult | None = None
    verification: VerificationResult | None = None
    review: ReviewResult | None = None
    history: list[str] = field(default_factory=list)
    active_role: RoleSpec | None = None
    role_history: list[str] = field(default_factory=list)
    version: int = 0

    def assign_role(self, role: RoleSpec) -> None:
        self.active_role = role
        self.role_history.append(role.name)

    def transition(self, state: TaskState, note: str) -> None:
        if state not in TASK_TRANSITIONS[self.state]:
            raise InvalidTaskTransition(
                f"非法任务状态迁移: {self.state.value} -> {state.value}"
            )
        self.state = state
        self.history.append(f"{state.value}: {note}")
        self.version += 1

    def model_input(self) -> dict[str, Any]:
        """只暴露模型完成任务所需的信息，不包含运行时对象。"""
        return {
            "task_id": self.task_id,
            "user_request": self.user_request or self.objective,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
            "tech_stack": self.tech_stack,
            "constraints": self.constraints,
            "allowed_paths": self.allowed_paths,
            "prohibited_actions": self.prohibited_actions,
            "assumptions": self.assumptions,
            "attempt": self.attempt,
            "feedback": self.feedback,
            "role": self.active_role.model_input() if self.active_role else None,
        }
