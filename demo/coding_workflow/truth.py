from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


class ClaimKind(str, Enum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    PROPOSAL = "proposal"


class VerificationOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_VALIDATOR_KIND = re.compile(
    r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$"
)
_EVIDENCE_PREFIXES = ("artifact://", "evidence://", "verification://")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value.strip()


def _optional_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value.strip()


def _references(
    values: object,
    field_name: str,
    *,
    prefixes: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"{field_name} 必须是引用数组")
    parsed = tuple(_nonempty(item, field_name) for item in values)
    invalid = tuple(item for item in parsed if not item.startswith(prefixes))
    if invalid:
        raise ValueError(f"{field_name} 包含无效引用: {invalid[0]}")
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{field_name} 不能重复")
    return parsed


def workspace_digest(hashes: Mapping[str, str]) -> str:
    if not isinstance(hashes, Mapping):
        raise ValueError("workspace hashes 必须是对象")
    normalized: dict[str, str] = {}
    for path, digest in hashes.items():
        if not isinstance(path, str) or not path:
            raise ValueError("workspace path 不能为空")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest.lower()):
            raise ValueError(f"Workspace hash 无效: {path}")
        normalized[path] = digest.lower()
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Claim:
    """模型或 Worker 的可追踪陈述；Claim 本身不是已验证事实。"""

    claim_id: str
    kind: ClaimKind
    statement: str
    source: str
    evidence_refs: tuple[str, ...] = ()
    uncertainty: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _nonempty(self.claim_id, "claim_id"))
        if not isinstance(self.kind, ClaimKind):
            object.__setattr__(self, "kind", ClaimKind(self.kind))
        object.__setattr__(self, "statement", _nonempty(self.statement, "statement"))
        object.__setattr__(self, "source", _nonempty(self.source, "source"))
        object.__setattr__(
            self,
            "evidence_refs",
            _references(
                self.evidence_refs,
                "evidence_refs",
                prefixes=_EVIDENCE_PREFIXES,
            ),
        )
        uncertainty = _optional_string(self.uncertainty, "uncertainty")
        if self.kind is ClaimKind.OBSERVATION and not self.evidence_refs:
            raise ValueError("observation 必须引用原始证据")
        if self.kind is ClaimKind.INFERENCE and not uncertainty:
            raise ValueError("inference 必须明确不确定性")
        object.__setattr__(self, "uncertainty", uncertainty)
        object.__setattr__(
            self,
            "created_at",
            _optional_string(self.created_at, "created_at")
            or datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def create(
        cls,
        kind: ClaimKind,
        statement: str,
        source: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        uncertainty: str = "",
    ) -> "Claim":
        return cls(
            str(uuid4()), kind, statement, source,
            evidence_refs, uncertainty,
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "claim_id": self.claim_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "source": self.source,
            "evidence_refs": self.evidence_refs,
            "uncertainty": self.uncertainty,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Claim":
        return cls(
            value["claim_id"],
            ClaimKind(value["kind"]),
            value["statement"],
            value["source"],
            value.get("evidence_refs", ()),
            value.get("uncertainty", ""),
            value.get("created_at", ""),
        )


@dataclass(frozen=True)
class VerificationRecord:
    """Runtime 接纳的不可变验证证明；模型声明不能替代该记录。"""

    verification_id: str
    validator_kind: str
    outcome: VerificationOutcome
    subject_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    summary: str
    created_at: str = ""
    subject_hashes: Mapping[str, str] = MappingProxyType({})
    workspace_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "verification_id",
            _nonempty(self.verification_id, "verification_id"),
        )
        validator_kind = _nonempty(self.validator_kind, "validator_kind")
        if not _VALIDATOR_KIND.fullmatch(validator_kind):
            raise ValueError("validator_kind 必须使用 namespace:name")
        object.__setattr__(self, "validator_kind", validator_kind)
        if not isinstance(self.outcome, VerificationOutcome):
            object.__setattr__(
                self, "outcome", VerificationOutcome(self.outcome)
            )
        subjects = _references(
            self.subject_refs,
            "subject_refs",
            prefixes=("artifact://",),
        )
        if not subjects:
            raise ValueError("VerificationRecord 必须包含 subject_refs")
        object.__setattr__(self, "subject_refs", subjects)
        evidence = _references(
            self.evidence_refs,
            "evidence_refs",
            prefixes=_EVIDENCE_PREFIXES,
        )
        if self.outcome is not VerificationOutcome.UNKNOWN and not evidence:
            raise ValueError("passed/failed 验证必须包含执行证据")
        if self.outcome is not VerificationOutcome.UNKNOWN and set(
            subjects
        ).intersection(evidence):
            raise ValueError("被验证 Artifact 不能同时作为自身通过证据")
        object.__setattr__(self, "evidence_refs", evidence)
        object.__setattr__(self, "summary", _nonempty(self.summary, "summary"))
        object.__setattr__(
            self,
            "created_at",
            _optional_string(self.created_at, "created_at")
            or datetime.now(timezone.utc).isoformat(),
        )
        if not isinstance(self.subject_hashes, Mapping):
            raise ValueError("subject_hashes 必须是对象")
        subject_hashes: dict[str, str] = {}
        for reference, digest in self.subject_hashes.items():
            if reference not in subjects:
                raise ValueError("subject_hashes 包含非 subject 引用")
            if not isinstance(digest, str) or not _SHA256.fullmatch(
                digest.lower()
            ):
                raise ValueError("subject_hashes 必须是 SHA-256")
            subject_hashes[reference] = digest.lower()
        object.__setattr__(
            self, "subject_hashes", MappingProxyType(subject_hashes)
        )
        workspace_hash = _optional_string(self.workspace_hash, "workspace_hash")
        if workspace_hash and not _SHA256.fullmatch(workspace_hash.lower()):
            raise ValueError("workspace_hash 必须是 SHA-256")
        object.__setattr__(self, "workspace_hash", workspace_hash.lower())

    @property
    def reference(self) -> str:
        return f"verification://{self.verification_id}"

    @classmethod
    def create(
        cls,
        validator_kind: str,
        outcome: VerificationOutcome,
        subject_refs: tuple[str, ...],
        evidence_refs: tuple[str, ...],
        summary: str,
        *,
        subject_hashes: Mapping[str, str] | None = None,
        workspace_hash: str = "",
    ) -> "VerificationRecord":
        return cls(
            str(uuid4()), validator_kind, outcome,
            subject_refs, evidence_refs, summary, "",
            subject_hashes or MappingProxyType({}), workspace_hash,
        )

    def is_fresh(
        self,
        subject_hashes: Mapping[str, str],
        *,
        workspace_hash: str = "",
    ) -> bool:
        if dict(self.subject_hashes) != dict(subject_hashes):
            return False
        if self.workspace_hash:
            return bool(workspace_hash) and self.workspace_hash == workspace_hash
        return True

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "verification_id": self.verification_id,
            "validator_kind": self.validator_kind,
            "outcome": self.outcome.value,
            "subject_refs": self.subject_refs,
            "evidence_refs": self.evidence_refs,
            "summary": self.summary,
            "created_at": self.created_at,
            "subject_hashes": dict(self.subject_hashes),
            "workspace_hash": self.workspace_hash,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VerificationRecord":
        return cls(
            value["verification_id"],
            value["validator_kind"],
            VerificationOutcome(value["outcome"]),
            value["subject_refs"],
            value.get("evidence_refs", ()),
            value["summary"],
            value.get("created_at", ""),
            value.get("subject_hashes", {}),
            value.get("workspace_hash", ""),
        )
