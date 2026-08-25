from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import coding_workflow.runtime_persistence as runtime_persistence
from coding_workflow.runtime_domain import (
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimePersistenceFaultPoint,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventApplyResult,
    ThreadEventMutation,
)


DESTINATION = "core:runtime_events"
EXPECTED_SINK = "core:test-sink"
POLICY_VERSION = "outbox-policy/test-v1"
EVENT_T0 = "2026-08-25T00:00:00+00:00"
EVENT_T5 = "2026-08-25T00:00:05+00:00"
EVENT_T10 = "2026-08-25T00:00:10+00:00"
CLAIM_TIME = datetime(2026, 8, 25, 0, 1, tzinfo=timezone.utc)
NACK_TIME = datetime(2026, 8, 25, 0, 1, 30, tzinfo=timezone.utc)
PUBLISHER_A = "publisher:red-a"
PUBLISHER_B = "publisher:red-b"
TOKEN_1 = "obc-v1-" + "1" * 64
TOKEN_2 = "obc-v1-" + "2" * 64
TOKEN_3 = "obc-v1-" + "3" * 64
TOKEN_4 = "obc-v1-" + "4" * 64

OUTBOX_SELECT = """SELECT delivery_key, source_event_id, scope_id,
                           destination, event_digest, created_at,
                           intent_digest, policy_version, policy_digest,
                           state, updated_at, claim_generation, attempt_count,
                           available_at, claim_token, publisher_id,
                           claim_expires_at, last_error_code, suppress_reason,
                           published_at, receipt_id
                    FROM runtime_outbox"""


class InjectedLifecycleFault(RuntimeError):
    pass


class FakeClock:
    def __init__(self, current: datetime) -> None:
        self._current = current
        self._lock = threading.Lock()
        self.calls = 0

    def set(self, current: datetime) -> None:
        with self._lock:
            self._current = current

    def now(self) -> datetime:
        with self._lock:
            self.calls += 1
            return self._current


class DeterministicTokenFactory:
    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)
        self._lock = threading.Lock()
        self.calls = 0

    def new_token(self) -> str:
        with self._lock:
            self.calls += 1
            if not self._tokens:
                raise AssertionError("claim token factory was called unexpectedly")
            return self._tokens.pop(0)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def text_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def canonical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def expected_delivery_key(event_id: str) -> str:
    digest = sha256(f"{DESTINATION}\0{event_id}".encode("utf-8")).hexdigest()
    return f"obx-v1-{digest}"


def expected_event_digest(event: RuntimeEvent) -> str:
    return text_digest(canonical_json(dict(event.to_dict())))


def expected_policy_digest(policy: OutboxPolicy) -> str:
    return text_digest(canonical_json({
        "schema": "outbox-policy/v1",
        "policy_version": policy.policy_version,
        "destination": policy.destination,
        "expected_sink_id": policy.expected_sink_id,
        "claim_ttl_ms": policy.claim_ttl_ms,
        "batch_limit": policy.batch_limit,
        "retry_delays_ms": list(policy.retry_delays_ms),
    }))


def expected_intent_digest(event: RuntimeEvent, policy: OutboxPolicy) -> str:
    delivery_key = expected_delivery_key(event.event_id)
    return text_digest(canonical_json({
        "schema": "outbox-intent/v1",
        "scope_id": event.scope_id,
        "source_event_id": event.event_id,
        "event_digest": expected_event_digest(event),
        "destination": DESTINATION,
        "delivery_key": delivery_key,
        "created_at": event.recorded_at,
        "policy_version": policy.policy_version,
        "policy_digest": expected_policy_digest(policy),
    }))


def build_policy(*, batch_limit: int = 10) -> OutboxPolicy:
    return OutboxPolicy(
        policy_version=POLICY_VERSION,
        destination=DESTINATION,
        expected_sink_id=EXPECTED_SINK,
        claim_ttl_ms=60_000,
        batch_limit=batch_limit,
        retry_delays_ms=(1_000, 5_000, 30_000),
    )


