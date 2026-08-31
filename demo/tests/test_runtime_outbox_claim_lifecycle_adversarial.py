from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from time import monotonic

from coding_workflow.runtime_domain import (
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from coding_workflow.runtime_persistence import (
    OutboxClaimOwnership,
    OutboxClaimTokenFactory,
    OutboxNackErrorCode,
    OutboxPolicy,
    RuntimeDatabaseBusyError,
    RuntimeOutboxAttemptExhaustedError,
    RuntimeOutboxClockError,
    RuntimeOutboxOwnershipLostError,
    RuntimeOutboxTokenFactoryError,
    RuntimeOutboxValidationError,
    RuntimePersistenceFaultPoint,
    RuntimeSQLiteConfig,
    RuntimeStoredDataCorruptionError,
    RuntimeUnitOfWorkStateError,
    SQLiteOutboxLifecycleStore,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventApplyResult,
    ThreadEventMutation,
)


DESTINATION = "core:runtime_events"
EXPECTED_SINK = "core:test-sink"
POLICY_VERSION = "outbox-policy/adversarial-v1"
T0_TEXT = "2026-08-25T00:00:00+00:00"
T1_TEXT = "2026-08-25T00:01:00+00:00"
T2_TEXT = "2026-08-25T00:02:00+00:00"
T0 = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 25, 0, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 25, 0, 2, tzinfo=timezone.utc)
PUBLISHER_A = "publisher:attack-a"
PUBLISHER_B = "publisher:attack-b"
TOKEN_A = "obc-v1-" + "a" * 64
TOKEN_B = "obc-v1-" + "b" * 64
TOKEN_C = "obc-v1-" + "c" * 64
SQLITE_INT64_MAX = (1 << 63) - 1

OUTBOX_SELECT = """SELECT delivery_key, source_event_id, scope_id,
                           destination, event_digest, created_at,
                           intent_digest, policy_version, policy_digest,
                           state, updated_at, claim_generation, attempt_count,
                           available_at, claim_token, publisher_id,
                           claim_expires_at, last_error_code, suppress_reason,
                           published_at, receipt_id
                    FROM runtime_outbox"""


class AttackClock:
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


class AttackTokenFactory(OutboxClaimTokenFactory):
    def __init__(self, *values: object) -> None:
        self._values = list(values)
        self._lock = threading.Lock()
        self.calls = 0

    def new_token(self) -> str:
        with self._lock:
            self.calls += 1
            if not self._values:
                raise AssertionError("unexpected token request")
            value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def delivery_key(event_id: str) -> str:
    digest = sha256(f"{DESTINATION}\0{event_id}".encode("utf-8")).hexdigest()
    return f"obx-v1-{digest}"


def event_digest(event: RuntimeEvent) -> str:
    raw = canonical_json(dict(event.to_dict()))
    return sha256(raw.encode("utf-8")).hexdigest()


def token_for(label: str) -> str:
    return "obc-v1-" + sha256(label.encode("utf-8")).hexdigest()


def build_policy(
    *,
    claim_ttl_ms: int = 60_000,
    batch_limit: int = 10,
    retry_delays_ms: tuple[int, ...] = (1_000, 5_000, 30_000),
) -> OutboxPolicy:
    return OutboxPolicy(
        policy_version=POLICY_VERSION,
        destination=DESTINATION,
        expected_sink_id=EXPECTED_SINK,
        claim_ttl_ms=claim_ttl_ms,
        batch_limit=batch_limit,
        retry_delays_ms=retry_delays_ms,
    )


def build_mutation(
    *,
    scope_id: str = "scope-a",
    thread_id: str = "thread-a",
    version: int = 1,
    recorded_at: str = T0_TEXT,
    created_at: str | None = None,
) -> ThreadEventMutation:
    participant = ScopedRef(scope_id, "core:principal", "user-1", 1)
    thread = Thread(
        thread_id=thread_id,
        scope_id=scope_id,
        title=f"{scope_id}/{thread_id}/v{version}",
        participant_refs=(participant,),
        version=version,
        created_at=created_at or recorded_at,
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


def _cross_process_lifecycle_worker(
    path_text,
    label,
    operation,
    clock_text,
    publisher_id,
    token,
    ownership_values,
    start_event,
    ready_queue,
    result_queue,
) -> None:
    """Spawn-safe worker; all observations cross the process boundary as data."""

    try:
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(Path(path_text), busy_timeout_ms=5_000),
            outbox_policy=build_policy(),
        )
        clock = AttackClock(datetime.fromisoformat(clock_text))
        tokens = AttackTokenFactory(*(() if token is None else (token,)))
        lifecycle = SQLiteOutboxLifecycleStore(
            database,
            publisher_id=publisher_id,
            clock=clock,
            claim_token_factory=tokens,
        )
        ready_queue.put(label)
        if not start_event.wait(10):
            result_queue.put({
                "label": label,
                "status": "barrier_timeout",
            })
            return
        if operation == "claim":
            claims = lifecycle.claim_eligible_batch("scope-a")
            result_queue.put({
                "label": label,
                "status": "claim_ok",
                "claims": tuple({
                    "generation": claim.ownership.claim_generation,
                    "token": claim.ownership.claim_token,
                    "publisher": claim.ownership.publisher_id,
                    "attempt": claim.attempt_count,
                    "claimed_at": claim.claimed_at,
                    "expires_at": claim.claim_expires_at,
                } for claim in claims),
                "clock_calls": clock.calls,
                "token_calls": tokens.calls,
            })
            return
        if operation != "nack" or ownership_values is None:
            raise AssertionError(f"unknown process operation: {operation}")
        ownership = OutboxClaimOwnership(
            scope_id=ownership_values[0],
            delivery_key=ownership_values[1],
            claim_generation=ownership_values[2],
            claim_token=ownership_values[3],
            publisher_id=ownership_values[4],
        )
        nack = lifecycle.nack(
            ownership,
            OutboxNackErrorCode.TRANSPORT_ERROR,
        )
        result_queue.put({
            "label": label,
            "status": "nack_ok",
            "generation": nack.claim_generation,
            "attempt": nack.attempt_count,
            "failed_at": nack.failed_at,
            "available_at": nack.available_at,
            "clock_calls": clock.calls,
            "token_calls": tokens.calls,
        })
    except BaseException as exc:
        result_queue.put({
            "label": label,
            "status": "error",
            "error_type": type(exc).__name__,
            "error_text": str(exc),
        })


