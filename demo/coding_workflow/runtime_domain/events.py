from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .common import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeProtocolError,
    ScopedRef,
    enum_value,
    freeze_json,
    namespaced,
    nonempty,
    optional_ref_from_dict,
    optional_ref_to_dict,
    positive_int,
    refs_from_dict,
    refs_to_dict,
    require_fields,
    require_schema_version,
    scoped_refs,
    thaw_json,
    timestamp,
)


class RuntimeActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    RUNTIME = "runtime"
    WORKER = "worker"
    TOOL = "tool"
    SYSTEM = "system"


MAX_EVENT_PAYLOAD_BYTES = 16_384
_FORBIDDEN_PAYLOAD_FIELDS = frozenset({
    "body",
    "content",
    "raw_content",
    "raw_media",
    "media_bytes",
    "prompt",
    "completion",
    "reasoning",
    "chain_of_thought",
    "thoughts",
    "access_token",
    "refresh_token",
    "api_key",
    "authorization",
    "password",
    "secret",
    "client_secret",
    "private_key",
    "credential",
    "cookie",
    "set_cookie",
})
_FORBIDDEN_PAYLOAD_SUFFIXES = (
    "_body",
    "_content",
    "_media",
    "_bytes",
    "_prompt",
    "_completion",
    "_reasoning",
    "_thoughts",
    "_access_token",
    "_refresh_token",
    "_api_key",
    "_password",
    "_secret",
    "_credential",
    "_private_key",
    "_ref",
    "_refs",
)
_ACTOR_REF_TYPES: Mapping[RuntimeActorType, tuple[str, ...]] = MappingProxyType({
    RuntimeActorType.USER: ("core:user", "core:principal"),
    RuntimeActorType.AGENT: ("core:agent_instance", "core:principal"),
    RuntimeActorType.RUNTIME: ("core:runtime", "core:runtime_principal"),
    RuntimeActorType.WORKER: ("core:worker", "core:worker_principal"),
    RuntimeActorType.TOOL: ("core:tool", "core:tool_principal"),
    RuntimeActorType.SYSTEM: ("core:system", "core:runtime_principal"),
})
_SCOPED_REF_FIELDS = frozenset({
    "scope_id", "entity_type", "entity_id", "version",
})
_EVENT_PAYLOAD_FIELD_TYPES: Mapping[str, type] = MappingProxyType({
    "state": str,
    "status": str,
    "delivery_state": str,
    "execution_state": str,
    "cleanup_state": str,
    "previous_state": str,
    "next_state": str,
    "outcome": str,
    "disposition": str,
    "reason_code": str,
    "error_code": str,
    "severity": str,
    "phase": str,
    "category": str,
    "operation": str,
    "mutation_kind": str,
    "result_code": str,
    "attempt_number": int,
    "retry_count": int,
    "duration_ms": int,
    "token_count": int,
    "resource_count": int,
    "byte_count": int,
    "retryable": bool,
    "terminal": bool,
    "recovered": bool,
    "duplicate": bool,
    "late": bool,
})
_EVENT_METADATA_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


def _forbidden_payload_field(value: object) -> str:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = key.lower()
            if (
                normalized_key in _FORBIDDEN_PAYLOAD_FIELDS
                or normalized_key.endswith(_FORBIDDEN_PAYLOAD_SUFFIXES)
            ):
                return key
            found = _forbidden_payload_field(item)
            if found:
                return found
    elif isinstance(value, tuple):
        for item in value:
            found = _forbidden_payload_field(item)
            if found:
                return found
    return ""


