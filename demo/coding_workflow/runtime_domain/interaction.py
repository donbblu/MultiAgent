from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Mapping

from .common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
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
    string_tuple,
    timestamp,
)


class ScopeState(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ThreadState(str, Enum):
    OPEN = "open"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TurnState(str, Enum):
    OPEN = "open"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class AgentSessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


def _ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_types: tuple[str, ...],
) -> ScopedRef:
    if not isinstance(value, ScopedRef):
        raise RuntimeProtocolError(f"{field_name} 必须是 ScopedRef")
    value.assert_scope(scope_id, field_name)
    value.assert_type(*entity_types)
    return value


def _optional_ref(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    entity_types: tuple[str, ...],
) -> ScopedRef | None:
    if value is None:
        return None
    return _ref(
        value,
        field_name,
        scope_id=scope_id,
        entity_types=entity_types,
    )


def _time_fields(
    *,
    created_at: object,
    updated_at: object,
    terminal_at: object,
    terminal: bool,
    terminal_name: str,
) -> tuple[str, str, str]:
    created = timestamp(created_at, "created_at", default_now=True)
    updated_raw = optional_string(updated_at, "updated_at")
    updated = timestamp(updated_raw, "updated_at") if updated_raw else created
    ended = optional_timestamp(terminal_at, terminal_name)
    if terminal and not ended:
        raise RuntimeProtocolError(f"终态必须包含 {terminal_name}")
    if not terminal and ended:
        raise RuntimeProtocolError(f"非终态不能包含 {terminal_name}")
    created_instant = datetime.fromisoformat(created)
    updated_instant = datetime.fromisoformat(updated)
    if updated_instant < created_instant:
        raise RuntimeProtocolError("updated_at 不能早于 created_at")
    if ended:
        ended_instant = datetime.fromisoformat(ended)
        if ended_instant < created_instant:
            raise RuntimeProtocolError(f"{terminal_name} 不能早于 created_at")
        if ended_instant > updated_instant:
            raise RuntimeProtocolError(f"{terminal_name} 不能晚于 updated_at")
    return created, updated, ended


def _record_dict(**values: object) -> Mapping[str, object]:
    return MappingProxyType(values)


