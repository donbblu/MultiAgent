from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Mapping

from ..runtime_domain.common import RuntimeProtocolError
from ..runtime_domain.events import RuntimeEvent
from .sqlite import (
    OutboxPolicy,
    RuntimeDatabaseIntegrityError,
    _outbox_delivery_key,
    _outbox_intent_digest,
)


_SQLITE_SIGNED_INT64_MAX = (1 << 63) - 1
_OUTBOX_STATES = frozenset({
    "LEGACY_SUPPRESSED",
    "PENDING",
    "CLAIMED",
    "PUBLISHED",
})
_OUTBOX_TERMINAL_STATES = frozenset({"LEGACY_SUPPRESSED", "PUBLISHED"})
_OUTBOX_NACK_ERROR_CODES = frozenset({
    "outbox:transport_error",
    "outbox:ack_missing",
    "outbox:ack_invalid",
})
_CLAIM_TOKEN_PREFIX = "obc-v1-"
_LOWERCASE_HEX = frozenset("0123456789abcdef")


class RuntimeStoredDataCorruptionError(RuntimeDatabaseIntegrityError):
    """Stored JSON, digest, projection, or lifecycle data drifted."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def parse_aware_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 必须是非空 ISO-8601 时间"
        )
    try:
        instant = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 必须是 ISO-8601 时间"
        ) from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 必须包含时区"
        )
    return instant


def canonical_utc_timestamp(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_canonical_utc_timestamp(value: object, field_name: str) -> datetime:
    instant = parse_aware_timestamp(value, field_name)
    if value != canonical_utc_timestamp(instant):
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 必须是 canonical UTC 六位微秒时间"
        )
    return instant


def _strict_int64(value: object, field_name: str, *, minimum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > _SQLITE_SIGNED_INT64_MAX
    ):
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 不是有效 SQLite int64"
        )
    return value


def _valid_claim_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len(_CLAIM_TOKEN_PREFIX) + 64
        and value.startswith(_CLAIM_TOKEN_PREFIX)
        and all(character in _LOWERCASE_HEX for character in value[7:])
    )


def _valid_publisher_id(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if len(value) > 256:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def decode_runtime_event_row(row) -> RuntimeEvent:
    try:
        raw = str(row[13])
        decoded = json.loads(raw)
        if canonical_json(decoded) != raw or text_digest(raw) != row[14]:
            raise RuntimeStoredDataCorruptionError(
                "runtime_events canonical JSON/digest 漂移"
            )
        event = RuntimeEvent.from_dict(decoded)
        projections = (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6],
            row[7], row[8], row[9], row[10], row[11], row[12],
        )
        expected = (
            event.event_id,
            event.scope_id,
            event.event_type,
            event.aggregate_ref.entity_type,
            event.aggregate_ref.entity_id,
            event.aggregate_version,
            event.sequence_no,
            event.event_version,
            event.idempotency_key,
            event.trace_id,
            event.correlation_id,
            event.occurred_at,
            event.recorded_at,
        )
        if projections != expected:
            raise RuntimeStoredDataCorruptionError(
                "runtime_events projection 与 RuntimeEvent JSON 不一致"
            )
        for digest in (row[15], row[16]):
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in _LOWERCASE_HEX for character in digest)
            ):
                raise RuntimeStoredDataCorruptionError(
                    "runtime_events state/mutation digest 无效"
                )
        expected_mutation_digest = text_digest(canonical_json({
            "expected_version": event.aggregate_version - 1,
            "result_state_digest": row[15],
            "event_digest": row[14],
        }))
        if row[16] != expected_mutation_digest:
            raise RuntimeStoredDataCorruptionError(
                "runtime_events mutation digest 漂移"
            )
        return event
    except RuntimeStoredDataCorruptionError:
        raise
    except (RuntimeProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeStoredDataCorruptionError(
            "runtime_events 无法重建 RuntimeEvent"
        ) from exc


@dataclass(frozen=True)
class DecodedOutboxRow:
    event: RuntimeEvent
    event_digest: str
    delivery_key: str
    source_event_id: str
    scope_id: str
    destination: str
    created_at: str
    intent_digest: str
    policy_version: str
    policy_digest: str
    state: str
    updated_at: str
    claim_generation: int
    attempt_count: int
    available_at: str | None
    claim_token: str | None
    publisher_id: str | None
    claim_expires_at: str | None
    last_error_code: str | None
    suppress_reason: str | None
    published_at: str | None
    receipt_id: str | None
    created_instant: datetime
    updated_instant: datetime
    available_instant: datetime | None
    claim_expires_instant: datetime | None


def validate_outbox_aggregate_history(
    records: tuple[DecodedOutboxRow, ...],
) -> None:
    """Validate cross-row ordering without applying claim eligibility policy."""

    grouped: dict[tuple[str, str, str], list[DecodedOutboxRow]] = {}
    for record in records:
        aggregate = record.event.aggregate_ref
        key = (record.scope_id, aggregate.entity_type, aggregate.entity_id)
        grouped.setdefault(key, []).append(record)
    for group in grouped.values():
        group.sort(key=lambda item: item.event.sequence_no)
        expected_sequence = 1
        saw_nonterminal = False
        for record in group:
            if record.event.sequence_no != expected_sequence:
                raise RuntimeStoredDataCorruptionError(
                    "RuntimeEvent aggregate sequence 不连续"
                )
            expected_sequence += 1
            if record.state in _OUTBOX_TERMINAL_STATES:
                if saw_nonterminal:
                    raise RuntimeStoredDataCorruptionError(
                        "Outbox terminal state 越过更早非终态"
                    )
                continue
            if saw_nonterminal and record.claim_generation > 0:
                raise RuntimeStoredDataCorruptionError(
                    "Outbox 后序非终态在 predecessor 前已推进 lifecycle"
                )
            saw_nonterminal = True


def _checked_add_milliseconds(
    instant: datetime,
    milliseconds: int,
    field_name: str,
) -> datetime:
    try:
        return instant + timedelta(milliseconds=milliseconds)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox {field_name} 时间投影溢出"
        ) from exc


def _assert_none(values: Mapping[str, object]) -> None:
    for field_name, value in values.items():
        if value is not None:
            raise RuntimeStoredDataCorruptionError(
                f"Outbox lifecycle 字段必须为空: {field_name}"
            )


def decode_outbox_row(
    row,
    *,
    event: RuntimeEvent,
    event_digest: str,
    policy: OutboxPolicy,
) -> DecodedOutboxRow:
    if len(row) != 21:
        raise RuntimeStoredDataCorruptionError("runtime_outbox 列投影不完整")
    (
        delivery_key,
        source_event_id,
        scope_id,
        destination,
        stored_event_digest,
        created_at,
        intent_digest,
        policy_version,
        policy_digest,
        state,
        updated_at,
        raw_generation,
        raw_attempt_count,
        available_at,
        claim_token,
        publisher_id,
        claim_expires_at,
        last_error_code,
        suppress_reason,
        published_at,
        receipt_id,
    ) = tuple(row)

    expected_delivery_key = _outbox_delivery_key(
        policy.destination,
        event.event_id,
    )
    expected_intent_digest = _outbox_intent_digest(
        scope_id=event.scope_id,
        source_event_id=event.event_id,
        event_digest=event_digest,
        destination=policy.destination,
        delivery_key=expected_delivery_key,
        created_at=event.recorded_at,
        policy=policy,
    )
    expected_identity = (
        expected_delivery_key,
        event.event_id,
        event.scope_id,
        policy.destination,
        event_digest,
        event.recorded_at,
        expected_intent_digest,
        policy.policy_version,
        policy.policy_digest,
    )
    if tuple(row[:9]) != expected_identity:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox identity/digest 与 RuntimeEvent 不一致: {event.event_id}"
        )
    if state not in _OUTBOX_STATES:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox state 无效: {event.event_id}"
        )

    generation = _strict_int64(
        raw_generation,
        "claim_generation",
        minimum=0,
    )
    attempt_count = _strict_int64(
        raw_attempt_count,
        "attempt_count",
        minimum=0,
    )
    created_instant = parse_aware_timestamp(created_at, "created_at")
    updated_instant = parse_aware_timestamp(updated_at, "updated_at")
    parsed_available = (
        None
        if available_at is None
        else parse_aware_timestamp(available_at, "available_at")
    )
    parsed_expiry = (
        None
        if claim_expires_at is None
        else parse_aware_timestamp(claim_expires_at, "claim_expires_at")
    )

    if created_at != event.recorded_at:
        raise RuntimeStoredDataCorruptionError(
            f"Outbox created_at 与 Event 不一致: {event.event_id}"
        )

    if state == "LEGACY_SUPPRESSED":
        if (
            updated_at != event.recorded_at
            or generation != 0
            or attempt_count != 0
            or suppress_reason != "pre_outbox_cutover"
        ):
            raise RuntimeStoredDataCorruptionError(
                f"LEGACY_SUPPRESSED Outbox lifecycle 漂移: {event.event_id}"
            )
        _assert_none({
            "available_at": available_at,
            "claim_token": claim_token,
            "publisher_id": publisher_id,
            "claim_expires_at": claim_expires_at,
            "last_error_code": last_error_code,
            "published_at": published_at,
            "receipt_id": receipt_id,
        })
    elif state == "PENDING" and generation == 0:
        if (
            attempt_count != 0
            or updated_at != event.recorded_at
            or available_at != event.recorded_at
            or last_error_code is not None
            or suppress_reason is not None
        ):
            raise RuntimeStoredDataCorruptionError(
                f"初始 PENDING Outbox lifecycle 漂移: {event.event_id}"
            )
        _assert_none({
            "claim_token": claim_token,
            "publisher_id": publisher_id,
            "claim_expires_at": claim_expires_at,
            "published_at": published_at,
            "receipt_id": receipt_id,
        })
    elif state == "PENDING":
        if generation < 1 or attempt_count != generation:
            raise RuntimeStoredDataCorruptionError(
                f"NACK PENDING generation/attempt 漂移: {event.event_id}"
            )
        updated_instant = parse_canonical_utc_timestamp(updated_at, "updated_at")
        if available_at is None:
            raise RuntimeStoredDataCorruptionError(
                f"NACK PENDING available_at 缺失: {event.event_id}"
            )
        parsed_available = parse_canonical_utc_timestamp(
            available_at,
            "available_at",
        )
        if last_error_code not in _OUTBOX_NACK_ERROR_CODES:
            raise RuntimeStoredDataCorruptionError(
                f"NACK PENDING error code 无效: {event.event_id}"
            )
        delay_index = min(attempt_count - 1, len(policy.retry_delays_ms) - 1)
        expected_available = _checked_add_milliseconds(
            updated_instant,
            policy.retry_delays_ms[delay_index],
            "available_at",
        )
        if available_at != canonical_utc_timestamp(expected_available):
            raise RuntimeStoredDataCorruptionError(
                f"NACK PENDING retry 投影漂移: {event.event_id}"
            )
        _assert_none({
            "claim_token": claim_token,
            "publisher_id": publisher_id,
            "claim_expires_at": claim_expires_at,
            "suppress_reason": suppress_reason,
            "published_at": published_at,
            "receipt_id": receipt_id,
        })
    elif state == "CLAIMED":
        if generation < 1 or attempt_count != generation:
            raise RuntimeStoredDataCorruptionError(
                f"CLAIMED generation/attempt 漂移: {event.event_id}"
            )
        updated_instant = parse_canonical_utc_timestamp(updated_at, "updated_at")
        if claim_expires_at is None:
            raise RuntimeStoredDataCorruptionError(
                f"CLAIMED expiry 缺失: {event.event_id}"
            )
        parsed_expiry = parse_canonical_utc_timestamp(
            claim_expires_at,
            "claim_expires_at",
        )
        expected_expiry = _checked_add_milliseconds(
            updated_instant,
            policy.claim_ttl_ms,
            "claim_expires_at",
        )
        if claim_expires_at != canonical_utc_timestamp(expected_expiry):
            raise RuntimeStoredDataCorruptionError(
                f"CLAIMED TTL 投影漂移: {event.event_id}"
            )
        if not _valid_claim_token(claim_token):
            raise RuntimeStoredDataCorruptionError(
                f"CLAIMED claim token 无效: {event.event_id}"
            )
        if not _valid_publisher_id(publisher_id):
            raise RuntimeStoredDataCorruptionError(
                f"CLAIMED publisher ID 无效: {event.event_id}"
            )
        _assert_none({
            "available_at": available_at,
            "last_error_code": last_error_code,
            "suppress_reason": suppress_reason,
            "published_at": published_at,
            "receipt_id": receipt_id,
        })
    else:
        # 3B-1 cannot validate Receipt projections.  It only preserves the
        # v3 shape here; candidate ordering refuses to skip such predecessors.
        if generation < 1 or attempt_count != generation:
            raise RuntimeStoredDataCorruptionError(
                f"PUBLISHED generation/attempt 漂移: {event.event_id}"
            )
        _assert_none({
            "available_at": available_at,
            "claim_token": claim_token,
            "publisher_id": publisher_id,
            "claim_expires_at": claim_expires_at,
            "last_error_code": last_error_code,
            "suppress_reason": suppress_reason,
        })
        if not isinstance(published_at, str) or not published_at:
            raise RuntimeStoredDataCorruptionError(
                f"PUBLISHED published_at 缺失: {event.event_id}"
            )
        if not isinstance(receipt_id, str) or not receipt_id:
            raise RuntimeStoredDataCorruptionError(
                f"PUBLISHED receipt_id 缺失: {event.event_id}"
            )

    return DecodedOutboxRow(
        event=event,
        event_digest=event_digest,
        delivery_key=delivery_key,
        source_event_id=source_event_id,
        scope_id=scope_id,
        destination=destination,
        created_at=created_at,
        intent_digest=intent_digest,
        policy_version=policy_version,
        policy_digest=policy_digest,
        state=state,
        updated_at=updated_at,
        claim_generation=generation,
        attempt_count=attempt_count,
        available_at=available_at,
        claim_token=claim_token,
        publisher_id=publisher_id,
        claim_expires_at=claim_expires_at,
        last_error_code=last_error_code,
        suppress_reason=suppress_reason,
        published_at=published_at,
        receipt_id=receipt_id,
        created_instant=created_instant,
        updated_instant=updated_instant,
        available_instant=parsed_available,
        claim_expires_instant=parsed_expiry,
    )
