from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..runtime_domain.common import (
    RuntimeProtocolError,
    nonempty,
    positive_int,
    timestamp,
)
from ..runtime_domain.interaction import AgentSessionState, Message
from ._record_codec import (
    RuntimeStoredDataCorruptionError,
    canonical_json,
    text_digest,
)
from .agent import (
    AgentClosedError,
    AgentPausedError,
    AgentRecord,
    AgentStoreAccessError,
    SQLiteAgentStore,
)
from .sqlite import RuntimePersistenceError, RuntimeUnitOfWork, SQLiteRuntimeDatabase


class MailboxError(RuntimePersistenceError):
    """Base error for the durable Agent Mailbox."""


class MailboxValidationError(MailboxError):
    """A Message or mailbox locator violates the 01B contract."""


class MailboxConflictError(MailboxError):
    """A message identity or durable cursor conflicts."""


class MailboxSendResult(str, Enum):
    ENQUEUED = "enqueued"
    ALREADY_ENQUEUED = "already_enqueued"


@dataclass(frozen=True)
class MailboxCursor:
    scope_id: str
    thread_id: str
    agent_instance_id: str
    agent_session_id: str
    last_enqueued_sequence: int
    consume_cursor: int
    version: int
    updated_at: str

    def __post_init__(self) -> None:
        for field_name in (
            "scope_id",
            "thread_id",
            "agent_instance_id",
            "agent_session_id",
        ):
            object.__setattr__(
                self,
                field_name,
                nonempty(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "last_enqueued_sequence",
            positive_int(
                self.last_enqueued_sequence,
                "last_enqueued_sequence",
                allow_zero=True,
            ),
        )
        object.__setattr__(
            self,
            "consume_cursor",
            positive_int(self.consume_cursor, "consume_cursor", allow_zero=True),
        )
        if self.consume_cursor > self.last_enqueued_sequence:
            raise RuntimeProtocolError(
                "consume_cursor 不能超过 last_enqueued_sequence"
            )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        object.__setattr__(self, "updated_at", timestamp(self.updated_at, "updated_at"))


@dataclass(frozen=True)
class MailboxDelivery:
    message: Message
    recipient_agent_instance_id: str
    recipient_agent_session_id: str
    mailbox_sequence: int
    enqueued_at: str
    consumed_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.message, Message):
            raise MailboxValidationError("message 必须是 Message")
        object.__setattr__(
            self,
            "recipient_agent_instance_id",
            nonempty(
                self.recipient_agent_instance_id,
                "recipient_agent_instance_id",
            ),
        )
        object.__setattr__(
            self,
            "recipient_agent_session_id",
            nonempty(
                self.recipient_agent_session_id,
                "recipient_agent_session_id",
            ),
        )
        object.__setattr__(
            self,
            "mailbox_sequence",
            positive_int(self.mailbox_sequence, "mailbox_sequence"),
        )
        object.__setattr__(self, "enqueued_at", timestamp(self.enqueued_at, "enqueued_at"))
        if self.consumed_at:
            object.__setattr__(
                self,
                "consumed_at",
                timestamp(self.consumed_at, "consumed_at"),
            )
            if datetime.fromisoformat(self.consumed_at) < datetime.fromisoformat(
                self.enqueued_at
            ):
                raise MailboxValidationError(
                    "consumed_at 不能早于 enqueued_at"
                )

    @property
    def consumed(self) -> bool:
        return bool(self.consumed_at)


