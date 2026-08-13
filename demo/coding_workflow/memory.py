from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Iterable, Mapping
from uuid import uuid4

from .models import ProjectFile, TaskContext
from .roles import FIXER, IMPLEMENTER, PLANNER, REVIEWER, TESTER, RoleSpec


class MemoryKind(str, Enum):
    PERCEPTION = "perception"
    WORKING = "working"
    LONG_TERM = "long_term"
    ENTITY = "entity"


@dataclass(frozen=True)
class MemoryRecord:
    """带来源、证据、权限和版本的统一记忆记录。"""

    memory_id: str
    kind: MemoryKind
    subtype: str
    summary: str
    content: Mapping[str, object]
    source: str
    scope: str
    visibility: frozenset[str]
    task_id: str | None = None
    source_ref: str = ""
    evidence_refs: tuple[str, ...] = ()
    sensitivity: str = "internal"
    confidence: float = 1.0
    created_at: str = ""
    expires_at: str | None = None
    version: int = 1
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.memory_id or not self.subtype or not self.summary:
            raise ValueError("记忆 ID、类型和摘要不能为空")
        if not 0 <= self.confidence <= 1:
            raise ValueError("记忆可信度必须在 0 到 1 之间")
        if self.sensitivity not in {"public", "internal", "restricted"}:
            raise ValueError("记忆敏感级别无效")
        if self.sensitivity == "restricted":
            raise ValueError("受限内容不得进入 Agent 记忆")

    @classmethod
    def create(
        cls,
        kind: MemoryKind,
        subtype: str,
        summary: str,
        *,
        content: Mapping[str, object] | None = None,
        source: str = "harness",
        scope: str = "task",
        visibility: Iterable[str] = (),
        task_id: str | None = None,
        source_ref: str = "",
        evidence_refs: Iterable[str] = (),
        confidence: float = 1.0,
    ) -> "MemoryRecord":
        return cls(
            str(uuid4()), kind, subtype, summary,
            MappingProxyType(dict(content or {})), source, scope,
            frozenset(visibility), task_id, source_ref, tuple(evidence_refs),
            confidence=confidence,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


class MemoryStore:
    """第一阶段内存存储；接口可由 SQLite 实现替换。"""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = RLock()

    def append(self, record: MemoryRecord) -> None:
        with self._lock:
            if record.memory_id in self._records:
                raise ValueError(f"记忆 ID 已存在: {record.memory_id}")
            self._records[record.memory_id] = record

    def query(
        self,
        *,
        task_id: str | None = None,
        kinds: Iterable[MemoryKind] = (),
        role: str = "",
        text: str = "",
    ) -> tuple[MemoryRecord, ...]:
        allowed_kinds = set(kinds)
        words = {word.lower() for word in text.split() if word}
        with self._lock:
            records = tuple(self._records.values())
        result = []
        for record in records:
            if task_id is not None and record.task_id not in {None, task_id}:
                continue
            if allowed_kinds and record.kind not in allowed_kinds:
                continue
            if record.visibility and role not in record.visibility:
                continue
            haystack = record.summary.lower()
            if words and not any(word in haystack for word in words):
                continue
            result.append(record)
        return tuple(sorted(result, key=lambda item: (item.confidence, item.created_at), reverse=True))


@dataclass
class TaskWorkingMemory:
    """当前 Task 的可检查、可做 checkpoint 的工作集。"""

    task_id: str
    plan_summary: str = ""
    active_artifacts: dict[str, str] = field(default_factory=dict)
    node_summaries: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    version: int = 0

    def remember(self, record: MemoryRecord) -> None:
        if record.task_id not in {None, self.task_id}:
            raise ValueError("不能把其他任务的记忆写入当前工作集")
        if record.memory_id not in self.memory_refs:
            self.memory_refs.append(record.memory_id)
            self.version += 1

    def checkpoint(self) -> Mapping[str, object]:
        return MappingProxyType({
            "task_id": self.task_id,
            "plan_summary": self.plan_summary,
            "active_artifacts": dict(self.active_artifacts),
            "node_summaries": dict(self.node_summaries),
            "assumptions": tuple(self.assumptions),
            "feedback": tuple(self.feedback),
            "memory_refs": tuple(self.memory_refs),
            "version": self.version,
        })


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
    memories: tuple[MemoryRecord, ...] = ()

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
            "memory_summaries": [
                {
                    "kind": item.kind.value,
                    "subtype": item.subtype,
                    "summary": item.summary,
                    "source_ref": item.source_ref,
                    "confidence": item.confidence,
                }
                for item in self.memories
            ],
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
        self,
        policies: Mapping[str, MemoryPolicy] = DEFAULT_MEMORY_POLICIES,
        store: MemoryStore | None = None,
    ) -> None:
        self.policies = MappingProxyType(dict(policies))
        self.store = store or MemoryStore()
        self._working: dict[str, TaskWorkingMemory] = {}
        self._lock = RLock()

    def working_memory(self, task_id: str) -> TaskWorkingMemory:
        with self._lock:
            if task_id not in self._working:
                loader = getattr(self.store, "load_checkpoint", None)
                restored = loader(task_id) if loader else None
                self._working[task_id] = restored or TaskWorkingMemory(task_id)
            return self._working[task_id]

    def save_checkpoint(self, task_id: str) -> Mapping[str, object]:
        working = self.working_memory(task_id)
        saver = getattr(self.store, "save_checkpoint", None)
        if saver:
            saver(working)
        return working.checkpoint()

    def consolidate(self, task_id: str, *, verified: bool) -> tuple[MemoryRecord, ...]:
        """只将通过验证、具有节点证据的结果晋升为项目长期记忆。"""
        if not verified:
            return ()
        working = self.working_memory(task_id)
        promoted: list[MemoryRecord] = []
        for node_id, summary in working.node_summaries.items():
            evidence = tuple(working.active_artifacts.values())
            record = MemoryRecord.create(
                MemoryKind.LONG_TERM, "verified_node_result", summary,
                task_id=None, source="harness", scope="project",
                source_ref=f"{task_id}:{node_id}", evidence_refs=evidence,
                confidence=1.0,
            )
            self.record(record, include_in_working=False)
            promoted.append(record)
        return tuple(promoted)

    def record(self, record: MemoryRecord, *, include_in_working: bool = True) -> None:
        self.store.append(record)
        if include_in_working and record.task_id:
            self.working_memory(record.task_id).remember(record)

    def trigger(
        self,
        event: str,
        task: TaskContext,
        role: RoleSpec,
        *,
        query: str = "",
    ) -> tuple[MemoryRecord, ...]:
        """Harness 主动触发入口；确定性事件决定需要检索的记忆层。"""
        kinds_by_event = {
            "task_created": (MemoryKind.LONG_TERM, MemoryKind.ENTITY),
            "task_claimed": (MemoryKind.WORKING, MemoryKind.ENTITY, MemoryKind.LONG_TERM),
            "verification_failed": (MemoryKind.PERCEPTION, MemoryKind.WORKING, MemoryKind.ENTITY, MemoryKind.LONG_TERM),
            "task_resumed": (MemoryKind.WORKING,),
            "task_completed": (MemoryKind.WORKING,),
        }
        kinds = kinds_by_event.get(event, (MemoryKind.WORKING,))
        return self.store.query(task_id=task.task_id, kinds=kinds, role=role.name, text=query)

    def query(
        self,
        task: TaskContext,
        role: RoleSpec,
        query: str,
        kinds: Iterable[MemoryKind] = (),
    ) -> tuple[MemoryRecord, ...]:
        """Agent 被动检索入口，仍强制执行角色可见性过滤。"""
        return self.store.query(task_id=task.task_id, kinds=kinds, role=role.name, text=query)

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
        *,
        trigger: str = "task_claimed",
        query: str = "",
    ) -> RoleMemoryView:
        policy = self.policy_for(role)
        selected = self._limit_files(project_files, policy)
        memories = self.trigger(trigger, task, role, query=query)
        memory_budget = max(
            0,
            policy.max_context_chars - sum(len(item.content) for item in selected),
        )
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
            memories=self._limit_memories(memories, memory_budget),
        )

    @staticmethod
    def _limit_memories(
        memories: tuple[MemoryRecord, ...], budget: int
    ) -> tuple[MemoryRecord, ...]:
        remaining = budget
        selected: list[MemoryRecord] = []
        for item in memories:
            size = len(item.summary)
            if size > remaining:
                continue
            selected.append(item)
            remaining -= size
        return tuple(selected)

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