@dataclass(frozen=True)
class Scope:
    scope_id: str
    name: str
    state: ScopeState = ScopeState.ACTIVE
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    archived_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:scope"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        state = enum_value(self.state, ScopeState, "state")
        created, updated, archived = _time_fields(
            created_at=self.created_at,
            updated_at=self.updated_at,
            terminal_at=self.archived_at,
            terminal=state is ScopeState.ARCHIVED,
            terminal_name="archived_at",
        )
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "name", nonempty(self.name, "name"))
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "archived_at", archived)
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Scope"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.scope_id, self.version
        )

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            scope_id=self.scope_id,
            name=self.name,
            state=self.state.value,
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            archived_at=self.archived_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Scope":
        root = require_fields(
            value,
            type_name="Scope",
            required=frozenset({
                "schema_version", "scope_id", "name", "state", "version",
                "created_at", "updated_at", "archived_at",
            }),
        )
        return cls(
            scope_id=root["scope_id"],
            name=root["name"],
            state=root["state"],
            version=root["version"],
            created_at=root["created_at"],
            updated_at=root["updated_at"],
            archived_at=root["archived_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class Thread:
    thread_id: str
    scope_id: str
    title: str
    participant_refs: tuple[ScopedRef, ...]
    state: ThreadState = ThreadState.OPEN
    policy_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    archived_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:thread"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        state = enum_value(self.state, ThreadState, "state")
        created, updated, archived = _time_fields(
            created_at=self.created_at,
            updated_at=self.updated_at,
            terminal_at=self.archived_at,
            terminal=state is ThreadState.ARCHIVED,
            terminal_name="archived_at",
        )
        object.__setattr__(self, "thread_id", nonempty(self.thread_id, "thread_id"))
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "title", nonempty(self.title, "title"))
        object.__setattr__(
            self,
            "participant_refs",
            scoped_refs(
                self.participant_refs,
                "participant_refs",
                scope_id=scope_id,
                allow_empty=False,
                entity_types=(
                    "core:principal", "core:runtime_principal",
                    "core:agent_instance",
                ),
            ),
        )
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "policy_ref",
            _optional_ref(
                self.policy_ref,
                "policy_ref",
                scope_id=scope_id,
                entity_types=("core:thread_policy",),
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "archived_at", archived)
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Thread"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.thread_id, self.version
        )

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            thread_id=self.thread_id,
            scope_id=self.scope_id,
            title=self.title,
            participant_refs=refs_to_dict(self.participant_refs),
            state=self.state.value,
            policy_ref=optional_ref_to_dict(self.policy_ref),
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            archived_at=self.archived_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Thread":
        root = require_fields(
            value,
            type_name="Thread",
            required=frozenset({
                "schema_version", "thread_id", "scope_id", "title",
                "participant_refs", "state", "policy_ref", "version",
                "created_at", "updated_at", "archived_at",
            }),
        )
        return cls(
            thread_id=root["thread_id"],
            scope_id=root["scope_id"],
            title=root["title"],
            participant_refs=refs_from_dict(
                root["participant_refs"], "participant_refs"
            ),
            state=root["state"],
            policy_ref=optional_ref_from_dict(root["policy_ref"], "policy_ref"),
            version=root["version"],
            created_at=root["created_at"],
            updated_at=root["updated_at"],
            archived_at=root["archived_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class Turn:
    turn_id: str
    scope_id: str
    thread_ref: ScopedRef
    trigger_ref: ScopedRef
    state: TurnState = TurnState.OPEN
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:turn"
    _TERMINAL: ClassVar[frozenset[TurnState]] = frozenset({
        TurnState.CLOSED, TurnState.CANCELLED,
    })

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        state = enum_value(self.state, TurnState, "state")
        created, updated, closed = _time_fields(
            created_at=self.created_at,
            updated_at=self.updated_at,
            terminal_at=self.closed_at,
            terminal=state in self._TERMINAL,
            terminal_name="closed_at",
        )
        object.__setattr__(self, "turn_id", nonempty(self.turn_id, "turn_id"))
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "thread_ref",
            _ref(
                self.thread_ref,
                "thread_ref",
                scope_id=scope_id,
                entity_types=(Thread.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self,
            "trigger_ref",
            _ref(
                self.trigger_ref,
                "trigger_ref",
                scope_id=scope_id,
                entity_types=("core:message", "core:runtime_event"),
            ),
        )
        if (
            self.trigger_ref.entity_type == "core:runtime_event"
            and self.trigger_ref.version != 1
        ):
            raise RuntimeProtocolError(
                "trigger_ref 引用的 RuntimeEvent version 必须为 1"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "closed_at", closed)
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Turn"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.turn_id, self.version
        )

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            turn_id=self.turn_id,
            scope_id=self.scope_id,
            thread_ref=dict(self.thread_ref.to_dict()),
            trigger_ref=dict(self.trigger_ref.to_dict()),
            state=self.state.value,
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            closed_at=self.closed_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Turn":
        root = require_fields(
            value,
            type_name="Turn",
            required=frozenset({
                "schema_version", "turn_id", "scope_id", "thread_ref",
                "trigger_ref", "state", "version", "created_at",
                "updated_at", "closed_at",
            }),
        )
        thread = root["thread_ref"]
        trigger = root["trigger_ref"]
        if not isinstance(thread, Mapping):
            raise RuntimeProtocolError("thread_ref 必须是引用对象")
        if not isinstance(trigger, Mapping):
            raise RuntimeProtocolError("trigger_ref 必须是引用对象")
        return cls(
            turn_id=root["turn_id"],
            scope_id=root["scope_id"],
            thread_ref=ScopedRef.from_dict(thread),
            trigger_ref=ScopedRef.from_dict(trigger),
            state=root["state"],
            version=root["version"],
            created_at=root["created_at"],
            updated_at=root["updated_at"],
            closed_at=root["closed_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class Message:
    message_id: str
    scope_id: str
    thread_ref: ScopedRef
    turn_ref: ScopedRef
    sequence: int
    sender_ref: ScopedRef
    recipient_refs: tuple[ScopedRef, ...]
    kind: str
    body: str = ""
    artifact_refs: tuple[ScopedRef, ...] = ()
    parent_ref: ScopedRef | None = None
    causation_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:message"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        message_id = nonempty(self.message_id, "message_id")
        if not isinstance(self.body, str):
            raise RuntimeProtocolError("body 必须是字符串")
        body = self.body
        artifacts = scoped_refs(
            self.artifact_refs,
            "artifact_refs",
            scope_id=scope_id,
            entity_types=("core:artifact",),
        )
        if not body.strip() and not artifacts:
            raise RuntimeProtocolError("Message 必须包含正文或 Artifact 引用")
        parent = _optional_ref(
            self.parent_ref,
            "parent_ref",
            scope_id=scope_id,
            entity_types=("core:message",),
        )
        causation = _optional_ref(
            self.causation_ref,
            "causation_ref",
            scope_id=scope_id,
            entity_types=(
                "core:message", "core:runtime_event", "core:invocation", "core:turn",
            ),
        )
        if (
            causation is not None
            and causation.entity_type == "core:runtime_event"
            and causation.version != 1
        ):
            raise RuntimeProtocolError(
                "causation_ref 引用的 RuntimeEvent version 必须为 1"
            )
        for field_name, reference in (
            ("parent_ref", parent), ("causation_ref", causation),
        ):
            if (
                reference is not None
                and reference.entity_type == self.REFERENCE_TYPE
                and reference.entity_id == message_id
            ):
                raise RuntimeProtocolError(f"Message 不能把自身作为 {field_name}")
        object.__setattr__(self, "message_id", message_id)
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "thread_ref",
            _ref(
                self.thread_ref,
                "thread_ref",
                scope_id=scope_id,
                entity_types=(Thread.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self,
            "turn_ref",
            _ref(
                self.turn_ref,
                "turn_ref",
                scope_id=scope_id,
                entity_types=(Turn.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self, "sequence", positive_int(self.sequence, "sequence")
        )
        object.__setattr__(
            self,
            "sender_ref",
            _ref(
                self.sender_ref,
                "sender_ref",
                scope_id=scope_id,
                entity_types=(
                    "core:principal", "core:runtime_principal",
                    "core:agent_instance",
                ),
            ),
        )
        object.__setattr__(
            self,
            "recipient_refs",
            scoped_refs(
                self.recipient_refs,
                "recipient_refs",
                scope_id=scope_id,
                allow_empty=False,
                entity_types=(
                    "core:principal", "core:runtime_principal",
                    "core:agent_instance",
                ),
            ),
        )
        object.__setattr__(self, "kind", namespaced(self.kind, "kind"))
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "artifact_refs", artifacts)
        object.__setattr__(self, "parent_ref", parent)
        object.__setattr__(self, "causation_ref", causation)
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "created_at",
            timestamp(self.created_at, "created_at", default_now=True),
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "Message"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.message_id, self.version
        )

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    @property
    def turn_id(self) -> str:
        return self.turn_ref.entity_id

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            message_id=self.message_id,
            scope_id=self.scope_id,
            thread_ref=dict(self.thread_ref.to_dict()),
            turn_ref=dict(self.turn_ref.to_dict()),
            sequence=self.sequence,
            sender_ref=dict(self.sender_ref.to_dict()),
            recipient_refs=refs_to_dict(self.recipient_refs),
            kind=self.kind,
            body=self.body,
            artifact_refs=refs_to_dict(self.artifact_refs),
            parent_ref=optional_ref_to_dict(self.parent_ref),
            causation_ref=optional_ref_to_dict(self.causation_ref),
            version=self.version,
            created_at=self.created_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Message":
        root = require_fields(
            value,
            type_name="Message",
            required=frozenset({
                "schema_version", "message_id", "scope_id", "thread_ref",
                "turn_ref", "sequence", "sender_ref", "recipient_refs", "kind",
                "body", "artifact_refs", "parent_ref", "causation_ref", "version",
                "created_at",
            }),
        )
        thread = root["thread_ref"]
        turn = root["turn_ref"]
        sender = root["sender_ref"]
        if not isinstance(thread, Mapping):
            raise RuntimeProtocolError("thread_ref 必须是引用对象")
        if not isinstance(turn, Mapping):
            raise RuntimeProtocolError("turn_ref 必须是引用对象")
        if not isinstance(sender, Mapping):
            raise RuntimeProtocolError("sender_ref 必须是引用对象")
        return cls(
            message_id=root["message_id"],
            scope_id=root["scope_id"],
            thread_ref=ScopedRef.from_dict(thread),
            turn_ref=ScopedRef.from_dict(turn),
            sequence=root["sequence"],
            sender_ref=ScopedRef.from_dict(sender),
            recipient_refs=refs_from_dict(root["recipient_refs"], "recipient_refs"),
            kind=root["kind"],
            body=root["body"],
            artifact_refs=refs_from_dict(root["artifact_refs"], "artifact_refs"),
            parent_ref=optional_ref_from_dict(root["parent_ref"], "parent_ref"),
            causation_ref=optional_ref_from_dict(
                root["causation_ref"], "causation_ref"
            ),
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    scope_id: str
    objective: str
    responsibilities: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    capability_ceiling: tuple[str, ...] = ()
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:agent_role"

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", namespaced(self.role_id, "role_id"))
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(
            self, "objective", nonempty(self.objective, "objective")
        )
        object.__setattr__(
            self,
            "responsibilities",
            string_tuple(
                self.responsibilities,
                "responsibilities",
                allow_empty=False,
            ),
        )
        object.__setattr__(
            self,
            "constraints",
            string_tuple(self.constraints, "constraints"),
        )
        object.__setattr__(
            self,
            "capability_ceiling",
            string_tuple(
                self.capability_ceiling,
                "capability_ceiling",
                require_namespaced=True,
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "created_at",
            timestamp(self.created_at, "created_at", default_now=True),
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "AgentRole"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.role_id, self.version
        )

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            role_id=self.role_id,
            scope_id=self.scope_id,
            objective=self.objective,
            responsibilities=list(self.responsibilities),
            constraints=list(self.constraints),
            capability_ceiling=list(self.capability_ceiling),
            version=self.version,
            created_at=self.created_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentRole":
        root = require_fields(
            value,
            type_name="AgentRole",
            required=frozenset({
                "schema_version", "role_id", "scope_id", "objective",
                "responsibilities", "constraints", "capability_ceiling",
                "version", "created_at",
            }),
        )
        return cls(
            role_id=root["role_id"],
            scope_id=root["scope_id"],
            objective=root["objective"],
            responsibilities=root["responsibilities"],
            constraints=root["constraints"],
            capability_ceiling=root["capability_ceiling"],
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    scope_id: str
    role_ref: ScopedRef
    backend_policy_ref: ScopedRef | None = None
    tool_policy_ref: ScopedRef | None = None
    context_policy_ref: ScopedRef | None = None
    output_contract_ref: ScopedRef | None = None
    budget_policy_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:agent_profile"

    _OPTIONAL_REF_TYPES: ClassVar[Mapping[str, str]] = MappingProxyType({
        "backend_policy_ref": "core:backend_policy",
        "tool_policy_ref": "core:tool_policy",
        "context_policy_ref": "core:context_policy",
        "output_contract_ref": "core:output_contract",
        "budget_policy_ref": "core:budget_policy",
    })

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(
            self, "profile_id", nonempty(self.profile_id, "profile_id")
        )
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "role_ref",
            _ref(
                self.role_ref,
                "role_ref",
                scope_id=scope_id,
                entity_types=(AgentRole.REFERENCE_TYPE,),
            ),
        )
        for field_name, entity_type in self._OPTIONAL_REF_TYPES.items():
            object.__setattr__(
                self,
                field_name,
                _optional_ref(
                    getattr(self, field_name),
                    field_name,
                    scope_id=scope_id,
                    entity_types=(entity_type,),
                ),
            )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "created_at",
            timestamp(self.created_at, "created_at", default_now=True),
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "AgentProfile"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, self.REFERENCE_TYPE, self.profile_id, self.version
        )

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            profile_id=self.profile_id,
            scope_id=self.scope_id,
            role_ref=dict(self.role_ref.to_dict()),
            backend_policy_ref=optional_ref_to_dict(self.backend_policy_ref),
            tool_policy_ref=optional_ref_to_dict(self.tool_policy_ref),
            context_policy_ref=optional_ref_to_dict(self.context_policy_ref),
            output_contract_ref=optional_ref_to_dict(self.output_contract_ref),
            budget_policy_ref=optional_ref_to_dict(self.budget_policy_ref),
            version=self.version,
            created_at=self.created_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentProfile":
        root = require_fields(
            value,
            type_name="AgentProfile",
            required=frozenset({
                "schema_version", "profile_id", "scope_id", "role_ref",
                "backend_policy_ref", "tool_policy_ref", "context_policy_ref",
                "output_contract_ref", "budget_policy_ref", "version", "created_at",
            }),
        )
        role = root["role_ref"]
        if not isinstance(role, Mapping):
            raise RuntimeProtocolError("role_ref 必须是引用对象")
        return cls(
            profile_id=root["profile_id"],
            scope_id=root["scope_id"],
            role_ref=ScopedRef.from_dict(role),
            backend_policy_ref=optional_ref_from_dict(
                root["backend_policy_ref"], "backend_policy_ref"
            ),
            tool_policy_ref=optional_ref_from_dict(
                root["tool_policy_ref"], "tool_policy_ref"
            ),
            context_policy_ref=optional_ref_from_dict(
                root["context_policy_ref"], "context_policy_ref"
            ),
            output_contract_ref=optional_ref_from_dict(
                root["output_contract_ref"], "output_contract_ref"
            ),
            budget_policy_ref=optional_ref_from_dict(
                root["budget_policy_ref"], "budget_policy_ref"
            ),
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class AgentInstance:
    agent_instance_id: str
    scope_id: str
    thread_ref: ScopedRef
    profile_ref: ScopedRef
    principal_id: str
    mailbox_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:agent_instance"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        object.__setattr__(
            self,
            "agent_instance_id",
            nonempty(self.agent_instance_id, "agent_instance_id"),
        )
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "thread_ref",
            _ref(
                self.thread_ref,
                "thread_ref",
                scope_id=scope_id,
                entity_types=(Thread.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self,
            "profile_ref",
            _ref(
                self.profile_ref,
                "profile_ref",
                scope_id=scope_id,
                entity_types=(AgentProfile.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self, "principal_id", nonempty(self.principal_id, "principal_id")
        )
        object.__setattr__(
            self,
            "mailbox_ref",
            _optional_ref(
                self.mailbox_ref,
                "mailbox_ref",
                scope_id=scope_id,
                entity_types=("core:mailbox",),
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(
            self,
            "created_at",
            timestamp(self.created_at, "created_at", default_now=True),
        )
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "AgentInstance"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id,
            self.REFERENCE_TYPE,
            self.agent_instance_id,
            self.version,
        )

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            agent_instance_id=self.agent_instance_id,
            scope_id=self.scope_id,
            thread_ref=dict(self.thread_ref.to_dict()),
            profile_ref=dict(self.profile_ref.to_dict()),
            principal_id=self.principal_id,
            mailbox_ref=optional_ref_to_dict(self.mailbox_ref),
            version=self.version,
            created_at=self.created_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentInstance":
        root = require_fields(
            value,
            type_name="AgentInstance",
            required=frozenset({
                "schema_version", "agent_instance_id", "scope_id", "thread_ref",
                "profile_ref", "principal_id", "mailbox_ref", "version",
                "created_at",
            }),
        )
        thread = root["thread_ref"]
        profile = root["profile_ref"]
        if not isinstance(thread, Mapping):
            raise RuntimeProtocolError("thread_ref 必须是引用对象")
        if not isinstance(profile, Mapping):
            raise RuntimeProtocolError("profile_ref 必须是引用对象")
        return cls(
            agent_instance_id=root["agent_instance_id"],
            scope_id=root["scope_id"],
            thread_ref=ScopedRef.from_dict(thread),
            profile_ref=ScopedRef.from_dict(profile),
            principal_id=root["principal_id"],
            mailbox_ref=optional_ref_from_dict(root["mailbox_ref"], "mailbox_ref"),
            version=root["version"],
            created_at=root["created_at"],
            schema_version=root["schema_version"],
        )


@dataclass(frozen=True)
class AgentSession:
    agent_session_id: str
    scope_id: str
    thread_ref: ScopedRef
    agent_instance_ref: ScopedRef
    state: AgentSessionState = AgentSessionState.ACTIVE
    event_cursor: int = 0
    summary_ref: ScopedRef | None = None
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    REFERENCE_TYPE: ClassVar[str] = "core:agent_session"

    def __post_init__(self) -> None:
        scope_id = nonempty(self.scope_id, "scope_id")
        state = enum_value(self.state, AgentSessionState, "state")
        created, updated, closed = _time_fields(
            created_at=self.created_at,
            updated_at=self.updated_at,
            terminal_at=self.closed_at,
            terminal=state is AgentSessionState.CLOSED,
            terminal_name="closed_at",
        )
        object.__setattr__(
            self,
            "agent_session_id",
            nonempty(self.agent_session_id, "agent_session_id"),
        )
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(
            self,
            "thread_ref",
            _ref(
                self.thread_ref,
                "thread_ref",
                scope_id=scope_id,
                entity_types=(Thread.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(
            self,
            "agent_instance_ref",
            _ref(
                self.agent_instance_ref,
                "agent_instance_ref",
                scope_id=scope_id,
                entity_types=(AgentInstance.REFERENCE_TYPE,),
            ),
        )
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "event_cursor",
            positive_int(self.event_cursor, "event_cursor", allow_zero=True),
        )
        object.__setattr__(
            self,
            "summary_ref",
            _optional_ref(
                self.summary_ref,
                "summary_ref",
                scope_id=scope_id,
                entity_types=("core:artifact",),
            ),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "closed_at", closed)
        object.__setattr__(
            self,
            "schema_version",
            require_schema_version(self.schema_version, "AgentSession"),
        )

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id,
            self.REFERENCE_TYPE,
            self.agent_session_id,
            self.version,
        )

    @property
    def thread_id(self) -> str:
        return self.thread_ref.entity_id

    @property
    def agent_instance_id(self) -> str:
        return self.agent_instance_ref.entity_id

    def to_dict(self) -> Mapping[str, object]:
        return _record_dict(
            schema_version=self.schema_version,
            agent_session_id=self.agent_session_id,
            scope_id=self.scope_id,
            thread_ref=dict(self.thread_ref.to_dict()),
            agent_instance_ref=dict(self.agent_instance_ref.to_dict()),
            state=self.state.value,
            event_cursor=self.event_cursor,
            summary_ref=optional_ref_to_dict(self.summary_ref),
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            closed_at=self.closed_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AgentSession":
        root = require_fields(
            value,
            type_name="AgentSession",
            required=frozenset({
                "schema_version", "agent_session_id", "scope_id", "thread_ref",
                "agent_instance_ref", "state", "event_cursor", "summary_ref",
                "version", "created_at", "updated_at", "closed_at",
            }),
        )
        thread = root["thread_ref"]
        instance = root["agent_instance_ref"]
        if not isinstance(thread, Mapping):
            raise RuntimeProtocolError("thread_ref 必须是引用对象")
        if not isinstance(instance, Mapping):
            raise RuntimeProtocolError("agent_instance_ref 必须是引用对象")
        return cls(
            agent_session_id=root["agent_session_id"],
            scope_id=root["scope_id"],
            thread_ref=ScopedRef.from_dict(thread),
            agent_instance_ref=ScopedRef.from_dict(instance),
            state=root["state"],
            event_cursor=root["event_cursor"],
            summary_ref=optional_ref_from_dict(root["summary_ref"], "summary_ref"),
            version=root["version"],
            created_at=root["created_at"],
            updated_at=root["updated_at"],
            closed_at=root["closed_at"],
            schema_version=root["schema_version"],
        )


def _same_ref_entity(left: ScopedRef, right: ScopedRef) -> bool:
    return (
        left.scope_id == right.scope_id
        and left.entity_type == right.entity_type
        and left.entity_id == right.entity_id
    )


def _assert_records_same_scope(*records: object) -> str:
    scopes = {getattr(item, "scope_id", None) for item in records}
    if None in scopes or len(scopes) != 1:
        raise ScopeBoundaryError("交互关系跨 Scope")
    return next(iter(scopes))


def validate_turn_binding(thread: Thread, turn: Turn) -> None:
    if not isinstance(thread, Thread) or not isinstance(turn, Turn):
        raise TypeError("thread/turn 类型无效")
    _assert_records_same_scope(thread, turn)
    if not _same_ref_entity(turn.thread_ref, thread.reference):
        raise RuntimeProtocolError("Turn 绑定了错误 Thread")
    if turn.thread_ref.version > thread.version:
        raise RuntimeProtocolError("Turn 引用了未来 Thread 版本")


def validate_message_binding(
    thread: Thread,
    turn: Turn,
    message: Message,
    *,
    parent: Message | None = None,
) -> None:
    if not isinstance(message, Message):
        raise TypeError("message 类型无效")
    validate_turn_binding(thread, turn)
    _assert_records_same_scope(thread, turn, message)
    if not _same_ref_entity(message.thread_ref, thread.reference):
        raise RuntimeProtocolError("Message 绑定了错误 Thread")
    if message.thread_ref.version > thread.version:
        raise RuntimeProtocolError("Message 引用了未来 Thread 版本")
    if not _same_ref_entity(message.turn_ref, turn.reference):
        raise RuntimeProtocolError("Message 绑定了错误 Turn")
    if message.turn_ref.version > turn.version:
        raise RuntimeProtocolError("Message 引用了未来 Turn 版本")
    if message.parent_ref is None:
        if parent is not None:
            raise RuntimeProtocolError("Message 没有 parent_ref")
        return
    if parent is None:
        raise RuntimeProtocolError("必须解析 parent_ref 后校验 Message")
    if not isinstance(parent, Message):
        raise TypeError("parent 类型无效")
    _assert_records_same_scope(message, parent)
    if not _same_ref_entity(message.parent_ref, parent.reference):
        raise RuntimeProtocolError("Message 绑定了错误 parent")
    if message.parent_ref.version > parent.version:
        raise RuntimeProtocolError("Message 引用了未来 parent 版本")
    if not _same_ref_entity(parent.thread_ref, thread.reference):
        raise RuntimeProtocolError("Message parent 来自其他 Thread")
    if parent.sequence >= message.sequence:
        raise RuntimeProtocolError("Message parent sequence 必须更早")


def validate_agent_instance_binding(
    thread: Thread,
    instance: AgentInstance,
) -> None:
    if not isinstance(thread, Thread) or not isinstance(instance, AgentInstance):
        raise TypeError("thread/instance 类型无效")
    _assert_records_same_scope(thread, instance)
    if not _same_ref_entity(instance.thread_ref, thread.reference):
        raise RuntimeProtocolError("AgentInstance 绑定了错误 Thread")
    if instance.thread_ref.version > thread.version:
        raise RuntimeProtocolError("AgentInstance 引用了未来 Thread 版本")


def validate_agent_session_binding(
    thread: Thread,
    instance: AgentInstance,
    session: AgentSession,
) -> None:
    if not isinstance(session, AgentSession):
        raise TypeError("session 类型无效")
    validate_agent_instance_binding(thread, instance)
    _assert_records_same_scope(thread, instance, session)
    if not _same_ref_entity(session.thread_ref, thread.reference):
        raise RuntimeProtocolError("AgentSession 绑定了错误 Thread")
    if session.thread_ref.version > thread.version:
        raise RuntimeProtocolError("AgentSession 引用了未来 Thread 版本")
    if not _same_ref_entity(session.agent_instance_ref, instance.reference):
        raise RuntimeProtocolError("AgentSession 绑定了错误 AgentInstance")
    if session.agent_instance_ref.version > instance.version:
        raise RuntimeProtocolError("AgentSession 引用了未来 AgentInstance 版本")


__all__ = [
    "AgentInstance",
    "AgentProfile",
    "AgentRole",
    "AgentSession",
    "AgentSessionState",
    "Message",
    "Scope",
    "ScopeState",
    "Thread",
    "ThreadState",
    "Turn",
    "TurnState",
    "validate_agent_instance_binding",
    "validate_agent_session_binding",
    "validate_message_binding",
    "validate_turn_binding",
]
