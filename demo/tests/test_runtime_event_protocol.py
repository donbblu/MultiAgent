import json
import unittest
from types import MappingProxyType

from coding_workflow.runtime_domain.common import (
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
)
from coding_workflow.runtime_domain.events import RuntimeActorType, RuntimeEvent


NOW = "2026-08-23T08:00:00+00:00"


def runtime_event() -> RuntimeEvent:
    return RuntimeEvent(
        "scope-a",
        "event-2",
        "core:message_committed",
        ScopedRef("scope-a", "core:message", "message-1", 2),
        2,
        7,
        "trace-1",
        "correlation-1",
        RuntimeActorType.RUNTIME,
        ScopedRef("scope-a", "core:runtime_principal", "runtime", 1),
        "commit-message-1-v2",
        NOW,
        NOW,
        causation_event_ref=ScopedRef(
            "scope-a", "core:runtime_event", "event-1", 1
        ),
        thread_ref=ScopedRef("scope-a", "core:thread", "thread-1", 4),
        related_refs=(
            ScopedRef("scope-a", "core:turn", "turn-1", 2),
        ),
        payload={"delivery_state": "committed", "attempt_number": 1},
    )


class RuntimeEventProtocolTests(unittest.TestCase):
    def test_event_is_deeply_frozen_and_round_trips(self) -> None:
        event = runtime_event()
        self.assertIsInstance(event.payload, MappingProxyType)
        with self.assertRaises(TypeError):
            event.payload["delivery_state"] = "delivered"
        encoded = json.loads(json.dumps(dict(event.to_dict())))
        self.assertEqual(RuntimeEvent.from_dict(encoded), event)

    def test_event_rejects_cross_scope_and_self_causation(self) -> None:
        args = dict(
            scope_id="scope-a",
            event_id="event-2",
            event_type="core:message_committed",
            aggregate_ref=ScopedRef("scope-a", "core:message", "message-1", 1),
            aggregate_version=1,
            sequence_no=1,
            trace_id="trace-1",
            correlation_id="correlation-1",
            actor_type=RuntimeActorType.RUNTIME,
            actor_ref=ScopedRef(
                "scope-a", "core:runtime_principal", "runtime", 1
            ),
            idempotency_key="key-1",
            occurred_at=NOW,
            recorded_at=NOW,
        )
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            RuntimeEvent(
                **args,
                related_refs=(
                    ScopedRef("scope-b", "core:thread", "thread-1", 1),
                ),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "自身"):
            RuntimeEvent(
                **args,
                causation_event_ref=ScopedRef(
                    "scope-a", "core:runtime_event", "event-2", 1
                ),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "version 必须为 1"):
            RuntimeEvent(
                **args,
                causation_event_ref=ScopedRef(
                    "scope-a", "core:runtime_event", "event-1", 99
                ),
            )

    def test_event_rejects_aggregate_version_drift_and_raw_bytes(self) -> None:
        encoded = dict(runtime_event().to_dict())
        encoded["aggregate_version"] = 3
        with self.assertRaisesRegex(RuntimeProtocolError, "aggregate_version"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["payload"] = {"raw_media": b"not allowed"}
        with self.assertRaisesRegex(RuntimeProtocolError, "JSON"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["payload"] = {"body": "Message truth must remain in Message"}
        with self.assertRaisesRegex(RuntimeProtocolError, "正文"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["payload"] = {
            "message_body": "renaming the field must not duplicate Message truth"
        }
        with self.assertRaisesRegex(RuntimeProtocolError, "正文"):
            RuntimeEvent.from_dict(encoded)

        for payload in (
            {"access_token": "secret-token"},
            {"accessToken": "secret-token"},
            {"artifact_ref": "artifact://scope-b/private"},
            {"reasoning": "private chain of thought"},
            {"messageText": "raw Message body"},
        ):
            with self.subTest(payload=payload):
                encoded = dict(runtime_event().to_dict())
                encoded["payload"] = payload
                with self.assertRaisesRegex(RuntimeProtocolError, "payload"):
                    RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["payload"] = {
            "reference": {
                "scope_id": "scope-b",
                "entity_type": "core:artifact",
                "entity_id": "foreign",
                "version": 1,
            }
        }
        with self.assertRaisesRegex(RuntimeProtocolError, "payload"):
            RuntimeEvent.from_dict(encoded)

    def test_event_rejects_cross_scope_actor_and_time_reversal(self) -> None:
        encoded = dict(runtime_event().to_dict())
        encoded["actor_ref"] = dict(ScopedRef(
            "scope-b", "core:runtime_principal", "runtime", 1
        ).to_dict())
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["recorded_at"] = "2026-08-23T07:59:59+00:00"
        with self.assertRaisesRegex(RuntimeProtocolError, "不能早于"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["actor_type"] = RuntimeActorType.USER.value
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型无效"):
            RuntimeEvent.from_dict(encoded)

    def test_event_rejects_unknown_fields_and_bad_schema(self) -> None:
        encoded = dict(runtime_event().to_dict())
        encoded["message_body"] = "duplicate truth"
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["schema_version"] = "2.0"
        with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
            RuntimeEvent.from_dict(encoded)

        encoded = dict(runtime_event().to_dict())
        encoded["event_version"] = 2
        with self.assertRaisesRegex(RuntimeProtocolError, "新 event_id"):
            RuntimeEvent.from_dict(encoded)


if __name__ == "__main__":
    unittest.main()
