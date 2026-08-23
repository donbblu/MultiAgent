from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
    canonical_digest,
    enum_value,
    namespaced,
    nonempty,
    optional_ref_from_dict,
    optional_ref_to_dict,
    optional_string,
    optional_timestamp,
    positive_int,
    refs_from_dict,
    refs_to_dict,
    require_fields,
    require_schema_version,
    scoped_refs,
    sha256_digest,
    timestamp,
)


class ExecutionState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class CleanupState(str, Enum):
    ALLOCATED = "allocated"
    ACTIVE = "active"
    DRAINING = "draining"
    TERMINATING = "terminating"
    REAPED = "reaped"
    TERMINATION_FAILED = "termination_failed"


TERMINAL_EXECUTION_STATES = frozenset({
    ExecutionState.SUCCEEDED,
    ExecutionState.FAILED,
    ExecutionState.CANCELLED,
    ExecutionState.TIMED_OUT,
})


_EXECUTION_TRANSITIONS: Mapping[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.CREATED: frozenset({
        ExecutionState.QUEUED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
    }),
    ExecutionState.QUEUED: frozenset({
        ExecutionState.CLAIMED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
    }),
    ExecutionState.CLAIMED: frozenset({
        ExecutionState.RUNNING,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.TIMED_OUT,
    }),
    ExecutionState.RUNNING: TERMINAL_EXECUTION_STATES,
    ExecutionState.SUCCEEDED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.TIMED_OUT: frozenset(),
}


_CLEANUP_TRANSITIONS: Mapping[CleanupState, frozenset[CleanupState]] = {
    CleanupState.ALLOCATED: frozenset({
        CleanupState.ACTIVE,
        CleanupState.DRAINING,
    }),
    CleanupState.ACTIVE: frozenset({CleanupState.DRAINING}),
    CleanupState.DRAINING: frozenset({
        CleanupState.TERMINATING,
        CleanupState.REAPED,
    }),
    CleanupState.TERMINATING: frozenset({
        CleanupState.REAPED,
        CleanupState.TERMINATION_FAILED,
    }),
    # A failed cleanup is a recoverable state for the idempotent Reaper.
    CleanupState.TERMINATION_FAILED: frozenset({CleanupState.TERMINATING}),
    CleanupState.REAPED: frozenset(),
}


def validate_execution_transition(
    previous: ExecutionState | str,
    current: ExecutionState | str,
) -> None:
    before = enum_value(previous, ExecutionState, "previous_execution_state")
    after = enum_value(current, ExecutionState, "execution_state")
    if after not in _EXECUTION_TRANSITIONS[before]:
        raise RuntimeProtocolError(
            f"非法执行状态迁移: {before.value} -> {after.value}"
        )


def validate_cleanup_transition(
    previous: CleanupState | str,
    current: CleanupState | str,
) -> None:
    before = enum_value(previous, CleanupState, "previous_cleanup_state")
    after = enum_value(current, CleanupState, "cleanup_state")
    if after not in _CLEANUP_TRANSITIONS[before]:
        raise RuntimeProtocolError(
            f"非法清理状态迁移: {before.value} -> {after.value}"
        )


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeProtocolError(f"{field_name} 必须是布尔值")
    return value


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _same_entity(left: ScopedRef, right: ScopedRef) -> bool:
    """Compare identity while allowing each relation to retain its own version."""

    return (
        left.scope_id == right.scope_id
        and left.entity_type == right.entity_type
        and left.entity_id == right.entity_id
    )


def _entity_identities(values: tuple[ScopedRef, ...]) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (item.scope_id, item.entity_type, item.entity_id) for item in values
    )


def _reject_new_active_refs(
    previous: object,
    current: object,
    field_names: tuple[str, ...],
) -> None:
    for field_name in field_names:
        before = _entity_identities(getattr(previous, field_name))
        after = _entity_identities(getattr(current, field_name))
        if not after.issubset(before):
            raise RuntimeProtocolError(
                f"执行终态或清理阶段不得新增 {field_name}"
            )


def _validate_monotonic_lease(previous: "Attempt", current: "Attempt") -> None:
    before = previous.lease
    after = current.lease
    if before is None:
        if after is not None and current.execution_state is not ExecutionState.CLAIMED:
            raise RuntimeProtocolError("Lease 只能在 Attempt claimed 时签发")
        return
    if after is None:
        raise RuntimeProtocolError("Attempt 已签发的 Lease 必须保留为审计事实")
    immutable = (
        "scope_id", "thread_ref", "invocation_ref", "attempt_ref", "owner_id",
        "acquired_at", "fence",
    )
    if any(getattr(before, name) != getattr(after, name) for name in immutable):
        raise RuntimeProtocolError("Attempt Lease 的执行绑定不得改变")
    if not _same_entity(before.lease_ref, after.lease_ref):
        raise RuntimeProtocolError("Attempt Lease identity 不得改变")
    if after.lease_ref.version < before.lease_ref.version:
        raise RuntimeProtocolError("Attempt Lease version 不得倒退")
    if _instant(after.expires_at) < _instant(before.expires_at):
        raise RuntimeProtocolError("Attempt Lease expires_at 不得倒退")
    if before.last_heartbeat_at and (
        not after.last_heartbeat_at
        or _instant(after.last_heartbeat_at) < _instant(before.last_heartbeat_at)
    ):
        raise RuntimeProtocolError("Attempt Lease heartbeat 不得倒退")
    content_changed = (
        after.expires_at != before.expires_at
        or after.last_heartbeat_at != before.last_heartbeat_at
    )
    expected_version = (
        before.lease_ref.version + 1
        if content_changed
        else before.lease_ref.version
    )
    if after.lease_ref.version != expected_version:
        raise RuntimeProtocolError(
            "Attempt Lease 内容变化必须将 lease_ref.version 恰好递增 1"
        )


def _typed_ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_type: str,
) -> ScopedRef:
    if not isinstance(value, ScopedRef):
        raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef")
    value.assert_scope(scope_id, field_name)
    value.assert_type(entity_type)
    return value


def _optional_typed_ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_type: str,
) -> ScopedRef | None:
    if value is None:
        return None
    return _typed_ref(
        value, field_name, scope_id=scope_id, entity_type=entity_type
    )


def _optional_scoped_ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
) -> ScopedRef | None:
    if value is None:
        return None
    if not isinstance(value, ScopedRef):
        raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef 或 null")
    value.assert_scope(scope_id, field_name)
    return value


