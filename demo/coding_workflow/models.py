from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from .requirements import CodingRequirement
from .roles import RoleSpec


_COMMAND_OUTPUT_LIMIT_CHARS = 10_000
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*(['\"]?)[^\s,'\";]+\2"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def _fallback_sanitize_output(
    value: str | bytes | None,
    limit_chars: int,
) -> tuple[str, bool, int, str]:
    """Fail-safe output sanitizer used until the execution boundary is loaded."""
    if isinstance(value, bytes):
        raw = value.decode("utf-8", errors="replace")
    elif value is None:
        raw = ""
    else:
        raw = str(value)
    digest = sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", raw)
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        redacted,
    )
    if len(redacted) <= limit_chars:
        return redacted, False, len(raw), digest
    head = limit_chars // 2
    tail = limit_chars - head
    return (
        redacted[:head]
        + f"\n... [TRUNCATED {len(redacted) - limit_chars} CHARS] ...\n"
        + redacted[-tail:],
        True,
        len(raw),
        digest,
    )


def _sanitize_command_output(
    value: str | bytes | None,
    limit_chars: int = _COMMAND_OUTPUT_LIMIT_CHARS,
) -> tuple[str, bool, int, str]:
    """Use the unified boundary when present without making models own it."""
    try:
        from .local_execution import sanitize_output
    except (ImportError, AttributeError):
        return _fallback_sanitize_output(value, limit_chars)
    bounded = sanitize_output(value, limit_chars=limit_chars)
    return (
        bounded.text,
        bounded.truncated,
        bounded.raw_chars,
        bounded.raw_sha256,
    )


def _redact_command_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        raw = value.decode("utf-8", errors="replace")
    elif value is None:
        raw = ""
    else:
        raw = str(value)
    try:
        from .local_execution import redact_text
    except (ImportError, AttributeError):
        redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", raw)
        redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
        return _SECRET_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}=[REDACTED]",
            redacted,
        )
    return redact_text(raw)


def _sanitize_result_value(value: object) -> object:
    """Copy nested public evidence while removing directly retained secrets."""
    if isinstance(value, (str, bytes)):
        return _sanitize_command_output(value)[0]
    if isinstance(value, Mapping):
        return MappingProxyType({
            _sanitize_result_value(key): _sanitize_result_value(item)
            for key, item in value.items()
        })
    if isinstance(value, tuple):
        return tuple(_sanitize_result_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_result_value(item) for item in value]
    if isinstance(value, set):
        return {_sanitize_result_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_sanitize_result_value(item) for item in value)
    return value


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
    stdout_chars: int = 0
    stderr_chars: int = 0
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    profile_manifest: Mapping[str, object] | None = None
    cleanup_evidence: Mapping[str, object] | None = None
    cleanup_evidence_digest: str = ""

    def __post_init__(self) -> None:
        raw_stdout = self.stdout
        raw_stderr = self.stderr
        supplied_stdout = (
            self.stdout_chars,
            self.stdout_sha256,
            self.stdout_truncated,
        )
        supplied_stderr = (
            self.stderr_chars,
            self.stderr_sha256,
            self.stderr_truncated,
        )
        stdout, stdout_truncated, stdout_chars, stdout_digest = (
            _sanitize_command_output(self.stdout)
        )
        stderr, stderr_truncated, stderr_chars, stderr_digest = (
            _sanitize_command_output(self.stderr)
        )
        object.__setattr__(
            self,
            "command",
            [_sanitize_command_output(part)[0] for part in self.command],
        )
        for stream, raw_value, supplied, computed in (
            (
                "stdout",
                raw_stdout,
                supplied_stdout,
                (stdout, stdout_chars, stdout_digest, stdout_truncated),
            ),
            (
                "stderr",
                raw_stderr,
                supplied_stderr,
                (stderr, stderr_chars, stderr_digest, stderr_truncated),
            ),
        ):
            supplied_chars, supplied_digest, supplied_truncated = supplied
            (
                computed_text,
                computed_chars,
                computed_digest,
                computed_truncated,
            ) = computed
            metadata_supplied = (
                _valid_sha256(supplied_digest)
                and isinstance(supplied_chars, int)
                and not isinstance(supplied_chars, bool)
                and supplied_chars >= 0
                and isinstance(supplied_truncated, bool)
            )
            object.__setattr__(
                self,
                stream,
                _redact_command_output(raw_value)
                if metadata_supplied
                else computed_text,
            )
            object.__setattr__(
                self,
                f"{stream}_chars",
                supplied_chars if metadata_supplied else computed_chars,
            )
            object.__setattr__(
                self,
                f"{stream}_sha256",
                supplied_digest if metadata_supplied else computed_digest,
            )
            object.__setattr__(
                self,
                f"{stream}_truncated",
                supplied_truncated if metadata_supplied else computed_truncated,
            )
        object.__setattr__(
            self,
            "cleanup_evidence_digest",
            _sanitize_command_output(self.cleanup_evidence_digest)[0],
        )
        if self.profile_manifest is not None:
            object.__setattr__(
                self,
                "profile_manifest",
                _sanitize_result_value(self.profile_manifest),
            )
        if self.cleanup_evidence is not None:
            object.__setattr__(
                self,
                "cleanup_evidence",
                _sanitize_result_value(self.cleanup_evidence),
            )


@dataclass
class VerificationResult:
    passed: bool
    summary: str
    feedback: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    command_results: list[CommandResult] = field(default_factory=list)
    criteria_results: list[CriterionResult] = field(default_factory=list)


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
    project_id: str = ""
    tech_stack: dict[str, str] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=lambda: ["**"])
    prohibited_actions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    state: TaskState = TaskState.RECEIVED
    attempt: int = 0
    feedback: list[str] = field(default_factory=list)
    verification: VerificationResult | None = None
    history: list[str] = field(default_factory=list)
    active_role: RoleSpec | None = None
    role_history: list[str] = field(default_factory=list)
    version: int = 0
    coding_requirement: CodingRequirement | None = None

    def __post_init__(self) -> None:
        if self.coding_requirement is not None and not isinstance(
            self.coding_requirement, CodingRequirement
        ):
            raise ValueError("coding_requirement 必须是 CodingRequirement")
        if not self.project_id and self.project_root:
            canonical = str(Path(self.project_root).expanduser().resolve())
            self.project_id = sha256(canonical.encode("utf-8")).hexdigest()

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
            "coding_requirement": (
                dict(self.coding_requirement.to_dict())
                if self.coding_requirement else None
            ),
        }