def build_mutation(
    *,
    scope_id: str = "scope-a",
    thread_id: str = "thread-a",
    version: int = 1,
    recorded_at: str = EVENT_T0,
    created_at: str | None = None,
) -> ThreadEventMutation:
    participant = ScopedRef(scope_id, "core:principal", "user-1", 1)
    thread_created_at = created_at or recorded_at
    thread = Thread(
        thread_id=thread_id,
        scope_id=scope_id,
        title=f"{scope_id}/{thread_id}/v{version}",
        participant_refs=(participant,),
        version=version,
        created_at=thread_created_at,
        updated_at=recorded_at,
    )
    event_type = "core:thread_created"
    payload = {"state": "open"}
    if version > 1:
        event_type = "core:thread_updated"
        payload["previous_state"] = "open"
    event = RuntimeEvent(
        scope_id=scope_id,
        event_id=f"event-{scope_id}-{thread_id}-{version}",
        event_type=event_type,
        aggregate_ref=thread.reference,
        aggregate_version=version,
        sequence_no=version,
        trace_id=f"trace-{scope_id}-{thread_id}",
        correlation_id=f"correlation-{scope_id}-{thread_id}",
        actor_type=RuntimeActorType.USER,
        actor_ref=participant,
        idempotency_key=f"idem-{scope_id}-{thread_id}-{version}",
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        thread_ref=thread.reference,
        payload=payload,
    )
    return ThreadEventMutation(version - 1, thread, event)


