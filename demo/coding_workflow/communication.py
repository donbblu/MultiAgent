from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class MessageType(str, Enum):
    REQUEST = "request"
    HANDOFF = "handoff"
    RESULT = "result"
    FEEDBACK = "feedback"
    STATUS = "status"
    FINAL = "final"


class MessageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentMessage:
    """Agent 间唯一允许的结构化交流格式。"""

    message_id: str
    task_id: str
    task_version: int
    sender: str
    recipient: str
    message_type: MessageType
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    created_at: str = ""

    SENSITIVE_KEYS = frozenset(
        {"api_key", "apikey", "authorization", "password", "secret", "token"}
    )
    MAX_PAYLOAD_BYTES = 64_000

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        task_version: int,
        sender: str,
        recipient: str,
        message_type: MessageType,
        summary: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> "AgentMessage":
        message = cls(
            message_id=uuid4().hex,
            task_id=task_id,
            task_version=task_version,
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            summary=summary,
            payload=dict(payload or {}),
            correlation_id=correlation_id or uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        message.validate()
        return message

    def validate(self) -> None:
        for field_name, value in (
            ("message_id", self.message_id),
            ("task_id", self.task_id),
            ("sender", self.sender),
            ("recipient", self.recipient),
            ("summary", self.summary),
            ("correlation_id", self.correlation_id),
            ("created_at", self.created_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise MessageValidationError(f"{field_name} 不能为空")
        if self.task_version < 0:
            raise MessageValidationError("task_version 不能小于 0")
        if len(self.summary) > 1000:
            raise MessageValidationError("消息摘要超过 1000 字符")
        self._reject_sensitive_keys(self.payload)
        try:
            size = len(json.dumps(self.payload, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise MessageValidationError("payload 必须可以序列化为 JSON") from exc
        if size > self.MAX_PAYLOAD_BYTES:
            raise MessageValidationError("payload 超过 64KB")

    @classmethod
    def _reject_sensitive_keys(cls, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in cls.SENSITIVE_KEYS or normalized.endswith(
                    ("_token", "_api_key", "_password", "_secret")
                ):
                    raise MessageValidationError(f"payload 禁止敏感字段: {key}")
                cls._reject_sensitive_keys(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._reject_sensitive_keys(item)
