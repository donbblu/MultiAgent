from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from ..runtime_domain.common import (
    RuntimeProtocolError,
    freeze_json,
    nonempty,
    positive_int,
    thaw_json,
    timestamp,
)
from ..runtime_domain.interaction import (
    AgentInstance,
    AgentSession,
    AgentSessionState,
    validate_agent_session_binding,
)
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .sqlite import (
    RuntimePersistenceError,
    RuntimeUnitOfWork,
    SQLiteRuntimeDatabase,
)
from .state_event import SQLiteThreadEventStore


_MAX_PRIVATE_STATE_BYTES = 64 * 1024


class AgentStoreError(RuntimePersistenceError):
    """Base error for the durable single-process Agent Store."""


class AgentStoreValidationError(AgentStoreError):
    """An Agent mutation or locator violates the frozen MVP contract."""


class AgentStoreConflictError(AgentStoreError):
    """A durable Agent identity or expected version conflicts."""


class AgentStoreAccessError(AgentStoreError, PermissionError):
    """A caller attempted to cross a Scope, Thread, Agent, or Session boundary."""


class AgentStateTransitionError(AgentStoreError):
    """An AgentSession lifecycle transition is not permitted."""


class AgentClosedError(AgentStoreError):
    """A closed Agent rejected new work or private-state writes."""


class AgentPausedError(AgentStoreError):
    """A paused Agent rejected new work admission."""


class AgentCreateResult(str, Enum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"


@dataclass(frozen=True)
class AgentRecord:
    instance: AgentInstance
    session: AgentSession

    def __post_init__(self) -> None:
        if not isinstance(self.instance, AgentInstance):
            raise AgentStoreValidationError("instance 必须是 AgentInstance")
        if not isinstance(self.session, AgentSession):
            raise AgentStoreValidationError("session 必须是 AgentSession")


@dataclass(frozen=True)
class AgentPrivateState:
    scope_id: str
    thread_id: str
    agent_instance_id: str
    agent_session_id: str
    value: object
    version: int
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_id", nonempty(self.scope_id, "scope_id"))
        object.__setattr__(self, "thread_id", nonempty(self.thread_id, "thread_id"))
        object.__setattr__(
            self,
            "agent_instance_id",
            nonempty(self.agent_instance_id, "agent_instance_id"),
        )
        object.__setattr__(
            self,
            "agent_session_id",
            nonempty(self.agent_session_id, "agent_session_id"),
        )
        object.__setattr__(self, "value", freeze_json(self.value, "private_state"))
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "updated_at", timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "scope_id": self.scope_id,
            "thread_id": self.thread_id,
            "agent_instance_id": self.agent_instance_id,
            "agent_session_id": self.agent_session_id,
            "value": thaw_json(self.value),
            "version": self.version,
            "updated_at": self.updated_at,
        })