class RuntimeOutboxClaimLifecycleExpectedRedTests(unittest.TestCase):
    """Seven frozen EXPECTED_RED gates for 3B-1 claim/NACK ownership."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "runtime.sqlite3"
        self.policy = build_policy()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def require_public(self, name: str):
        value = getattr(runtime_persistence, name, None)
        self.assertIsNotNone(
            value,
            f"PROD-01B-3B-1 requires public runtime_persistence.{name}",
        )
        return value

    def runtime(
        self,
        path: Path | None = None,
        *,
        fault_hook=None,
        initialize: bool = True,
    ):
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path or self.path),
            outbox_policy=self.policy,
            fault_hook=fault_hook,
        )
        if initialize:
            database.initialize()
        return database, SQLiteThreadEventStore(database)

    @staticmethod
    def apply_and_commit(database, store, mutation) -> ThreadEventApplyResult:
        with database.unit_of_work() as uow:
            result = store.apply(uow, mutation)
            uow.commit()
        return result

    @staticmethod
    def raw_outbox_rows(path: Path) -> tuple[tuple[object, ...], ...]:
        with sqlite3.connect(str(path)) as connection:
            return tuple(
                tuple(row)
                for row in connection.execute(
                    OUTBOX_SELECT + " ORDER BY source_event_id"
                )
            )

    @staticmethod
    def raw_outbox_row(path: Path, delivery_key: str) -> tuple[object, ...]:
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute(
                OUTBOX_SELECT + " WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        if row is None:
            raise AssertionError(f"missing raw Outbox row: {delivery_key}")
        return tuple(row)

    @staticmethod
    def replace_raw(
        row: tuple[object, ...],
        **changes: object,
    ) -> tuple[object, ...]:
        indexes = {
            "state": 9,
            "updated_at": 10,
            "claim_generation": 11,
            "attempt_count": 12,
            "available_at": 13,
            "claim_token": 14,
            "publisher_id": 15,
            "claim_expires_at": 16,
            "last_error_code": 17,
            "suppress_reason": 18,
            "published_at": 19,
            "receipt_id": 20,
        }
        values = list(row)
        for field_name, value in changes.items():
            values[indexes[field_name]] = value
        return tuple(values)

    def assert_writer_unlocked(self, path: Path) -> None:
        with sqlite3.connect(str(path), timeout=0) as connection:
            connection.execute("PRAGMA busy_timeout = 0")
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()

    def lifecycle(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        publisher_id: str,
        clock: FakeClock,
        token_factory: DeterministicTokenFactory,
    ):
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        return lifecycle_type(
            database,
            publisher_id=publisher_id,
            clock=clock,
            claim_token_factory=token_factory,
        )

    def test_public_read_api_returns_frozen_scope_safe_snapshot(self) -> None:
        outbox_state = self.require_public("OutboxState")
        self.require_public("OutboxNackErrorCode")
        snapshot_type = self.require_public("OutboxSnapshot")
        self.require_public("OutboxClaimOwnership")
        self.require_public("OutboxClaim")
        self.require_public("OutboxNackResult")
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")

        database, store = self.runtime()
        mutation = build_mutation()
        self.assertIs(
            self.apply_and_commit(database, store, mutation),
            ThreadEventApplyResult.APPLIED,
        )
        before = self.raw_outbox_rows(self.path)
        clock = FakeClock(CLAIM_TIME)
        tokens = DeterministicTokenFactory(TOKEN_1)
        lifecycle = lifecycle_type(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            claim_token_factory=tokens,
        )
        delivery_key = expected_delivery_key(mutation.event.event_id)

        snapshot = lifecycle.get("scope-a", delivery_key)

        self.assertIsInstance(snapshot, snapshot_type)
        self.assertEqual(snapshot.scope_id, "scope-a")
        self.assertEqual(snapshot.delivery_key, delivery_key)
        self.assertEqual(snapshot.source_event_id, mutation.event.event_id)
        self.assertEqual(snapshot.destination, DESTINATION)
        self.assertEqual(snapshot.event_digest, expected_event_digest(mutation.event))
        self.assertEqual(
            snapshot.intent_digest,
            expected_intent_digest(mutation.event, self.policy),
        )
        self.assertEqual(snapshot.policy_version, POLICY_VERSION)
        self.assertEqual(snapshot.policy_digest, expected_policy_digest(self.policy))
        self.assertIs(snapshot.state, outbox_state.PENDING)
        self.assertEqual(snapshot.claim_generation, 0)
        self.assertEqual(snapshot.attempt_count, 0)
        self.assertEqual(snapshot.available_at, EVENT_T0)
        self.assertIsNone(snapshot.publisher_id)
        self.assertIsNone(snapshot.claim_expires_at)
        self.assertIsNone(snapshot.last_error_code)
        self.assertIsNone(snapshot.suppress_reason)
        self.assertEqual(snapshot.created_at, EVENT_T0)
        self.assertEqual(snapshot.updated_at, EVENT_T0)
        self.assertIsNone(snapshot.published_at)
        self.assertIsNone(snapshot.receipt_id)
        self.assertFalse(hasattr(snapshot, "claim_token"))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            snapshot.state = outbox_state.CLAIMED
        self.assertIsNone(lifecycle.get("scope-b", delivery_key))
        self.assertIsNone(lifecycle.get("scope-a", expected_delivery_key("missing")))
        self.assertEqual(self.raw_outbox_rows(self.path), before)
        self.assertEqual(tokens.calls, 0)

    def test_initial_pending_claim_commits_exact_owner_and_releases_writer(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        claim_type = self.require_public("OutboxClaim")
        database, store = self.runtime()
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        delivery_key = expected_delivery_key(mutation.event.event_id)
        before = self.raw_outbox_row(self.path, delivery_key)
        clock = FakeClock(CLAIM_TIME)
        tokens = DeterministicTokenFactory(TOKEN_1, TOKEN_2)
        lifecycle = lifecycle_type(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            claim_token_factory=tokens,
        )

        claims = lifecycle.claim_eligible_batch("scope-a")

        self.assertIsInstance(claims, tuple)
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertIsInstance(claim, claim_type)
        self.assertEqual(claim.ownership.scope_id, "scope-a")
        self.assertEqual(claim.ownership.delivery_key, delivery_key)
        self.assertEqual(claim.ownership.claim_generation, 1)
        self.assertEqual(claim.ownership.claim_token, TOKEN_1)
        self.assertEqual(claim.ownership.publisher_id, PUBLISHER_A)
        self.assertEqual(claim.source_event_id, mutation.event.event_id)
        self.assertEqual(claim.destination, DESTINATION)
        self.assertEqual(claim.event_digest, expected_event_digest(mutation.event))
        self.assertEqual(claim.attempt_count, 1)
        self.assertEqual(claim.claimed_at, canonical_utc(CLAIM_TIME))
        expires = CLAIM_TIME + timedelta(milliseconds=self.policy.claim_ttl_ms)
        self.assertEqual(claim.claim_expires_at, canonical_utc(expires))
        self.assertEqual(claim.policy_version, POLICY_VERSION)
        self.assertEqual(claim.policy_digest, expected_policy_digest(self.policy))
        expected = self.replace_raw(
            before,
            state="CLAIMED",
            updated_at=canonical_utc(CLAIM_TIME),
            claim_generation=1,
            attempt_count=1,
            available_at=None,
            claim_token=TOKEN_1,
            publisher_id=PUBLISHER_A,
            claim_expires_at=canonical_utc(expires),
            last_error_code=None,
        )
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), expected)
        self.assert_writer_unlocked(self.path)
        self.assertEqual(lifecycle.claim_eligible_batch("scope-a"), ())
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), expected)
        self.assertEqual(clock.calls, 2)
        self.assertEqual(tokens.calls, 1)

    def test_current_owner_nack_schedules_retry_and_next_claim_advances_generation(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        nack_code = self.require_public("OutboxNackErrorCode")
        nack_result_type = self.require_public("OutboxNackResult")
        database, store = self.runtime()
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        delivery_key = expected_delivery_key(mutation.event.event_id)
        initial = self.raw_outbox_row(self.path, delivery_key)
        clock = FakeClock(CLAIM_TIME)
        tokens = DeterministicTokenFactory(TOKEN_1, TOKEN_2, TOKEN_3)
        lifecycle = lifecycle_type(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            claim_token_factory=tokens,
        )
        first = lifecycle.claim_eligible_batch("scope-a")[0]
        clock.set(NACK_TIME)

        result = lifecycle.nack(first.ownership, nack_code.TRANSPORT_ERROR)

        available = NACK_TIME + timedelta(milliseconds=1_000)
        self.assertIsInstance(result, nack_result_type)
        self.assertEqual(result.scope_id, "scope-a")
        self.assertEqual(result.delivery_key, delivery_key)
        self.assertEqual(result.claim_generation, 1)
        self.assertEqual(result.attempt_count, 1)
        self.assertIs(result.error_code, nack_code.TRANSPORT_ERROR)
        self.assertEqual(result.failed_at, canonical_utc(NACK_TIME))
        self.assertEqual(result.available_at, canonical_utc(available))
        nacked = self.replace_raw(
            initial,
            state="PENDING",
            updated_at=canonical_utc(NACK_TIME),
            claim_generation=1,
            attempt_count=1,
            available_at=canonical_utc(available),
            claim_token=None,
            publisher_id=None,
            claim_expires_at=None,
            last_error_code=nack_code.TRANSPORT_ERROR.value,
        )
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), nacked)

        clock.set(available - timedelta(microseconds=1))
        self.assertEqual(lifecycle.claim_eligible_batch("scope-a"), ())
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), nacked)
        clock.set(available)
        second = lifecycle.claim_eligible_batch("scope-a")
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].ownership.claim_generation, 2)
        self.assertEqual(second[0].ownership.claim_token, TOKEN_2)
        self.assertEqual(second[0].attempt_count, 2)
        expires = available + timedelta(milliseconds=self.policy.claim_ttl_ms)
        claimed_again = self.replace_raw(
            nacked,
            state="CLAIMED",
            updated_at=canonical_utc(available),
            claim_generation=2,
            attempt_count=2,
            available_at=None,
            claim_token=TOKEN_2,
            publisher_id=PUBLISHER_A,
            claim_expires_at=canonical_utc(expires),
            last_error_code=None,
        )
        self.assertEqual(
            self.raw_outbox_row(self.path, delivery_key),
            claimed_again,
        )
        self.assertEqual(clock.calls, 4)
        self.assertEqual(tokens.calls, 2)

    def test_expiry_reclaim_fences_old_owner_at_exact_boundary(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        nack_code = self.require_public("OutboxNackErrorCode")
        ownership_lost = self.require_public("RuntimeOutboxOwnershipLostError")
        database, store = self.runtime()
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        delivery_key = expected_delivery_key(mutation.event.event_id)
        clock_a = FakeClock(CLAIM_TIME)
        tokens_a = DeterministicTokenFactory(TOKEN_1)
        lifecycle_a = lifecycle_type(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock_a,
            claim_token_factory=tokens_a,
        )
        first = lifecycle_a.claim_eligible_batch("scope-a")[0]
        expires = CLAIM_TIME + timedelta(milliseconds=self.policy.claim_ttl_ms)
        claimed_once = self.raw_outbox_row(self.path, delivery_key)
        clock_a.set(expires)

        with self.assertRaises(ownership_lost):
            lifecycle_a.nack(first.ownership, nack_code.TRANSPORT_ERROR)
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), claimed_once)

        clock_b = FakeClock(expires)
        tokens_b = DeterministicTokenFactory(TOKEN_2)
        lifecycle_b = lifecycle_type(
            database,
            publisher_id=PUBLISHER_B,
            clock=clock_b,
            claim_token_factory=tokens_b,
        )
        reclaimed = lifecycle_b.claim_eligible_batch("scope-a")
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].ownership.claim_generation, 2)
        self.assertEqual(reclaimed[0].ownership.claim_token, TOKEN_2)
        self.assertEqual(reclaimed[0].ownership.publisher_id, PUBLISHER_B)
        second_expiry = expires + timedelta(milliseconds=self.policy.claim_ttl_ms)
        expected = self.replace_raw(
            claimed_once,
            state="CLAIMED",
            updated_at=canonical_utc(expires),
            claim_generation=2,
            attempt_count=2,
            available_at=None,
            claim_token=TOKEN_2,
            publisher_id=PUBLISHER_B,
            claim_expires_at=canonical_utc(second_expiry),
            last_error_code=None,
        )
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), expected)

        with self.assertRaises(ownership_lost):
            lifecycle_a.nack(first.ownership, nack_code.TRANSPORT_ERROR)
        self.assertEqual(self.raw_outbox_row(self.path, delivery_key), expected)
        self.assertEqual(clock_a.calls, 3)
        self.assertEqual(clock_b.calls, 1)
        self.assertEqual(tokens_a.calls, 1)
        self.assertEqual(tokens_b.calls, 1)

    def test_claim_enforces_aggregate_order_cross_aggregate_order_and_scope_isolation(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        database, store = self.runtime()
        aggregate_a_1 = build_mutation(
            scope_id="scope-a",
            thread_id="aggregate-a",
            recorded_at=EVENT_T0,
        )
        aggregate_a_2 = build_mutation(
            scope_id="scope-a",
            thread_id="aggregate-a",
            version=2,
            recorded_at=EVENT_T10,
            created_at=EVENT_T0,
        )
        aggregate_b_1 = build_mutation(
            scope_id="scope-a",
            thread_id="aggregate-b",
            recorded_at=EVENT_T5,
        )
        other_scope = build_mutation(
            scope_id="scope-b",
            thread_id="aggregate-c",
            recorded_at=EVENT_T0,
        )
        for mutation in (
            aggregate_a_1,
            aggregate_a_2,
            aggregate_b_1,
            other_scope,
        ):
            self.apply_and_commit(database, store, mutation)

        clock_a = FakeClock(CLAIM_TIME)
        tokens_a = DeterministicTokenFactory(TOKEN_1, TOKEN_2, TOKEN_3)
        lifecycle_a = lifecycle_type(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock_a,
            claim_token_factory=tokens_a,
        )
        claims_a = lifecycle_a.claim_eligible_batch("scope-a")

        self.assertEqual(
            [claim.source_event_id for claim in claims_a],
            [aggregate_a_1.event.event_id, aggregate_b_1.event.event_id],
        )
        rows = {
            row[1]: row
            for row in self.raw_outbox_rows(self.path)
        }
        self.assertEqual(rows[aggregate_a_1.event.event_id][9], "CLAIMED")
        self.assertEqual(rows[aggregate_b_1.event.event_id][9], "CLAIMED")
        self.assertEqual(rows[aggregate_a_2.event.event_id][9], "PENDING")
        self.assertEqual(rows[other_scope.event.event_id][9], "PENDING")
        self.assertEqual(lifecycle_a.claim_eligible_batch("scope-a"), ())
        self.assertEqual(tokens_a.calls, 2)

        clock_b = FakeClock(CLAIM_TIME)
        tokens_b = DeterministicTokenFactory(TOKEN_4)
        lifecycle_b = lifecycle_type(
            database,
            publisher_id=PUBLISHER_B,
            clock=clock_b,
            claim_token_factory=tokens_b,
        )
        claims_b = lifecycle_b.claim_eligible_batch("scope-b")
        self.assertEqual(
            [claim.source_event_id for claim in claims_b],
            [other_scope.event.event_id],
        )
        rows_after = {
            row[1]: row
            for row in self.raw_outbox_rows(self.path)
        }
        self.assertEqual(rows_after[aggregate_a_2.event.event_id][9], "PENDING")
        self.assertEqual(rows_after[other_scope.event.event_id][9], "CLAIMED")
        self.assertEqual(clock_a.calls, 2)
        self.assertEqual(clock_b.calls, 1)

    def test_concurrent_claim_has_one_winner_and_one_empty_loser(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        database, store = self.runtime()
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        delivery_key = expected_delivery_key(mutation.event.event_id)
        clocks = (FakeClock(CLAIM_TIME), FakeClock(CLAIM_TIME))
        factories = (
            DeterministicTokenFactory(TOKEN_1),
            DeterministicTokenFactory(TOKEN_2),
        )
        publishers = (PUBLISHER_A, PUBLISHER_B)
        lifecycles = tuple(
            lifecycle_type(
                database,
                publisher_id=publisher,
                clock=clock,
                claim_token_factory=factory,
            )
            for publisher, clock, factory in zip(publishers, clocks, factories)
        )
        barrier = threading.Barrier(3)
        results: list[tuple[str, tuple[object, ...]]] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def run(index: int) -> None:
            try:
                barrier.wait(timeout=5)
                claimed = lifecycles[index].claim_eligible_batch("scope-a")
                with result_lock:
                    results.append((publishers[index], claimed))
            except BaseException as exc:
                with result_lock:
                    errors.append(exc)

        threads = tuple(
            threading.Thread(target=run, args=(index,))
            for index in range(2)
        )
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(len(claims) for _, claims in results), [0, 1])
        winner_publisher, winner_claims = next(
            item for item in results if len(item[1]) == 1
        )
        winner = winner_claims[0]
        row = self.raw_outbox_row(self.path, delivery_key)
        self.assertEqual(row[9], "CLAIMED")
        self.assertEqual(row[11:13], (1, 1))
        self.assertEqual(row[14], winner.ownership.claim_token)
        self.assertEqual(row[15], winner_publisher)
        self.assertEqual(row[15], winner.ownership.publisher_id)
        self.assertEqual(sum(clock.calls for clock in clocks), 2)
        self.assertEqual(sum(factory.calls for factory in factories), 1)

    def test_claim_and_nack_faults_after_cas_roll_back_exact_rows(self) -> None:
        lifecycle_type = self.require_public("SQLiteOutboxLifecycleStore")
        nack_code = self.require_public("OutboxNackErrorCode")
        claim_fault = getattr(
            RuntimePersistenceFaultPoint,
            "OUTBOX_AFTER_CLAIM_UPDATE",
            None,
        )
        nack_fault = getattr(
            RuntimePersistenceFaultPoint,
            "OUTBOX_AFTER_NACK_UPDATE",
            None,
        )
        self.assertIsNotNone(claim_fault)
        self.assertIsNotNone(nack_fault)

        claim_path = self.root / "claim-fault.sqlite3"
        claim_database, claim_event_store = self.runtime(claim_path)
        claim_mutation = build_mutation(thread_id="claim-fault")
        self.apply_and_commit(
            claim_database,
            claim_event_store,
            claim_mutation,
        )
        claim_key = expected_delivery_key(claim_mutation.event.event_id)
        pending_before = self.raw_outbox_row(claim_path, claim_key)

        def fail_claim(point) -> None:
            if point is claim_fault:
                raise InjectedLifecycleFault("after-claim-update")

        fault_claim_database, _ = self.runtime(
            claim_path,
            fault_hook=fail_claim,
        )
        fault_claim_clock = FakeClock(CLAIM_TIME)
        fault_claim_tokens = DeterministicTokenFactory(TOKEN_1)
        fault_claim_lifecycle = lifecycle_type(
            fault_claim_database,
            publisher_id=PUBLISHER_A,
            clock=fault_claim_clock,
            claim_token_factory=fault_claim_tokens,
        )
        with self.assertRaisesRegex(
            InjectedLifecycleFault,
            "after-claim-update",
        ):
            fault_claim_lifecycle.claim_eligible_batch("scope-a")
        self.assertEqual(
            self.raw_outbox_row(claim_path, claim_key),
            pending_before,
        )
        self.assert_writer_unlocked(claim_path)
        healthy_claim = lifecycle_type(
            claim_database,
            publisher_id=PUBLISHER_A,
            clock=FakeClock(CLAIM_TIME),
            claim_token_factory=DeterministicTokenFactory(TOKEN_2),
        ).claim_eligible_batch("scope-a")
        self.assertEqual(len(healthy_claim), 1)
        self.assertEqual(healthy_claim[0].ownership.claim_generation, 1)

        nack_path = self.root / "nack-fault.sqlite3"
        nack_database, nack_event_store = self.runtime(nack_path)
        nack_mutation = build_mutation(thread_id="nack-fault")
        self.apply_and_commit(nack_database, nack_event_store, nack_mutation)
        nack_key = expected_delivery_key(nack_mutation.event.event_id)
        healthy_lifecycle = lifecycle_type(
            nack_database,
            publisher_id=PUBLISHER_A,
            clock=FakeClock(CLAIM_TIME),
            claim_token_factory=DeterministicTokenFactory(TOKEN_3),
        )
        ownership = healthy_lifecycle.claim_eligible_batch("scope-a")[0].ownership
        claimed_before = self.raw_outbox_row(nack_path, nack_key)

        def fail_nack(point) -> None:
            if point is nack_fault:
                raise InjectedLifecycleFault("after-nack-update")

        fault_nack_database, _ = self.runtime(
            nack_path,
            fault_hook=fail_nack,
        )
        fault_nack_lifecycle = lifecycle_type(
            fault_nack_database,
            publisher_id=PUBLISHER_A,
            clock=FakeClock(NACK_TIME),
            claim_token_factory=DeterministicTokenFactory(TOKEN_4),
        )
        with self.assertRaisesRegex(
            InjectedLifecycleFault,
            "after-nack-update",
        ):
            fault_nack_lifecycle.nack(
                ownership,
                nack_code.TRANSPORT_ERROR,
            )
        self.assertEqual(
            self.raw_outbox_row(nack_path, nack_key),
            claimed_before,
        )
        self.assert_writer_unlocked(nack_path)
        healthy_nack = lifecycle_type(
            nack_database,
            publisher_id=PUBLISHER_A,
            clock=FakeClock(NACK_TIME),
            claim_token_factory=DeterministicTokenFactory(TOKEN_4),
        ).nack(ownership, nack_code.TRANSPORT_ERROR)
        self.assertEqual(healthy_nack.claim_generation, 1)
        self.assertEqual(
            self.raw_outbox_row(nack_path, nack_key)[9],
            "PENDING",
        )


if __name__ == "__main__":
    unittest.main()