class SQLiteMailboxStore:
    """Persistent single-recipient FIFO mailboxes with receive-time cursors.

    Receiving advances the durable cursor in the same transaction.  The 01B
    MVP intentionally has no ack, retry lease, or crash redelivery semantics.
    """

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise MailboxValidationError(
                "database 必须是 SQLiteRuntimeDatabase"
            )
        self._database = database
        self._agent_store = SQLiteAgentStore(database)

    def send(
        self,
        uow: RuntimeUnitOfWork,
        *,
        message: Message,
        recipient_agent_instance_id: str,
        recipient_agent_session_id: str,
        enqueued_at: str,
    ) -> tuple[MailboxSendResult, MailboxDelivery]:
        try:
            self._require_uow(uow)
            if not isinstance(message, Message):
                raise MailboxValidationError("message 必须是 Message")
            recipient_id = nonempty(
                recipient_agent_instance_id,
                "recipient_agent_instance_id",
            )
            recipient_session_id = nonempty(
                recipient_agent_session_id,
                "recipient_agent_session_id",
            )
            enqueue_time = timestamp(enqueued_at, "enqueued_at")
            if datetime.fromisoformat(enqueue_time) < datetime.fromisoformat(
                message.created_at
            ):
                raise MailboxValidationError(
                    "enqueued_at 不能早于 Message.created_at"
                )
            self._validate_message_shape(message, recipient_id)

            existing_row = self._message_row(uow, message.message_id)
            if existing_row is not None:
                existing = self._decode_message_row(existing_row)
                if (
                    existing.message == message
                    and existing.recipient_agent_instance_id == recipient_id
                    and existing.recipient_agent_session_id
                    == recipient_session_id
                ):
                    return MailboxSendResult.ALREADY_ENQUEUED, existing
                raise MailboxConflictError(
                    "message_id 已存在且持久内容不同"
                )

            recipient = self._agent_store._required_record(
                uow,
                message.scope_id,
                message.thread_id,
                recipient_id,
            )
            self._assert_session(recipient, recipient_session_id)
            sender_id = message.sender_ref.entity_id
            sender = self._agent_store._required_record(
                uow,
                message.scope_id,
                message.thread_id,
                sender_id,
            )
            self._assert_message_participants(message, sender, recipient)
            if sender.session.state is AgentSessionState.CLOSED:
                raise AgentClosedError("closed sender Agent 拒绝发送消息")
            if recipient.session.state is AgentSessionState.CLOSED:
                raise AgentClosedError("closed recipient Agent 拒绝新投递")

            cursor = self._cursor_by_agent_id(uow, recipient_id)
            if cursor is None:
                cursor = MailboxCursor(
                    scope_id=message.scope_id,
                    thread_id=message.thread_id,
                    agent_instance_id=recipient_id,
                    agent_session_id=recipient_session_id,
                    last_enqueued_sequence=0,
                    consume_cursor=0,
                    version=1,
                    updated_at=enqueue_time,
                )
                self._insert_cursor(uow, cursor)
            else:
                self._assert_cursor_owner(cursor, recipient, recipient_session_id)
                if datetime.fromisoformat(enqueue_time) < datetime.fromisoformat(
                    cursor.updated_at
                ):
                    raise MailboxValidationError(
                        "Mailbox enqueued_at 不能倒退"
                    )

            delivery = MailboxDelivery(
                message=message,
                recipient_agent_instance_id=recipient_id,
                recipient_agent_session_id=recipient_session_id,
                mailbox_sequence=cursor.last_enqueued_sequence + 1,
                enqueued_at=enqueue_time,
            )
            raw = canonical_json(dict(message.to_dict()))
            uow._execute_managed(
                """INSERT INTO runtime_agent_messages(
                    message_id, scope_id, thread_id,
                    recipient_agent_instance_id, recipient_agent_session_id,
                    sender_agent_instance_id, mailbox_sequence, enqueued_at,
                    consumed_at, message_json, message_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                (
                    message.message_id,
                    message.scope_id,
                    message.thread_id,
                    recipient_id,
                    recipient_session_id,
                    sender_id,
                    delivery.mailbox_sequence,
                    enqueue_time,
                    raw,
                    text_digest(raw),
                ),
            )
            changed = uow._execute_managed(
                """UPDATE runtime_agent_mailbox_cursors
                   SET last_enqueued_sequence = ?, version = ?, updated_at = ?
                   WHERE agent_instance_id = ? AND version = ?""",
                (
                    delivery.mailbox_sequence,
                    cursor.version + 1,
                    enqueue_time,
                    recipient_id,
                    cursor.version,
                ),
            ).rowcount
            if changed != 1:
                raise MailboxConflictError("Mailbox enqueue cursor CAS 失败")
            return MailboxSendResult.ENQUEUED, delivery
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def receive_next(
        self,
        uow: RuntimeUnitOfWork,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
        consumed_at: str,
    ) -> MailboxDelivery | None:
        try:
            self._require_uow(uow)
            locator = self._agent_store._locator(
                scope_id,
                thread_id,
                agent_instance_id,
            )
            session_id = nonempty(agent_session_id, "agent_session_id")
            consume_time = timestamp(consumed_at, "consumed_at")
            record = self._agent_store._required_record(uow, *locator)
            self._assert_session(record, session_id)
            if record.session.state is AgentSessionState.CLOSED:
                raise AgentClosedError("closed Agent 拒绝领取消息")
            if record.session.state is AgentSessionState.PAUSED:
                raise AgentPausedError("paused Agent 拒绝领取消息")

            cursor = self._cursor_by_agent_id(uow, locator[2])
            if cursor is None:
                return None
            self._assert_cursor_owner(cursor, record, session_id)
            if datetime.fromisoformat(consume_time) < datetime.fromisoformat(
                cursor.updated_at
            ):
                raise MailboxValidationError(
                    "Mailbox consumed_at 不能倒退"
                )
            next_sequence = cursor.consume_cursor + 1
            if next_sequence > cursor.last_enqueued_sequence:
                return None
            row = uow.execute(
                """SELECT message_id, scope_id, thread_id,
                          recipient_agent_instance_id,
                          recipient_agent_session_id,
                          sender_agent_instance_id, mailbox_sequence,
                          enqueued_at, consumed_at, message_json,
                          message_digest
                   FROM runtime_agent_messages
                   WHERE recipient_agent_instance_id = ?
                     AND mailbox_sequence = ?""",
                (locator[2], next_sequence),
            ).fetchone()
            if row is None:
                raise RuntimeStoredDataCorruptionError(
                    "Mailbox cursor 指向缺失消息"
                )
            pending = self._decode_message_row(row)
            if pending.consumed:
                raise RuntimeStoredDataCorruptionError(
                    "Mailbox cursor 指向已消费消息"
                )
            if datetime.fromisoformat(consume_time) < datetime.fromisoformat(
                pending.enqueued_at
            ):
                raise MailboxValidationError(
                    "consumed_at 不能早于 enqueued_at"
                )
            changed_message = uow._execute_managed(
                """UPDATE runtime_agent_messages SET consumed_at = ?
                   WHERE message_id = ? AND consumed_at = ''""",
                (consume_time, pending.message.message_id),
            ).rowcount
            changed_cursor = uow._execute_managed(
                """UPDATE runtime_agent_mailbox_cursors
                   SET consume_cursor = ?, version = ?, updated_at = ?
                   WHERE agent_instance_id = ? AND version = ?
                     AND consume_cursor = ?""",
                (
                    next_sequence,
                    cursor.version + 1,
                    consume_time,
                    locator[2],
                    cursor.version,
                    cursor.consume_cursor,
                ),
            ).rowcount
            if changed_message != 1 or changed_cursor != 1:
                raise MailboxConflictError("Mailbox receive cursor CAS 失败")
            return MailboxDelivery(
                message=pending.message,
                recipient_agent_instance_id=pending.recipient_agent_instance_id,
                recipient_agent_session_id=pending.recipient_agent_session_id,
                mailbox_sequence=pending.mailbox_sequence,
                enqueued_at=pending.enqueued_at,
                consumed_at=consume_time,
            )
        except BaseException:
            if isinstance(uow, RuntimeUnitOfWork):
                uow._abort_managed_operation()
            raise

    def list_mailbox(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
    ) -> tuple[MailboxDelivery, ...]:
        locator = self._agent_store._locator(
            scope_id,
            thread_id,
            agent_instance_id,
        )
        session_id = nonempty(agent_session_id, "agent_session_id")
        connection = self._open_read_connection()
        try:
            record = self._agent_store._required_record(connection, *locator)
            self._assert_session(record, session_id)
            rows = connection.execute(
                """SELECT message_id, scope_id, thread_id,
                          recipient_agent_instance_id,
                          recipient_agent_session_id,
                          sender_agent_instance_id, mailbox_sequence,
                          enqueued_at, consumed_at, message_json,
                          message_digest
                   FROM runtime_agent_messages
                   WHERE recipient_agent_instance_id = ?
                   ORDER BY mailbox_sequence""",
                (locator[2],),
            ).fetchall()
            return tuple(self._decode_message_row(row) for row in rows)
        finally:
            connection.close()

    def get_cursor(
        self,
        *,
        scope_id: str,
        thread_id: str,
        agent_instance_id: str,
        agent_session_id: str,
    ) -> MailboxCursor | None:
        locator = self._agent_store._locator(
            scope_id,
            thread_id,
            agent_instance_id,
        )
        session_id = nonempty(agent_session_id, "agent_session_id")
        connection = self._open_read_connection()
        try:
            record = self._agent_store._required_record(connection, *locator)
            self._assert_session(record, session_id)
            cursor = self._cursor_by_agent_id(connection, locator[2])
            if cursor is not None:
                self._assert_cursor_owner(cursor, record, session_id)
            return cursor
        finally:
            connection.close()

    def _verify_connection(self, connection: sqlite3.Connection) -> None:
        try:
            self._verify_records(connection)
        except RuntimeStoredDataCorruptionError:
            raise
        except (
            MailboxError,
            RuntimePersistenceError,
            RuntimeProtocolError,
            TypeError,
            ValueError,
            sqlite3.DatabaseError,
        ) as exc:
            raise RuntimeStoredDataCorruptionError(
                "Agent Mailbox 持久不变量无效"
            ) from exc

    def _verify_records(self, connection: sqlite3.Connection) -> None:
        cursor_rows = connection.execute(
            """SELECT agent_instance_id, scope_id, thread_id,
                      agent_session_id, last_enqueued_sequence,
                      consume_cursor, version, updated_at
               FROM runtime_agent_mailbox_cursors ORDER BY agent_instance_id"""
        ).fetchall()
        for row in cursor_rows:
            cursor = self._decode_cursor_row(row)
            record = self._agent_store._required_record(
                connection,
                cursor.scope_id,
                cursor.thread_id,
                cursor.agent_instance_id,
            )
            self._assert_cursor_owner(cursor, record, cursor.agent_session_id)
            rows = connection.execute(
                """SELECT message_id, scope_id, thread_id,
                          recipient_agent_instance_id,
                          recipient_agent_session_id,
                          sender_agent_instance_id, mailbox_sequence,
                          enqueued_at, consumed_at, message_json,
                          message_digest
                   FROM runtime_agent_messages
                   WHERE recipient_agent_instance_id = ?
                   ORDER BY mailbox_sequence""",
                (cursor.agent_instance_id,),
            ).fetchall()
            deliveries = tuple(self._decode_message_row(item) for item in rows)
            if tuple(item.mailbox_sequence for item in deliveries) != tuple(
                range(1, cursor.last_enqueued_sequence + 1)
            ):
                raise RuntimeStoredDataCorruptionError(
                    "Mailbox sequence 不连续"
                )
            for delivery in deliveries:
                expected_consumed = delivery.mailbox_sequence <= cursor.consume_cursor
                if delivery.consumed != expected_consumed:
                    raise RuntimeStoredDataCorruptionError(
                        "Mailbox consume cursor 与消息状态不一致"
                    )
                sender = self._agent_store._required_record(
                    connection,
                    delivery.message.scope_id,
                    delivery.message.thread_id,
                    delivery.message.sender_ref.entity_id,
                )
                self._assert_message_participants(delivery.message, sender, record)
        orphan = connection.execute(
            """SELECT message_id FROM runtime_agent_messages AS message
               WHERE NOT EXISTS (
                   SELECT 1 FROM runtime_agent_mailbox_cursors AS cursor
                   WHERE cursor.agent_instance_id =
                         message.recipient_agent_instance_id
               ) LIMIT 1"""
        ).fetchone()
        if orphan is not None:
            raise RuntimeStoredDataCorruptionError(
                f"Mailbox Message 缺少 cursor: {orphan[0]}"
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

    @staticmethod
    def _validate_message_shape(message: Message, recipient_id: str) -> None:
        if message.sender_ref.entity_type != "core:agent_instance":
            raise MailboxValidationError(
                "01B Mailbox sender 必须是 AgentInstance"
            )
        if len(message.recipient_refs) != 1:
            raise MailboxValidationError(
                "01B Mailbox 只支持单recipient Agent Message"
            )
        recipient = message.recipient_refs[0]
        if (
            recipient.entity_type != "core:agent_instance"
            or recipient.entity_id != recipient_id
        ):
            raise MailboxValidationError(
                "Message recipient 与目标Mailbox不一致"
            )

    @staticmethod
    def _assert_message_participants(
        message: Message,
        sender: AgentRecord,
        recipient: AgentRecord,
    ) -> None:
        expected_sender = sender.instance.reference
        expected_recipient = recipient.instance.reference
        if message.sender_ref != expected_sender:
            raise AgentStoreAccessError(
                "Message sender 不是持久Agent身份"
            )
        if message.recipient_refs != (expected_recipient,):
            raise AgentStoreAccessError(
                "Message recipient 不是目标Mailbox持有者"
            )
        if (
            sender.instance.scope_id != recipient.instance.scope_id
            or sender.instance.thread_id != recipient.instance.thread_id
            or message.scope_id != recipient.instance.scope_id
            or message.thread_id != recipient.instance.thread_id
        ):
            raise AgentStoreAccessError(
                "Mailbox Message 跨越 Scope 或 Thread 边界"
            )

    @staticmethod
    def _assert_session(record: AgentRecord, session_id: str) -> None:
        if record.session.agent_session_id != session_id:
            raise AgentStoreAccessError(
                "Mailbox AgentSession 不属于目标Agent"
            )

    @staticmethod
    def _assert_cursor_owner(
        cursor: MailboxCursor,
        record: AgentRecord,
        session_id: str,
    ) -> None:
        actual = (
            cursor.scope_id,
            cursor.thread_id,
            cursor.agent_instance_id,
            cursor.agent_session_id,
        )
        expected = (
            record.instance.scope_id,
            record.instance.thread_id,
            record.instance.agent_instance_id,
            session_id,
        )
        if actual != expected or record.session.agent_session_id != session_id:
            raise AgentStoreAccessError(
                "Mailbox cursor 跨越 Agent/Session 边界"
            )

    @staticmethod
    def _message_row(reader, message_id: str):
        return reader.execute(
            """SELECT message_id, scope_id, thread_id,
                      recipient_agent_instance_id,
                      recipient_agent_session_id,
                      sender_agent_instance_id, mailbox_sequence,
                      enqueued_at, consumed_at, message_json, message_digest
               FROM runtime_agent_messages WHERE message_id = ?""",
            (message_id,),
        ).fetchone()

    @staticmethod
    def _cursor_by_agent_id(reader, agent_instance_id: str) -> MailboxCursor | None:
        row = reader.execute(
            """SELECT agent_instance_id, scope_id, thread_id,
                      agent_session_id, last_enqueued_sequence,
                      consume_cursor, version, updated_at
               FROM runtime_agent_mailbox_cursors
               WHERE agent_instance_id = ?""",
            (agent_instance_id,),
        ).fetchone()
        return None if row is None else SQLiteMailboxStore._decode_cursor_row(row)

    @staticmethod
    def _insert_cursor(uow: RuntimeUnitOfWork, cursor: MailboxCursor) -> None:
        uow._execute_managed(
            """INSERT INTO runtime_agent_mailbox_cursors(
                agent_instance_id, scope_id, thread_id, agent_session_id,
                last_enqueued_sequence, consume_cursor, version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cursor.agent_instance_id,
                cursor.scope_id,
                cursor.thread_id,
                cursor.agent_session_id,
                cursor.last_enqueued_sequence,
                cursor.consume_cursor,
                cursor.version,
                cursor.updated_at,
            ),
        )

    @staticmethod
    def _decode_cursor_row(row) -> MailboxCursor:
        try:
            return MailboxCursor(
                agent_instance_id=row[0],
                scope_id=row[1],
                thread_id=row[2],
                agent_session_id=row[3],
                last_enqueued_sequence=row[4],
                consume_cursor=row[5],
                version=row[6],
                updated_at=row[7],
            )
        except (RuntimeProtocolError, TypeError, ValueError) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_mailbox_cursors 无法重建"
            ) from exc

    @staticmethod
    def _decode_message_row(row) -> MailboxDelivery:
        try:
            raw = str(row[9])
            decoded = json.loads(raw)
            if canonical_json(decoded) != raw or text_digest(raw) != row[10]:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_messages canonical JSON/digest 漂移"
                )
            message = Message.from_dict(decoded)
            expected = (
                message.message_id,
                message.scope_id,
                message.thread_id,
                row[3],
                row[4],
                message.sender_ref.entity_id,
                row[6],
                row[7],
                row[8],
            )
            if tuple(row[:9]) != expected:
                raise RuntimeStoredDataCorruptionError(
                    "runtime_agent_messages projection 与 Message JSON 不一致"
                )
            delivery = MailboxDelivery(
                message=message,
                recipient_agent_instance_id=row[3],
                recipient_agent_session_id=row[4],
                mailbox_sequence=row[6],
                enqueued_at=row[7],
                consumed_at=row[8],
            )
            SQLiteMailboxStore._validate_message_shape(
                message,
                delivery.recipient_agent_instance_id,
            )
            return delivery
        except RuntimeStoredDataCorruptionError:
            raise
        except (
            MailboxError,
            RuntimeProtocolError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeStoredDataCorruptionError(
                "runtime_agent_messages 无法重建 Message"
            ) from exc

    def _require_uow(self, uow: RuntimeUnitOfWork) -> None:
        if not isinstance(uow, RuntimeUnitOfWork):
            raise MailboxValidationError("uow 必须是 RuntimeUnitOfWork")
        if uow._database is not self._database:
            raise MailboxValidationError(
                "uow 与 SQLiteMailboxStore 必须属于同一数据库"
            )


__all__ = [
    "MailboxConflictError",
    "MailboxCursor",
    "MailboxDelivery",
    "MailboxError",
    "MailboxSendResult",
    "MailboxValidationError",
    "SQLiteMailboxStore",
]