def _optional_nested(
    value: object,
    field_name: str,
    factory: object,
) -> object | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeProtocolError(f"{field_name} 必须是对象或 null")
    return factory.from_dict(value)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class InvocationInputRef:
    """A versioned input reference pinned to immutable content bytes."""

    ref: ScopedRef
    content_hash: str
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ScopedRef):
            raise RuntimeProtocolError("ref 必须是 ScopedRef")
        object.__setattr__(
            self, "content_hash", sha256_digest(self.content_hash, "content_hash")
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "InvocationInputRef"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "ref": dict(self.ref.to_dict()),
            "content_hash": self.content_hash,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "InvocationInputRef":
        root = require_fields(
            value,
            type_name="InvocationInputRef",
            required=frozenset({"schema_version", "ref", "content_hash"}),
        )
        if not isinstance(root["ref"], Mapping):
            raise RuntimeProtocolError("ref 必须是引用对象")
        return cls(
            ScopedRef.from_dict(root["ref"]),
            root["content_hash"],
            root["schema_version"],
        )


ScopedSnapshotRef = InvocationInputRef


def input_refs_to_dict(
    value: tuple[InvocationInputRef, ...],
) -> list[dict[str, object]]:
    return [dict(item.to_dict()) for item in value]


def input_refs_from_dict(
    value: object,
    field_name: str = "input_refs",
) -> tuple[InvocationInputRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是输入快照引用数组")
    parsed: list[InvocationInputRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RuntimeProtocolError(f"{field_name} 必须包含输入快照引用对象")
        parsed.append(InvocationInputRef.from_dict(item))
    return tuple(parsed)


def digest_invocation_inputs(value: tuple[InvocationInputRef, ...]) -> str:
    """Order is significant because it is part of the immutable call input."""

    return canonical_digest(input_refs_to_dict(value))


def _invocation_inputs(
    value: object,
    *,
    scope_id: str,
    allow_empty: bool = False,
) -> tuple[InvocationInputRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError("input_refs 必须是输入快照引用数组")
    if not value and not allow_empty:
        raise RuntimeProtocolError("input_refs 不能为空")
    parsed = tuple(value)
    if not all(isinstance(item, InvocationInputRef) for item in parsed):
        raise RuntimeProtocolError("input_refs 必须包含 InvocationInputRef")
    seen: set[ScopedRef] = set()
    for item in parsed:
        item.ref.assert_scope(scope_id, "input_refs")
        if item.ref in seen:
            raise RuntimeProtocolError("同一 ref/version 不能重复或声明不同 content_hash")
        seen.add(item.ref)
    return parsed


@dataclass(frozen=True)
class TerminationIntent:
    intent_id: str
    scope_id: str
    subject_ref: ScopedRef
    reason_code: str
    requested_at: str
    requested_by_ref: ScopedRef | None = None
    detail: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", nonempty(self.intent_id, "intent_id"))
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        if not isinstance(self.subject_ref, ScopedRef):
            raise RuntimeProtocolError("subject_ref 必须是 ScopedRef")
        self.subject_ref.assert_scope(scope_id, "subject_ref")
        self.subject_ref.assert_type("core:invocation", "core:attempt")
        object.__setattr__(
            self, "reason_code", namespaced(self.reason_code, "reason_code")
        )
        object.__setattr__(
            self, "requested_at", timestamp(self.requested_at, "requested_at")
        )
        object.__setattr__(
            self,
            "requested_by_ref",
            _optional_scoped_ref(
                self.requested_by_ref, "requested_by_ref", scope_id=scope_id
            ),
        )
        if self.requested_by_ref is not None:
            self.requested_by_ref.assert_type(
                "core:user",
                "core:principal",
                "core:agent_instance",
                "core:runtime",
                "core:runtime_principal",
            )
        object.__setattr__(self, "detail", optional_string(self.detail, "detail"))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "TerminationIntent"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "intent_id": self.intent_id,
            "scope_id": self.scope_id,
            "subject_ref": dict(self.subject_ref.to_dict()),
            "reason_code": self.reason_code,
            "requested_at": self.requested_at,
            "requested_by_ref": optional_ref_to_dict(self.requested_by_ref),
            "detail": self.detail,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TerminationIntent":
        root = require_fields(
            value,
            type_name="TerminationIntent",
            required=frozenset({
                "schema_version", "intent_id", "scope_id", "subject_ref",
                "reason_code", "requested_at", "requested_by_ref", "detail",
            }),
        )
        if not isinstance(root["subject_ref"], Mapping):
            raise RuntimeProtocolError("subject_ref 必须是引用对象")
        return cls(
            root["intent_id"],
            root["scope_id"],
            ScopedRef.from_dict(root["subject_ref"]),
            root["reason_code"],
            root["requested_at"],
            optional_ref_from_dict(root["requested_by_ref"], "requested_by_ref"),
            root["detail"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class TerminalRecord:
    record_id: str
    scope_id: str
    subject_ref: ScopedRef
    terminal_state: ExecutionState
    reason_code: str
    finished_at: str
    output_refs: tuple[InvocationInputRef, ...]
    output_digest: str
    usage_ref: ScopedRef | None = None
    detail: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", nonempty(self.record_id, "record_id"))
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        if not isinstance(self.subject_ref, ScopedRef):
            raise RuntimeProtocolError("subject_ref 必须是 ScopedRef")
        self.subject_ref.assert_scope(scope_id, "subject_ref")
        self.subject_ref.assert_type("core:invocation", "core:attempt")
        state = enum_value(self.terminal_state, ExecutionState, "terminal_state")
        if state not in TERMINAL_EXECUTION_STATES:
            raise RuntimeProtocolError("TerminalRecord 必须记录执行终态")
        object.__setattr__(self, "terminal_state", state)
        object.__setattr__(
            self, "reason_code", namespaced(self.reason_code, "reason_code")
        )
        object.__setattr__(
            self, "finished_at", timestamp(self.finished_at, "finished_at")
        )
        outputs = _invocation_inputs(
            self.output_refs, scope_id=scope_id, allow_empty=True
        )
        object.__setattr__(self, "output_refs", outputs)
        output_digest = sha256_digest(self.output_digest, "output_digest")
        if output_digest != digest_invocation_inputs(outputs):
            raise RuntimeProtocolError("output_digest 与 output_refs 不匹配")
        object.__setattr__(self, "output_digest", output_digest)
        object.__setattr__(
            self,
            "usage_ref",
            _optional_typed_ref(
                self.usage_ref, "usage_ref", scope_id=scope_id,
                entity_type="core:usage",
            ),
        )
        object.__setattr__(self, "detail", optional_string(self.detail, "detail"))
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "TerminalRecord"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "scope_id": self.scope_id,
            "subject_ref": dict(self.subject_ref.to_dict()),
            "terminal_state": self.terminal_state.value,
            "reason_code": self.reason_code,
            "finished_at": self.finished_at,
            "output_refs": input_refs_to_dict(self.output_refs),
            "output_digest": self.output_digest,
            "usage_ref": optional_ref_to_dict(self.usage_ref),
            "detail": self.detail,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TerminalRecord":
        root = require_fields(
            value,
            type_name="TerminalRecord",
            required=frozenset({
                "schema_version", "record_id", "scope_id", "subject_ref",
                "terminal_state", "reason_code", "finished_at", "output_refs",
                "output_digest", "usage_ref", "detail",
            }),
        )
        if not isinstance(root["subject_ref"], Mapping):
            raise RuntimeProtocolError("subject_ref 必须是引用对象")
        return cls(
            root["record_id"], root["scope_id"],
            ScopedRef.from_dict(root["subject_ref"]), root["terminal_state"],
            root["reason_code"], root["finished_at"],
            input_refs_from_dict(root["output_refs"], "output_refs"),
            root["output_digest"],
            optional_ref_from_dict(root["usage_ref"], "usage_ref"),
            root["detail"], root["schema_version"],
        )


@dataclass(frozen=True)
class FenceToken:
    scope_id: str
    thread_ref: ScopedRef
    invocation_ref: ScopedRef
    attempt_ref: ScopedRef
    generation: int
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self, "thread_ref", _typed_ref(
                self.thread_ref, "thread_ref", scope_id=scope_id,
                entity_type="core:thread",
            )
        )
        object.__setattr__(
            self, "invocation_ref", _typed_ref(
                self.invocation_ref, "invocation_ref", scope_id=scope_id,
                entity_type="core:invocation",
            )
        )
        object.__setattr__(
            self, "attempt_ref", _typed_ref(
                self.attempt_ref, "attempt_ref", scope_id=scope_id,
                entity_type="core:attempt",
            )
        )
        object.__setattr__(
            self, "generation", positive_int(self.generation, "generation")
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "FenceToken"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "invocation_ref": dict(self.invocation_ref.to_dict()),
            "attempt_ref": dict(self.attempt_ref.to_dict()),
            "generation": self.generation,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FenceToken":
        root = require_fields(
            value,
            type_name="FenceToken",
            required=frozenset({
                "schema_version", "scope_id", "thread_ref", "invocation_ref",
                "attempt_ref", "generation",
            }),
        )
        for name in ("thread_ref", "invocation_ref", "attempt_ref"):
            if not isinstance(root[name], Mapping):
                raise RuntimeProtocolError(f"{name} 必须是引用对象")
        return cls(
            root["scope_id"],
            ScopedRef.from_dict(root["thread_ref"]),
            ScopedRef.from_dict(root["invocation_ref"]),
            ScopedRef.from_dict(root["attempt_ref"]),
            root["generation"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class AttemptLease:
    lease_ref: ScopedRef
    scope_id: str
    thread_ref: ScopedRef
    invocation_ref: ScopedRef
    attempt_ref: ScopedRef
    owner_id: str
    acquired_at: str
    expires_at: str
    fence: FenceToken
    last_heartbeat_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self, "lease_ref", _typed_ref(
                self.lease_ref, "lease_ref", scope_id=scope_id,
                entity_type="core:lease",
            )
        )
        for name, kind in (
            ("thread_ref", "core:thread"),
            ("invocation_ref", "core:invocation"),
            ("attempt_ref", "core:attempt"),
        ):
            object.__setattr__(
                self, name, _typed_ref(
                    getattr(self, name), name, scope_id=scope_id, entity_type=kind
                )
            )
        object.__setattr__(self, "owner_id", nonempty(self.owner_id, "owner_id"))
        acquired = timestamp(self.acquired_at, "acquired_at")
        expires = timestamp(self.expires_at, "expires_at")
        if _instant(expires) <= _instant(acquired):
            raise RuntimeProtocolError("expires_at 必须晚于 acquired_at")
        object.__setattr__(self, "acquired_at", acquired)
        object.__setattr__(self, "expires_at", expires)
        heartbeat = optional_timestamp(self.last_heartbeat_at, "last_heartbeat_at")
        if heartbeat and not (
            _instant(acquired) <= _instant(heartbeat) < _instant(expires)
        ):
            raise RuntimeProtocolError("last_heartbeat_at 必须位于 Lease 有效期内")
        object.__setattr__(self, "last_heartbeat_at", heartbeat)
        if not isinstance(self.fence, FenceToken):
            raise RuntimeProtocolError("fence 必须是 FenceToken")
        if not (
            _same_entity(self.fence.thread_ref, self.thread_ref)
            and self.fence.invocation_ref == self.invocation_ref
            and self.fence.attempt_ref == self.attempt_ref
        ):
            raise RuntimeProtocolError("Lease 与 fence 的执行引用不匹配")
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "AttemptLease"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "lease_ref": dict(self.lease_ref.to_dict()),
            "scope_id": self.scope_id,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "invocation_ref": dict(self.invocation_ref.to_dict()),
            "attempt_ref": dict(self.attempt_ref.to_dict()),
            "owner_id": self.owner_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "fence": dict(self.fence.to_dict()),
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AttemptLease":
        root = require_fields(
            value,
            type_name="AttemptLease",
            required=frozenset({
                "schema_version", "lease_ref", "scope_id", "thread_ref",
                "invocation_ref", "attempt_ref", "owner_id", "acquired_at",
                "expires_at", "last_heartbeat_at", "fence",
            }),
        )
        for name in (
            "lease_ref", "thread_ref", "invocation_ref", "attempt_ref", "fence"
        ):
            if not isinstance(root[name], Mapping):
                raise RuntimeProtocolError(f"{name} 必须是对象")
        return cls(
            ScopedRef.from_dict(root["lease_ref"]), root["scope_id"],
            ScopedRef.from_dict(root["thread_ref"]),
            ScopedRef.from_dict(root["invocation_ref"]),
            ScopedRef.from_dict(root["attempt_ref"]), root["owner_id"],
            root["acquired_at"], root["expires_at"],
            FenceToken.from_dict(root["fence"]), root["last_heartbeat_at"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class Invocation:
    scope_id: str
    invocation_id: str
    thread_ref: ScopedRef
    turn_ref: ScopedRef
    agent_instance_ref: ScopedRef
    agent_session_ref: ScopedRef
    input_refs: tuple[InvocationInputRef, ...]
    input_digest: str
    policy_snapshot_ref: ScopedRef
    budget_reservation_ref: ScopedRef
    deadline_at: str
    task_ref: ScopedRef | None = None
    scenario_run_ref: ScopedRef | None = None
    route_ref: ScopedRef | None = None
    parent_invocation_ref: ScopedRef | None = None
    execution_state: ExecutionState = ExecutionState.CREATED
    cleanup_state: CleanupState = CleanupState.ALLOCATED
    active_child_invocation_refs: tuple[ScopedRef, ...] = ()
    active_attempt_ref: ScopedRef | None = None
    active_grant_refs: tuple[ScopedRef, ...] = ()
    active_lease_refs: tuple[ScopedRef, ...] = ()
    active_resource_refs: tuple[ScopedRef, ...] = ()
    fence_generation: int = 0
    fence_revoked: bool = True
    termination_intent: TerminationIntent | None = None
    terminal_record: TerminalRecord | None = None
    cleanup_failure_ref: ScopedRef | None = None
    cleanup_failure_reason: str = ""
    version: int = 1
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        invocation_id = nonempty(self.invocation_id, "invocation_id")
        version = positive_int(self.version, "version")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "version", version)
        for name, kind in (
            ("thread_ref", "core:thread"),
            ("turn_ref", "core:turn"),
            ("agent_instance_ref", "core:agent_instance"),
            ("agent_session_ref", "core:agent_session"),
            ("policy_snapshot_ref", "core:policy_snapshot"),
            ("budget_reservation_ref", "core:budget_reservation"),
        ):
            object.__setattr__(
                self, name, _typed_ref(
                    getattr(self, name), name, scope_id=scope_id, entity_type=kind
                )
            )
        for name, kind in (
            ("task_ref", "core:task"),
            ("scenario_run_ref", "core:scenario_run"),
            ("route_ref", "core:route_edge"),
            ("parent_invocation_ref", "core:invocation"),
        ):
            object.__setattr__(
                self, name, _optional_typed_ref(
                    getattr(self, name), name, scope_id=scope_id,
                    entity_type=kind,
                )
            )
        if (
            self.parent_invocation_ref is not None
            and self.parent_invocation_ref.entity_id == invocation_id
        ):
            raise RuntimeProtocolError("Invocation 不能以自身为 parent")
        if self.route_ref is not None and self.parent_invocation_ref is None:
            raise RuntimeProtocolError("route_ref 必须绑定 parent_invocation_ref")

        inputs = _invocation_inputs(self.input_refs, scope_id=scope_id)
        object.__setattr__(self, "input_refs", inputs)
        input_digest = sha256_digest(self.input_digest, "input_digest")
        if input_digest != digest_invocation_inputs(inputs):
            raise RuntimeProtocolError("input_digest 与完整输入快照不匹配")
        object.__setattr__(self, "input_digest", input_digest)
        object.__setattr__(
            self, "deadline_at", timestamp(self.deadline_at, "deadline_at")
        )
        execution = enum_value(
            self.execution_state, ExecutionState, "execution_state"
        )
        cleanup = enum_value(self.cleanup_state, CleanupState, "cleanup_state")
        object.__setattr__(self, "execution_state", execution)
        object.__setattr__(self, "cleanup_state", cleanup)

        children = scoped_refs(
            self.active_child_invocation_refs,
            "active_child_invocation_refs",
            scope_id=scope_id,
            entity_types=("core:invocation",),
        )
        if any(item.entity_id == invocation_id for item in children):
            raise RuntimeProtocolError("Invocation 不能以自身为 child")
        object.__setattr__(self, "active_child_invocation_refs", children)
        active_attempt = _optional_typed_ref(
            self.active_attempt_ref,
            "active_attempt_ref",
            scope_id=scope_id,
            entity_type="core:attempt",
        )
        object.__setattr__(self, "active_attempt_ref", active_attempt)
        for name, kind in (
            ("active_grant_refs", "core:capability_grant"),
            ("active_lease_refs", "core:lease"),
            ("active_resource_refs", "core:execution_resource"),
        ):
            object.__setattr__(
                self, name, scoped_refs(
                    getattr(self, name), name, scope_id=scope_id,
                    entity_types=(kind,),
                )
            )
        if active_attempt is None and self.active_lease_refs:
            raise RuntimeProtocolError(
                "没有活动 Attempt 时 Invocation 不得保留活动 Lease"
            )
        if execution in {ExecutionState.CREATED, ExecutionState.QUEUED} and (
            self.active_child_invocation_refs
            or self.active_grant_refs
            or self.active_lease_refs
            or self.active_resource_refs
        ):
            raise RuntimeProtocolError(
                "created/queued Invocation 不得提前持有活动执行资源"
            )
        if (
            execution in {ExecutionState.CLAIMED, ExecutionState.RUNNING}
            and cleanup is not CleanupState.ACTIVE
        ):
            raise RuntimeProtocolError(
                "claimed/running Invocation 的 cleanup_state 必须是 active"
            )
        generation = positive_int(
            self.fence_generation, "fence_generation", allow_zero=True
        )
        revoked = _boolean(self.fence_revoked, "fence_revoked")
        object.__setattr__(self, "fence_generation", generation)
        object.__setattr__(self, "fence_revoked", revoked)
        if active_attempt is not None:
            if generation == 0 or revoked:
                raise RuntimeProtocolError(
                    "活动 Attempt 需要未撤销的正数 fencing generation"
                )
            if execution not in {ExecutionState.CLAIMED, ExecutionState.RUNNING}:
                raise RuntimeProtocolError("只有 claimed/running 可有活动 Attempt")
        elif not revoked:
            raise RuntimeProtocolError("没有活动 Attempt 时 fence 必须已撤销")
        if execution is ExecutionState.CLAIMED and active_attempt is None:
            raise RuntimeProtocolError("claimed Invocation 必须引用活动 Attempt")

        own_ref = self.reference
        intent = self.termination_intent
        if intent is not None:
            if not isinstance(intent, TerminationIntent):
                raise RuntimeProtocolError(
                    "termination_intent 必须是 TerminationIntent 或 null"
                )
            if intent.scope_id != scope_id:
                raise ScopeBoundaryError("termination_intent 跨 Scope")
            if not _same_entity(intent.subject_ref, own_ref):
                raise RuntimeProtocolError("termination_intent 绑定了错误 subject")
            if intent.subject_ref.version > version:
                raise RuntimeProtocolError("termination_intent 引用了未来 subject 版本")
        terminal = self.terminal_record
        if execution in TERMINAL_EXECUTION_STATES:
            if not isinstance(terminal, TerminalRecord):
                raise RuntimeProtocolError("执行终态必须包含 TerminalRecord")
            if terminal.scope_id != scope_id:
                raise ScopeBoundaryError("terminal_record 跨 Scope")
            if not _same_entity(terminal.subject_ref, own_ref):
                raise RuntimeProtocolError("terminal_record 绑定了错误 subject")
            if terminal.subject_ref.version > version:
                raise RuntimeProtocolError("terminal_record 引用了未来 subject 版本")
            if terminal.terminal_state is not execution:
                raise RuntimeProtocolError("terminal_record 与 execution_state 不匹配")
            if active_attempt is not None or not revoked:
                raise RuntimeProtocolError("执行终态不得保留活动 Attempt 或有效 fence")
            if execution is ExecutionState.SUCCEEDED and intent is not None:
                raise RuntimeProtocolError("存在终止意图时不能记录 succeeded")
            if (
                execution is ExecutionState.SUCCEEDED
                and _instant(terminal.finished_at) >= _instant(self.deadline_at)
            ):
                raise RuntimeProtocolError(
                    "succeeded Invocation 的 finished_at 必须早于 deadline"
                )
        elif terminal is not None:
            raise RuntimeProtocolError("非终态不得包含 TerminalRecord")

        if cleanup in {
            CleanupState.DRAINING,
            CleanupState.TERMINATING,
            CleanupState.REAPED,
            CleanupState.TERMINATION_FAILED,
        } and execution not in TERMINAL_EXECUTION_STATES:
            raise RuntimeProtocolError("终止清理状态要求 execution 已进入终态")
        failure_ref = _optional_typed_ref(
            self.cleanup_failure_ref,
            "cleanup_failure_ref",
            scope_id=scope_id,
            entity_type="core:cleanup_failure",
        )
        failure_reason = optional_string(
            self.cleanup_failure_reason, "cleanup_failure_reason"
        )
        object.__setattr__(self, "cleanup_failure_ref", failure_ref)
        object.__setattr__(self, "cleanup_failure_reason", failure_reason)
        if cleanup is CleanupState.TERMINATION_FAILED:
            if failure_ref is None or not failure_reason:
                raise RuntimeProtocolError(
                    "TERMINATION_FAILED 必须包含 cleanup failure ref/reason"
                )
        elif failure_ref is not None or failure_reason:
            raise RuntimeProtocolError(
                "只有 TERMINATION_FAILED 可携带 cleanup failure ref/reason"
            )
        if cleanup is CleanupState.REAPED and (
            active_attempt is not None
            or self.active_child_invocation_refs
            or self.active_grant_refs
            or self.active_lease_refs
            or self.active_resource_refs
            or not revoked
        ):
            raise RuntimeProtocolError("REAPED 不得保留活动执行资源")
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Invocation"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, "core:invocation", self.invocation_id, self.version
        )

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    @property
    def closed(self) -> bool:
        return (
            self.execution_state in TERMINAL_EXECUTION_STATES
            and self.cleanup_state is CleanupState.REAPED
            and self.active_attempt_ref is None
            and not self.active_child_invocation_refs
            and not self.active_grant_refs
            and not self.active_lease_refs
            and not self.active_resource_refs
            and self.fence_revoked
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "invocation_id": self.invocation_id,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "turn_ref": dict(self.turn_ref.to_dict()),
            "agent_instance_ref": dict(self.agent_instance_ref.to_dict()),
            "agent_session_ref": dict(self.agent_session_ref.to_dict()),
            "input_refs": input_refs_to_dict(self.input_refs),
            "input_digest": self.input_digest,
            "policy_snapshot_ref": dict(self.policy_snapshot_ref.to_dict()),
            "budget_reservation_ref": dict(self.budget_reservation_ref.to_dict()),
            "deadline_at": self.deadline_at,
            "task_ref": optional_ref_to_dict(self.task_ref),
            "scenario_run_ref": optional_ref_to_dict(self.scenario_run_ref),
            "route_ref": optional_ref_to_dict(self.route_ref),
            "parent_invocation_ref": optional_ref_to_dict(
                self.parent_invocation_ref
            ),
            "execution_state": self.execution_state.value,
            "cleanup_state": self.cleanup_state.value,
            "active_child_invocation_refs": refs_to_dict(
                self.active_child_invocation_refs
            ),
            "active_attempt_ref": optional_ref_to_dict(self.active_attempt_ref),
            "active_grant_refs": refs_to_dict(self.active_grant_refs),
            "active_lease_refs": refs_to_dict(self.active_lease_refs),
            "active_resource_refs": refs_to_dict(self.active_resource_refs),
            "fence_generation": self.fence_generation,
            "fence_revoked": self.fence_revoked,
            "termination_intent": (
                dict(self.termination_intent.to_dict())
                if self.termination_intent is not None else None
            ),
            "terminal_record": (
                dict(self.terminal_record.to_dict())
                if self.terminal_record is not None else None
            ),
            "cleanup_failure_ref": optional_ref_to_dict(self.cleanup_failure_ref),
            "cleanup_failure_reason": self.cleanup_failure_reason,
            "version": self.version,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Invocation":
        root = require_fields(
            value,
            type_name="Invocation",
            required=frozenset({
                "schema_version", "scope_id", "invocation_id", "thread_ref",
                "turn_ref", "agent_instance_ref", "agent_session_ref",
                "input_refs", "input_digest", "policy_snapshot_ref",
                "budget_reservation_ref", "deadline_at", "task_ref",
                "scenario_run_ref", "route_ref", "parent_invocation_ref",
                "execution_state", "cleanup_state",
                "active_child_invocation_refs", "active_attempt_ref",
                "active_grant_refs", "active_lease_refs",
                "active_resource_refs", "fence_generation", "fence_revoked",
                "termination_intent", "terminal_record", "cleanup_failure_ref",
                "cleanup_failure_reason", "version",
            }),
        )
        for name in (
            "thread_ref", "turn_ref", "agent_instance_ref", "agent_session_ref",
            "policy_snapshot_ref", "budget_reservation_ref",
        ):
            if not isinstance(root[name], Mapping):
                raise RuntimeProtocolError(f"{name} 必须是引用对象")
        return cls(
            scope_id=root["scope_id"], invocation_id=root["invocation_id"],
            thread_ref=ScopedRef.from_dict(root["thread_ref"]),
            turn_ref=ScopedRef.from_dict(root["turn_ref"]),
            agent_instance_ref=ScopedRef.from_dict(root["agent_instance_ref"]),
            agent_session_ref=ScopedRef.from_dict(root["agent_session_ref"]),
            input_refs=input_refs_from_dict(root["input_refs"]),
            input_digest=root["input_digest"],
            policy_snapshot_ref=ScopedRef.from_dict(root["policy_snapshot_ref"]),
            budget_reservation_ref=ScopedRef.from_dict(
                root["budget_reservation_ref"]
            ), deadline_at=root["deadline_at"],
            task_ref=optional_ref_from_dict(root["task_ref"], "task_ref"),
            scenario_run_ref=optional_ref_from_dict(
                root["scenario_run_ref"], "scenario_run_ref"
            ),
            route_ref=optional_ref_from_dict(root["route_ref"], "route_ref"),
            parent_invocation_ref=optional_ref_from_dict(
                root["parent_invocation_ref"], "parent_invocation_ref"
            ), execution_state=root["execution_state"],
            cleanup_state=root["cleanup_state"],
            active_child_invocation_refs=refs_from_dict(
                root["active_child_invocation_refs"],
                "active_child_invocation_refs",
            ), active_attempt_ref=optional_ref_from_dict(
                root["active_attempt_ref"], "active_attempt_ref"
            ), active_grant_refs=refs_from_dict(
                root["active_grant_refs"], "active_grant_refs"
            ), active_lease_refs=refs_from_dict(
                root["active_lease_refs"], "active_lease_refs"
            ), active_resource_refs=refs_from_dict(
                root["active_resource_refs"], "active_resource_refs"
            ), fence_generation=root["fence_generation"],
            fence_revoked=root["fence_revoked"],
            termination_intent=_optional_nested(
                root["termination_intent"], "termination_intent",
                TerminationIntent,
            ), terminal_record=_optional_nested(
                root["terminal_record"], "terminal_record", TerminalRecord,
            ), cleanup_failure_ref=optional_ref_from_dict(
                root["cleanup_failure_ref"], "cleanup_failure_ref"
            ), cleanup_failure_reason=root["cleanup_failure_reason"],
            version=root["version"], schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class Attempt:
    scope_id: str
    attempt_id: str
    invocation_ref: ScopedRef
    thread_ref: ScopedRef
    turn_ref: ScopedRef
    agent_instance_ref: ScopedRef
    agent_session_ref: ScopedRef
    ordinal: int
    input_digest: str
    policy_snapshot_ref: ScopedRef
    deadline_at: str
    execution_state: ExecutionState = ExecutionState.CREATED
    cleanup_state: CleanupState = CleanupState.ALLOCATED
    worker_id: str = ""
    principal_id: str = ""
    selection_ref: ScopedRef | None = None
    fence: FenceToken | None = None
    fence_revoked: bool = True
    lease: AttemptLease | None = None
    lease_active: bool = False
    active_child_invocation_refs: tuple[ScopedRef, ...] = ()
    active_grant_refs: tuple[ScopedRef, ...] = ()
    active_resource_refs: tuple[ScopedRef, ...] = ()
    termination_intent: TerminationIntent | None = None
    terminal_record: TerminalRecord | None = None
    cleanup_failure_ref: ScopedRef | None = None
    cleanup_failure_reason: str = ""
    version: int = 1
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        attempt_id = nonempty(self.attempt_id, "attempt_id")
        version = positive_int(self.version, "version")
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "version", version)
        for name, kind in (
            ("invocation_ref", "core:invocation"),
            ("thread_ref", "core:thread"),
            ("turn_ref", "core:turn"),
            ("agent_instance_ref", "core:agent_instance"),
            ("agent_session_ref", "core:agent_session"),
            ("policy_snapshot_ref", "core:policy_snapshot"),
        ):
            object.__setattr__(
                self, name, _typed_ref(
                    getattr(self, name), name, scope_id=scope_id, entity_type=kind
                )
            )
        object.__setattr__(self, "ordinal", positive_int(self.ordinal, "ordinal"))
        object.__setattr__(
            self, "input_digest", sha256_digest(self.input_digest, "input_digest")
        )
        deadline = timestamp(self.deadline_at, "deadline_at")
        object.__setattr__(self, "deadline_at", deadline)
        execution = enum_value(
            self.execution_state, ExecutionState, "execution_state"
        )
        cleanup = enum_value(self.cleanup_state, CleanupState, "cleanup_state")
        object.__setattr__(self, "execution_state", execution)
        object.__setattr__(self, "cleanup_state", cleanup)
        worker_id = optional_string(self.worker_id, "worker_id")
        principal_id = optional_string(self.principal_id, "principal_id")
        object.__setattr__(self, "worker_id", worker_id)
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(
            self, "selection_ref", _optional_typed_ref(
                self.selection_ref, "selection_ref", scope_id=scope_id,
                entity_type="core:worker_selection",
            )
        )

        own_ref = self.reference
        fence = self.fence
        if fence is not None:
            if not isinstance(fence, FenceToken):
                raise RuntimeProtocolError("fence 必须是 FenceToken 或 null")
            if not (
                _same_entity(fence.thread_ref, self.thread_ref)
                and fence.invocation_ref == self.invocation_ref
                and _same_entity(fence.attempt_ref, own_ref)
                and fence.attempt_ref.version <= version
            ):
                raise RuntimeProtocolError("Attempt 与 fence 的执行引用不匹配")
        revoked = _boolean(self.fence_revoked, "fence_revoked")
        object.__setattr__(self, "fence_revoked", revoked)
        if fence is None and not revoked:
            raise RuntimeProtocolError("没有 FenceToken 时 fence 必须已撤销")
        lease = self.lease
        if lease is not None:
            if not isinstance(lease, AttemptLease):
                raise RuntimeProtocolError("lease 必须是 AttemptLease 或 null")
            if not (
                _same_entity(lease.thread_ref, self.thread_ref)
                and lease.invocation_ref == self.invocation_ref
                and _same_entity(lease.attempt_ref, own_ref)
                and lease.attempt_ref.version <= version
                and fence is not None
                and lease.fence == fence
            ):
                raise RuntimeProtocolError("Attempt 与 Lease 的执行引用不匹配")
            if _instant(lease.expires_at) > _instant(deadline):
                raise RuntimeProtocolError("Lease 不能越过 Attempt deadline")
        lease_active = _boolean(self.lease_active, "lease_active")
        object.__setattr__(self, "lease_active", lease_active)
        if lease_active and (lease is None or revoked):
            raise RuntimeProtocolError("活动 Lease 需要未撤销的 fence")
        if execution in {ExecutionState.CLAIMED, ExecutionState.RUNNING}:
            if cleanup is not CleanupState.ACTIVE:
                raise RuntimeProtocolError(
                    "claimed/running Attempt 的 cleanup_state 必须是 active"
                )
            if fence is None or revoked or not lease_active:
                raise RuntimeProtocolError(
                    "claimed/running Attempt 必须持有活动 Lease 与 fence"
                )
            if not worker_id or not principal_id:
                raise RuntimeProtocolError(
                    "claimed/running Attempt 必须记录 worker_id/principal_id"
                )

        for name, kind in (
            ("active_child_invocation_refs", "core:invocation"),
            ("active_grant_refs", "core:capability_grant"),
            ("active_resource_refs", "core:execution_resource"),
        ):
            values = scoped_refs(
                getattr(self, name), name, scope_id=scope_id, entity_types=(kind,)
            )
            if name == "active_child_invocation_refs" and any(
                item.entity_id == self.invocation_ref.entity_id for item in values
            ):
                raise RuntimeProtocolError("Attempt 不能把所属 Invocation 作为 child")
            object.__setattr__(self, name, values)
        if execution in {ExecutionState.CREATED, ExecutionState.QUEUED} and (
            worker_id
            or principal_id
            or self.selection_ref is not None
            or fence is not None
            or lease is not None
            or lease_active
            or self.active_child_invocation_refs
            or self.active_grant_refs
            or self.active_resource_refs
        ):
            raise RuntimeProtocolError(
                "created/queued Attempt 不得提前绑定 Worker、Lease、Fence 或执行资源"
            )
        if lease is not None and lease.owner_id != worker_id:
            raise RuntimeProtocolError("Attempt Lease owner_id 必须匹配 worker_id")

        intent = self.termination_intent
        if intent is not None:
            if not isinstance(intent, TerminationIntent):
                raise RuntimeProtocolError(
                    "termination_intent 必须是 TerminationIntent 或 null"
                )
            if intent.scope_id != scope_id:
                raise ScopeBoundaryError("termination_intent 跨 Scope")
            if not _same_entity(intent.subject_ref, own_ref):
                raise RuntimeProtocolError("termination_intent 绑定了错误 subject")
            if intent.subject_ref.version > version:
                raise RuntimeProtocolError("termination_intent 引用了未来 subject 版本")
        terminal = self.terminal_record
        if execution in TERMINAL_EXECUTION_STATES:
            if not isinstance(terminal, TerminalRecord):
                raise RuntimeProtocolError("执行终态必须包含 TerminalRecord")
            if terminal.scope_id != scope_id:
                raise ScopeBoundaryError("terminal_record 跨 Scope")
            if not _same_entity(terminal.subject_ref, own_ref):
                raise RuntimeProtocolError("terminal_record 绑定了错误 subject")
            if terminal.subject_ref.version > version:
                raise RuntimeProtocolError("terminal_record 引用了未来 subject 版本")
            if terminal.terminal_state is not execution:
                raise RuntimeProtocolError("terminal_record 与 execution_state 不匹配")
            if not revoked or lease_active:
                raise RuntimeProtocolError("执行终态必须撤销 fence 和 Lease")
            if execution is ExecutionState.SUCCEEDED and intent is not None:
                raise RuntimeProtocolError("存在终止意图时不能记录 succeeded")
            if (
                execution is ExecutionState.SUCCEEDED
                and _instant(terminal.finished_at) >= _instant(self.deadline_at)
            ):
                raise RuntimeProtocolError(
                    "succeeded Attempt 的 finished_at 必须早于 deadline"
                )
        elif terminal is not None:
            raise RuntimeProtocolError("非终态不得包含 TerminalRecord")

        if cleanup in {
            CleanupState.DRAINING,
            CleanupState.TERMINATING,
            CleanupState.REAPED,
            CleanupState.TERMINATION_FAILED,
        } and execution not in TERMINAL_EXECUTION_STATES:
            raise RuntimeProtocolError("终止清理状态要求 execution 已进入终态")
        failure_ref = _optional_typed_ref(
            self.cleanup_failure_ref,
            "cleanup_failure_ref",
            scope_id=scope_id,
            entity_type="core:cleanup_failure",
        )
        failure_reason = optional_string(
            self.cleanup_failure_reason, "cleanup_failure_reason"
        )
        object.__setattr__(self, "cleanup_failure_ref", failure_ref)
        object.__setattr__(self, "cleanup_failure_reason", failure_reason)
        if cleanup is CleanupState.TERMINATION_FAILED:
            if failure_ref is None or not failure_reason:
                raise RuntimeProtocolError(
                    "TERMINATION_FAILED 必须包含 cleanup failure ref/reason"
                )
        elif failure_ref is not None or failure_reason:
            raise RuntimeProtocolError(
                "只有 TERMINATION_FAILED 可携带 cleanup failure ref/reason"
            )
        if cleanup is CleanupState.REAPED and (
            lease_active
            or self.active_child_invocation_refs
            or self.active_grant_refs
            or self.active_resource_refs
            or not revoked
        ):
            raise RuntimeProtocolError("REAPED 不得保留活动执行资源")
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Attempt"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(self.scope_id, "core:attempt", self.attempt_id, self.version)

    @property
    def invocation_id(self) -> str:
        return self.invocation_ref.entity_id

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    @property
    def closed(self) -> bool:
        return (
            self.execution_state in TERMINAL_EXECUTION_STATES
            and self.cleanup_state is CleanupState.REAPED
            and not self.lease_active
            and not self.active_child_invocation_refs
            and not self.active_grant_refs
            and not self.active_resource_refs
            and self.fence_revoked
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "attempt_id": self.attempt_id,
            "invocation_ref": dict(self.invocation_ref.to_dict()),
            "thread_ref": dict(self.thread_ref.to_dict()),
            "turn_ref": dict(self.turn_ref.to_dict()),
            "agent_instance_ref": dict(self.agent_instance_ref.to_dict()),
            "agent_session_ref": dict(self.agent_session_ref.to_dict()),
            "ordinal": self.ordinal,
            "input_digest": self.input_digest,
            "policy_snapshot_ref": dict(self.policy_snapshot_ref.to_dict()),
            "deadline_at": self.deadline_at,
            "execution_state": self.execution_state.value,
            "cleanup_state": self.cleanup_state.value,
            "worker_id": self.worker_id,
            "principal_id": self.principal_id,
            "selection_ref": optional_ref_to_dict(self.selection_ref),
            "fence": dict(self.fence.to_dict()) if self.fence is not None else None,
            "fence_revoked": self.fence_revoked,
            "lease": dict(self.lease.to_dict()) if self.lease is not None else None,
            "lease_active": self.lease_active,
            "active_child_invocation_refs": refs_to_dict(
                self.active_child_invocation_refs
            ),
            "active_grant_refs": refs_to_dict(self.active_grant_refs),
            "active_resource_refs": refs_to_dict(self.active_resource_refs),
            "termination_intent": (
                dict(self.termination_intent.to_dict())
                if self.termination_intent is not None else None
            ),
            "terminal_record": (
                dict(self.terminal_record.to_dict())
                if self.terminal_record is not None else None
            ),
            "cleanup_failure_ref": optional_ref_to_dict(self.cleanup_failure_ref),
            "cleanup_failure_reason": self.cleanup_failure_reason,
            "version": self.version,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Attempt":
        root = require_fields(
            value,
            type_name="Attempt",
            required=frozenset({
                "schema_version", "scope_id", "attempt_id", "invocation_ref",
                "thread_ref", "turn_ref", "agent_instance_ref",
                "agent_session_ref", "ordinal", "input_digest",
                "policy_snapshot_ref", "deadline_at", "execution_state",
                "cleanup_state", "worker_id", "principal_id", "selection_ref",
                "fence", "fence_revoked", "lease", "lease_active",
                "active_child_invocation_refs", "active_grant_refs",
                "active_resource_refs", "termination_intent", "terminal_record",
                "cleanup_failure_ref", "cleanup_failure_reason", "version",
            }),
        )
        for name in (
            "invocation_ref", "thread_ref", "turn_ref", "agent_instance_ref",
            "agent_session_ref", "policy_snapshot_ref",
        ):
            if not isinstance(root[name], Mapping):
                raise RuntimeProtocolError(f"{name} 必须是引用对象")
        fence_data = root["fence"]
        if fence_data is not None and not isinstance(fence_data, Mapping):
            raise RuntimeProtocolError("fence 必须是对象或 null")
        lease_data = root["lease"]
        if lease_data is not None and not isinstance(lease_data, Mapping):
            raise RuntimeProtocolError("lease 必须是对象或 null")
        return cls(
            scope_id=root["scope_id"], attempt_id=root["attempt_id"],
            invocation_ref=ScopedRef.from_dict(root["invocation_ref"]),
            thread_ref=ScopedRef.from_dict(root["thread_ref"]),
            turn_ref=ScopedRef.from_dict(root["turn_ref"]),
            agent_instance_ref=ScopedRef.from_dict(root["agent_instance_ref"]),
            agent_session_ref=ScopedRef.from_dict(root["agent_session_ref"]),
            ordinal=root["ordinal"], input_digest=root["input_digest"],
            policy_snapshot_ref=ScopedRef.from_dict(root["policy_snapshot_ref"]),
            deadline_at=root["deadline_at"],
            execution_state=root["execution_state"],
            cleanup_state=root["cleanup_state"], worker_id=root["worker_id"],
            principal_id=root["principal_id"],
            selection_ref=optional_ref_from_dict(
                root["selection_ref"], "selection_ref"
            ), fence=(
                FenceToken.from_dict(fence_data)
                if isinstance(fence_data, Mapping) else None
            ), fence_revoked=root["fence_revoked"], lease=(
                AttemptLease.from_dict(lease_data)
                if isinstance(lease_data, Mapping) else None
            ), lease_active=root["lease_active"],
            active_child_invocation_refs=refs_from_dict(
                root["active_child_invocation_refs"],
                "active_child_invocation_refs",
            ), active_grant_refs=refs_from_dict(
                root["active_grant_refs"], "active_grant_refs"
            ), active_resource_refs=refs_from_dict(
                root["active_resource_refs"], "active_resource_refs"
            ), termination_intent=_optional_nested(
                root["termination_intent"], "termination_intent",
                TerminationIntent,
            ), terminal_record=_optional_nested(
                root["terminal_record"], "terminal_record", TerminalRecord,
            ), cleanup_failure_ref=optional_ref_from_dict(
                root["cleanup_failure_ref"], "cleanup_failure_ref"
            ), cleanup_failure_reason=root["cleanup_failure_reason"],
            version=root["version"], schema_version=root["schema_version"],
        )


def validate_attempt_binding(invocation: Invocation, attempt: Attempt) -> None:
    if not isinstance(invocation, Invocation) or not isinstance(attempt, Attempt):
        raise TypeError("invocation/attempt 类型无效")
    if invocation.scope_id != attempt.scope_id:
        raise ScopeBoundaryError("Attempt 与 Invocation 跨 Scope")
    if not _same_entity(invocation.thread_ref, attempt.thread_ref):
        raise RuntimeProtocolError("Attempt 与 Invocation 不属于同一 Thread")
    if not _same_entity(invocation.turn_ref, attempt.turn_ref):
        raise RuntimeProtocolError("Attempt 与 Invocation 不属于同一 Turn")
    if not _same_entity(invocation.reference, attempt.invocation_ref):
        raise RuntimeProtocolError("Attempt 绑定了错误 Invocation")
    if attempt.invocation_ref.version > invocation.version:
        raise RuntimeProtocolError("Attempt 引用了未来 Invocation 版本")
    if not _same_entity(invocation.agent_instance_ref, attempt.agent_instance_ref):
        raise RuntimeProtocolError("Attempt 绑定了错误 AgentInstance")
    if not _same_entity(invocation.agent_session_ref, attempt.agent_session_ref):
        raise RuntimeProtocolError("Attempt 绑定了错误 AgentSession")
    if invocation.input_digest != attempt.input_digest:
        raise RuntimeProtocolError("Attempt input_digest 与 Invocation 不匹配")
    if invocation.policy_snapshot_ref != attempt.policy_snapshot_ref:
        raise RuntimeProtocolError("Attempt policy_snapshot_ref 与 Invocation 不匹配")
    if _instant(attempt.deadline_at) > _instant(invocation.deadline_at):
        raise RuntimeProtocolError("Attempt deadline 不能越过 Invocation deadline")


def validate_parent_child(parent: Invocation, child: Invocation) -> None:
    if not isinstance(parent, Invocation) or not isinstance(child, Invocation):
        raise TypeError("parent/child 类型无效")
    if parent.scope_id != child.scope_id:
        raise ScopeBoundaryError("Parent/Child Invocation 跨 Scope")
    if not _same_entity(parent.thread_ref, child.thread_ref):
        raise RuntimeProtocolError("Parent/Child Invocation 必须属于同一 Thread")
    if not _same_entity(parent.turn_ref, child.turn_ref):
        raise RuntimeProtocolError("Parent/Child Invocation 必须属于同一 Turn")
    if parent.invocation_id == child.invocation_id:
        raise RuntimeProtocolError("Invocation 不能以自身为 parent/child")
    link = child.parent_invocation_ref
    if link is None or not _same_entity(link, parent.reference):
        raise RuntimeProtocolError("ChildInvocation 缺少正确 parent 引用")
    if link.version > parent.version:
        raise RuntimeProtocolError("ChildInvocation 引用了未来 parent 版本")


def validate_invocation_transition(previous: Invocation, current: Invocation) -> None:
    """Validate both state axes and fencing as one aggregate transition."""

    if not isinstance(previous, Invocation) or not isinstance(current, Invocation):
        raise TypeError("previous/current 必须是 Invocation")
    immutable = (
        "scope_id", "invocation_id", "thread_ref", "turn_ref",
        "agent_instance_ref", "agent_session_ref", "input_refs", "input_digest",
        "policy_snapshot_ref", "budget_reservation_ref", "deadline_at",
        "task_ref", "scenario_run_ref", "route_ref", "parent_invocation_ref",
    )
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise RuntimeProtocolError("Invocation 不可变绑定不得在 transition 中改变")
    if current.version != previous.version + 1:
        raise RuntimeProtocolError("Invocation transition 必须将 version 递增 1")
    if current.execution_state is not previous.execution_state:
        validate_execution_transition(
            previous.execution_state, current.execution_state
        )
    if current.cleanup_state is not previous.cleanup_state:
        validate_cleanup_transition(previous.cleanup_state, current.cleanup_state)
    if (
        previous.termination_intent is not None
        and current.termination_intent != previous.termination_intent
    ):
        raise RuntimeProtocolError("TerminationIntent 一旦记录不得删除或改写")
    if (
        previous.terminal_record is not None
        and current.terminal_record != previous.terminal_record
    ):
        raise RuntimeProtocolError("TerminalRecord 一旦记录不得改写")
    if (
        current.execution_state in TERMINAL_EXECUTION_STATES
        or current.termination_intent is not None
    ):
        _reject_new_active_refs(
            previous,
            current,
            (
                "active_child_invocation_refs",
                "active_grant_refs",
                "active_lease_refs",
                "active_resource_refs",
            ),
        )
    if current.termination_intent is not None:
        if (
            current.active_attempt_ref is not None
            and (
                previous.active_attempt_ref is None
                or not _same_entity(
                    previous.active_attempt_ref, current.active_attempt_ref
                )
            )
        ):
            raise RuntimeProtocolError(
                "TerminationIntent 生效后不得签发新的活动 Attempt"
            )
        if current.fence_generation > previous.fence_generation:
            raise RuntimeProtocolError(
                "TerminationIntent 生效后不得递增 fencing generation"
            )
    if current.fence_generation < previous.fence_generation:
        raise RuntimeProtocolError("fencing generation 不得倒退")
    if current.fence_generation > previous.fence_generation:
        if current.fence_generation != previous.fence_generation + 1:
            raise RuntimeProtocolError("fencing generation 每次只能递增 1")
        if current.active_attempt_ref is None or current.fence_revoked:
            raise RuntimeProtocolError("新 fencing generation 必须签发给活动 Attempt")
        if (
            previous.active_attempt_ref is not None
            and _same_entity(previous.active_attempt_ref, current.active_attempt_ref)
        ):
            raise RuntimeProtocolError("同一 Attempt 不得获取新的 fencing generation")
    else:
        before_attempt = previous.active_attempt_ref
        after_attempt = current.active_attempt_ref
        # Revocation may remove an active Attempt. Updating the observed version
        # of the same active Attempt does not mint a new execution authority.
        if before_attempt is None:
            if after_attempt is not None:
                raise RuntimeProtocolError(
                    "新增活动 Attempt 必须递增 fencing generation"
                )
        elif after_attempt is not None:
            if not _same_entity(before_attempt, after_attempt):
                raise RuntimeProtocolError(
                    "更换活动 Attempt 必须递增 fencing generation"
                )
            if after_attempt.version < before_attempt.version:
                raise RuntimeProtocolError("活动 Attempt 引用版本不得倒退")


def validate_attempt_transition(previous: Attempt, current: Attempt) -> None:
    if not isinstance(previous, Attempt) or not isinstance(current, Attempt):
        raise TypeError("previous/current 必须是 Attempt")
    immutable = (
        "scope_id", "attempt_id", "invocation_ref", "thread_ref", "turn_ref",
        "agent_instance_ref", "agent_session_ref", "ordinal", "input_digest",
        "policy_snapshot_ref", "deadline_at",
    )
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise RuntimeProtocolError("Attempt 不可变绑定不得在 transition 中改变")
    if current.version != previous.version + 1:
        raise RuntimeProtocolError("Attempt transition 必须将 version 递增 1")
    if current.execution_state is not previous.execution_state:
        validate_execution_transition(
            previous.execution_state, current.execution_state
        )
    if current.cleanup_state is not previous.cleanup_state:
        validate_cleanup_transition(previous.cleanup_state, current.cleanup_state)
    for field_name in ("worker_id", "principal_id", "selection_ref"):
        before = getattr(previous, field_name)
        after = getattr(current, field_name)
        if before not in ("", None) and after != before:
            raise RuntimeProtocolError(
                f"Attempt 已绑定的 {field_name} 不得改变"
            )
        if before in ("", None) and after not in ("", None):
            if current.execution_state is not ExecutionState.CLAIMED:
                raise RuntimeProtocolError(
                    f"Attempt {field_name} 只能在进入 claimed 时首次绑定"
                )
    if previous.fence is not None and current.fence != previous.fence:
        raise RuntimeProtocolError("Attempt 已签发的 fence 不得替换")
    if previous.fence is None and current.fence is not None:
        if current.execution_state is not ExecutionState.CLAIMED:
            raise RuntimeProtocolError("Fence 只能在 Attempt claimed 时签发")
    _validate_monotonic_lease(previous, current)
    if (
        previous.termination_intent is not None
        and current.termination_intent != previous.termination_intent
    ):
        raise RuntimeProtocolError("TerminationIntent 一旦记录不得删除或改写")
    if (
        previous.terminal_record is not None
        and current.terminal_record != previous.terminal_record
    ):
        raise RuntimeProtocolError("TerminalRecord 一旦记录不得改写")
    if (
        current.execution_state in TERMINAL_EXECUTION_STATES
        or current.termination_intent is not None
    ):
        _reject_new_active_refs(
            previous,
            current,
            (
                "active_child_invocation_refs",
                "active_grant_refs",
                "active_resource_refs",
            ),
        )
    if current.termination_intent is not None and (
        (not previous.worker_id and bool(current.worker_id))
        or (previous.fence is None and current.fence is not None)
        or (previous.lease is None and current.lease is not None)
    ):
        raise RuntimeProtocolError(
            "TerminationIntent 生效后不得绑定新的 Worker、Fence 或 Lease"
        )


class FencedMutationDecisionCode(str, Enum):
    ACCEPT = "accept"
    DUPLICATE_NOOP = "duplicate_noop"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    SCOPE_MISMATCH = "scope_mismatch"
    THREAD_MISMATCH = "thread_mismatch"
    TURN_MISMATCH = "turn_mismatch"
    INVOCATION_MISMATCH = "invocation_mismatch"
    ATTEMPT_MISMATCH = "attempt_mismatch"
    AGENT_INSTANCE_MISMATCH = "agent_instance_mismatch"
    AGENT_SESSION_MISMATCH = "agent_session_mismatch"
    ATTEMPT_DEADLINE_INVALID = "attempt_deadline_invalid"
    INPUT_DIGEST_MISMATCH = "input_digest_mismatch"
    POLICY_SNAPSHOT_MISMATCH = "policy_snapshot_mismatch"
    TERMINATION_REQUESTED = "termination_requested"
    INVOCATION_TERMINAL = "invocation_terminal"
    ATTEMPT_TERMINAL = "attempt_terminal"
    CLEANUP_IN_PROGRESS = "cleanup_in_progress"
    STALE_FENCE = "stale_fence"
    FUTURE_FENCE = "future_fence"
    FENCE_REVOKED = "fence_revoked"
    NOT_RUNNING = "not_running"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    LEASE_MISSING = "lease_missing"
    LEASE_NOT_YET_VALID = "lease_not_yet_valid"
    LEASE_EXPIRED = "lease_expired"
    SUBMISSION_TIME_INVALID = "submission_time_invalid"


@dataclass(frozen=True)
class FencedMutation:
    mutation_id: str
    mutation_kind: str
    thread_ref: ScopedRef
    fence: FenceToken
    input_digest: str
    policy_snapshot_ref: ScopedRef
    payload_digest: str
    submitted_at: str
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "mutation_id", nonempty(self.mutation_id, "mutation_id")
        )
        object.__setattr__(
            self, "mutation_kind", namespaced(self.mutation_kind, "mutation_kind")
        )
        if not isinstance(self.fence, FenceToken):
            raise RuntimeProtocolError("fence 必须是 FenceToken")
        object.__setattr__(
            self, "thread_ref", _typed_ref(
                self.thread_ref, "thread_ref", scope_id=self.fence.scope_id,
                entity_type="core:thread",
            )
        )
        object.__setattr__(
            self, "input_digest", sha256_digest(self.input_digest, "input_digest")
        )
        object.__setattr__(
            self, "policy_snapshot_ref", _typed_ref(
                self.policy_snapshot_ref, "policy_snapshot_ref",
                scope_id=self.fence.scope_id, entity_type="core:policy_snapshot",
            )
        )
        object.__setattr__(
            self, "payload_digest", sha256_digest(self.payload_digest, "payload_digest")
        )
        object.__setattr__(
            self, "submitted_at", timestamp(self.submitted_at, "submitted_at")
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "FencedMutation"),
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "mutation_id": self.mutation_id,
            "mutation_kind": self.mutation_kind,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "fence": dict(self.fence.to_dict()),
            "input_digest": self.input_digest,
            "policy_snapshot_ref": dict(self.policy_snapshot_ref.to_dict()),
            "payload_digest": self.payload_digest,
            "submitted_at": self.submitted_at,
        })

    @property
    def idempotency_digest(self) -> str:
        """Bind the key to the semantic mutation envelope, not only its payload."""

        return canonical_digest({
            "schema_version": self.schema_version,
            "mutation_kind": self.mutation_kind,
            "thread_ref": dict(self.thread_ref.to_dict()),
            "fence": dict(self.fence.to_dict()),
            "input_digest": self.input_digest,
            "policy_snapshot_ref": dict(self.policy_snapshot_ref.to_dict()),
            "payload_digest": self.payload_digest,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FencedMutation":
        root = require_fields(
            value,
            type_name="FencedMutation",
            required=frozenset({
                "schema_version", "mutation_id", "mutation_kind", "thread_ref",
                "fence", "input_digest", "policy_snapshot_ref",
                "payload_digest", "submitted_at",
            }),
        )
        for name in ("thread_ref", "fence", "policy_snapshot_ref"):
            if not isinstance(root[name], Mapping):
                raise RuntimeProtocolError(f"{name} 必须是对象")
        return cls(
            root["mutation_id"], root["mutation_kind"],
            ScopedRef.from_dict(root["thread_ref"]),
            FenceToken.from_dict(root["fence"]), root["input_digest"],
            ScopedRef.from_dict(root["policy_snapshot_ref"]),
            root["payload_digest"], root["submitted_at"],
            root["schema_version"],
        )


@dataclass(frozen=True)
class FencedMutationDecision:
    code: FencedMutationDecisionCode
    reason: str
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "code", enum_value(self.code, FencedMutationDecisionCode, "code")
        )
        object.__setattr__(self, "reason", nonempty(self.reason, "reason"))
        object.__setattr__(
            self, "schema_version", require_schema_version(
                self.schema_version, "FencedMutationDecision"
            )
        )

    @property
    def may_mutate(self) -> bool:
        return self.code is FencedMutationDecisionCode.ACCEPT

    @property
    def duplicate(self) -> bool:
        return self.code is FencedMutationDecisionCode.DUPLICATE_NOOP

    @property
    def audit_only(self) -> bool:
        return not self.may_mutate

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "code": self.code.value,
            "reason": self.reason,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FencedMutationDecision":
        root = require_fields(
            value,
            type_name="FencedMutationDecision",
            required=frozenset({"schema_version", "code", "reason"}),
        )
        return cls(root["code"], root["reason"], root["schema_version"])


def _decision(
    code: FencedMutationDecisionCode,
    reason: str,
) -> FencedMutationDecision:
    return FencedMutationDecision(code, reason)


def evaluate_fenced_mutation(
    invocation: Invocation,
    attempt: Attempt,
    mutation: FencedMutation,
    *,
    observed_at: str,
    existing_mutation_digest: str = "",
) -> FencedMutationDecision:
    """Pure admission check; it never persists output or advances an aggregate."""

    if not isinstance(invocation, Invocation):
        raise TypeError("invocation 必须是 Invocation")
    if not isinstance(attempt, Attempt):
        raise TypeError("attempt 必须是 Attempt")
    if not isinstance(mutation, FencedMutation):
        raise TypeError("mutation 必须是 FencedMutation")
    observed = timestamp(observed_at, "observed_at")
    existing = optional_string(existing_mutation_digest, "existing_mutation_digest")
    if existing:
        existing = sha256_digest(existing, "existing_mutation_digest")
    fence = mutation.fence

    if attempt.scope_id != invocation.scope_id or fence.scope_id != invocation.scope_id:
        return _decision(
            FencedMutationDecisionCode.SCOPE_MISMATCH,
            "Attempt 或 mutation 跨 Scope",
        )
    if not (
        _same_entity(attempt.thread_ref, invocation.thread_ref)
        and _same_entity(mutation.thread_ref, invocation.thread_ref)
        and _same_entity(fence.thread_ref, invocation.thread_ref)
    ):
        return _decision(
            FencedMutationDecisionCode.THREAD_MISMATCH,
            "Attempt 或 mutation 绑定了错误 Thread",
        )
    if not _same_entity(attempt.turn_ref, invocation.turn_ref):
        return _decision(
            FencedMutationDecisionCode.TURN_MISMATCH,
            "Attempt 绑定了错误 Turn",
        )
    if not (
        _same_entity(attempt.invocation_ref, invocation.reference)
        and _same_entity(fence.invocation_ref, invocation.reference)
    ):
        return _decision(
            FencedMutationDecisionCode.INVOCATION_MISMATCH,
            "Attempt 或 fence 绑定了错误 Invocation",
        )
    if attempt.invocation_ref.version > invocation.version:
        return _decision(
            FencedMutationDecisionCode.INVOCATION_MISMATCH,
            "Attempt 引用了未来 Invocation 版本",
        )
    if not _same_entity(
        attempt.agent_instance_ref, invocation.agent_instance_ref
    ):
        return _decision(
            FencedMutationDecisionCode.AGENT_INSTANCE_MISMATCH,
            "Attempt 绑定了错误 AgentInstance",
        )
    if not _same_entity(
        attempt.agent_session_ref, invocation.agent_session_ref
    ):
        return _decision(
            FencedMutationDecisionCode.AGENT_SESSION_MISMATCH,
            "Attempt 绑定了错误 AgentSession",
        )
    if _instant(attempt.deadline_at) > _instant(invocation.deadline_at):
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_DEADLINE_INVALID,
            "Attempt deadline 越过 Invocation deadline",
        )
    if not _same_entity(fence.attempt_ref, attempt.reference):
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_MISMATCH,
            "fence 绑定了错误 Attempt",
        )
    if (
        attempt.input_digest != invocation.input_digest
        or mutation.input_digest != invocation.input_digest
    ):
        return _decision(
            FencedMutationDecisionCode.INPUT_DIGEST_MISMATCH,
            "mutation 未绑定不可变输入快照",
        )
    if (
        attempt.policy_snapshot_ref != invocation.policy_snapshot_ref
        or mutation.policy_snapshot_ref != invocation.policy_snapshot_ref
    ):
        return _decision(
            FencedMutationDecisionCode.POLICY_SNAPSHOT_MISMATCH,
            "mutation 未绑定当前策略快照",
        )

    # The caller supplies this digest only after looking up this mutation_id.
    # An already accepted exact duplicate stays a no-op after terminalization.
    if existing:
        if existing == mutation.idempotency_digest:
            return _decision(
                FencedMutationDecisionCode.DUPLICATE_NOOP,
                "相同 mutation 已接纳，本次不重复产生副作用",
            )
        return _decision(
            FencedMutationDecisionCode.IDEMPOTENCY_CONFLICT,
            "相同 mutation_id 对应不同 mutation envelope",
        )
    if invocation.termination_intent is not None or attempt.termination_intent is not None:
        return _decision(
            FencedMutationDecisionCode.TERMINATION_REQUESTED,
            "终止意图已生效，拒绝新的结果和副作用",
        )
    if invocation.execution_state in TERMINAL_EXECUTION_STATES:
        return _decision(
            FencedMutationDecisionCode.INVOCATION_TERMINAL,
            "Invocation 已进入执行终态",
        )
    if attempt.execution_state in TERMINAL_EXECUTION_STATES:
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_TERMINAL,
            "Attempt 已进入执行终态",
        )
    if (
        invocation.cleanup_state is not CleanupState.ACTIVE
        or attempt.cleanup_state is not CleanupState.ACTIVE
    ):
        return _decision(
            FencedMutationDecisionCode.CLEANUP_IN_PROGRESS,
            "执行域未处于可接纳 mutation 的 active 状态",
        )
    if fence.generation < invocation.fence_generation:
        return _decision(
            FencedMutationDecisionCode.STALE_FENCE,
            "旧 fencing generation 的迟到 mutation 已被拒绝",
        )
    if fence.generation > invocation.fence_generation:
        return _decision(
            FencedMutationDecisionCode.FUTURE_FENCE,
            "Worker 提交了尚未由 Runtime 签发的 fencing generation",
        )
    active = invocation.active_attempt_ref
    if (
        active is None
        or not _same_entity(active, attempt.reference)
        or active.version != attempt.version
    ):
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_MISMATCH,
            "Attempt 不是 Invocation 当前活动执行单元",
        )
    if attempt.fence is None or attempt.fence != fence:
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_MISMATCH,
            "mutation fence 与 Attempt 当前 fence 不匹配",
        )
    if invocation.fence_revoked or attempt.fence_revoked:
        return _decision(
            FencedMutationDecisionCode.FENCE_REVOKED,
            "fence 已撤销",
        )
    if (
        invocation.execution_state is not ExecutionState.RUNNING
        or attempt.execution_state is not ExecutionState.RUNNING
    ):
        return _decision(
            FencedMutationDecisionCode.NOT_RUNNING,
            "Invocation 与 Attempt 必须同时处于 running",
        )
    observed_instant = _instant(observed)
    submitted_instant = _instant(mutation.submitted_at)
    if submitted_instant > observed_instant:
        return _decision(
            FencedMutationDecisionCode.SUBMISSION_TIME_INVALID,
            "mutation submitted_at 不能晚于 Runtime observed_at",
        )
    if observed_instant >= _instant(invocation.deadline_at) or observed_instant >= _instant(
        attempt.deadline_at
    ):
        return _decision(
            FencedMutationDecisionCode.DEADLINE_EXCEEDED,
            "mutation 到达时已超过执行 deadline",
        )
    if attempt.lease is None or not attempt.lease_active:
        return _decision(
            FencedMutationDecisionCode.LEASE_MISSING,
            "Attempt 没有活动 Lease",
        )
    if not any(
        item == attempt.lease.lease_ref for item in invocation.active_lease_refs
    ):
        return _decision(
            FencedMutationDecisionCode.LEASE_MISSING,
            "Invocation 未登记 Attempt 的活动 Lease",
        )
    if attempt.lease.fence != fence:
        return _decision(
            FencedMutationDecisionCode.ATTEMPT_MISMATCH,
            "Lease 与 mutation fence 不匹配",
        )
    if (
        observed_instant < _instant(attempt.lease.acquired_at)
        or submitted_instant < _instant(attempt.lease.acquired_at)
    ):
        return _decision(
            FencedMutationDecisionCode.LEASE_NOT_YET_VALID,
            "mutation 早于 Lease 生效时间",
        )
    if observed_instant >= _instant(attempt.lease.expires_at):
        return _decision(
            FencedMutationDecisionCode.LEASE_EXPIRED,
            "mutation 到达时 Lease 已过期",
        )
    return _decision(
        FencedMutationDecisionCode.ACCEPT,
        "Attempt、Lease、fence、输入与策略绑定均有效",
    )


InvocationExecutionState = ExecutionState
AttemptExecutionState = ExecutionState
InvocationCleanupState = CleanupState
AttemptCleanupState = CleanupState
