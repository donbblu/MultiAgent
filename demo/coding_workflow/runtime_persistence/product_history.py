from __future__ import annotations

import json
import sqlite3
from types import MappingProxyType
from typing import Mapping

from ..artifacts import Artifact, ArtifactStore
from ..runtime_domain.common import nonempty, thaw_json
from ..truth import VerificationRecord
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .sqlite import RuntimePersistenceError, SQLiteRuntimeDatabase


class ProductHistoryError(RuntimePersistenceError):
    pass


class ProductHistoryConflictError(ProductHistoryError):
    pass


class SQLiteProductHistoryStore:
    """Append-only public product result, Artifact and Verification history."""

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database必须是SQLiteRuntimeDatabase")
        self._database = database

    def record(
        self,
        *,
        scope_id: str,
        task_id: str,
        result_payload: Mapping[str, object],
        artifacts: ArtifactStore,
        verification_ref: str,
    ) -> None:
        scope = nonempty(scope_id, "scope_id")
        task = nonempty(task_id, "task_id")
        if not isinstance(result_payload, Mapping):
            raise TypeError("result_payload必须是Mapping")
        if not isinstance(artifacts, ArtifactStore):
            raise TypeError("artifacts必须是ArtifactStore")
        task_artifacts = tuple(
            artifact
            for artifact, _ in artifacts.snapshot()
            if artifact.task_id == task
        )
        by_ref = {
            f"artifact://{artifact.artifact_id}": artifact
            for artifact in task_artifacts
        }
        public_refs = tuple(
            result_payload.get(field)
            for field in (
                "result_artifact_ref",
                "validator_report_ref",
                "verification_ref",
            )
        )
        if any(public_refs) and not all(public_refs):
            raise ProductHistoryConflictError("产品结果验收引用不完整")
        verification = None
        if all(public_refs):
            verification = artifacts.verification(verification_ref)
            for field in ("result_artifact_ref", "validator_report_ref"):
                reference = result_payload.get(field)
                if not isinstance(reference, str) or reference not in by_ref:
                    raise ProductHistoryConflictError(
                        f"产品结果缺少持久Artifact: {field}"
                    )
            if result_payload.get("verification_ref") != verification.reference:
                raise ProductHistoryConflictError(
                    "产品结果Verification引用不匹配"
                )
            for reference in (
                *verification.subject_refs,
                *verification.evidence_refs,
            ):
                if reference.startswith("artifact://") and reference not in by_ref:
                    raise ProductHistoryConflictError(
                        "Verification引用了本任务之外的Artifact"
                    )
        elif task_artifacts:
            raise ProductHistoryConflictError(
                "无验收终态不能附带未引用Artifact"
            )

        result_raw = canonical_json(thaw_json(result_payload))
        result_digest = text_digest(result_raw)
        artifact_rows = tuple(
            self._encode_artifact(scope, artifact) for artifact in task_artifacts
        )
        verification_raw = (
            None
            if verification is None
            else canonical_json(thaw_json(verification.to_dict()))
        )
        verification_digest = (
            None
            if verification_raw is None
            else text_digest(verification_raw)
        )

        with self._database.unit_of_work() as uow:
            existing = uow._execute_managed(
                """SELECT result_json, result_digest
                   FROM runtime_product_task_results
                   WHERE scope_id = ? AND task_id = ?""",
                (scope, task),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing[0]) == result_raw
                    and str(existing[1]) == result_digest
                ):
                    uow.commit()
                    return
                raise ProductHistoryConflictError(
                    "task_id已绑定不同产品验收结果"
                )
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_product_task_results(
                        scope_id, task_id, result_json, result_digest
                    ) VALUES (?, ?, ?, ?)""",
                    (scope, task, result_raw, result_digest),
                )
                for row in artifact_rows:
                    uow._execute_managed(
                        """INSERT INTO runtime_product_artifacts(
                            artifact_id, scope_id, task_id,
                            artifact_json, artifact_digest
                        ) VALUES (?, ?, ?, ?, ?)""",
                        row,
                    )
                if verification is not None:
                    uow._execute_managed(
                        """INSERT INTO runtime_product_verifications(
                            verification_id, scope_id, task_id,
                            verification_json, verification_digest
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            verification.verification_id,
                            scope,
                            task,
                            verification_raw,
                            verification_digest,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ProductHistoryConflictError(
                    "产品验收历史append冲突"
                ) from exc
            uow.commit()

    def result_for(
        self,
        *,
        scope_id: str,
        task_id: str,
    ) -> Mapping[str, object] | None:
        scope = nonempty(scope_id, "scope_id")
        task = nonempty(task_id, "task_id")
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT result_json, result_digest
                   FROM runtime_product_task_results
                   WHERE scope_id = ? AND task_id = ?""",
                (scope, task),
            ).fetchone()
            if row is None:
                return None
            value = self._decode_json(row[0], row[1], "ProductTaskResult")
            if not isinstance(value, Mapping):
                raise RuntimeStoredDataCorruptionError(
                    "ProductTaskResult必须是对象"
                )
            return MappingProxyType(dict(value))
        finally:
            connection.close()

    def artifact_for(self, reference: str) -> Artifact:
        if not isinstance(reference, str) or not reference.startswith("artifact://"):
            raise ValueError("Artifact引用无效")
        artifact_id = nonempty(
            reference.removeprefix("artifact://"),
            "artifact_id",
        )
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT artifact_json, artifact_digest
                   FROM runtime_product_artifacts
                   WHERE artifact_id = ?""",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Artifact不存在: {reference}")
            return self._decode_artifact(row[0], row[1])
        finally:
            connection.close()

    def verification_for(self, reference: str) -> VerificationRecord:
        if not isinstance(reference, str) or not reference.startswith(
            "verification://"
        ):
            raise ValueError("Verification引用无效")
        verification_id = nonempty(
            reference.removeprefix("verification://"),
            "verification_id",
        )
        connection = self._open_read_connection()
        try:
            row = connection.execute(
                """SELECT verification_json, verification_digest
                   FROM runtime_product_verifications
                   WHERE verification_id = ?""",
                (verification_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Verification不存在: {reference}")
            return self._decode_verification(row[0], row[1])
        finally:
            connection.close()

    @staticmethod
    def _encode_artifact(
        scope_id: str,
        artifact: Artifact,
    ) -> tuple[str, str, str, str, str]:
        raw = canonical_json({
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
            "task_id": artifact.task_id,
            "kind": artifact.kind,
            "content": thaw_json(artifact.content),
            "metadata": thaw_json(artifact.metadata),
            "created_at": artifact.created_at,
        })
        return (
            artifact.artifact_id,
            scope_id,
            artifact.task_id,
            raw,
            text_digest(raw),
        )

    @classmethod
    def _decode_artifact(cls, raw: object, digest: object) -> Artifact:
        value = cls._decode_json(raw, digest, "Artifact")
        required = {
            "artifact_id",
            "name",
            "task_id",
            "kind",
            "content",
            "metadata",
            "created_at",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or not isinstance(value["metadata"], Mapping)
        ):
            raise RuntimeStoredDataCorruptionError("Artifact schema无效")
        return Artifact(
            artifact_id=value["artifact_id"],
            name=value["name"],
            task_id=value["task_id"],
            kind=value["kind"],
            content=value["content"],
            metadata=MappingProxyType(dict(value["metadata"])),
            created_at=value["created_at"],
        )

    @classmethod
    def _decode_verification(
        cls,
        raw: object,
        digest: object,
    ) -> VerificationRecord:
        value = cls._decode_json(raw, digest, "VerificationRecord")
        if not isinstance(value, Mapping):
            raise RuntimeStoredDataCorruptionError(
                "VerificationRecord schema无效"
            )
        try:
            return VerificationRecord.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "VerificationRecord无法重建"
            ) from exc

    @staticmethod
    def _decode_json(raw: object, digest: object, name: str) -> object:
        if not isinstance(raw, str) or not isinstance(digest, str):
            raise RuntimeStoredDataCorruptionError(f"{name}列类型无效")
        if text_digest(raw) != digest:
            raise RuntimeStoredDataCorruptionError(f"{name} digest不匹配")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeStoredDataCorruptionError(f"{name} JSON无效") from exc

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
        artifacts = {
            f"artifact://{row[0]}": self._decode_artifact(row[1], row[2])
            for row in connection.execute(
                """SELECT artifact_id, artifact_json, artifact_digest
                   FROM runtime_product_artifacts"""
            )
        }
        verifications = {
            f"verification://{row[0]}": self._decode_verification(row[1], row[2])
            for row in connection.execute(
                """SELECT verification_id, verification_json,
                          verification_digest
                   FROM runtime_product_verifications"""
            )
        }
        for row in connection.execute(
            """SELECT scope_id, task_id, result_json, result_digest
               FROM runtime_product_task_results"""
        ):
            result = self._decode_json(row[2], row[3], "ProductTaskResult")
            if not isinstance(result, Mapping):
                raise RuntimeStoredDataCorruptionError(
                    "ProductTaskResult schema无效"
                )
            refs = tuple(
                result.get(field)
                for field in (
                    "result_artifact_ref",
                    "validator_report_ref",
                    "verification_ref",
                )
            )
            if not any(refs):
                continue
            if not all(refs):
                raise RuntimeStoredDataCorruptionError(
                    "ProductTaskResult验收引用不完整"
                )
            for field in ("result_artifact_ref", "validator_report_ref"):
                if result.get(field) not in artifacts:
                    raise RuntimeStoredDataCorruptionError(
                        f"ProductTaskResult缺少Artifact: {field}"
                    )
            verification_ref = result.get("verification_ref")
            if verification_ref not in verifications:
                raise RuntimeStoredDataCorruptionError(
                    "ProductTaskResult缺少Verification"
                )
            verification = verifications[verification_ref]
            if any(reference not in artifacts for reference in (
                *verification.subject_refs,
                *(
                    item
                    for item in verification.evidence_refs
                    if item.startswith("artifact://")
                ),
            )):
                raise RuntimeStoredDataCorruptionError(
                    "Verification引用缺失Artifact"
                )


__all__ = [
    "ProductHistoryConflictError",
    "ProductHistoryError",
    "SQLiteProductHistoryStore",
]
