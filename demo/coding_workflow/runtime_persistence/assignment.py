from __future__ import annotations

import json
import sqlite3
from enum import Enum

from ..runtime_domain import AgentSessionState, RoleAssignment
from ..runtime_domain.common import RuntimeProtocolError, nonempty
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .agent import SQLiteAgentStore
from .sqlite import RuntimePersistenceError, RuntimeUnitOfWork, SQLiteRuntimeDatabase


class RoleAssignmentStoreError(RuntimePersistenceError):
    """Base error for durable RoleAssignment records."""


class RoleAssignmentStoreValidationError(RoleAssignmentStoreError):
    """The assignment or locator violates the persistence contract."""


class RoleAssignmentStoreConflictError(RoleAssignmentStoreError):
    """An immutable assignment identity or generation conflicts."""


class AssignmentRecordResult(str, Enum):
    CREATED = "created"
    ALREADY_RECORDED = "already_recorded"


class SQLiteRoleAssignmentStore:
    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise RoleAssignmentStoreValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database
        self._agent_store = SQLiteAgentStore(database)

    def record(
        self,
        uow: RuntimeUnitOfWork,
        assignment: RoleAssignment,
    ) -> AssignmentRecordResult:
        try:
            self._require_uow(uow)
            if not isinstance(assignment, RoleAssignment):
                raise RoleAssignmentStoreValidationError(
                    "assignment 必须是 RoleAssignment"
                )
            existing = self._assignment_by_id(uow, assignment.assignment_id)
            if existing is not None:
                if existing == assignment:
                    return AssignmentRecordResult.ALREADY_RECORDED
                raise RoleAssignmentStoreConflictError(
                    "assignment_id 已存在且内容不同"
                )

            slot = uow._execute_managed(
                """SELECT assignment_id FROM runtime_role_assignments
                   WHERE scope_id = ? AND thread_id = ? AND work_type = ?
                     AND work_id = ? AND role_id = ? AND generation = ?""",
                (
                    assignment.scope_id,
                    assignment.thread_id,
                    assignment.requirement.work_ref.entity_type,
                    assignment.requirement.work_ref.entity_id,
                    assignment.requirement.role_ref.entity_id,
                    assignment.generation,
                ),
            ).fetchone()
            if slot is not None:
                raise RoleAssignmentStoreConflictError(
                    "同一 work/role generation 已有 Assignment"
                )

            if assignment.supersedes_ref is not None:
                previous = self._assignment_by_id(
                    uow, assignment.supersedes_ref.entity_id
                )
                if previous is None:
                    raise RoleAssignmentStoreConflictError(
                        "supersedes_ref 指向的 Assignment 不存在"
                    )
                previous_key = (
                    previous.scope_id,
                    previous.thread_id,
                    previous.requirement.work_ref,
                    previous.requirement.role_ref,
                    previous.generation + 1,
                )
                requested_key = (
                    assignment.scope_id,
                    assignment.thread_id,
                    assignment.requirement.work_ref,
                    assignment.requirement.role_ref,
                    assignment.generation,
                )
                if previous_key != requested_key:
                    raise RoleAssignmentStoreConflictError(
                        "superseding Assignment 必须延续同一 work/role 的下一代"
                    )

            selected_agent_id = None
            selected_session_id = None
            selected_profile_id = None
            if assignment.selected_agent_instance_ref is not None:
                selected_agent_id = assignment.selected_agent_instance_ref.entity_id
                selected_session_id = assignment.selected_agent_session_ref.entity_id
                selected_profile_id = assignment.selected_profile_ref.entity_id
                record = self._agent_store._required_record(
                    uow,
                    assignment.scope_id,
                    assignment.thread_id,
                    selected_agent_id,
                )
                if (
                    record.instance.reference
                    != assignment.selected_agent_instance_ref
                    or record.instance.profile_ref != assignment.selected_profile_ref
                ):
                    raise RoleAssignmentStoreValidationError(
                        "selected Agent/Profile 持久绑定不一致"
                    )
                if (
                    record.session.reference
                    != assignment.selected_agent_session_ref
                    or record.session.state is not AgentSessionState.ACTIVE
                ):
                    raise RoleAssignmentStoreConflictError(
                        "selected Agent 候选快照已过期或 Session 非 active"
                    )

            raw = canonical_json(dict(assignment.to_dict()))
            try:
                uow._execute_managed(
                    """INSERT INTO runtime_role_assignments(
                        assignment_id, scope_id, thread_id, work_type, work_id,
                        role_id, generation, decision,
                        selected_agent_instance_id, selected_agent_session_id,
                        selected_profile_id, supersedes_assignment_id, created_at,
                        assignment_json, assignment_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        assignment.assignment_id,
                        assignment.scope_id,
                        assignment.thread_id,
                        assignment.requirement.work_ref.entity_type,
                        assignment.requirement.work_ref.entity_id,
                        assignment.requirement.role_ref.entity_id,
                        assignment.generation,
                        assignment.decision.value,
                        selected_agent_id,
                        selected_session_id,
                        selected_profile_id,
                        (
                            None
                            if assignment.supersedes_ref is None
                            else assignment.supersedes_ref.entity_id
                        ),
                        assignment.created_at,
                        raw,
                        text_digest(raw),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RoleAssignmentStoreConflictError(
                    "RoleAssignment 持久约束冲突"
                ) from exc
            return AssignmentRecordResult.CREATED
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def get_assignment(
        self,
        *,
        scope_id: str,
        thread_id: str,
        assignment_id: str,
    ) -> RoleAssignment | None:
        locator = (
            nonempty(scope_id, "scope_id"),
            nonempty(thread_id, "thread_id"),
            nonempty(assignment_id, "assignment_id"),
        )
        connection = self._open_read_connection()
        try:
            assignment = self._assignment_by_id(connection, locator[2])
            if assignment is None:
                return None
            if (assignment.scope_id, assignment.thread_id) != locator[:2]:
                raise RoleAssignmentStoreValidationError(
                    "Assignment 读取跨越 Scope 或 Thread 边界"
                )
            return assignment
        finally:
            connection.close()

    def list_assignments(
        self,
        *,
        scope_id: str,
        thread_id: str,
    ) -> tuple[RoleAssignment, ...]:
        locator = (
            nonempty(scope_id, "scope_id"),
            nonempty(thread_id, "thread_id"),
        )
        connection = self._open_read_connection()
        try:
            rows = connection.execute(
                """SELECT assignment_id, scope_id, thread_id, work_type, work_id,
                          role_id, generation, decision,
                          selected_agent_instance_id, selected_agent_session_id,
                          selected_profile_id, supersedes_assignment_id,
                          created_at, assignment_json, assignment_digest
                   FROM runtime_role_assignments
                   WHERE scope_id = ? AND thread_id = ?
                   ORDER BY created_at, generation, assignment_id""",
                locator,
            ).fetchall()
            return tuple(self._decode_row(row) for row in rows)
        finally:
            connection.close()

    def _verify_connection(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """SELECT assignment_id, scope_id, thread_id, work_type, work_id,
                      role_id, generation, decision,
                      selected_agent_instance_id, selected_agent_session_id,
                      selected_profile_id, supersedes_assignment_id, created_at,
                      assignment_json, assignment_digest
               FROM runtime_role_assignments
               ORDER BY assignment_id"""
        ).fetchall()
        assignments = {
            assignment.assignment_id: assignment
            for assignment in (self._decode_row(row) for row in rows)
        }
        for assignment in assignments.values():
            if assignment.selected_agent_instance_ref is not None:
                record = self._agent_store._required_record(
                    connection,
                    assignment.scope_id,
                    assignment.thread_id,
                    assignment.selected_agent_instance_ref.entity_id,
                )
                if (
                    record.session.agent_session_id
                    != assignment.selected_agent_session_ref.entity_id
                    or record.instance.profile_ref != assignment.selected_profile_ref
                ):
                    raise RuntimeStoredDataCorruptionError(
                        "RoleAssignment selected Agent 持久绑定漂移"
                    )
            if assignment.supersedes_ref is not None:
                previous = assignments.get(assignment.supersedes_ref.entity_id)
                if previous is None or (
                    previous.scope_id,
                    previous.thread_id,
                    previous.requirement.work_ref,
                    previous.requirement.role_ref,
                    previous.generation + 1,
                ) != (
                    assignment.scope_id,
                    assignment.thread_id,
                    assignment.requirement.work_ref,
                    assignment.requirement.role_ref,
                    assignment.generation,
                ):
                    raise RuntimeStoredDataCorruptionError(
                        "RoleAssignment supersedes 因果链漂移"
                    )

    def _assignment_by_id(self, reader, assignment_id: str) -> RoleAssignment | None:
        row = reader.execute(
            """SELECT assignment_id, scope_id, thread_id, work_type, work_id,
                      role_id, generation, decision, selected_agent_instance_id,
                      selected_agent_session_id, selected_profile_id,
                      supersedes_assignment_id, created_at, assignment_json,
                      assignment_digest
               FROM runtime_role_assignments WHERE assignment_id = ?""",
            (assignment_id,),
        ).fetchone()
        return None if row is None else self._decode_row(row)

    @staticmethod
    def _decode_row(row) -> RoleAssignment:
        try:
            raw = str(row[13])
            decoded = json.loads(raw)
            if canonical_json(decoded) != raw or text_digest(raw) != row[14]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_role_assignments canonical JSON/digest 漂移"
                )
            assignment = RoleAssignment.from_dict(decoded)
            expected = (
                assignment.assignment_id,
                assignment.scope_id,
                assignment.thread_id,
                assignment.requirement.work_ref.entity_type,
                assignment.requirement.work_ref.entity_id,
                assignment.requirement.role_ref.entity_id,
                assignment.generation,
                assignment.decision.value,
                None if assignment.selected_agent_instance_ref is None else assignment.selected_agent_instance_ref.entity_id,
                None if assignment.selected_agent_session_ref is None else assignment.selected_agent_session_ref.entity_id,
                None if assignment.selected_profile_ref is None else assignment.selected_profile_ref.entity_id,
                None if assignment.supersedes_ref is None else assignment.supersedes_ref.entity_id,
                assignment.created_at,
            )
            if tuple(row[:13]) != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_role_assignments projection 与 JSON 不一致"
                )
            return assignment
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_role_assignments 无法重建 RoleAssignment"
            ) from exc

    def _open_read_connection(self):
        self._database._require_outbox_policy()
        connection = self._database._connect()
        try:
            self._database._assert_wal(connection)
            state = self._database._inspect_schema(connection)
            if state is None:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_kernel schema 尚未初始化"
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

    def _require_uow(self, uow: RuntimeUnitOfWork) -> None:
        if not isinstance(uow, RuntimeUnitOfWork):
            raise RoleAssignmentStoreValidationError(
                "uow 必须是 RuntimeUnitOfWork"
            )
        if uow._database is not self._database:
            raise RoleAssignmentStoreValidationError(
                "uow 与 SQLiteRoleAssignmentStore 必须属于同一数据库"
            )


__all__ = [
    "AssignmentRecordResult",
    "RoleAssignmentStoreConflictError",
    "RoleAssignmentStoreError",
    "RoleAssignmentStoreValidationError",
    "SQLiteRoleAssignmentStore",
]
