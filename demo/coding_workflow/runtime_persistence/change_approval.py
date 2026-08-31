from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from typing import Mapping

from ..change_approval import (
    ChangeApplication,
    ChangeApplicationReceipt,
    ChangeApproval,
    ChangeApprovalRejected,
    ChangeApprovalStatus,
    ChangeProposal,
    ChangeSet,
    ChangeTargetKind,
    UserChangeApprovalConfirmation,
)
from ..runtime_domain import RuntimeProtocolError
from ..runtime_domain.common import nonempty
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .sqlite import SQLiteRuntimeDatabase


def _change_set_from_dict(value: object) -> ChangeSet:
    required = {
        "schema_version",
        "proposal_id",
        "scope_id",
        "target_kind",
        "target_ref",
        "reason",
        "exact_change",
        "affected_refs",
        "requested_capabilities",
        "dependency_digests",
        "evidence_refs",
        "risk",
        "verification",
        "base_state_digest",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise RuntimeProtocolError("ChangeSet schema无效")
    if not isinstance(value["exact_change"], Mapping):
        raise RuntimeProtocolError("ChangeSet exact_change无效")
    for name in (
        "affected_refs",
        "requested_capabilities",
        "dependency_digests",
        "evidence_refs",
    ):
        if not isinstance(value[name], list):
            raise RuntimeProtocolError(f"ChangeSet {name}无效")
    return ChangeSet(
        proposal_id=value["proposal_id"],
        scope_id=value["scope_id"],
        target_kind=ChangeTargetKind(value["target_kind"]),
        target_ref=value["target_ref"],
        reason=value["reason"],
        exact_change=value["exact_change"],
        affected_refs=tuple(value["affected_refs"]),
        requested_capabilities=tuple(value["requested_capabilities"]),
        dependency_digests=tuple(value["dependency_digests"]),
        evidence_refs=tuple(value["evidence_refs"]),
        risk=value["risk"],
        verification=value["verification"],
        base_state_digest=value["base_state_digest"],
        schema_version=value["schema_version"],
    )


class SQLiteChangeApprovalStore:
    """Append-only proposal, user approval, claim and applied receipt store."""

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database must be SQLiteRuntimeDatabase")
        self._database = database

    def record_proposal(self, change_set: ChangeSet) -> ChangeProposal:
        if not isinstance(change_set, ChangeSet):
            raise TypeError("change_set must be ChangeSet")
        raw = canonical_json(change_set.digest_payload())
        digest = text_digest(raw)
        if digest != change_set.change_digest:
            raise ChangeApprovalRejected("change_digest_mismatch")
        with self._database.unit_of_work() as uow:
            row = uow._execute_managed(
                """SELECT change_json, change_digest
                   FROM runtime_change_proposals
                   WHERE proposal_id = ?""",
                (change_set.proposal_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode_change_set(row[0], row[1])
                if existing == change_set:
                    uow.commit()
                    return ChangeProposal(existing)
                raise ChangeApprovalRejected("proposal_conflict")
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_change_proposals(
                        proposal_id, scope_id, target_kind, target_ref,
                        base_state_digest, change_digest, change_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        change_set.proposal_id,
                        change_set.scope_id,
                        change_set.target_kind.value,
                        change_set.target_ref,
                        change_set.base_state_digest,
                        digest,
                        raw,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ChangeApprovalRejected("proposal_conflict") from exc
            uow.commit()
        return ChangeProposal(change_set)

    def proposal_for(self, proposal_id: str) -> ChangeSet | None:
        locator = nonempty(proposal_id, "proposal_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT change_json, change_digest
                   FROM runtime_change_proposals
                   WHERE proposal_id = ?""",
                (locator,),
            ).fetchone()
            return None if row is None else self._decode_change_set(row[0], row[1])
        finally:
            connection.close()

    def record_user_approval(
        self,
        confirmation: UserChangeApprovalConfirmation,
    ) -> ChangeApproval:
        if not isinstance(confirmation, UserChangeApprovalConfirmation):
            raise TypeError("confirmation must be UserChangeApprovalConfirmation")
        approval_id = "user-approval-" + sha256(canonical_json({
            "proposal_id": confirmation.proposal_id,
            "change_digest": confirmation.change_digest,
            "base_state_digest": confirmation.base_state_digest,
            "user_id": confirmation.user_id,
        }).encode("utf-8")).hexdigest()
        with self._database.unit_of_work() as uow:
            existing = uow._execute_managed(
                """SELECT approval_id FROM runtime_change_user_approvals
                   WHERE proposal_id = ?""",
                (confirmation.proposal_id,),
            ).fetchone()
            if existing is not None:
                raise ChangeApprovalRejected("user_approval_already_recorded")
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_change_user_approvals(
                        approval_id, proposal_id, change_digest,
                        base_state_digest, user_id
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        approval_id,
                        confirmation.proposal_id,
                        confirmation.change_digest,
                        confirmation.base_state_digest,
                        confirmation.user_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ChangeApprovalRejected("user_approval_mismatch") from exc
            uow.commit()
        return ChangeApproval(
            approval_id=approval_id,
            proposal_id=confirmation.proposal_id,
            change_digest=confirmation.change_digest,
            base_state_digest=confirmation.base_state_digest,
            user_id=confirmation.user_id,
        )

    def approval_for(self, proposal_id: str) -> ChangeApproval | None:
        locator = nonempty(proposal_id, "proposal_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT approval_id, proposal_id, change_digest,
                          base_state_digest, user_id
                   FROM runtime_change_user_approvals
                   WHERE proposal_id = ?""",
                (locator,),
            ).fetchone()
            if row is None:
                return None
            return ChangeApproval(
                approval_id=str(row[0]),
                proposal_id=str(row[1]),
                change_digest=str(row[2]),
                base_state_digest=str(row[3]),
                user_id=str(row[4]),
            )
        finally:
            connection.close()

    def application_for(
        self, proposal_id: str
    ) -> ChangeApplication | None:
        locator = nonempty(proposal_id, "proposal_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT proposal_id, change_digest, result_digest
                   FROM runtime_change_applications
                   WHERE proposal_id = ?""",
                (locator,),
            ).fetchone()
            if row is None:
                return None
            return ChangeApplication(
                proposal_id=str(row[0]),
                change_digest=str(row[1]),
                result_digest=str(row[2]),
            )
        finally:
            connection.close()

    def application_claimed(self, proposal_id: str) -> bool:
        locator = nonempty(proposal_id, "proposal_id")
        connection = self._open_read_connection()
        try:
            return connection.execute(
                """SELECT 1 FROM runtime_change_application_claims
                   WHERE proposal_id = ?""",
                (locator,),
            ).fetchone() is not None
        finally:
            connection.close()

    def claim_application(
        self,
        change_set: ChangeSet,
        approval: ChangeApproval,
    ) -> None:
        if not isinstance(change_set, ChangeSet) or not isinstance(
            approval, ChangeApproval
        ):
            raise TypeError("change_set/approval types are invalid")
        with self._database.unit_of_work() as uow:
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_change_application_claims(
                        proposal_id, approval_id, change_digest,
                        base_state_digest
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        change_set.proposal_id,
                        approval.approval_id,
                        change_set.change_digest,
                        change_set.base_state_digest,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ChangeApprovalRejected(
                    "change_application_unresolved"
                ) from exc
            uow.commit()

    def record_application(
        self,
        change_set: ChangeSet,
        approval: ChangeApproval,
        receipt: ChangeApplicationReceipt,
    ) -> ChangeApplication:
        if not isinstance(receipt, ChangeApplicationReceipt):
            raise TypeError("receipt must be ChangeApplicationReceipt")
        with self._database.unit_of_work() as uow:
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_change_applications(
                        proposal_id, approval_id, change_digest,
                        base_state_digest, result_digest
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        change_set.proposal_id,
                        approval.approval_id,
                        change_set.change_digest,
                        change_set.base_state_digest,
                        receipt.result_digest,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ChangeApprovalRejected("change_application_unresolved") from exc
            uow.commit()
        return ChangeApplication(
            proposal_id=change_set.proposal_id,
            change_digest=change_set.change_digest,
            result_digest=receipt.result_digest,
        )

    def _decode_change_set(self, raw: object, digest: object) -> ChangeSet:
        if not isinstance(raw, str) or not isinstance(digest, str):
            raise RuntimeStoredDataCorruptionError(
                "runtime_change_proposals列类型无效"
            )
        if text_digest(raw) != digest:
            raise RuntimeStoredDataCorruptionError(
                "runtime_change_proposals digest不匹配"
            )
        try:
            value = json.loads(raw)
            change_set = _change_set_from_dict(value)
        except (json.JSONDecodeError, RuntimeProtocolError, TypeError, ValueError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_change_proposals无法重建"
            ) from exc
        if change_set.change_digest != digest:
            raise RuntimeStoredDataCorruptionError(
                "runtime_change_proposals规范digest漂移"
            )
        return change_set

    def _open_read_connection(self):
        self._database._require_outbox_policy()
        connection = self._database._connect()
        try:
            self._database._assert_wal(connection)
            state = self._database._inspect_schema(connection)
            if state is None:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_kernel schema尚未初始化"
                )
            self._database._validate_schema(
                state,
                connection=connection,
                require_current=True,
            )
            self._database._assert_outbox_policy_binding(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _verify_connection(self, connection: sqlite3.Connection) -> None:
        for table in (
            "runtime_change_proposals",
            "runtime_change_user_approvals",
            "runtime_change_application_claims",
            "runtime_change_applications",
        ):
            rows = tuple(connection.execute(f"SELECT * FROM {table}"))
            del rows


__all__ = ["SQLiteChangeApprovalStore"]
