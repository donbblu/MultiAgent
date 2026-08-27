from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from .runtime_domain import (
    AgentInstance,
    AgentSession,
    AgentSessionState,
    Message,
    ScopedRef,
)
from .runtime_persistence import (
    MailboxCursor,
    MailboxDelivery,
    MailboxSendResult,
    SQLiteMailboxStore,
    SQLiteRuntimeDatabase,
)
from .runtime_persistence.agent import (
    AgentClosedError,
    AgentPausedError,
    AgentPrivateState,
    AgentRecord,
    SQLiteAgentStore,
)


AgentClock = Callable[[], str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentManager:
    """Application API for the 01A durable single-session Agent lifecycle."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        clock: AgentClock | None = None,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database 必须是 SQLiteRuntimeDatabase")
        if clock is not None and not callable(clock):
            raise TypeError("clock 必须可调用")
        self._database = database
        self._store = SQLiteAgentStore(database)
        self._clock = clock or _utc_now

    @property
    def store(self) -> SQLiteAgentStore:
        return self._store

    @property
    def database(self) -> SQLiteRuntimeDatabase:
        return self._database

    def create_agent(
        self,
        *,
        agent_instance_id: str,
        agent_session_id: str,
        scope_id: str,
        thread_ref: ScopedRef,
        profile_ref: ScopedRef,
        principal_id: str,
        created_at: str | None = None,
    ) -> AgentRecord:
        instant = created_at or self._clock()
        instance = AgentInstance(
            agent_instance_id=agent_instance_id,
            scope_id=scope_id,
            thread_ref=thread_ref,
            profile_ref=profile_ref,
            principal_id=principal_id,
            created_at=instant,
        )
        session = AgentSession(
            agent_session_id=agent_session_id,
            scope_id=scope_id,
            thread_ref=thread_ref,
            agent_instance_ref=instance.reference,
            created_at=instant,
            updated_at=instant,
        )
        with self._database.unit_of_work() as uow:
            self._store.create(uow, instance, session)
            uow.commit()
        return AgentRecord(instance, session)

    def get_agent(
        self,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> AgentRecord | None:
        return self._store.get_agent(scope_id, thread_id, agent_instance_id)

    def pause_agent(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        expected_session_version: int,
        updated_at: str | None = None,
    ) -> AgentSession:
        return self._transition(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
            expected_session_version=expected_session_version,
            target_state=AgentSessionState.PAUSED,
            updated_at=updated_at,
        )

    def resume_agent(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        expected_session_version: int,
        updated_at: str | None = None,
    ) -> AgentSession:
        return self._transition(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
            expected_session_version=expected_session_version,
            target_state=AgentSessionState.ACTIVE,
            updated_at=updated_at,
        )

    def close_agent(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        expected_session_version: int,
        updated_at: str | None = None,
    ) -> AgentSession:
        return self._transition(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
            expected_session_version=expected_session_version,
            target_state=AgentSessionState.CLOSED,
            updated_at=updated_at,
        )

    def write_private_state(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        value: object,
        expected_version: int,
        requesting_agent_instance_id: str | None = None,
        requesting_agent_session_id: str | None = None,
        updated_at: str | None = None,
    ) -> AgentPrivateState:
        with self._database.unit_of_work() as uow:
            snapshot = self._store.write_private_state(
                uow,
                scope_id=scope_id,
                thread_id=thread_id,
                agent_instance_id=agent_instance_id,
                agent_session_id=agent_session_id,
                requesting_agent_instance_id=(
                    agent_instance_id
                    if requesting_agent_instance_id is None
                    else requesting_agent_instance_id
                ),
                requesting_agent_session_id=(
                    agent_session_id
                    if requesting_agent_session_id is None
                    else requesting_agent_session_id
                ),
                value=value,
                expected_version=expected_version,
                updated_at=updated_at or self._clock(),
            )
            uow.commit()
        return snapshot

    def read_private_state(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        requesting_agent_instance_id: str | None = None,
        requesting_agent_session_id: str | None = None,
    ) -> AgentPrivateState | None:
        return self._store.read_private_state(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
            requesting_agent_instance_id=(
                agent_instance_id
                if requesting_agent_instance_id is None
                else requesting_agent_instance_id
            ),
            requesting_agent_session_id=(
                agent_session_id
                if requesting_agent_session_id is None
                else requesting_agent_session_id
            ),
        )

    def require_work_admission(
        self,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
    ) -> AgentRecord:
        return self._store.require_work_admission(
            scope_id,
            thread_id,
            agent_instance_id,
        )

    def _transition(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        expected_session_version: int,
        target_state: AgentSessionState,
        updated_at: str | None,
    ) -> AgentSession:
        with self._database.unit_of_work() as uow:
            session = self._store.transition(
                uow,
                scope_id=scope_id,
                thread_id=thread_id,
                agent_instance_id=agent_instance_id,
                agent_session_id=agent_session_id,
                expected_version=expected_session_version,
                target_state=target_state,
                updated_at=updated_at or self._clock(),
            )
            uow.commit()
        return session


class MailboxManager:
    """Transactional application API for 01B persistent Agent mailboxes."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        clock: AgentClock | None = None,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database 必须是 SQLiteRuntimeDatabase")
        if clock is not None and not callable(clock):
            raise TypeError("clock 必须可调用")
        self._database = database
        self._store = SQLiteMailboxStore(database)
        self._agent_manager = AgentManager(database, clock=clock)
        self._clock = clock or _utc_now

    @property
    def store(self) -> SQLiteMailboxStore:
        return self._store

    @property
    def agent_manager(self) -> AgentManager:
        return self._agent_manager

    def send_message(
        self,
        message: Message,
        *,
        recipient_agent_instance_id: str,
        recipient_agent_session_id: str,
        enqueued_at: str | None = None,
    ) -> tuple[MailboxSendResult, MailboxDelivery]:
        with self._database.unit_of_work() as uow:
            result = self._store.send(
                uow,
                message=message,
                recipient_agent_instance_id=recipient_agent_instance_id,
                recipient_agent_session_id=recipient_agent_session_id,
                enqueued_at=enqueued_at or self._clock(),
            )
            uow.commit()
        return result

    def receive_next(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        consumed_at: str | None = None,
    ) -> MailboxDelivery | None:
        with self._database.unit_of_work() as uow:
            delivery = self._store.receive_next(
                uow,
                scope_id=scope_id,
                thread_id=thread_id,
                agent_instance_id=agent_instance_id,
                agent_session_id=agent_session_id,
                consumed_at=consumed_at or self._clock(),
            )
            uow.commit()
        return delivery

    def list_mailbox(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
    ) -> tuple[MailboxDelivery, ...]:
        return self._store.list_mailbox(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
        )

    def get_cursor(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
    ) -> MailboxCursor | None:
        return self._store.get_cursor(
            scope_id=scope_id,
            thread_id=thread_id,
            agent_instance_id=agent_instance_id,
            agent_session_id=agent_session_id,
        )


