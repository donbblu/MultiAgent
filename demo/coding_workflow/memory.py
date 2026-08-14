from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
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


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class MemoryPermissionError(PermissionError):
    pass


class MemorySanitizer:
    """在持久化之前移除常见凭证格式；不保存原始敏感值。"""

    _assignment = re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)"
        r"\s*[:=]\s*(['\"]?)[^\s,'\";]+\2"
    )
    _bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*")
    _private_key = re.compile(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        re.DOTALL,
    )
    _sensitive_keys = {
        "api_key", "apikey", "access_token", "token", "password",
        "passwd", "secret", "authorization", "private_key",
    }

    @classmethod
    def redact_text(cls, value: str) -> str:
        value = cls._private_key.sub("[REDACTED PRIVATE KEY]", value)
        value = cls._bearer.sub("Bearer [REDACTED]", value)
        return cls._assignment.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)

    @classmethod
    def redact_value(cls, value: object) -> object:
        if isinstance(value, str):
            return cls.redact_text(value)
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "[REDACTED]"
                    if str(key).casefold() in cls._sensitive_keys
                    else cls.redact_value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(cls.redact_value(item) for item in value)
        if isinstance(value, list):
            return [cls.redact_value(item) for item in value]
        return value

    @classmethod
    def sanitize(cls, record: "MemoryRecord") -> "MemoryRecord":
        return replace(
            record,
            summary=cls.redact_text(record.summary),
            content=MappingProxyType(dict(cls.redact_value(record.content))),
        )


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
    project_id: str
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
    semantic_key: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    invalidated_at: str | None = None
    invalidated_reason: str = ""
    last_confirmed_at: str = ""

    def __post_init__(self) -> None:
        if not self.memory_id or not self.subtype or not self.summary:
            raise ValueError("记忆 ID、类型和摘要不能为空")
        if not 0 <= self.confidence <= 1:
            raise ValueError("记忆可信度必须在 0 到 1 之间")
        if self.sensitivity not in {"public", "internal", "restricted"}:
            raise ValueError("记忆敏感级别无效")
        if self.sensitivity == "restricted":
            raise ValueError("受限内容不得进入 Agent 记忆")
        if not isinstance(self.status, MemoryStatus):
            raise ValueError("记忆状态无效")
        if self.expires_at:
            expires = datetime.fromisoformat(self.expires_at)
            if expires.tzinfo is None:
                raise ValueError("记忆过期时间必须包含时区")

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
        project_id: str = "",
        visibility: Iterable[str] = (),
        task_id: str | None = None,
        source_ref: str = "",
        evidence_refs: Iterable[str] = (),
        confidence: float = 1.0,
        expires_at: str | None = None,
        semantic_key: str = "",
    ) -> "MemoryRecord":
        now = datetime.now(timezone.utc).isoformat()
        if kind is MemoryKind.LONG_TERM and not semantic_key:
            normalized = " ".join(summary.casefold().split())
            semantic_key = sha256(
                f"{project_id}\0{subtype}\0{normalized}".encode("utf-8")
            ).hexdigest()
        return cls(
            str(uuid4()), kind, subtype, summary,
            MappingProxyType(dict(content or {})), source, scope, project_id,
            frozenset(visibility), task_id, source_ref, tuple(evidence_refs),
            confidence=confidence,
            created_at=now,
            expires_at=expires_at,
            semantic_key=semantic_key,
            last_confirmed_at=now,
        )

    def payload_fingerprint(self) -> str:
        payload = json.dumps(
            {
                "kind": self.kind.value,
                "subtype": self.subtype,
                "summary": self.summary,
                "content": dict(self.content),
                "scope": self.scope,
                "project_id": self.project_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class MemoryStore:
    """第一阶段内存存储；接口可由 SQLite 实现替换。"""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = RLock()

    def append(self, record: MemoryRecord) -> MemoryRecord:
        record = MemorySanitizer.sanitize(record)
        with self._lock:
            if record.memory_id in self._records:
                raise ValueError(f"记忆 ID 已存在: {record.memory_id}")
            if record.kind is MemoryKind.LONG_TERM:
                active = [
                    item for item in self._records.values()
                    if item.kind is MemoryKind.LONG_TERM
                    and item.project_id == record.project_id
                    and item.status is MemoryStatus.ACTIVE
                ]
                same_key = next(
                    (item for item in active if item.semantic_key == record.semantic_key),
                    None,
                )
                duplicate = next(
                    (
                        item for item in active
                        if item.payload_fingerprint() == record.payload_fingerprint()
                    ),
                    None,
                )
                existing = same_key or duplicate
                if existing and existing.payload_fingerprint() == record.payload_fingerprint():
                    confirmed = replace(
                        existing,
                        evidence_refs=tuple(dict.fromkeys(
                            (*existing.evidence_refs, *record.evidence_refs)
                        )),
                        last_confirmed_at=record.created_at,
                    )
                    self._records[existing.memory_id] = confirmed
                    return confirmed
                if same_key:
                    now = record.created_at
                    self._records[same_key.memory_id] = replace(
                        same_key,
                        status=MemoryStatus.SUPERSEDED,
                        invalidated_at=now,
                        invalidated_reason=f"由 {record.memory_id} 替代",
                    )
                    record = replace(
                        record,
                        version=same_key.version + 1,
                        supersedes=same_key.memory_id,
                    )
            self._records[record.memory_id] = record
            return record

    def invalidate(self, memory_id: str, reason: str) -> MemoryRecord:
        if not reason.strip():
            raise ValueError("记忆失效原因不能为空")
        with self._lock:
            try:
                record = self._records[memory_id]
            except KeyError as exc:
                raise KeyError(f"记忆不存在: {memory_id}") from exc
            if record.status is not MemoryStatus.ACTIVE:
                raise ValueError(f"只有有效记忆可以失效: {memory_id}")
            updated = replace(
                record,
                status=MemoryStatus.INVALIDATED,
                invalidated_at=datetime.now(timezone.utc).isoformat(),
                invalidated_reason=reason,
            )
            self._records[memory_id] = updated
            return updated

    def query(
        self,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
        kinds: Iterable[MemoryKind] = (),
        scopes: Iterable[str] = (),
        role: str = "",
        text: str = "",
        include_inactive: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        allowed_kinds = set(kinds)
        allowed_scopes = set(scopes)
        words = {word.lower() for word in text.split() if word}
        with self._lock:
            records = tuple(self._records.values())
        result = []
        now = datetime.now(timezone.utc)
        for original in records:
            record = original
            if (
                record.status is MemoryStatus.ACTIVE
                and record.expires_at
                and datetime.fromisoformat(record.expires_at) <= now
            ):
                record = replace(
                    record,
                    status=MemoryStatus.EXPIRED,
                    invalidated_at=now.isoformat(),
                    invalidated_reason="已到期",
                )
                with self._lock:
                    self._records[record.memory_id] = record
            if not include_inactive and record.status is not MemoryStatus.ACTIVE:
                continue
            if project_id is not None and record.project_id != project_id:
                continue
            if task_id is not None and record.task_id not in {None, task_id}:
                continue
            if allowed_kinds and record.kind not in allowed_kinds:
                continue
            if allowed_scopes and record.scope not in allowed_scopes:
                continue
            if role and record.visibility and role not in record.visibility:
                continue
            haystack = record.summary.lower()
            if words and not any(word in haystack for word in words):
                continue
            result.append(record)
        return tuple(sorted(result, key=lambda item: (item.confidence, item.created_at), reverse=True))


@dataclass(frozen=True)
class WorkingNodeState:
    node_id: str
    role: str
    state: str
    attempt: int = 0
    summary: str = ""
    last_error: str = ""
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkingArtifactState:
    reference: str
    producer_node_id: str
    state: str
    affected_paths: tuple[str, ...] = ()
    superseded_by: str | None = None
    verification_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailureObservation:
    failure_id: str
    source: str
    summary: str
    feedback: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    affected_artifacts: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    resolved_by: str | None = None


@dataclass(frozen=True)
class QualityGateState:
    affected_checks_completed: bool = False
    affected_checks_passed: bool | None = None
    full_gate_completed: bool = False
    passed: bool | None = None
    summary: str = ""
    verification_refs: tuple[str, ...] = ()


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
    nodes: dict[str, WorkingNodeState] = field(default_factory=dict)
    artifacts: dict[str, WorkingArtifactState] = field(default_factory=dict)
    failures: dict[str, FailureObservation] = field(default_factory=dict)
    quality_gate: QualityGateState = field(default_factory=QualityGateState)
    open_questions: list[str] = field(default_factory=list)
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
            "nodes": {
                key: {
                    "node_id": item.node_id, "role": item.role,
                    "state": item.state, "attempt": item.attempt,
                    "summary": item.summary, "last_error": item.last_error,
                    "input_artifacts": item.input_artifacts,
                    "output_artifacts": item.output_artifacts,
                }
                for key, item in self.nodes.items()
            },
            "artifacts": {
                key: {
                    "reference": item.reference,
                    "producer_node_id": item.producer_node_id,
                    "state": item.state,
                    "affected_paths": item.affected_paths,
                    "superseded_by": item.superseded_by,
                    "verification_refs": item.verification_refs,
                }
                for key, item in self.artifacts.items()
            },
            "failures": {
                key: {
                    "failure_id": item.failure_id, "source": item.source,
                    "summary": item.summary, "feedback": item.feedback,
                    "affected_paths": item.affected_paths,
                    "affected_artifacts": item.affected_artifacts,
                    "evidence_refs": item.evidence_refs,
                    "resolved_by": item.resolved_by,
                }
                for key, item in self.failures.items()
            },
            "quality_gate": {
                "affected_checks_completed": self.quality_gate.affected_checks_completed,
                "affected_checks_passed": self.quality_gate.affected_checks_passed,
                "full_gate_completed": self.quality_gate.full_gate_completed,
                "passed": self.quality_gate.passed,
                "summary": self.quality_gate.summary,
                "verification_refs": self.quality_gate.verification_refs,
            },
            "open_questions": tuple(self.open_questions),
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
    working_progress: Mapping[str, object]
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
            "working_progress": dict(self.working_progress),
        }


DEFAULT_MEMORY_POLICIES: Mapping[str, MemoryPolicy] = MappingProxyType(
    {
        PLANNER.name: MemoryPolicy(
            frozenset({"task", "project"}),
            frozenset({"task"}),
            15_000,
        ),
        IMPLEMENTER.name: MemoryPolicy(
            frozenset({"task", "project"}),
            frozenset({"task"}),
            40_000,
            include_project_files=True,
        ),
        TESTER.name: MemoryPolicy(
            frozenset({"task"}),
            frozenset({"task"}),
            0,
            include_verification_commands=True,
        ),
        FIXER.name: MemoryPolicy(
            frozenset({"task"}),
            frozenset({"task"}),
            30_000,
            include_project_files=True,
            include_feedback=True,
        ),
        REVIEWER.name: MemoryPolicy(
            frozenset({"task", "project"}),
            frozenset({"task"}),
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

    def update_node(self, task_id: str, node: WorkingNodeState) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            working.nodes[node.node_id] = node
            if node.summary:
                working.node_summaries[node.node_id] = node.summary
            working.version += 1

    def update_artifact(self, task_id: str, artifact: WorkingArtifactState) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            working.artifacts[artifact.reference] = artifact
            if artifact.state in {"failed", "superseded"}:
                for name, reference in tuple(working.active_artifacts.items()):
                    if reference == artifact.reference:
                        working.active_artifacts.pop(name, None)
            working.version += 1

    def register_artifact_names(
        self, task_id: str, references: Mapping[str, str]
    ) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            working.active_artifacts.update(references)
            working.version += 1

    def observe_failure(self, task_id: str, failure: FailureObservation) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            working.failures[failure.failure_id] = failure
            working.feedback = list(failure.feedback) or [failure.summary]
            working.version += 1

    def resolve_failures(
        self,
        task_id: str,
        resolved_by: str,
        failure_ids: Iterable[str] = (),
    ) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            selected = frozenset(failure_ids)
            changed = False
            for failure_id, failure in tuple(working.failures.items()):
                if (
                    failure.resolved_by is None
                    and (not selected or failure_id in selected)
                ):
                    working.failures[failure_id] = replace(
                        failure, resolved_by=resolved_by
                    )
                    changed = True
            if changed:
                working.feedback = []
                working.version += 1

    def update_quality_gate(self, task_id: str, gate: QualityGateState) -> None:
        with self._lock:
            working = self.working_memory(task_id)
            working.quality_gate = gate
            working.version += 1

    def unresolved_feedback(self, task_id: str) -> tuple[str, ...]:
        with self._lock:
            working = self.working_memory(task_id)
            result: list[str] = []
            for failure in working.failures.values():
                if failure.resolved_by is None:
                    result.extend(failure.feedback or (failure.summary,))
            return tuple(dict.fromkeys(result))

    def failure_ids(self, task_id: str, *, prefix: str = "") -> tuple[str, ...]:
        with self._lock:
            return tuple(
                failure_id
                for failure_id in self.working_memory(task_id).failures
                if not prefix or failure_id.startswith(prefix)
            )

    def node_states(self, task_id: str) -> Mapping[str, WorkingNodeState]:
        with self._lock:
            return MappingProxyType(dict(self.working_memory(task_id).nodes))

    def progress_view(self, task_id: str, role: RoleSpec) -> Mapping[str, object]:
        with self._lock:
            working = self.working_memory(task_id)
            artifact_states = tuple(working.artifacts.values())
            failure_states = tuple(working.failures.values())
            node_states = tuple(working.nodes.values())
            quality_gate = working.quality_gate
            open_questions = tuple(working.open_questions)
        active_artifacts = [
            {
                "reference": item.reference,
                "producer_node_id": item.producer_node_id,
                "state": item.state,
                "affected_paths": list(item.affected_paths),
            }
            for item in artifact_states
            if item.state not in {"failed", "superseded"}
        ]
        if role.name == FIXER.name:
            failures = [
                {
                    "failure_id": item.failure_id,
                    "summary": item.summary,
                    "feedback": list(item.feedback),
                    "affected_paths": list(item.affected_paths),
                    "affected_artifacts": list(item.affected_artifacts),
                }
                for item in failure_states
                if item.resolved_by is None
            ]
            return MappingProxyType({
                "unresolved_failures": failures,
                "active_artifacts": active_artifacts,
            })
        if role.name == TESTER.name:
            return MappingProxyType({
                "active_artifacts": active_artifacts,
                "quality_gate": {
                    "full_gate_completed": quality_gate.full_gate_completed,
                    "passed": quality_gate.passed,
                },
            })
        nodes = [
            {
                "node_id": item.node_id, "state": item.state,
                "attempt": item.attempt, "summary": item.summary,
            }
            for item in node_states
            if role.name in {PLANNER.name, REVIEWER.name} or item.role == role.name
        ]
        return MappingProxyType({
            "nodes": nodes,
            "active_artifacts": active_artifacts,
            "open_questions": list(open_questions),
        })

    def save_checkpoint(self, task_id: str) -> Mapping[str, object]:
        with self._lock:
            working = self.working_memory(task_id)
            saver = getattr(self.store, "save_checkpoint", None)
            if saver:
                saver(working)
            return working.checkpoint()

    def consolidate(
        self,
        task_id: str,
        *,
        project_id: str,
        verified_artifacts: Iterable[str],
        verification_refs: Iterable[str] = (),
    ) -> tuple[MemoryRecord, ...]:
        """只晋升仍然生效且显式通过验证的 Artifact 对应节点结果。"""
        verified = frozenset(verified_artifacts)
        if not verified:
            return ()
        promoted: list[MemoryRecord] = []
        node_results = self.store.query(
            project_id=project_id,
            task_id=task_id,
            kinds=(MemoryKind.WORKING,),
            scopes=("task",),
        )
        for result in node_results:
            evidence = tuple(ref for ref in result.evidence_refs if ref in verified)
            if result.subtype != "node_result" or not evidence:
                continue
            record = MemoryRecord.create(
                MemoryKind.LONG_TERM, "verified_node_result", result.summary,
                task_id=None, source="harness", scope="project",
                project_id=project_id,
                source_ref=f"{task_id}:{result.source_ref}",
                evidence_refs=(*evidence, *tuple(verification_refs)),
                confidence=1.0,
                semantic_key=sha256(
                    (
                        f"{project_id}\0verified_node_result\0{task_id}\0"
                        f"{result.source_ref}"
                    ).encode("utf-8")
                ).hexdigest(),
            )
            promoted.append(self.record(record, include_in_working=False))
        return tuple(promoted)

    def record(
        self, record: MemoryRecord, *, include_in_working: bool = True
    ) -> MemoryRecord:
        stored = self.store.append(record)
        if include_in_working and stored.task_id:
            self.working_memory(stored.task_id).remember(stored)
        return stored

    def record_for_role(
        self, task: TaskContext, role: RoleSpec, record: MemoryRecord
    ) -> None:
        if record.scope not in self.policy_for(role).writable_scopes:
            raise MemoryPermissionError(
                f"角色 {role.name} 不允许写入记忆 scope: {record.scope}"
            )
        if record.project_id != task.project_id:
            raise MemoryPermissionError("角色不能写入其他项目的记忆")
        if record.scope == "task" and record.task_id != task.task_id:
            raise MemoryPermissionError("角色不能写入其他任务的记忆")
        self.record(record)

    def invalidate(self, memory_id: str, reason: str) -> MemoryRecord:
        invalidator = getattr(self.store, "invalidate", None)
        if invalidator is None:
            raise TypeError("当前 MemoryStore 不支持记忆失效")
        return invalidator(memory_id, reason)

    def trigger(
        self,
        event: str,
        task: TaskContext,
        role: RoleSpec,
        *,
        query: str = "",
    ) -> tuple[MemoryRecord, ...]:
        """Harness 主动触发入口；确定性事件决定需要检索的记忆层。"""
        policy = self.policy_for(role)
        kinds_by_event = {
            "task_created": (MemoryKind.LONG_TERM, MemoryKind.ENTITY),
            "task_claimed": (MemoryKind.WORKING, MemoryKind.ENTITY, MemoryKind.LONG_TERM),
            "verification_failed": (MemoryKind.PERCEPTION, MemoryKind.WORKING, MemoryKind.ENTITY, MemoryKind.LONG_TERM),
            "task_resumed": (MemoryKind.WORKING,),
            "task_completed": (MemoryKind.WORKING,),
        }
        kinds = kinds_by_event.get(event, (MemoryKind.WORKING,))
        return self.store.query(
            project_id=task.project_id,
            task_id=task.task_id,
            kinds=kinds,
            scopes=policy.readable_scopes,
            role=role.name,
            text=query,
        )

    def query(
        self,
        task: TaskContext,
        role: RoleSpec,
        query: str,
        kinds: Iterable[MemoryKind] = (),
    ) -> tuple[MemoryRecord, ...]:
        """Agent 被动检索入口，仍强制执行角色可见性过滤。"""
        policy = self.policy_for(role)
        return self.store.query(
            project_id=task.project_id,
            task_id=task.task_id,
            kinds=kinds,
            scopes=policy.readable_scopes,
            role=role.name,
            text=query,
        )

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
        progress = self.progress_view(task.task_id, role)
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
            feedback=(
                tuple(dict.fromkeys((*self.unresolved_feedback(task.task_id), *task.feedback)))
                if policy.include_feedback else ()
            ),
            verification_commands=(
                tuple(tuple(command) for command in task.verification_commands)
                if policy.include_verification_commands
                else ()
            ),
            project_files=selected,
            policy=policy,
            working_progress=progress,
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
