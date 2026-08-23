from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping, TypeVar


RUNTIME_PROTOCOL_VERSION = "1.0"


class RuntimeProtocolError(ValueError):
    """A persisted Runtime value violates the versioned domain contract."""


class ScopeBoundaryError(PermissionError):
    """A reference crosses the fail-closed Scope boundary."""


_NAMESPACED = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeProtocolError(f"{field_name} 不能为空")
    return value.strip()


def optional_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise RuntimeProtocolError(f"{field_name} 必须是字符串")
    return value.strip()


def positive_int(value: object, field_name: str, *, allow_zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeProtocolError(f"{field_name} 必须是整数")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        comparison = "大于等于 0" if allow_zero else "大于 0"
        raise RuntimeProtocolError(f"{field_name} 必须{comparison}")
    return value


def namespaced(value: object, field_name: str) -> str:
    parsed = nonempty(value, field_name).lower()
    if not _NAMESPACED.fullmatch(parsed):
        raise RuntimeProtocolError(f"{field_name} 必须使用 namespace:name")
    return parsed


def sha256_digest(value: object, field_name: str) -> str:
    parsed = nonempty(value, field_name).lower()
    if not _SHA256.fullmatch(parsed):
        raise RuntimeProtocolError(f"{field_name} 必须是 SHA-256")
    return parsed


def timestamp(value: object, field_name: str, *, default_now: bool = False) -> str:
    if default_now and value == "":
        return datetime.now(timezone.utc).isoformat()
    parsed = nonempty(value, field_name)
    try:
        instant = datetime.fromisoformat(parsed)
    except ValueError as exc:
        raise RuntimeProtocolError(f"{field_name} 必须是 ISO-8601") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise RuntimeProtocolError(f"{field_name} 必须包含时区")
    return parsed


def optional_timestamp(value: object, field_name: str) -> str:
    parsed = optional_string(value, field_name)
    return timestamp(parsed, field_name) if parsed else ""


def string_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
    require_namespaced: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是字符串数组")
    parser = namespaced if require_namespaced else nonempty
    parsed = tuple(parser(item, field_name) for item in value)
    if not allow_empty and not parsed:
        raise RuntimeProtocolError(f"{field_name} 不能为空")
    if len(parsed) != len(set(parsed)):
        raise RuntimeProtocolError(f"{field_name} 不能重复")
    return parsed


def require_schema_version(value: object, type_name: str) -> str:
    if value != RUNTIME_PROTOCOL_VERSION:
        raise RuntimeProtocolError(
            f"{type_name} 只支持 schema_version {RUNTIME_PROTOCOL_VERSION}"
        )
    return RUNTIME_PROTOCOL_VERSION


def require_fields(
    value: object,
    *,
    type_name: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeProtocolError(f"{type_name} 必须是对象")
    actual = set(value)
    missing = required - actual
    unexpected = actual - required - optional
    if missing:
        raise RuntimeProtocolError(
            f"{type_name} 缺少字段: {', '.join(sorted(missing))}"
        )
    if unexpected:
        raise RuntimeProtocolError(
            f"{type_name} 包含未知字段: {', '.join(sorted(unexpected))}"
        )
    return value


def freeze_json(value: object, field_name: str) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeProtocolError(f"{field_name} 不能包含 NaN 或 Infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise RuntimeProtocolError(f"{field_name} 的对象键必须是非空字符串")
            frozen[key] = freeze_json(item, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item, field_name) for item in value)
    raise RuntimeProtocolError(f"{field_name} 只能包含 JSON 值")


def thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


_EnumT = TypeVar("_EnumT")


def enum_value(value: object, enum_type: type[_EnumT], field_name: str) -> _EnumT:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        raise RuntimeProtocolError(f"{field_name} 无效") from exc


def optional_ref_to_dict(value: "ScopedRef | None") -> object:
    return dict(value.to_dict()) if value is not None else None


def optional_ref_from_dict(value: object, field_name: str) -> "ScopedRef | None":
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeProtocolError(f"{field_name} 必须是引用对象或 null")
    return ScopedRef.from_dict(value)


@dataclass(frozen=True)
class ScopedRef:
    """Versioned opaque reference carrying its Scope for fail-closed checks.

    Ownership links keep the version observed when the relationship was made;
    they identify the same entity even after a later version exists.  Callers
    that require a current immutable snapshot (Invocation inputs, Acceptance
    subjects, evidence) must additionally compare the referenced version and
    digest against their authoritative store.
    """

    scope_id: str
    entity_type: str
    entity_id: str
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(
            self, "entity_type", namespaced(self.entity_type, "entity_type")
        )
        object.__setattr__(self, "entity_id", nonempty(self.entity_id, "entity_id"))
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.scope_id, self.entity_type, self.entity_id, self.version)

    def assert_scope(self, scope_id: str, field_name: str = "reference") -> None:
        if self.scope_id != scope_id:
            raise ScopeBoundaryError(
                f"{field_name} 跨 Scope: {self.scope_id} != {scope_id}"
            )

    def assert_type(self, *entity_types: str) -> None:
        allowed = tuple(namespaced(item, "entity_type") for item in entity_types)
        if self.entity_type not in allowed:
            raise RuntimeProtocolError(
                f"引用类型无效: {self.entity_type}，期望 {allowed}"
            )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "scope_id": self.scope_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "version": self.version,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ScopedRef":
        root = require_fields(
            value,
            type_name="ScopedRef",
            required=frozenset({"scope_id", "entity_type", "entity_id", "version"}),
        )
        return cls(
            root["scope_id"],
            root["entity_type"],
            root["entity_id"],
            root["version"],
        )


def scoped_refs(
    value: object,
    field_name: str,
    *,
    scope_id: str,
    allow_empty: bool = True,
    entity_types: tuple[str, ...] = (),
) -> tuple[ScopedRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是引用数组")
    parsed = tuple(value)
    if not allow_empty and not parsed:
        raise RuntimeProtocolError(f"{field_name} 不能为空")
    if not all(isinstance(item, ScopedRef) for item in parsed):
        raise RuntimeProtocolError(f"{field_name} 必须包含 ScopedRef")
    for item in parsed:
        item.assert_scope(scope_id, field_name)
        if entity_types:
            item.assert_type(*entity_types)
    if len(parsed) != len(set(parsed)):
        raise RuntimeProtocolError(f"{field_name} 不能重复")
    return parsed


def refs_from_dict(value: object, field_name: str) -> tuple[ScopedRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeProtocolError(f"{field_name} 必须是引用数组")
    return tuple(
        ScopedRef.from_dict(item) if isinstance(item, Mapping) else _invalid_ref(field_name)
        for item in value
    )


def _invalid_ref(field_name: str) -> ScopedRef:
    raise RuntimeProtocolError(f"{field_name} 必须包含引用对象")


def refs_to_dict(value: tuple[ScopedRef, ...]) -> list[dict[str, object]]:
    return [dict(item.to_dict()) for item in value]