MailboxHandler = Callable[[MailboxDelivery], object]


class AgentLaneRuntime:
    """Shared thread pool with at most one active drain loop per Agent."""

    def __init__(
        self,
        mailbox: MailboxManager,
        *,
        max_workers: int = 4,
    ) -> None:
        if not isinstance(mailbox, MailboxManager):
            raise TypeError("mailbox 必须是 MailboxManager")
        if (
            not isinstance(max_workers, int)
            or isinstance(max_workers, bool)
            or max_workers < 1
        ):
            raise ValueError("max_workers 必须是大于 0 的整数")
        self._mailbox = mailbox
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-lane",
        )
        self._inflight: dict[tuple[str, str, str, str], Future[int]] = {}
        self._lock = RLock()
        self._closed = False

    def schedule(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        handler: MailboxHandler,
    ) -> Future[int]:
        if not callable(handler):
            raise TypeError("handler 必须可调用")
        key = (
            scope_id,
            thread_id,
            agent_instance_id,
            agent_session_id,
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("AgentLaneRuntime 已关闭")
            self._mailbox.agent_manager.require_work_admission(
                scope_id,
                thread_id,
                agent_instance_id,
            )
            current = self._inflight.get(key)
            if current is not None and not current.done():
                return current
            future = self._executor.submit(self._drain, key, handler)
            self._inflight[key] = future
            future.add_done_callback(
                lambda completed, lane_key=key: self._clear(lane_key, completed)
            )
            return future

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> "AgentLaneRuntime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.shutdown()
        return False

    def _drain(
        self,
        key: tuple[str, str, str, str],
        handler: MailboxHandler,
    ) -> int:
        processed = 0
        while True:
            try:
                delivery = self._mailbox.receive_next(
                    scope_id=key[0],
                    thread_id=key[1],
                    agent_instance_id=key[2],
                    agent_session_id=key[3],
                )
            except (AgentPausedError, AgentClosedError):
                return processed
            if delivery is None:
                return processed
            handler(delivery)
            processed += 1

    def _clear(
        self,
        key: tuple[str, str, str, str],
        completed: Future[int],
    ) -> None:
        with self._lock:
            if self._inflight.get(key) is completed:
                self._inflight.pop(key, None)


__all__ = [
    "AgentClock",
    "AgentLaneRuntime",
    "AgentManager",
    "MailboxHandler",
    "MailboxManager",
]
