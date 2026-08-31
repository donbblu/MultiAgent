from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Protocol

from .runtime_domain import RuntimeProtocolError
from .runtime_domain.common import (
    canonical_digest,
    freeze_json,
    namespaced,
    nonempty,
    sha256_digest,
    string_tuple,
    thaw_json,
)


class ChangeTargetKind(str, Enum):
    AGENT_PROMPT = "agent_prompt"
    ROLE_PROFILE = "role_profile"
    MODEL_POLICY = "model_policy"
    TOOL_PERMISSION = "tool_permission"
    RUNTIME_POLICY = "runtime_policy"
    SKILL = "skill"
    CONTEXT_MEMORY_POLICY = "context_memory_policy"
    VALIDATOR_ACCEPTANCE = "validator_acceptance"
    SYSTEM_CODE = "system_code"


class ChangeApprovalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    USER_APPROVED = "USER_APPROVED"
    APPLIED = "APPLIED"


@dataclass(frozen=True)
class ChangeSet:
    proposal_id: str
    scope_id: str
    target_kind: ChangeTargetKind
    target_ref: str
    reason: str
    exact_change: Mapping[str, object]
    affected_refs: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    dependency_digests: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    risk: str
    verification: str
    base_state_digest: str
    schema_version: str = "change-set/v1"

    def __post_init__(self) -> None:
        if self.schema_version != "change-set/v1":
            raise RuntimeProtocolError("ChangeSet schema_version不受支持")
        object.__setattr__(self, "proposal_id", nonempty(
            self.proposal_id, "proposal_id"
        ))
        object.__setattr__(self, "scope_id", nonempty(
            self.scope_id, "scope_id"
        ))
        if not isinstance(self.target_kind, ChangeTargetKind):
            raise RuntimeProtocolError("target_kind无效")
        object.__setattr__(self, "target_ref", namespaced(
            self.target_ref, "target_ref"
        ))
        for name in ("reason", "risk", "verification"):
            object.__setattr__(self, name, nonempty(getattr(self, name), name))
        frozen_change = freeze_json(self.exact_change, "exact_change")
        if not isinstance(frozen_change, Mapping) or not frozen_change:
            raise RuntimeProtocolError("exact_change必须是非空对象")
        object.__setattr__(self, "exact_change", frozen_change)
        object.__setattr__(self, "affected_refs", string_tuple(
            self.affected_refs,
            "affected_refs",
            allow_empty=False,
            require_namespaced=True,
        ))
        object.__setattr__(self, "requested_capabilities", string_tuple(
            self.requested_capabilities,
            "requested_capabilities",
            allow_empty=False,
            require_namespaced=True,
        ))
        dependencies = tuple(
            sha256_digest(value, "dependency_digests")
            for value in self.dependency_digests
        )
        if len(dependencies) != len(set(dependencies)):
            raise RuntimeProtocolError("dependency_digests不能重复")
        object.__setattr__(self, "dependency_digests", dependencies)
        object.__setattr__(self, "evidence_refs", string_tuple(
            self.evidence_refs,
            "evidence_refs",
            allow_empty=False,
            require_namespaced=True,
        ))
        object.__setattr__(self, "base_state_digest", sha256_digest(
            self.base_state_digest, "base_state_digest"
        ))

    def digest_payload(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "scope_id": self.scope_id,
            "target_kind": self.target_kind.value,
            "target_ref": self.target_ref,
            "reason": self.reason,
            "exact_change": thaw_json(self.exact_change),
            "affected_refs": list(self.affected_refs),
            "requested_capabilities": list(self.requested_capabilities),
            "dependency_digests": list(self.dependency_digests),
            "evidence_refs": list(self.evidence_refs),
            "risk": self.risk,
            "verification": self.verification,
            "base_state_digest": self.base_state_digest,
        }

    @property
    def change_digest(self) -> str:
        return canonical_digest(self.digest_payload())


@dataclass(frozen=True)
class ChangeProposal:
    change_set: ChangeSet
    status: ChangeApprovalStatus = ChangeApprovalStatus.PROPOSED
    review_status: str = "PENDING_USER_REVIEW"

    def __post_init__(self) -> None:
        if not isinstance(self.change_set, ChangeSet):
            raise RuntimeProtocolError("proposal change_set无效")
        if self.status is not ChangeApprovalStatus.PROPOSED:
            raise RuntimeProtocolError("proposal status无效")
        if self.review_status != "PENDING_USER_REVIEW":
            raise RuntimeProtocolError("proposal review_status无效")

    @property
    def change_digest(self) -> str:
        return self.change_set.change_digest


@dataclass(frozen=True)
class UserChangeApprovalConfirmation:
    proposal_id: str
    change_digest: str
    base_state_digest: str
    user_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", nonempty(
            self.proposal_id, "proposal_id"
        ))
        object.__setattr__(self, "change_digest", sha256_digest(
            self.change_digest, "change_digest"
        ))
        object.__setattr__(self, "base_state_digest", sha256_digest(
            self.base_state_digest, "base_state_digest"
        ))
        object.__setattr__(self, "user_id", nonempty(self.user_id, "user_id"))