def _embedded_scoped_ref_path(value: object, path: str = "payload") -> str:
    if isinstance(value, Mapping):
        if _SCOPED_REF_FIELDS.issubset(value.keys()):
            return path
        for key, item in value.items():
            found = _embedded_scoped_ref_path(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            found = _embedded_scoped_ref_path(item, f"{path}[{index}]")
            if found:
                return found
    return ""


def _require_append_only_event_ref(reference: ScopedRef, field_name: str) -> None:
    if reference.entity_type == "core:runtime_event" and reference.version != 1:
        raise RuntimeProtocolError(
            f"{field_name} 引用的 RuntimeEvent version 必须为 1"
        )


def _validate_payload_metadata(value: Mapping[str, object]) -> None:
    for key, item in value.items():
        expected_type = _EVENT_PAYLOAD_FIELD_TYPES.get(key)
        if expected_type is None:
            raise RuntimeProtocolError(
                f"RuntimeEvent payload 字段未在 v1 元数据协议中: {key}"
            )
        if type(item) is not expected_type:
            raise RuntimeProtocolError(
                f"RuntimeEvent payload 字段类型无效: {key}"
            )
        if isinstance(item, str) and not _EVENT_METADATA_CODE.fullmatch(item):
            raise RuntimeProtocolError(
                f"RuntimeEvent payload 字符串只能是短代码，正文必须存在 Message/Artifact: {key}"
            )
        if isinstance(item, int) and not isinstance(item, bool) and item < 0:
            raise RuntimeProtocolError(
                f"RuntimeEvent payload 计数不能为负数: {key}"
            )


@dataclass(frozen=True)
class RuntimeEvent:
    """Immutable audit envelope; large bodies and media remain in Artifacts."""

    scope_id: str
    event_id: str
    event_type: str
    aggregate_ref: ScopedRef
    aggregate_version: int
    sequence_no: int
    trace_id: str
    correlation_id: str
    actor_type: RuntimeActorType
    actor_ref: ScopedRef
    idempotency_key: str
    occurred_at: str
    recorded_at: str
    causation_event_ref: ScopedRef | None = None
    parent_event_ref: ScopedRef | None = None
    thread_ref: ScopedRef | None = None
    invocation_ref: ScopedRef | None = None
    attempt_ref: ScopedRef | None = None
    related_refs: tuple[ScopedRef, ...] = ()
    payload: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    event_version: int = 1
    schema_version: str = RUNTIME_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version, "RuntimeEvent")
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(self, "event_id", nonempty(self.event_id, "event_id"))
        object.__setattr__(
            self, "event_type", namespaced(self.event_type, "event_type")
        )
        if not isinstance(self.aggregate_ref, ScopedRef):
            raise RuntimeProtocolError("aggregate_ref 必须是 ScopedRef")
        self.aggregate_ref.assert_scope(self.scope_id, "aggregate_ref")
        aggregate_version = positive_int(
            self.aggregate_version, "aggregate_version"
        )
        if aggregate_version != self.aggregate_ref.version:
            raise RuntimeProtocolError(
                "aggregate_version 必须与 aggregate_ref.version 一致"
            )
        object.__setattr__(self, "aggregate_version", aggregate_version)
        object.__setattr__(
            self, "sequence_no", positive_int(self.sequence_no, "sequence_no")
        )
        object.__setattr__(self, "trace_id", nonempty(self.trace_id, "trace_id"))
        object.__setattr__(
            self, "correlation_id", nonempty(self.correlation_id, "correlation_id")
        )
        actor_type = enum_value(self.actor_type, RuntimeActorType, "actor_type")
        object.__setattr__(self, "actor_type", actor_type)
        if not isinstance(self.actor_ref, ScopedRef):
            raise RuntimeProtocolError("actor_ref 必须是 ScopedRef")
        self.actor_ref.assert_scope(self.scope_id, "actor_ref")
        self.actor_ref.assert_type(*_ACTOR_REF_TYPES[actor_type])
        object.__setattr__(
            self,
            "idempotency_key",
            nonempty(self.idempotency_key, "idempotency_key"),
        )
        object.__setattr__(
            self, "occurred_at", timestamp(self.occurred_at, "occurred_at")
        )
        object.__setattr__(
            self, "recorded_at", timestamp(self.recorded_at, "recorded_at")
        )
        if datetime.fromisoformat(self.recorded_at) < datetime.fromisoformat(
            self.occurred_at
        ):
            raise RuntimeProtocolError("recorded_at 不能早于 occurred_at")
        for field_name in ("causation_event_ref", "parent_event_ref"):
            reference = getattr(self, field_name)
            if reference is not None:
                if not isinstance(reference, ScopedRef):
                    raise RuntimeProtocolError(
                        f"{field_name} 必须是 ScopedRef 或 null"
                    )
                reference.assert_scope(self.scope_id, field_name)
                reference.assert_type("core:runtime_event")
                _require_append_only_event_ref(reference, field_name)
                if reference.entity_id == self.event_id:
                    raise RuntimeProtocolError("RuntimeEvent 不能引用自身")
        for field_name, entity_type in (
            ("thread_ref", "core:thread"),
            ("invocation_ref", "core:invocation"),
            ("attempt_ref", "core:attempt"),
        ):
            reference = getattr(self, field_name)
            if reference is not None:
                if not isinstance(reference, ScopedRef):
                    raise RuntimeProtocolError(
                        f"{field_name} 必须是 ScopedRef 或 null"
                    )
                reference.assert_scope(self.scope_id, field_name)
                reference.assert_type(entity_type)
        related_refs = scoped_refs(
            self.related_refs,
            "related_refs",
            scope_id=self.scope_id,
        )
        for reference in related_refs:
            _require_append_only_event_ref(reference, "related_refs")
        object.__setattr__(self, "related_refs", related_refs)
        frozen_payload = freeze_json(self.payload, "payload")
        if not isinstance(frozen_payload, Mapping):
            raise RuntimeProtocolError("payload 必须是 JSON 对象")
        forbidden = _forbidden_payload_field(frozen_payload)
        if forbidden:
            raise RuntimeProtocolError(
                "RuntimeEvent payload 不能保存正文、私密推理、秘密、原始媒体"
                f"或未校验引用字段: {forbidden}"
            )
        embedded_ref = _embedded_scoped_ref_path(frozen_payload)
        if embedded_ref:
            raise RuntimeProtocolError(
                "RuntimeEvent payload 不能内嵌 ScopedRef；"
                f"请使用显式引用字段: {embedded_ref}"
            )
        _validate_payload_metadata(frozen_payload)
        payload_bytes = len(json.dumps(
            thaw_json(frozen_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"))
        if payload_bytes > MAX_EVENT_PAYLOAD_BYTES:
            raise RuntimeProtocolError("RuntimeEvent payload 超过 16 KiB")
        object.__setattr__(self, "payload", frozen_payload)
        event_version = positive_int(self.event_version, "event_version")
        if event_version != 1:
            raise RuntimeProtocolError(
                "RuntimeEvent append 后不可改写；更正必须使用新 event_id"
            )
        object.__setattr__(self, "event_version", event_version)

    @property
    def reference(self) -> ScopedRef:
        return ScopedRef(
            self.scope_id, "core:runtime_event", self.event_id, self.event_version
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_ref": dict(self.aggregate_ref.to_dict()),
            "aggregate_version": self.aggregate_version,
            "sequence_no": self.sequence_no,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "actor_type": self.actor_type.value,
            "actor_ref": dict(self.actor_ref.to_dict()),
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "causation_event_ref": optional_ref_to_dict(
                self.causation_event_ref
            ),
            "parent_event_ref": optional_ref_to_dict(self.parent_event_ref),
            "thread_ref": optional_ref_to_dict(self.thread_ref),
            "invocation_ref": optional_ref_to_dict(self.invocation_ref),
            "attempt_ref": optional_ref_to_dict(self.attempt_ref),
            "related_refs": refs_to_dict(self.related_refs),
            "payload": thaw_json(self.payload),
            "event_version": self.event_version,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuntimeEvent":
        root = require_fields(
            value,
            type_name="RuntimeEvent",
            required=frozenset({
                "schema_version", "scope_id", "event_id", "event_type",
                "aggregate_ref", "aggregate_version", "sequence_no", "trace_id",
                "correlation_id", "actor_type", "actor_ref", "idempotency_key",
                "occurred_at", "recorded_at", "causation_event_ref",
                "parent_event_ref", "thread_ref", "invocation_ref", "attempt_ref",
                "related_refs", "payload", "event_version",
            }),
        )
        aggregate_ref = root["aggregate_ref"]
        actor_ref = root["actor_ref"]
        if not isinstance(aggregate_ref, Mapping) or not isinstance(actor_ref, Mapping):
            raise RuntimeProtocolError("aggregate_ref/actor_ref 必须是引用对象")
        return cls(
            root["scope_id"],
            root["event_id"],
            root["event_type"],
            ScopedRef.from_dict(aggregate_ref),
            root["aggregate_version"],
            root["sequence_no"],
            root["trace_id"],
            root["correlation_id"],
            root["actor_type"],
            ScopedRef.from_dict(actor_ref),
            root["idempotency_key"],
            root["occurred_at"],
            root["recorded_at"],
            optional_ref_from_dict(
                root["causation_event_ref"], "causation_event_ref"
            ),
            optional_ref_from_dict(root["parent_event_ref"], "parent_event_ref"),
            optional_ref_from_dict(root["thread_ref"], "thread_ref"),
            optional_ref_from_dict(root["invocation_ref"], "invocation_ref"),
            optional_ref_from_dict(root["attempt_ref"], "attempt_ref"),
            refs_from_dict(root["related_refs"], "related_refs"),
            root["payload"],
            root["event_version"],
            root["schema_version"],
        )