def _lifecycle_exit_worker(
    path_text,
    operation,
    window,
    clock_text,
    publisher_id,
    token,
    ownership_values,
) -> None:
    """Exit at a frozen transactional boundary without interpreter cleanup."""

    update_fault = (
        RuntimePersistenceFaultPoint.OUTBOX_AFTER_CLAIM_UPDATE
        if operation == "claim"
        else RuntimePersistenceFaultPoint.OUTBOX_AFTER_NACK_UPDATE
    )

    def fault(point) -> None:
        if window == "after_update" and point is update_fault:
            os._exit(71)
        if (
            window == "before_commit"
            and point is RuntimePersistenceFaultPoint.UOW_BEFORE_COMMIT
        ):
            os._exit(72)

    database = SQLiteRuntimeDatabase(
        RuntimeSQLiteConfig(Path(path_text), busy_timeout_ms=5_000),
        outbox_policy=build_policy(),
        fault_hook=fault,
    )
    tokens = AttackTokenFactory(*(() if token is None else (token,)))
    lifecycle = SQLiteOutboxLifecycleStore(
        database,
        publisher_id=publisher_id,
        clock=AttackClock(datetime.fromisoformat(clock_text)),
        claim_token_factory=tokens,
    )
    if operation == "claim":
        claims = lifecycle.claim_eligible_batch("scope-a")
        if len(claims) != 1:
            os._exit(78)
    elif operation == "nack" and ownership_values is not None:
        ownership = OutboxClaimOwnership(
            scope_id=ownership_values[0],
            delivery_key=ownership_values[1],
            claim_generation=ownership_values[2],
            claim_token=ownership_values[3],
            publisher_id=ownership_values[4],
        )
        lifecycle.nack(ownership, OutboxNackErrorCode.TRANSPORT_ERROR)
    else:
        os._exit(79)
    os._exit(73)