@dataclass(frozen=True)
class ChangeApproval:
    approval_id: str
    proposal_id: str
    change_digest: str
    base_state_digest: str
    user_id: str
    status: ChangeApprovalStatus = ChangeApprovalStatus.USER_APPROVED


@dataclass(frozen=True)
class ChangeApplicationReceipt:
    result_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_digest", sha256_digest(
            self.result_digest, "result_digest"
        ))


@dataclass(frozen=True)
class ChangeApplication:
    proposal_id: str
    change_digest: str
    result_digest: str
    status: ChangeApprovalStatus = ChangeApprovalStatus.APPLIED


class ChangeApprovalRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = nonempty(code, "code")
        super().__init__(self.code)


class ChangeApprovalStore(Protocol):
    def record_proposal(self, change_set: ChangeSet) -> ChangeProposal: ...

    def proposal_for(self, proposal_id: str) -> ChangeSet | None: ...

    def record_user_approval(
        self,
        confirmation: UserChangeApprovalConfirmation,
    ) -> ChangeApproval: ...

    def approval_for(self, proposal_id: str) -> ChangeApproval | None: ...

    def application_for(
        self, proposal_id: str
    ) -> ChangeApplication | None: ...

    def application_claimed(self, proposal_id: str) -> bool: ...

    def claim_application(
        self,
        change_set: ChangeSet,
        approval: ChangeApproval,
    ) -> None: ...

    def record_application(
        self,
        change_set: ChangeSet,
        approval: ChangeApproval,
        receipt: ChangeApplicationReceipt,
    ) -> ChangeApplication: ...


class ChangeApprovalRuntime:
    def __init__(self, *, store: ChangeApprovalStore) -> None:
        required = (
            "record_proposal",
            "proposal_for",
            "record_user_approval",
            "approval_for",
            "application_for",
            "application_claimed",
            "claim_application",
            "record_application",
        )
        if any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("store must implement ChangeApprovalStore")
        self._store = store

    def propose(self, change_set: ChangeSet) -> ChangeProposal:
        if not isinstance(change_set, ChangeSet):
            raise TypeError("change_set must be ChangeSet")
        return self._store.record_proposal(change_set)

    def approve(
        self,
        confirmation: UserChangeApprovalConfirmation,
    ) -> ChangeApproval:
        if not isinstance(confirmation, UserChangeApprovalConfirmation):
            raise TypeError("confirmation must be UserChangeApprovalConfirmation")
        proposal = self._store.proposal_for(confirmation.proposal_id)
        if proposal is None:
            raise ChangeApprovalRejected("proposal_not_found")
        if confirmation.change_digest != proposal.change_digest:
            raise ChangeApprovalRejected("change_digest_mismatch")
        if confirmation.base_state_digest != proposal.base_state_digest:
            raise ChangeApprovalRejected("state_digest_mismatch")
        return self._store.record_user_approval(confirmation)

    def apply(
        self,
        change_set: ChangeSet,
        *,
        current_state_digest: str,
        effect: Callable[[ChangeSet], ChangeApplicationReceipt],
    ) -> ChangeApplication:
        if not isinstance(change_set, ChangeSet):
            raise TypeError("change_set must be ChangeSet")
        state_digest = sha256_digest(
            current_state_digest, "current_state_digest"
        )
        if not callable(effect):
            raise TypeError("effect must be callable")
        proposal = self._store.proposal_for(change_set.proposal_id)
        if proposal is None:
            raise ChangeApprovalRejected("proposal_not_found")
        if proposal != change_set:
            raise ChangeApprovalRejected("change_digest_mismatch")
        if self._store.application_for(change_set.proposal_id) is not None:
            raise ChangeApprovalRejected("change_already_applied")
        if self._store.application_claimed(change_set.proposal_id):
            raise ChangeApprovalRejected("change_application_unresolved")
        approval = self._store.approval_for(change_set.proposal_id)
        if approval is None:
            raise ChangeApprovalRejected("user_approval_required")
        if (
            approval.change_digest != change_set.change_digest
            or approval.base_state_digest != change_set.base_state_digest
        ):
            raise ChangeApprovalRejected("change_digest_mismatch")
        if state_digest != change_set.base_state_digest:
            raise ChangeApprovalRejected("state_digest_mismatch")
        self._store.claim_application(change_set, approval)
        try:
            receipt = effect(change_set)
        except BaseException as exc:
            raise ChangeApprovalRejected("change_application_failed") from exc
        if not isinstance(receipt, ChangeApplicationReceipt):
            raise ChangeApprovalRejected("change_application_receipt_invalid")
        return self._store.record_application(change_set, approval, receipt)