class SQLiteAgentStore:
    """SQLite owner for one durable Session and private state per AgentInstance.

    This 01A slice deliberately has no Mailbox, scheduler, worker lane, or crash
    recovery.  It reuses the Runtime kernel migration ledger and UnitOfWork.
    """

    _TRANSITIONS = {
        AgentSessionState.ACTIVE: frozenset({
            AgentSessionState.PAUSED,
            AgentSessionState.CLOSED,
        }),
        AgentSessionState.PAUSED: frozenset({
            AgentSessionState.ACTIVE,
            AgentSessionState.CLOSED,
        }),
        AgentSessionState.CLOSED: frozenset(),
    }

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise AgentStoreValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database
        self._thread_store = SQLiteThreadEventStore(database)

    def create(
        self,
        uow: RuntimeUnitOfWork,
        instance: AgentInstance,
        session: AgentSession,
    ) -> AgentCreateResult:
        try:
            self._require_uow(uow)
            if not isinstance(instance, AgentInstance):
                raise AgentStoreValidationError(
                    "instance 必须是 AgentInstance"
                )
            if not isinstance(session, AgentSession):
                raise AgentStoreValidationError("session 必须是 AgentSession")
            if instance.version != 1 or session.version != 1:
                raise AgentStoreValidationError("Agent 创建版本必须为 1")
            if session.state is not AgentSessionState.ACTIVE:
                raise AgentStoreValidationError(
                    "AgentSession 创建时必须是 active"
                )

            requested = AgentRecord(instance, session)
            existing = self._record_by_agent_id(uow, instance.agent_instance_id)
            if existing is not None:
                if existing == requested:
                    return AgentCreateResult.ALREADY_EXISTS
                raise AgentStoreConflictError(
                    "agent_instance_id 已存在且内容不同"
                )
            session_owner = self._record_by_session_id(
                uow, session.agent_session_id
            )
            if session_owner is not None:
                raise AgentStoreConflictError(
                    "agent_session_id 已属于其他 Agent"
                )

            thread = self._load_thread(
                uow, instance.scope_id, instance.thread_id
            )
            validate_agent_session_binding(thread, instance, session)

            instance_json = canonical_json(dict(instance.to_dict()))
            session_json = canonical_json(dict(session.to_dict()))
            uow._execute_managed(
                """INSERT INTO runtime_agent_instances(
                    agent_instance_id, scope_id, thread_id, profile_id,
                    principal_id, version, created_at, instance_json,
                    instance_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instance.agent_instance_id,
                    instance.scope_id,
                    instance.thread_id,
                    instance.profile_ref.entity_id,
                    instance.principal_id,
                    instance.version,
                    instance.created_at,
                    instance_json,
                    text_digest(instance_json),
                ),
            )
            uow._execute_managed(
                """INSERT INTO runtime_agent_sessions(
                    agent_session_id, scope_id, thread_id, agent_instance_id,
                    state, version, created_at, updated_at, closed_at,
                    session_json, session_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.agent_session_id,
                    session.scope_id,
                    session.thread_id,
                    session.agent_instance_id,
                    session.state.value,
                    session.version,
                    session.created_at,
                    session.updated_at,
                    session.closed_at,
                    session_json,
                    text_digest(session_json),
                ),
            )
            return AgentCreateResult.CREATED
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def get_agent(
        self,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> AgentRecord | None:
        locator = self._locator(scope_id, thread_id, agent_instance_id)
        connection = self._open_read_connection()
        try:
            record = self._record_by_agent_id(connection, locator[2])
            if record is None:
                return None
            self._assert_record_location(record, *locator)
            return record
        finally:
            connection.close()

    def transition(
        self,
        uow: RuntimeUnitOfWork,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        expected_version: int,
        target_state: AgentSessionState,
        updated_at: str,
    ) -> AgentSession:
        try:
            self._require_uow(uow)
            locator = self._locator(scope_id, thread_id, agent_instance_id)
            session_id = nonempty(agent_session_id, "agent_session_id")
            if (
                not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 1
            ):
                raise AgentStoreValidationError(
                    "expected_version 必须是大于 0 的整数"
                )
            try:
                target = (
                    target_state
                    if isinstance(target_state, AgentSessionState)
                    else AgentSessionState(target_state)
                )
            except (TypeError, ValueError) as exc:
                raise AgentStoreValidationError("target_state 无效") from exc

            record = self._required_record(uow, *locator)
            if record.session.agent_session_id != session_id:
                raise AgentStoreAccessError(
                    "AgentSession 不属于请求的 Agent"
                )
            current = record.session
            if current.version != expected_version:
                raise AgentStoreConflictError(
                    "AgentSession expected_version 与持久版本不一致"
                )
            if target not in self._TRANSITIONS[current.state]:
                raise AgentStateTransitionError(
                    f"不允许 {current.state.value} -> {target.value}"
                )
            next_session = replace(
                current,
                state=target,
                version=current.version + 1,
                updated_at=updated_at,
                closed_at=updated_at if target is AgentSessionState.CLOSED else "",
            )
            if datetime.fromisoformat(next_session.updated_at) < datetime.fromisoformat(
                current.updated_at
            ):
                raise AgentStoreValidationError(
                    "AgentSession updated_at 不能早于当前版本"
                )
            session_json = canonical_json(dict(next_session.to_dict()))
            changed = uow._execute_managed(
                """UPDATE runtime_agent_sessions
                   SET state = ?, version = ?, updated_at = ?, closed_at = ?,
                       session_json = ?, session_digest = ?
                   WHERE agent_session_id = ? AND scope_id = ?
                     AND thread_id = ? AND agent_instance_id = ?
                     AND version = ?""",
                (
                    next_session.state.value,
                    next_session.version,
                    next_session.updated_at,
                    next_session.closed_at,
                    session_json,
                    text_digest(session_json),
                    session_id,
                    locator[0],
                    locator[1],
                    locator[2],
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise AgentStoreConflictError(
                    "AgentSession 状态更新 CAS 失败"
                )
            return next_session
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def write_private_state(
        self,
        uow: RuntimeUnitOfWork,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        requesting_agent_instance_id: str,
        requesting_agent_session_id: str,
        value: object,
        expected_version: int,
        updated_at: str,
    ) -> AgentPrivateState:
        try:
            self._require_uow(uow)
            locator = self._locator(scope_id, thread_id, agent_instance_id)
            session_id = nonempty(agent_session_id, "agent_session_id")
            self._assert_private_owner(
                locator[2],
                session_id,
                requesting_agent_instance_id,
                requesting_agent_session_id,
            )
            if (
                not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 0
            ):
                raise AgentStoreValidationError(
                    "expected_version 必须是大于等于 0 的整数"
                )
            record = self._required_record(uow, *locator)
            if record.session.agent_session_id != session_id:
                raise AgentStoreAccessError(
                    "AgentSession 不属于请求的 Agent"
                )
            if record.session.state is AgentSessionState.CLOSED:
                raise AgentClosedError("closed Agent 拒绝私有状态写入")

            row = self._private_state_row(uow, session_id)
            current = None if row is None else self._decode_private_state(row)
            current_version = 0 if current is None else current.version
            if current_version != expected_version:
                raise AgentStoreConflictError(
                    "private state expected_version 与持久版本不一致"
                )
            try:
                snapshot = AgentPrivateState(
                    scope_id=locator[0],
                    thread_id=locator[1],
                    agent_instance_id=locator[2],
                    agent_session_id=session_id,
                    value=value,
                    version=current_version + 1,
                    updated_at=updated_at,
                )
            except RuntimeProtocolError as exc:
                raise AgentStoreValidationError(
                    "private state 必须是受控 JSON 值"
                ) from exc
            minimum_updated_at = (
                record.session.created_at if current is None else current.updated_at
            )
            if datetime.fromisoformat(snapshot.updated_at) < datetime.fromisoformat(
                minimum_updated_at
            ):
                raise AgentStoreValidationError(
                    "private state updated_at 不能倒退"
                )
            raw = canonical_json(thaw_json(snapshot.value))
            if len(raw.encode("utf-8")) > _MAX_PRIVATE_STATE_BYTES:
                raise AgentStoreValidationError(
                    "private state 超过 64 KiB 上限"
                )
            if current is None:
                uow._execute_managed(
                    """INSERT INTO runtime_agent_private_state(
                        agent_session_id, scope_id, thread_id,
                        agent_instance_id, version, updated_at, state_json,
                        state_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        locator[0],
                        locator[1],
                        locator[2],
                        snapshot.version,
                        snapshot.updated_at,
                        raw,
                        text_digest(raw),
                    ),
                )
            else:
                changed = uow._execute_managed(
                    """UPDATE runtime_agent_private_state
                       SET version = ?, updated_at = ?, state_json = ?,
                           state_digest = ?
                       WHERE agent_session_id = ? AND version = ?""",
                    (
                        snapshot.version,
                        snapshot.updated_at,
                        raw,
                        text_digest(raw),
                        session_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise AgentStoreConflictError(
                        "private state 更新 CAS 失败"
                    )
            return snapshot
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def read_private_state(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        requesting_agent_instance_id: str,
        requesting_agent_session_id: str,
    ) -> AgentPrivateState | None:
        locator = self._locator(scope_id, thread_id, agent_instance_id)
        session_id = nonempty(agent_session_id, "agent_session_id")
        self._assert_private_owner(
            locator[2],
            session_id,
            requesting_agent_instance_id,
            requesting_agent_session_id,
        )
        connection = self._open_read_connection()
        try:
            record = self._required_record(connection, *locator)
            if record.session.agent_session_id != session_id:
                raise AgentStoreAccessError(
                    "AgentSession 不属于请求的 Agent"
                )
            row = self._private_state_row(connection, session_id)
            if row is None:
                return None
            snapshot = self._decode_private_state(row)
            if (
                snapshot.scope_id,
                snapshot.thread_id,
                snapshot.agent_instance_id,
                snapshot.agent_session_id,
            ) != (*locator, session_id):
                raise RuntimeStoredDataCorruptionError(
                    "private state 持有者投影漂移"
                )
            return snapshot
        finally:
            connection.close()

    def require_work_admission(
        self,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> AgentRecord:
        record = self.get_agent(scope_id, thread_id, agent_instance_id)
        if record is None:
            raise AgentStoreAccessError("Agent 不存在")
        if record.session.state is AgentSessionState.CLOSED:
            raise AgentClosedError("closed Agent 拒绝新工作")
        if record.session.state is AgentSessionState.PAUSED:
            raise AgentPausedError("paused Agent 拒绝领取新工作")
        return record

    def _verify_connection(self, connection: sqlite3.Connection) -> None:
        instance_rows = connection.execute(
            """SELECT agent_instance_id, scope_id, thread_id, profile_id,
                      principal_id, version, created_at, instance_json,
                      instance_digest
               FROM runtime_agent_instances ORDER BY agent_instance_id"""
        ).fetchall()
        instances = {
            row[0]: self._decode_instance_row(row) for row in instance_rows
        }
        session_rows = connection.execute(
            """SELECT agent_session_id, scope_id, thread_id,
                      agent_instance_id, state, version, created_at,
                      updated_at, closed_at, session_json, session_digest
               FROM runtime_agent_sessions ORDER BY agent_session_id"""
        ).fetchall()
        sessions: dict[str, AgentSession] = {}
        for row in session_rows:
            session = self._decode_session_row(row)
            instance = instances.get(session.agent_instance_id)
            if instance is None:
                raise RuntimeStoredDataCorruptionError(
                    "AgentSession 缺少 AgentInstance"
                )
            thread = self._load_thread(
                connection, instance.scope_id, instance.thread_id
            )
            try:
                validate_agent_session_binding(thread, instance, session)
            except (RuntimeProtocolError, TypeError) as exc:
                raise RuntimeStoredDataCorruptionError(
                    "AgentSession 持久绑定无效"
                ) from exc
            sessions[session.agent_session_id] = session
        if len(sessions) != len(instances):
            raise RuntimeStoredDataCorruptionError(
                "01A 每个 AgentInstance 必须恰好一个 Session"
            )
        state_rows = connection.execute(
            """SELECT agent_session_id, scope_id, thread_id,
                      agent_instance_id, version, updated_at, state_json,
                      state_digest
               FROM runtime_agent_private_state ORDER BY agent_session_id"""
        ).fetchall()
        for row in state_rows:
            snapshot = self._decode_private_state(row)
            session = sessions.get(snapshot.agent_session_id)
            if session is None or (
                session.scope_id,
                session.thread_id,
                session.agent_instance_id,
            ) != (
                snapshot.scope_id,
                snapshot.thread_id,
                snapshot.agent_instance_id,
            ):
                raise RuntimeStoredDataCorruptionError(
                    "private state 缺少正确 AgentSession"
                )

    def _open_read_connection(self) -> sqlite3.Connection:
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

    def _required_record(self, reader, *locator: str) -> AgentRecord:
        record = self._record_by_agent_id(reader, locator[2])
        if record is None:
            raise AgentStoreAccessError("Agent 不存在")
        self._assert_record_location(record, *locator)
        return record

    def _record_by_agent_id(self, reader, agent_instance_id: str) -> AgentRecord | None:
        instance_row = reader.execute(
            """SELECT agent_instance_id, scope_id, thread_id, profile_id,
                      principal_id, version, created_at, instance_json,
                      instance_digest
               FROM runtime_agent_instances WHERE agent_instance_id = ?""",
            (agent_instance_id,),
        ).fetchone()
        if instance_row is None:
            return None
        instance = self._decode_instance_row(instance_row)
        session_rows = reader.execute(
            """SELECT agent_session_id, scope_id, thread_id,
                      agent_instance_id, state, version, created_at,
                      updated_at, closed_at, session_json, session_digest
               FROM runtime_agent_sessions WHERE agent_instance_id = ?""",
            (agent_instance_id,),
        ).fetchall()
        if len(session_rows) != 1:
            raise RuntimeStoredDataCorruptionError(
                "01A AgentInstance 必须恰好有一个 Session"
            )
        return AgentRecord(instance, self._decode_session_row(session_rows[0]))

    def _record_by_session_id(self, reader, agent_session_id: str) -> AgentRecord | None:
        row = reader.execute(
            """SELECT agent_instance_id FROM runtime_agent_sessions
               WHERE agent_session_id = ?""",
            (agent_session_id,),
        ).fetchone()
        return None if row is None else self._record_by_agent_id(reader, row[0])

    def _load_thread(self, reader, scope_id: str, thread_id: str):
        row = reader.execute(
            """SELECT scope_id, entity_type, thread_id, version, state,
                      created_at, updated_at, archived_at, thread_json,
                      thread_digest, last_sequence_no, last_event_id
               FROM runtime_threads WHERE scope_id = ? AND thread_id = ?""",
            (scope_id, thread_id),
        ).fetchone()
        if row is None:
            raise AgentStoreValidationError(
                "Agent 必须绑定已持久的 Thread"
            )
        return self._thread_store._decode_thread_row(reader, row)

    @staticmethod
    def _decode_instance_row(row) -> AgentInstance:
        try:
            raw = str(row[7])
            decoded = json.loads(raw)
            if canonical_json(decoded) != raw or text_digest(raw) != row[8]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_instances canonical JSON/digest 漂移"
                )
            instance = AgentInstance.from_dict(decoded)
            expected = (
                instance.agent_instance_id,
                instance.scope_id,
                instance.thread_id,
                instance.profile_ref.entity_id,
                instance.principal_id,
                instance.version,
                instance.created_at,
            )
            if tuple(row[:7]) != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_instances projection 与 JSON 不一致"
                )
            return instance
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_instances 无法重建 AgentInstance"
            ) from exc

    @staticmethod
    def _decode_session_row(row) -> AgentSession:
        try:
            raw = str(row[9])
            decoded = json.loads(raw)
            if canonical_json(decoded) != raw or text_digest(raw) != row[10]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_sessions canonical JSON/digest 漂移"
                )
            session = AgentSession.from_dict(decoded)
            expected = (
                session.agent_session_id,
                session.scope_id,
                session.thread_id,
                session.agent_instance_id,
                session.state.value,
                session.version,
                session.created_at,
                session.updated_at,
                session.closed_at,
            )
            if tuple(row[:9]) != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_sessions projection 与 JSON 不一致"
                )
            return session
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_sessions 无法重建 AgentSession"
            ) from exc

    @staticmethod
    def _private_state_row(reader, agent_session_id: str):
        return reader.execute(
            """SELECT agent_session_id, scope_id, thread_id,
                      agent_instance_id, version, updated_at, state_json,
                      state_digest
               FROM runtime_agent_private_state WHERE agent_session_id = ?""",
            (agent_session_id,),
        ).fetchone()

    @staticmethod
    def _decode_private_state(row) -> AgentPrivateState:
        try:
            raw = str(row[6])
            value = json.loads(raw)
            if canonical_json(value) != raw or text_digest(raw) != row[7]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_private_state canonical JSON/digest 漂移"
                )
            return AgentPrivateState(
                agent_session_id=row[0],
                scope_id=row[1],
                thread_id=row[2],
                agent_instance_id=row[3],
                version=row[4],
                updated_at=row[5],
                value=value,
            )
        except RuntimeStoredDataCorruptionError:
            raise
        except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_private_state 无法重建"
            ) from exc

    @staticmethod
    def _locator(
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> tuple[str, str, str]:
        try:
            return (
                nonempty(scope_id, "scope_id"),
                nonempty(thread_id, "thread_id"),
                nonempty(agent_instance_id, "agent_instance_id"),
            )
        except RuntimeProtocolError as exc:
            raise AgentStoreValidationError("Agent locator 无效") from exc

    @staticmethod
    def _assert_record_location(
        record: AgentRecord,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> None:
        actual = (
            record.instance.scope_id,
            record.instance.thread_id,
            record.instance.agent_instance_id,
        )
        if actual != (scope_id, thread_id, agent_instance_id):
            raise AgentStoreAccessError(
                "Agent 读取跨越 Scope 或 Thread 边界"
            )

    @staticmethod
    def _assert_private_owner(
        agent_instance_id: str,
        agent_session_id: str,
        requesting_agent_instance_id: str,
        requesting_agent_session_id: str,
    ) -> None:
        try:
            requester = (
                nonempty(
                    requesting_agent_instance_id,
                    "requesting_agent_instance_id",
                ),
                nonempty(
                    requesting_agent_session_id,
                    "requesting_agent_session_id",
                ),
            )
        except RuntimeProtocolError as exc:
            raise AgentStoreValidationError("private state requester 无效") from exc
        if requester != (agent_instance_id, agent_session_id):
            raise AgentStoreAccessError(
                "跨 Agent/Session 私有状态读写默认拒绝"
            )

    def _require_uow(self, uow: RuntimeUnitOfWork) -> None:
        if not isinstance(uow, RuntimeUnitOfWork):
            raise AgentStoreValidationError("uow 必须是 RuntimeUnitOfWork")
        if uow._database is not self._database:
            raise AgentStoreValidationError(
                "uow 与 SQLiteAgentStore 必须属于同一数据库"
            )


__all__ = [
    "AgentClosedError",
    "AgentCreateResult",
    "AgentPausedError",
    "AgentPrivateState",
    "AgentRecord",
    "AgentStateTransitionError",
    "AgentStoreAccessError",
    "AgentStoreConflictError",
    "AgentStoreError",
    "AgentStoreValidationError",
    "SQLiteAgentStore",
]