class RuntimeOutboxClaimLifecycleAdversarialTests(unittest.TestCase):
    """Independent post-first-green attacks for PROD-01B-3B-1."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def runtime(
        self,
        path: Path,
        *,
        policy: OutboxPolicy | None = None,
        busy_timeout_ms: int = 5_000,
    ):
        chosen_policy = policy or build_policy()
        database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path, busy_timeout_ms=busy_timeout_ms),
            outbox_policy=chosen_policy,
        )
        database.initialize()
        return database, SQLiteThreadEventStore(database), chosen_policy

    @staticmethod
    def apply_and_commit(database, store, mutation) -> ThreadEventApplyResult:
        with database.unit_of_work() as uow:
            result = store.apply(uow, mutation)
            uow.commit()
        return result

    @staticmethod
    def lifecycle(
        database: SQLiteRuntimeDatabase,
        *,
        publisher_id: str,
        clock: AttackClock,
        tokens: AttackTokenFactory,
    ) -> SQLiteOutboxLifecycleStore:
        return SQLiteOutboxLifecycleStore(
            database,
            publisher_id=publisher_id,
            clock=clock,
            claim_token_factory=tokens,
        )

    @staticmethod
    def raw_rows(path: Path) -> tuple[tuple[object, ...], ...]:
        with sqlite3.connect(str(path)) as connection:
            return tuple(
                tuple(row)
                for row in connection.execute(
                    OUTBOX_SELECT + " ORDER BY source_event_id"
                )
            )

    @staticmethod
    def raw_row(path: Path, key: str) -> tuple[object, ...]:
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute(
                OUTBOX_SELECT + " WHERE delivery_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            raise AssertionError(f"missing raw Outbox row: {key}")
        return tuple(row)

    def run_process_race(
        self,
        path: Path,
        specs: tuple[dict[str, object], ...],
    ) -> dict[str, dict[str, object]]:
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        ready_queue = context.Queue()
        result_queue = context.Queue()
        processes = []
        for spec in specs:
            process = context.Process(
                target=_cross_process_lifecycle_worker,
                args=(
                    str(path),
                    spec["label"],
                    spec["operation"],
                    spec["clock_text"],
                    spec["publisher_id"],
                    spec.get("token"),
                    spec.get("ownership_values"),
                    start_event,
                    ready_queue,
                    result_queue,
                ),
            )
            processes.append(process)
            process.start()
        try:
            ready_labels = []
            for _ in processes:
                try:
                    ready_labels.append(ready_queue.get(timeout=10))
                except queue.Empty:
                    self.fail("cross-process worker did not reach start barrier")
            self.assertCountEqual(
                ready_labels,
                [spec["label"] for spec in specs],
            )
            start_event.set()
            for process in processes:
                process.join(timeout=10)
            hanging = [process.pid for process in processes if process.is_alive()]
            if hanging:
                self.fail(f"cross-process lifecycle workers hung: {hanging}")
            self.assertEqual(
                [process.exitcode for process in processes],
                [0] * len(processes),
            )
            payloads = []
            for _ in processes:
                try:
                    payloads.append(result_queue.get(timeout=5))
                except queue.Empty:
                    self.fail("cross-process worker produced no result")
            return {str(payload["label"]): payload for payload in payloads}
        finally:
            start_event.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            ready_queue.close()
            result_queue.close()
            ready_queue.join_thread()
            result_queue.join_thread()

    def run_exit_process(
        self,
        path: Path,
        *,
        operation: str,
        window: str,
        clock: datetime,
        publisher_id: str,
        token: str | None,
        ownership_values: tuple[str, str, int, str, str] | None,
    ) -> int | None:
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_lifecycle_exit_worker,
            args=(
                str(path),
                operation,
                window,
                canonical_utc(clock),
                publisher_id,
                token,
                ownership_values,
            ),
        )
        process.start()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            self.fail(
                f"os._exit lifecycle worker hung: {operation}/{window}"
            )
        return process.exitcode

    @staticmethod
    def verify_reopened(path: Path) -> None:
        reopened = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=build_policy(),
        )
        reopened.verify_integrity()

    @staticmethod
    def preserve_trigger_mutation(
        path: Path,
        *,
        trigger_name: str,
        statement: str,
        parameters: tuple[object, ...],
    ) -> None:
        with sqlite3.connect(str(path)) as connection:
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?",
                (trigger_name,),
            ).fetchone()
            if trigger_row is None:
                raise AssertionError(f"missing trigger: {trigger_name}")
            trigger_sql = str(trigger_row[0])
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
            connection.execute(statement, parameters)
            connection.execute(trigger_sql)

    def test_offset_event_and_one_microsecond_boundaries_use_instants(self) -> None:
        path = self.root / "offset-boundaries.sqlite3"
        database, store, policy = self.runtime(path)
        event_time = "2026-08-25T08:00:00+08:00"
        mutation = build_mutation(recorded_at=event_time)
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        initial = self.raw_row(path, key)
        instant = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
        clock = AttackClock(instant - timedelta(microseconds=1))
        tokens = AttackTokenFactory(TOKEN_A)
        lifecycle = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            tokens=tokens,
        )

        self.assertEqual(lifecycle.claim_eligible_batch("scope-a"), ())
        self.assertEqual(self.raw_row(path, key), initial)
        self.assertEqual(tokens.calls, 0)

        clock.set(instant)
        claim = lifecycle.claim_eligible_batch("scope-a")[0]
        claimed = self.raw_row(path, key)
        expiry = instant + timedelta(milliseconds=policy.claim_ttl_ms)
        self.assertEqual(claim.claimed_at, canonical_utc(instant))
        self.assertEqual(claim.claim_expires_at, canonical_utc(expiry))
        self.assertEqual(claimed[5], event_time)
        self.assertEqual(claimed[10], canonical_utc(instant))
        self.assertEqual(claimed[16], canonical_utc(expiry))

        nack_time = expiry - timedelta(microseconds=1)
        clock.set(nack_time)
        result = lifecycle.nack(
            claim.ownership,
            OutboxNackErrorCode.TRANSPORT_ERROR,
        )
        available = nack_time + timedelta(milliseconds=1_000)
        self.assertEqual(result.failed_at, canonical_utc(nack_time))
        self.assertEqual(result.available_at, canonical_utc(available))
        nacked = self.raw_row(path, key)
        self.assertEqual(nacked[9], "PENDING")
        self.assertEqual(nacked[10], canonical_utc(nack_time))
        self.assertEqual(nacked[13], canonical_utc(available))

    def test_clock_rollback_is_typed_and_never_rewrites_current_claim(self) -> None:
        path = self.root / "clock-rollback.sqlite3"
        database, store, _ = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        clock = AttackClock(T1)
        lifecycle = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            tokens=AttackTokenFactory(TOKEN_A),
        )
        ownership = lifecycle.claim_eligible_batch("scope-a")[0].ownership
        claimed = self.raw_row(path, key)
        clock.set(T1 - timedelta(microseconds=1))

        with self.assertRaises(RuntimeOutboxClockError):
            lifecycle.nack(ownership, OutboxNackErrorCode.TRANSPORT_ERROR)
        self.assertEqual(self.raw_row(path, key), claimed)
        with self.assertRaises(RuntimeOutboxClockError):
            lifecycle.claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_row(path, key), claimed)

    def test_claim_and_nack_datetime_overflow_are_typed_and_atomic(self) -> None:
        claim_path = self.root / "claim-overflow.sqlite3"
        claim_database, claim_store, _ = self.runtime(
            claim_path,
            policy=build_policy(claim_ttl_ms=1),
        )
        claim_mutation = build_mutation(
            thread_id="claim-overflow",
            recorded_at="9999-12-30T00:00:00+00:00",
        )
        self.apply_and_commit(claim_database, claim_store, claim_mutation)
        claim_key = delivery_key(claim_mutation.event.event_id)
        claim_before = self.raw_row(claim_path, claim_key)
        claim_lifecycle = self.lifecycle(
            claim_database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(datetime.max.replace(tzinfo=timezone.utc)),
            tokens=AttackTokenFactory(TOKEN_A),
        )
        with self.assertRaises(RuntimeOutboxClockError):
            claim_lifecycle.claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_row(claim_path, claim_key), claim_before)

        nack_path = self.root / "nack-overflow.sqlite3"
        overflow_policy = build_policy(
            claim_ttl_ms=3_600_000,
            retry_delays_ms=(604_800_000,),
        )
        nack_database, nack_store, _ = self.runtime(
            nack_path,
            policy=overflow_policy,
        )
        nack_mutation = build_mutation(
            thread_id="nack-overflow",
            recorded_at="9999-12-31T20:00:00+00:00",
        )
        self.apply_and_commit(nack_database, nack_store, nack_mutation)
        nack_key = delivery_key(nack_mutation.event.event_id)
        claim_time = datetime(9999, 12, 31, 21, 0, tzinfo=timezone.utc)
        nack_clock = AttackClock(claim_time)
        nack_lifecycle = self.lifecycle(
            nack_database,
            publisher_id=PUBLISHER_A,
            clock=nack_clock,
            tokens=AttackTokenFactory(TOKEN_B),
        )
        ownership = nack_lifecycle.claim_eligible_batch("scope-a")[0].ownership
        claimed_before = self.raw_row(nack_path, nack_key)
        nack_clock.set(
            datetime(9999, 12, 31, 21, 59, 59, 999999, tzinfo=timezone.utc)
        )
        with self.assertRaises(RuntimeOutboxClockError):
            nack_lifecycle.nack(
                ownership,
                OutboxNackErrorCode.TRANSPORT_ERROR,
            )
        self.assertEqual(self.raw_row(nack_path, nack_key), claimed_before)

    def test_policy_execution_profile_rejects_only_lifecycle_activation(self) -> None:
        invalid_profiles = {
            "ttl": build_policy(claim_ttl_ms=86_400_001),
            "batch": build_policy(batch_limit=1_001),
            "steps": build_policy(retry_delays_ms=tuple(range(65))),
            "delay": build_policy(retry_delays_ms=(604_800_001,)),
        }
        for index, (name, policy) in enumerate(invalid_profiles.items()):
            path = self.root / f"policy-{index}-{name}.sqlite3"
            database, store, _ = self.runtime(path, policy=policy)
            mutation = build_mutation(thread_id=f"policy-{name}")
            self.assertIs(
                self.apply_and_commit(database, store, mutation),
                ThreadEventApplyResult.APPLIED,
            )
            self.assertIsNotNone(store.get_thread("scope-a", f"policy-{name}"))
            before = self.raw_rows(path)
            with self.subTest(profile=name):
                with self.assertRaises(RuntimeOutboxValidationError):
                    self.lifecycle(
                        database,
                        publisher_id=PUBLISHER_A,
                        clock=AttackClock(T1),
                        tokens=AttackTokenFactory(TOKEN_A),
                    )
                self.assertEqual(self.raw_rows(path), before)
                self.assertEqual(database.schema_version(), 12)

    def test_batch_token_failure_and_duplicate_roll_back_every_candidate(self) -> None:
        cases = {
            "second-fails": (TOKEN_A, RuntimeError("factory-second")),
            "duplicate": (TOKEN_A, TOKEN_A),
        }
        for index, (name, values) in enumerate(cases.items()):
            path = self.root / f"batch-token-{index}.sqlite3"
            database, store, _ = self.runtime(path)
            for thread_id in ("batch-a", "batch-b"):
                self.apply_and_commit(
                    database,
                    store,
                    build_mutation(thread_id=thread_id),
                )
            before = self.raw_rows(path)
            tokens = AttackTokenFactory(*values)
            lifecycle = self.lifecycle(
                database,
                publisher_id=PUBLISHER_A,
                clock=AttackClock(T1),
                tokens=tokens,
            )
            with self.subTest(mode=name):
                with self.assertRaises(RuntimeOutboxTokenFactoryError):
                    lifecycle.claim_eligible_batch("scope-a")
                self.assertEqual(self.raw_rows(path), before)
                self.assertEqual(tokens.calls, 2)

    def test_expired_reclaim_rejects_reused_owner_token_without_writes(self) -> None:
        path = self.root / "token-reuse.sqlite3"
        database, store, policy = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        first = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1),
            tokens=AttackTokenFactory(TOKEN_A),
        ).claim_eligible_batch("scope-a")[0]
        before = self.raw_row(path, key)
        expiry = T1 + timedelta(milliseconds=policy.claim_ttl_ms)
        reclaimer = self.lifecycle(
            database,
            publisher_id=PUBLISHER_B,
            clock=AttackClock(expiry),
            tokens=AttackTokenFactory(first.ownership.claim_token),
        )
        with self.assertRaises(RuntimeOutboxTokenFactoryError):
            reclaimer.claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_row(path, key), before)

    def test_blocked_aggregate_does_not_consume_other_batch_capacity(self) -> None:
        path = self.root / "blocked-capacity.sqlite3"
        policy = build_policy(batch_limit=2)
        database, store, _ = self.runtime(path, policy=policy)
        blocked_1 = build_mutation(
            thread_id="blocked",
            recorded_at="2026-08-25T00:10:00+00:00",
        )
        blocked_2 = build_mutation(
            thread_id="blocked",
            version=2,
            recorded_at="2026-08-25T00:11:00+00:00",
            created_at="2026-08-25T00:10:00+00:00",
        )
        healthy_1 = build_mutation(
            thread_id="healthy-a",
            recorded_at=T0_TEXT,
        )
        healthy_2 = build_mutation(
            thread_id="healthy-b",
            recorded_at=T1_TEXT,
        )
        for mutation in (blocked_1, blocked_2, healthy_1, healthy_2):
            self.apply_and_commit(database, store, mutation)
        tokens = AttackTokenFactory(TOKEN_A, TOKEN_B, TOKEN_C)
        claims = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(datetime(2026, 8, 25, 0, 5, tzinfo=timezone.utc)),
            tokens=tokens,
        ).claim_eligible_batch("scope-a")

        self.assertEqual(
            [claim.source_event_id for claim in claims],
            [healthy_1.event.event_id, healthy_2.event.event_id],
        )
        states = {row[1]: row[9] for row in self.raw_rows(path)}
        self.assertEqual(states[blocked_1.event.event_id], "PENDING")
        self.assertEqual(states[blocked_2.event.event_id], "PENDING")
        self.assertEqual(states[healthy_1.event.event_id], "CLAIMED")
        self.assertEqual(states[healthy_2.event.event_id], "CLAIMED")
        self.assertEqual(tokens.calls, 2)

    def test_scope_corruption_is_isolated_but_same_scope_predecessor_blocks_all(self) -> None:
        isolated_path = self.root / "scope-isolation.sqlite3"
        database, store, _ = self.runtime(isolated_path)
        scope_a = build_mutation(scope_id="scope-a", thread_id="healthy-a")
        scope_b = build_mutation(scope_id="scope-b", thread_id="corrupt-b")
        self.apply_and_commit(database, store, scope_a)
        self.apply_and_commit(database, store, scope_b)
        corrupt_key = delivery_key(scope_b.event.event_id)
        updated = canonical_utc(T0)
        wrong_available = canonical_utc(T0 + timedelta(milliseconds=2_000))
        with sqlite3.connect(str(isolated_path)) as connection:
            connection.execute(
                """UPDATE runtime_outbox
                   SET updated_at=?, claim_generation=1, attempt_count=1,
                       available_at=?, last_error_code='outbox:transport_error'
                   WHERE delivery_key=?""",
                (updated, wrong_available, corrupt_key),
            )
        corrupt_before = self.raw_row(isolated_path, corrupt_key)

        claims = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1),
            tokens=AttackTokenFactory(TOKEN_A),
        ).claim_eligible_batch("scope-a")
        self.assertEqual([item.source_event_id for item in claims], [scope_a.event.event_id])
        self.assertEqual(self.raw_row(isolated_path, corrupt_key), corrupt_before)
        with self.assertRaises(RuntimeStoredDataCorruptionError):
            self.lifecycle(
                database,
                publisher_id=PUBLISHER_B,
                clock=AttackClock(T1),
                tokens=AttackTokenFactory(TOKEN_B),
            ).claim_eligible_batch("scope-b")
        self.assertEqual(self.raw_row(isolated_path, corrupt_key), corrupt_before)

        predecessor_path = self.root / "missing-predecessor.sqlite3"
        predecessor_db, predecessor_store, _ = self.runtime(predecessor_path)
        first = build_mutation(thread_id="broken")
        second = build_mutation(
            thread_id="broken",
            version=2,
            recorded_at=T1_TEXT,
            created_at=T0_TEXT,
        )
        healthy = build_mutation(thread_id="must-not-claim")
        for mutation in (first, second, healthy):
            self.apply_and_commit(predecessor_db, predecessor_store, mutation)
        self.preserve_trigger_mutation(
            predecessor_path,
            trigger_name="runtime_outbox_deny_delete",
            statement="DELETE FROM runtime_outbox WHERE delivery_key=?",
            parameters=(delivery_key(first.event.event_id),),
        )
        before = self.raw_rows(predecessor_path)
        with self.assertRaises(RuntimeStoredDataCorruptionError):
            self.lifecycle(
                predecessor_db,
                publisher_id=PUBLISHER_A,
                clock=AttackClock(T2),
                tokens=AttackTokenFactory(TOKEN_A, TOKEN_B),
            ).claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_rows(predecessor_path), before)

    def test_later_claimed_sequence_corrupts_scope_and_integrity_scan(self) -> None:
        path = self.root / "later-sequence-claimed.sqlite3"
        database, store, policy = self.runtime(path)
        first = build_mutation(thread_id="cross-row")
        second = build_mutation(
            thread_id="cross-row",
            version=2,
            recorded_at=T1_TEXT,
            created_at=T0_TEXT,
        )
        self.apply_and_commit(database, store, first)
        self.apply_and_commit(database, store, second)
        second_key = delivery_key(second.event.event_id)
        claimed_at = T1
        claim_expires_at = claimed_at + timedelta(
            milliseconds=policy.claim_ttl_ms
        )
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                """UPDATE runtime_outbox
                   SET state='CLAIMED', updated_at=?, claim_generation=1,
                       attempt_count=1, available_at=NULL, claim_token=?,
                       publisher_id=?, claim_expires_at=?,
                       last_error_code=NULL, suppress_reason=NULL,
                       published_at=NULL, receipt_id=NULL
                   WHERE delivery_key=?""",
                (
                    canonical_utc(claimed_at),
                    TOKEN_B,
                    PUBLISHER_B,
                    canonical_utc(claim_expires_at),
                    second_key,
                ),
            )
        before = self.raw_rows(path)
        tokens = AttackTokenFactory(TOKEN_A)
        lifecycle = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1 + timedelta(seconds=30)),
            tokens=tokens,
        )

        claim_error = None
        try:
            lifecycle.claim_eligible_batch("scope-a")
        except BaseException as exc:
            claim_error = exc
        after_claim = self.raw_rows(path)

        integrity_error = None
        try:
            database.verify_integrity()
        except BaseException as exc:
            integrity_error = exc

        nack_path = self.root / "later-sequence-claimed-nack.sqlite3"
        nack_database, nack_store, nack_policy = self.runtime(nack_path)
        nack_first = build_mutation(thread_id="cross-row-nack")
        nack_second = build_mutation(
            thread_id="cross-row-nack",
            version=2,
            recorded_at=T1_TEXT,
            created_at=T0_TEXT,
        )
        self.apply_and_commit(nack_database, nack_store, nack_first)
        self.apply_and_commit(nack_database, nack_store, nack_second)
        nack_second_key = delivery_key(nack_second.event.event_id)
        nack_claim_expires_at = claimed_at + timedelta(
            milliseconds=nack_policy.claim_ttl_ms
        )
        with sqlite3.connect(str(nack_path)) as connection:
            connection.execute(
                """UPDATE runtime_outbox
                   SET state='CLAIMED', updated_at=?, claim_generation=1,
                       attempt_count=1, available_at=NULL, claim_token=?,
                       publisher_id=?, claim_expires_at=?,
                       last_error_code=NULL, suppress_reason=NULL,
                       published_at=NULL, receipt_id=NULL
                   WHERE delivery_key=?""",
                (
                    canonical_utc(claimed_at),
                    TOKEN_B,
                    PUBLISHER_B,
                    canonical_utc(nack_claim_expires_at),
                    nack_second_key,
                ),
            )
        before_nack = self.raw_rows(nack_path)
        nack_clock = AttackClock(T1 + timedelta(seconds=30))
        nack_lifecycle = self.lifecycle(
            nack_database,
            publisher_id=PUBLISHER_B,
            clock=nack_clock,
            tokens=AttackTokenFactory(),
        )
        nack_ownership = OutboxClaimOwnership(
            scope_id="scope-a",
            delivery_key=nack_second_key,
            claim_generation=1,
            claim_token=TOKEN_B,
            publisher_id=PUBLISHER_B,
        )
        nack_error = None
        try:
            nack_lifecycle.nack(
                nack_ownership,
                OutboxNackErrorCode.TRANSPORT_ERROR,
            )
        except BaseException as exc:
            nack_error = exc
        after_nack = self.raw_rows(nack_path)

        with self.subTest(oracle="claim-typed-corruption"):
            self.assertIsInstance(
                claim_error,
                RuntimeStoredDataCorruptionError,
            )
        with self.subTest(oracle="claim-does-not-consume-token"):
            self.assertEqual(tokens.calls, 0)
        with self.subTest(oracle="claim-zero-write"):
            self.assertEqual(after_claim, before)
        with self.subTest(oracle="integrity-scan-detects-cross-row-corruption"):
            self.assertIsInstance(
                integrity_error,
                RuntimeStoredDataCorruptionError,
            )
        with self.subTest(oracle="nack-typed-corruption"):
            self.assertIsInstance(
                nack_error,
                RuntimeStoredDataCorruptionError,
            )
        with self.subTest(oracle="nack-samples-clock-once"):
            self.assertEqual(nack_clock.calls, 1)
        with self.subTest(oracle="nack-zero-write"):
            self.assertEqual(after_nack, before_nack)

    def test_published_predecessor_is_not_trusted_before_receipt_decoder(self) -> None:
        path = self.root / "published-predecessor.sqlite3"
        database, store, _ = self.runtime(path)
        first = build_mutation(thread_id="published-chain")
        second = build_mutation(
            thread_id="published-chain",
            version=2,
            recorded_at=T1_TEXT,
            created_at=T0_TEXT,
        )
        healthy = build_mutation(thread_id="healthy-peer")
        for mutation in (first, second, healthy):
            self.apply_and_commit(database, store, mutation)
        first_key = delivery_key(first.event.event_id)
        first_digest = event_digest(first.event)
        receipt_id = "receipt-adversarial-1"
        acked_at = canonical_utc(T1)
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                """INSERT INTO runtime_outbox_receipts(
                       receipt_id, delivery_key, destination, source_event_id,
                       event_digest, claim_generation, claim_token,
                       publisher_id, sink_id, ack_id, acked_at, ack_digest
                   ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt_id,
                    first_key,
                    DESTINATION,
                    first.event.event_id,
                    first_digest,
                    TOKEN_A,
                    PUBLISHER_A,
                    EXPECTED_SINK,
                    "ack-adversarial-1",
                    acked_at,
                    "d" * 64,
                ),
            )
            connection.execute(
                """UPDATE runtime_outbox
                   SET state='PUBLISHED', updated_at=?, claim_generation=1,
                       attempt_count=1, available_at=NULL, claim_token=NULL,
                       publisher_id=NULL, claim_expires_at=NULL,
                       last_error_code=NULL, suppress_reason=NULL,
                       published_at=?, receipt_id=?
                   WHERE delivery_key=?""",
                (acked_at, acked_at, receipt_id, first_key),
            )
        before = self.raw_rows(path)
        with self.assertRaises(RuntimeStoredDataCorruptionError):
            self.lifecycle(
                database,
                publisher_id=PUBLISHER_A,
                clock=AttackClock(T2),
                tokens=AttackTokenFactory(TOKEN_B, TOKEN_C),
            ).claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_rows(path), before)

    def test_int64_attempt_exhaustion_is_typed_and_atomic(self) -> None:
        path = self.root / "attempt-exhausted.sqlite3"
        database, store, policy = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        updated = canonical_utc(T1)
        available = canonical_utc(
            T1 + timedelta(milliseconds=policy.retry_delays_ms[-1])
        )
        with sqlite3.connect(str(path)) as connection:
            connection.execute(
                """UPDATE runtime_outbox
                   SET updated_at=?, claim_generation=?, attempt_count=?,
                       available_at=?, last_error_code='outbox:transport_error'
                   WHERE delivery_key=?""",
                (updated, SQLITE_INT64_MAX, SQLITE_INT64_MAX, available, key),
            )
        before = self.raw_row(path, key)
        tokens = AttackTokenFactory(TOKEN_A)
        with self.assertRaises(RuntimeOutboxAttemptExhaustedError):
            self.lifecycle(
                database,
                publisher_id=PUBLISHER_A,
                clock=AttackClock(
                    T1 + timedelta(milliseconds=policy.retry_delays_ms[-1])
                ),
                tokens=tokens,
            ).claim_eligible_batch("scope-a")
        self.assertEqual(self.raw_row(path, key), before)
        self.assertEqual(tokens.calls, 0)

    def test_foreign_publisher_store_cannot_consume_nack_ownership(self) -> None:
        path = self.root / "foreign-publisher.sqlite3"
        database, store, _ = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        claim = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1),
            tokens=AttackTokenFactory(TOKEN_A),
        ).claim_eligible_batch("scope-a")[0]
        before = self.raw_row(path, key)
        foreign_store = self.lifecycle(
            database,
            publisher_id=PUBLISHER_B,
            clock=AttackClock(T1 + timedelta(seconds=1)),
            tokens=AttackTokenFactory(TOKEN_B),
        )

        observed = None
        try:
            observed = foreign_store.nack(
                claim.ownership,
                OutboxNackErrorCode.TRANSPORT_ERROR,
            )
        except RuntimeOutboxOwnershipLostError:
            pass
        if observed is not None:
            self.fail(
                "foreign publisher consumed another Store's ownership: "
                f"result={observed!r}, row={self.raw_row(path, key)!r}"
            )
        self.assertEqual(self.raw_row(path, key), before)

    def test_public_uow_still_cannot_mutate_outbox_lifecycle(self) -> None:
        path = self.root / "public-uow.sqlite3"
        database, store, _ = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        before = self.raw_rows(path)
        statements = (
            "UPDATE runtime_outbox SET state='CLAIMED'",
            "UPDATE runtime_outbox SET claim_generation=claim_generation+1",
            "DELETE FROM runtime_outbox",
            "INSERT INTO runtime_outbox_receipts DEFAULT VALUES",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                with database.unit_of_work() as uow:
                    with self.assertRaises(RuntimeUnitOfWorkStateError):
                        uow.execute(statement)
        self.assertEqual(self.raw_rows(path), before)

    def test_multithread_claim_stress_has_one_generation_one_owner(self) -> None:
        for round_index in range(25):
            path = self.root / f"thread-race-{round_index}.sqlite3"
            database, store, _ = self.runtime(path)
            mutation = build_mutation(thread_id=f"race-{round_index}")
            self.apply_and_commit(database, store, mutation)
            key = delivery_key(mutation.event.event_id)
            publishers = (
                f"publisher:race-a-{round_index}",
                f"publisher:race-b-{round_index}",
            )
            lifecycles = tuple(
                self.lifecycle(
                    database,
                    publisher_id=publisher,
                    clock=AttackClock(T1),
                    tokens=AttackTokenFactory(
                        token_for(f"{round_index}:{publisher}")
                    ),
                )
                for publisher in publishers
            )
            barrier = threading.Barrier(3)
            results: list[tuple[object, ...]] = []
            errors: list[BaseException] = []
            guard = threading.Lock()

            def run(index: int) -> None:
                try:
                    barrier.wait(timeout=5)
                    value = lifecycles[index].claim_eligible_batch("scope-a")
                    with guard:
                        results.append(value)
                except BaseException as exc:
                    with guard:
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

            with self.subTest(round=round_index):
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual(errors, [])
                self.assertEqual(sorted(len(value) for value in results), [0, 1])
                row = self.raw_row(path, key)
                self.assertEqual(row[9], "CLAIMED")
                self.assertEqual(row[11:13], (1, 1))

    def test_cross_process_claim_reclaim_and_nack_reclaim_are_serializable(
        self,
    ) -> None:
        initial_path = self.root / "process-initial-claim-race.sqlite3"
        initial_database, initial_store, initial_policy = self.runtime(
            initial_path
        )
        initial_mutation = build_mutation(thread_id="process-initial-race")
        self.apply_and_commit(
            initial_database,
            initial_store,
            initial_mutation,
        )
        initial_key = delivery_key(initial_mutation.event.event_id)
        initial_results = self.run_process_race(
            initial_path,
            (
                {
                    "label": "initial-a",
                    "operation": "claim",
                    "clock_text": canonical_utc(T1),
                    "publisher_id": PUBLISHER_A,
                    "token": TOKEN_A,
                },
                {
                    "label": "initial-b",
                    "operation": "claim",
                    "clock_text": canonical_utc(T1),
                    "publisher_id": PUBLISHER_B,
                    "token": TOKEN_B,
                },
            ),
        )
        self.assertEqual(
            {result["status"] for result in initial_results.values()},
            {"claim_ok"},
        )
        self.assertEqual(
            sorted(len(result["claims"]) for result in initial_results.values()),
            [0, 1],
        )
        initial_winner = next(
            result["claims"][0]
            for result in initial_results.values()
            if result["claims"]
        )
        self.assertEqual(
            self.raw_row(initial_path, initial_key)[9:21],
            (
                "CLAIMED",
                canonical_utc(T1),
                1,
                1,
                None,
                initial_winner["token"],
                initial_winner["publisher"],
                canonical_utc(
                    T1 + timedelta(milliseconds=initial_policy.claim_ttl_ms)
                ),
                None,
                None,
                None,
                None,
            ),
        )
        self.verify_reopened(initial_path)

        reclaim_path = self.root / "process-expired-reclaim-race.sqlite3"
        reclaim_database, reclaim_store, reclaim_policy = self.runtime(
            reclaim_path
        )
        reclaim_mutation = build_mutation(thread_id="process-reclaim-race")
        self.apply_and_commit(
            reclaim_database,
            reclaim_store,
            reclaim_mutation,
        )
        reclaim_key = delivery_key(reclaim_mutation.event.event_id)
        self.lifecycle(
            reclaim_database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1),
            tokens=AttackTokenFactory(TOKEN_A),
        ).claim_eligible_batch("scope-a")
        publisher_c = "publisher:attack-c"
        reclaim_results = self.run_process_race(
            reclaim_path,
            (
                {
                    "label": "reclaim-b",
                    "operation": "claim",
                    "clock_text": canonical_utc(T2),
                    "publisher_id": PUBLISHER_B,
                    "token": TOKEN_B,
                },
                {
                    "label": "reclaim-c",
                    "operation": "claim",
                    "clock_text": canonical_utc(T2),
                    "publisher_id": publisher_c,
                    "token": TOKEN_C,
                },
            ),
        )
        self.assertEqual(
            {result["status"] for result in reclaim_results.values()},
            {"claim_ok"},
        )
        self.assertEqual(
            sorted(len(result["claims"]) for result in reclaim_results.values()),
            [0, 1],
        )
        reclaim_winner = next(
            result["claims"][0]
            for result in reclaim_results.values()
            if result["claims"]
        )
        self.assertEqual(
            self.raw_row(reclaim_path, reclaim_key)[9:21],
            (
                "CLAIMED",
                canonical_utc(T2),
                2,
                2,
                None,
                reclaim_winner["token"],
                reclaim_winner["publisher"],
                canonical_utc(
                    T2 + timedelta(milliseconds=reclaim_policy.claim_ttl_ms)
                ),
                None,
                None,
                None,
                None,
            ),
        )
        self.verify_reopened(reclaim_path)

        nack_race_path = self.root / "process-nack-reclaim-race.sqlite3"
        nack_database, nack_store, nack_policy = self.runtime(nack_race_path)
        nack_mutation = build_mutation(thread_id="process-nack-reclaim-race")
        self.apply_and_commit(nack_database, nack_store, nack_mutation)
        nack_key = delivery_key(nack_mutation.event.event_id)
        old_claim = self.lifecycle(
            nack_database,
            publisher_id=PUBLISHER_A,
            clock=AttackClock(T1),
            tokens=AttackTokenFactory(TOKEN_A),
        ).claim_eligible_batch("scope-a")[0]
        old_owner = old_claim.ownership
        ownership_values = (
            old_owner.scope_id,
            old_owner.delivery_key,
            old_owner.claim_generation,
            old_owner.claim_token,
            old_owner.publisher_id,
        )
        nack_time = T2 - timedelta(microseconds=1)
        nack_race_results = self.run_process_race(
            nack_race_path,
            (
                {
                    "label": "old-owner-nack",
                    "operation": "nack",
                    "clock_text": canonical_utc(nack_time),
                    "publisher_id": PUBLISHER_A,
                    "ownership_values": ownership_values,
                },
                {
                    "label": "expired-reclaim",
                    "operation": "claim",
                    "clock_text": canonical_utc(T2),
                    "publisher_id": PUBLISHER_B,
                    "token": TOKEN_B,
                },
            ),
        )
        reclaim_result = nack_race_results["expired-reclaim"]
        nack_result = nack_race_results["old-owner-nack"]
        self.assertEqual(reclaim_result["status"], "claim_ok")
        if len(reclaim_result["claims"]) == 1:
            self.assertEqual(nack_result["status"], "error")
            self.assertEqual(
                nack_result["error_type"],
                RuntimeOutboxOwnershipLostError.__name__,
            )
            self.assertEqual(
                self.raw_row(nack_race_path, nack_key)[9:21],
                (
                    "CLAIMED",
                    canonical_utc(T2),
                    2,
                    2,
                    None,
                    TOKEN_B,
                    PUBLISHER_B,
                    canonical_utc(
                        T2
                        + timedelta(milliseconds=nack_policy.claim_ttl_ms)
                    ),
                    None,
                    None,
                    None,
                    None,
                ),
            )
        else:
            self.assertEqual(reclaim_result["claims"], ())
            self.assertEqual(nack_result["status"], "nack_ok")
            available = nack_time + timedelta(
                milliseconds=nack_policy.retry_delays_ms[0]
            )
            self.assertEqual(
                self.raw_row(nack_race_path, nack_key)[9:21],
                (
                    "PENDING",
                    canonical_utc(nack_time),
                    1,
                    1,
                    canonical_utc(available),
                    None,
                    None,
                    None,
                    OutboxNackErrorCode.TRANSPORT_ERROR.value,
                    None,
                    None,
                    None,
                ),
            )
        self.verify_reopened(nack_race_path)

    def test_process_exit_claim_and_nack_recover_at_transaction_boundaries(
        self,
    ) -> None:
        exit_codes = {
            "after_update": 71,
            "before_commit": 72,
            "after_commit": 73,
        }
        for operation in ("claim", "nack"):
            for window, expected_exit_code in exit_codes.items():
                with self.subTest(operation=operation, window=window):
                    path = self.root / f"exit-{operation}-{window}.sqlite3"
                    database, store, policy = self.runtime(path)
                    mutation = build_mutation(
                        thread_id=f"exit-{operation}-{window}"
                    )
                    self.apply_and_commit(database, store, mutation)
                    key = delivery_key(mutation.event.event_id)
                    ownership_values = None
                    action_clock = T1
                    publisher_id = PUBLISHER_B
                    token = TOKEN_B
                    if operation == "nack":
                        claim = self.lifecycle(
                            database,
                            publisher_id=PUBLISHER_A,
                            clock=AttackClock(T1),
                            tokens=AttackTokenFactory(TOKEN_A),
                        ).claim_eligible_batch("scope-a")[0]
                        owner = claim.ownership
                        ownership_values = (
                            owner.scope_id,
                            owner.delivery_key,
                            owner.claim_generation,
                            owner.claim_token,
                            owner.publisher_id,
                        )
                        action_clock = T1 + timedelta(seconds=1)
                        publisher_id = PUBLISHER_A
                        token = None
                    before = self.raw_rows(path)
                    exit_code = self.run_exit_process(
                        path,
                        operation=operation,
                        window=window,
                        clock=action_clock,
                        publisher_id=publisher_id,
                        token=token,
                        ownership_values=ownership_values,
                    )
                    self.assertEqual(exit_code, expected_exit_code)
                    after = self.raw_rows(path)
                    if window != "after_commit":
                        self.assertEqual(after, before)
                    elif operation == "claim":
                        self.assertEqual(
                            self.raw_row(path, key)[9:21],
                            (
                                "CLAIMED",
                                canonical_utc(action_clock),
                                1,
                                1,
                                None,
                                TOKEN_B,
                                PUBLISHER_B,
                                canonical_utc(
                                    action_clock
                                    + timedelta(
                                        milliseconds=policy.claim_ttl_ms
                                    )
                                ),
                                None,
                                None,
                                None,
                                None,
                            ),
                        )
                    else:
                        available = action_clock + timedelta(
                            milliseconds=policy.retry_delays_ms[0]
                        )
                        self.assertEqual(
                            self.raw_row(path, key)[9:21],
                            (
                                "PENDING",
                                canonical_utc(action_clock),
                                1,
                                1,
                                canonical_utc(available),
                                None,
                                None,
                                None,
                                OutboxNackErrorCode.TRANSPORT_ERROR.value,
                                None,
                                None,
                                None,
                            ),
                        )
                    self.verify_reopened(path)

    def test_state_event_exact_retry_never_resets_claimed_or_nacked_row(self) -> None:
        path = self.root / "exact-retry.sqlite3"
        database, store, _ = self.runtime(path)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        clock = AttackClock(T1)
        lifecycle = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            tokens=AttackTokenFactory(TOKEN_A),
        )
        claim = lifecycle.claim_eligible_batch("scope-a")[0]
        claimed = self.raw_row(path, key)

        self.assertIs(
            self.apply_and_commit(database, store, mutation),
            ThreadEventApplyResult.ALREADY_COMMITTED,
        )
        self.assertEqual(self.raw_row(path, key), claimed)

        clock.set(T1 + timedelta(seconds=1))
        lifecycle.nack(claim.ownership, OutboxNackErrorCode.TRANSPORT_ERROR)
        nacked = self.raw_row(path, key)
        self.assertIs(
            self.apply_and_commit(database, store, mutation),
            ThreadEventApplyResult.ALREADY_COMMITTED,
        )
        self.assertEqual(self.raw_row(path, key), nacked)

    def test_claim_and_nack_busy_are_typed_bounded_and_retryable(self) -> None:
        path = self.root / "busy.sqlite3"
        database, store, _ = self.runtime(path, busy_timeout_ms=50)
        mutation = build_mutation()
        self.apply_and_commit(database, store, mutation)
        key = delivery_key(mutation.event.event_id)
        clock = AttackClock(T1)
        lifecycle = self.lifecycle(
            database,
            publisher_id=PUBLISHER_A,
            clock=clock,
            tokens=AttackTokenFactory(TOKEN_A),
        )
        pending = self.raw_row(path, key)

        lock = sqlite3.connect(str(path), isolation_level=None)
        lock.execute("PRAGMA busy_timeout=0")
        lock.execute("BEGIN IMMEDIATE")
        started = monotonic()
        try:
            with self.assertRaises(RuntimeDatabaseBusyError):
                lifecycle.claim_eligible_batch("scope-a")
        finally:
            elapsed = monotonic() - started
            lock.rollback()
            lock.close()
        self.assertLess(elapsed, 0.5)
        self.assertEqual(self.raw_row(path, key), pending)

        claim = lifecycle.claim_eligible_batch("scope-a")[0]
        claimed = self.raw_row(path, key)
        clock.set(T1 + timedelta(seconds=1))
        lock = sqlite3.connect(str(path), isolation_level=None)
        lock.execute("PRAGMA busy_timeout=0")
        lock.execute("BEGIN IMMEDIATE")
        started = monotonic()
        try:
            with self.assertRaises(RuntimeDatabaseBusyError):
                lifecycle.nack(
                    claim.ownership,
                    OutboxNackErrorCode.TRANSPORT_ERROR,
                )
        finally:
            elapsed = monotonic() - started
            lock.rollback()
            lock.close()
        self.assertLess(elapsed, 0.5)
        self.assertEqual(self.raw_row(path, key), claimed)
        result = lifecycle.nack(
            claim.ownership,
            OutboxNackErrorCode.TRANSPORT_ERROR,
        )
        self.assertEqual(result.claim_generation, 1)
        self.assertEqual(self.raw_row(path, key)[9], "PENDING")


if __name__ == "__main__":
    unittest.main()
