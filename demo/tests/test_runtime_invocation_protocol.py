import json
import unittest
from dataclasses import replace

from coding_workflow.runtime_domain.common import (
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
)
from coding_workflow.runtime_domain.invocation import (
    Attempt,
    AttemptLease,
    CleanupState,
    ExecutionState,
    FenceToken,
    FencedMutation,
    FencedMutationDecision,
    FencedMutationDecisionCode,
    Invocation,
    InvocationInputRef,
    TerminalRecord,
    TerminationIntent,
    digest_invocation_inputs,
    evaluate_fenced_mutation,
    validate_attempt_binding,
    validate_attempt_transition,
    validate_cleanup_transition,
    validate_execution_transition,
    validate_invocation_transition,
    validate_parent_child,
)


SCOPE = "scope-a"
THREAD = ScopedRef(SCOPE, "core:thread", "thread-1", 3)
TURN = ScopedRef(SCOPE, "core:turn", "turn-1", 2)
AGENT = ScopedRef(SCOPE, "core:agent_instance", "agent-1", 1)
SESSION = ScopedRef(SCOPE, "core:agent_session", "session-1", 4)
POLICY = ScopedRef(SCOPE, "core:policy_snapshot", "policy-1", 2)
BUDGET = ScopedRef(SCOPE, "core:budget_reservation", "budget-1", 1)
INPUT_ARTIFACT = ScopedRef(SCOPE, "core:artifact", "input-1", 5)
INPUT = InvocationInputRef(INPUT_ARTIFACT, "a" * 64)
INPUTS = (INPUT,)
INPUT_DIGEST = digest_invocation_inputs(INPUTS)
DEADLINE = "2026-08-23T12:00:00+00:00"
ACQUIRED = "2026-08-23T11:00:00+00:00"
HEARTBEAT = "2026-08-23T11:10:00+00:00"
LEASE_EXPIRES = "2026-08-23T11:30:00+00:00"
OBSERVED = "2026-08-23T11:20:00+00:00"


def make_invocation(**changes) -> Invocation:
    values = {
        "scope_id": SCOPE,
        "invocation_id": "inv-1",
        "thread_ref": THREAD,
        "turn_ref": TURN,
        "agent_instance_ref": AGENT,
        "agent_session_ref": SESSION,
        "input_refs": INPUTS,
        "input_digest": INPUT_DIGEST,
        "policy_snapshot_ref": POLICY,
        "budget_reservation_ref": BUDGET,
        "deadline_at": DEADLINE,
    }
    values.update(changes)
    return Invocation(**values)


def attempt_ref(attempt_id="attempt-1", version=1, scope=SCOPE):
    return ScopedRef(scope, "core:attempt", attempt_id, version)


def invocation_ref(invocation_id="inv-1", version=1, scope=SCOPE):
    return ScopedRef(scope, "core:invocation", invocation_id, version)


def make_fence(
    generation=1,
    *,
    inv_ref=None,
    att_ref=None,
    thread_ref=THREAD,
    scope=SCOPE,
) -> FenceToken:
    return FenceToken(
        scope,
        thread_ref,
        inv_ref or invocation_ref(),
        att_ref or attempt_ref(),
        generation,
    )


def make_lease(fence=None, **changes) -> AttemptLease:
    token = fence or make_fence()
    values = {
        "lease_ref": ScopedRef(SCOPE, "core:lease", "lease-1", 1),
        "scope_id": SCOPE,
        "thread_ref": THREAD,
        "invocation_ref": token.invocation_ref,
        "attempt_ref": token.attempt_ref,
        "owner_id": "worker-1",
        "acquired_at": ACQUIRED,
        "expires_at": LEASE_EXPIRES,
        "fence": token,
        "last_heartbeat_at": HEARTBEAT,
    }
    values.update(changes)
    return AttemptLease(**values)


def make_attempt(**changes) -> Attempt:
    fence_was_supplied = "fence" in changes
    supplied_fence = changes.pop("fence", None)
    bound_invocation_ref = changes.get("invocation_ref", invocation_ref())
    token = (
        supplied_fence
        if fence_was_supplied
        else make_fence(inv_ref=bound_invocation_ref)
    )
    values = {
        "scope_id": SCOPE,
        "attempt_id": "attempt-1",
        "invocation_ref": invocation_ref(),
        "thread_ref": THREAD,
        "turn_ref": TURN,
        "agent_instance_ref": AGENT,
        "agent_session_ref": SESSION,
        "ordinal": 1,
        "input_digest": INPUT_DIGEST,
        "policy_snapshot_ref": POLICY,
        "deadline_at": DEADLINE,
        "execution_state": ExecutionState.RUNNING,
        "cleanup_state": CleanupState.ACTIVE,
        "worker_id": "worker-1",
        "principal_id": "principal-1",
        "selection_ref": ScopedRef(
            SCOPE, "core:worker_selection", "selection-1", 1
        ),
        "fence": token,
        "fence_revoked": token is None,
        "lease": make_lease(token) if token is not None else None,
        "lease_active": token is not None,
    }
    values.update(changes)
    return Attempt(**values)


def running_pair(generation=1):
    fence = make_fence(generation)
    attempt = make_attempt(fence=fence)
    invocation = make_invocation(
        execution_state=ExecutionState.RUNNING,
        cleanup_state=CleanupState.ACTIVE,
        active_attempt_ref=attempt_ref(),
        active_lease_refs=(ScopedRef(SCOPE, "core:lease", "lease-1", 1),),
        fence_generation=generation,
        fence_revoked=False,
    )
    return invocation, attempt


def make_mutation(fence=None, **changes) -> FencedMutation:
    token = fence or make_fence()
    values = {
        "mutation_id": "mutation-1",
        "mutation_kind": "core:attempt_result",
        "thread_ref": THREAD,
        "fence": token,
        "input_digest": INPUT_DIGEST,
        "policy_snapshot_ref": POLICY,
        "payload_digest": "b" * 64,
        "submitted_at": "2026-08-23T11:19:00+00:00",
    }
    values.update(changes)
    return FencedMutation(**values)


def terminal_record(
    subject_ref,
    state=ExecutionState.SUCCEEDED,
    outputs=(),
    *,
    finished_at="2026-08-23T11:25:00+00:00",
):
    return TerminalRecord(
        "terminal-1",
        SCOPE,
        subject_ref,
        state,
        "runtime:completed" if state is ExecutionState.SUCCEEDED else "runtime:failed",
        finished_at,
        outputs,
        digest_invocation_inputs(outputs),
    )


class InvocationProtocolTests(unittest.TestCase):
    def test_snapshot_input_digest_binds_ref_version_hash_and_order(self):
        second = InvocationInputRef(
            ScopedRef(SCOPE, "core:message", "message-2", 1), "c" * 64
        )
        ordered = (INPUT, second)
        reversed_inputs = (second, INPUT)
        self.assertNotEqual(
            digest_invocation_inputs(ordered),
            digest_invocation_inputs(reversed_inputs),
        )
        invocation = make_invocation(
            input_refs=ordered,
            input_digest=digest_invocation_inputs(ordered),
        )
        self.assertEqual(invocation.input_refs, ordered)

        drifted = InvocationInputRef(INPUT_ARTIFACT, "d" * 64)
        with self.assertRaisesRegex(RuntimeProtocolError, "完整输入快照"):
            make_invocation(input_refs=(drifted,), input_digest=INPUT_DIGEST)
        with self.assertRaisesRegex(RuntimeProtocolError, "同一 ref/version"):
            make_invocation(
                input_refs=(INPUT, drifted),
                input_digest=digest_invocation_inputs((INPUT, drifted)),
            )

    def test_ownership_refs_are_typed_and_scope_closed(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "引用类型无效"):
            make_invocation(
                thread_ref=ScopedRef(SCOPE, "core:turn", "thread-1", 1)
            )
        with self.assertRaises(ScopeBoundaryError):
            make_invocation(
                agent_session_ref=ScopedRef(
                    "scope-b", "core:agent_session", "session-1", 1
                )
            )

    def test_running_invocation_retry_gap_has_no_active_lease(self):
        # Invocation stays RUNNING between failed Attempts so it does not move
        # backwards to CLAIMED, but the old execution unit must already be fenced.
        gap = make_invocation(
            execution_state=ExecutionState.RUNNING,
            cleanup_state=CleanupState.ACTIVE,
        )
        self.assertIsNone(gap.active_attempt_ref)
        self.assertTrue(gap.fence_revoked)
        with self.assertRaisesRegex(RuntimeProtocolError, "活动 Lease"):
            make_invocation(
                execution_state=ExecutionState.RUNNING,
                cleanup_state=CleanupState.ACTIVE,
                active_lease_refs=(ScopedRef(
                    SCOPE, "core:lease", "orphan-lease", 1
                ),),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "created/queued"):
            make_invocation(
                active_resource_refs=(ScopedRef(
                    SCOPE, "core:execution_resource", "early-port", 1
                ),),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "cleanup_state"):
            make_invocation(
                execution_state=ExecutionState.RUNNING,
                cleanup_state=CleanupState.ALLOCATED,
            )

    def test_execution_and_cleanup_transition_tables_are_strict(self):
        legal_execution = (
            (ExecutionState.CREATED, ExecutionState.QUEUED),
            (ExecutionState.QUEUED, ExecutionState.CLAIMED),
            (ExecutionState.CLAIMED, ExecutionState.RUNNING),
            (ExecutionState.RUNNING, ExecutionState.SUCCEEDED),
        )
        for before, after in legal_execution:
            with self.subTest(before=before, after=after):
                validate_execution_transition(before, after)
        with self.assertRaisesRegex(RuntimeProtocolError, "非法执行状态迁移"):
            validate_execution_transition(ExecutionState.SUCCEEDED, ExecutionState.RUNNING)
        with self.assertRaisesRegex(RuntimeProtocolError, "非法执行状态迁移"):
            validate_execution_transition(ExecutionState.RUNNING, ExecutionState.CLAIMED)

        for before, after in (
            (CleanupState.ALLOCATED, CleanupState.ACTIVE),
            (CleanupState.ACTIVE, CleanupState.DRAINING),
            (CleanupState.DRAINING, CleanupState.TERMINATING),
            (CleanupState.TERMINATING, CleanupState.TERMINATION_FAILED),
            (CleanupState.TERMINATION_FAILED, CleanupState.TERMINATING),
            (CleanupState.TERMINATING, CleanupState.REAPED),
        ):
            with self.subTest(before=before, after=after):
                validate_cleanup_transition(before, after)
        with self.assertRaisesRegex(RuntimeProtocolError, "非法清理状态迁移"):
            validate_cleanup_transition(CleanupState.REAPED, CleanupState.ACTIVE)

    def test_terminal_cleanup_states_require_terminal_execution(self):
        for cleanup in (
            CleanupState.DRAINING,
            CleanupState.TERMINATING,
            CleanupState.REAPED,
            CleanupState.TERMINATION_FAILED,
        ):
            with self.subTest(cleanup=cleanup):
                kwargs = {"cleanup_state": cleanup}
                if cleanup is CleanupState.TERMINATION_FAILED:
                    kwargs.update({
                        "cleanup_failure_ref": ScopedRef(
                            SCOPE, "core:cleanup_failure", "failure-1", 1
                        ),
                        "cleanup_failure_reason": "runtime:process_leak",
                    })
                with self.assertRaisesRegex(RuntimeProtocolError, "execution 已进入终态"):
                    make_invocation(**kwargs)

    def test_termination_failed_is_recoverable_but_not_closed(self):
        record = terminal_record(invocation_ref())
        failed_cleanup = make_invocation(
            execution_state=ExecutionState.SUCCEEDED,
            cleanup_state=CleanupState.TERMINATION_FAILED,
            terminal_record=record,
            cleanup_failure_ref=ScopedRef(
                SCOPE, "core:cleanup_failure", "failure-1", 1
            ),
            cleanup_failure_reason="runtime:process_leak",
        )
        self.assertFalse(failed_cleanup.closed)
        retry = replace(
            failed_cleanup,
            cleanup_state=CleanupState.TERMINATING,
            cleanup_failure_ref=None,
            cleanup_failure_reason="",
            version=2,
        )
        validate_invocation_transition(failed_cleanup, retry)
        reaped = replace(retry, cleanup_state=CleanupState.REAPED, version=3)
        validate_invocation_transition(retry, reaped)
        self.assertTrue(reaped.closed)

        with self.assertRaisesRegex(RuntimeProtocolError, "failure ref/reason"):
            make_invocation(
                execution_state=ExecutionState.FAILED,
                cleanup_state=CleanupState.TERMINATION_FAILED,
                terminal_record=terminal_record(
                    invocation_ref(), ExecutionState.FAILED
                ),
            )

    def test_closed_requires_no_active_execution_resources(self):
        record = terminal_record(invocation_ref())
        terminal = make_invocation(
            execution_state=ExecutionState.SUCCEEDED,
            cleanup_state=CleanupState.ACTIVE,
            terminal_record=record,
        )
        self.assertFalse(terminal.closed)
        reaped = replace(terminal, cleanup_state=CleanupState.REAPED)
        self.assertTrue(reaped.closed)
        with self.assertRaisesRegex(RuntimeProtocolError, "REAPED"):
            replace(
                reaped,
                active_resource_refs=(ScopedRef(
                    SCOPE, "core:execution_resource", "port-1", 1
                ),),
            )

    def test_success_cannot_be_recorded_at_or_after_deadline(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "早于 deadline"):
            make_invocation(
                execution_state=ExecutionState.SUCCEEDED,
                cleanup_state=CleanupState.REAPED,
                terminal_record=terminal_record(
                    invocation_ref(), finished_at=DEADLINE
                ),
            )

        with self.assertRaisesRegex(RuntimeProtocolError, "早于 deadline"):
            make_attempt(
                execution_state=ExecutionState.SUCCEEDED,
                cleanup_state=CleanupState.REAPED,
                fence_revoked=True,
                lease_active=False,
                terminal_record=terminal_record(
                    attempt_ref(), finished_at="2026-08-23T13:00:00+00:00"
                ),
            )

    def test_terminal_record_output_digest_includes_content_hash(self):
        output_ref = ScopedRef(SCOPE, "core:artifact", "output-1", 1)
        output = InvocationInputRef(output_ref, "e" * 64)
        record = terminal_record(invocation_ref(), outputs=(output,))
        drifted = InvocationInputRef(output_ref, "f" * 64)
        with self.assertRaisesRegex(RuntimeProtocolError, "output_digest"):
            replace(record, output_refs=(drifted,))

    def test_parent_child_uses_scoped_version_snapshots(self):
        parent = make_invocation(version=3)
        child = make_invocation(
            invocation_id="inv-child",
            parent_invocation_ref=invocation_ref(version=2),
            route_ref=ScopedRef(SCOPE, "core:route_edge", "route-1", 1),
        )
        validate_parent_child(parent, child)
        wrong_thread = replace(
            child,
            thread_ref=ScopedRef(SCOPE, "core:thread", "thread-other", 1),
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "同一 Thread"):
            validate_parent_child(parent, wrong_thread)

    def test_attempt_binding_accepts_historical_invocation_version(self):
        invocation = make_invocation(version=4)
        attempt = make_attempt(invocation_ref=invocation_ref(version=2))
        validate_attempt_binding(invocation, attempt)
        future = make_attempt(invocation_ref=invocation_ref(version=5))
        with self.assertRaisesRegex(RuntimeProtocolError, "未来 Invocation"):
            validate_attempt_binding(invocation, future)

    def test_aggregate_transition_requires_version_and_monotonic_fence(self):
        created = make_invocation()
        queued = replace(
            created, execution_state=ExecutionState.QUEUED, version=2
        )
        validate_invocation_transition(created, queued)
        claimed = replace(
            queued,
            execution_state=ExecutionState.CLAIMED,
            cleanup_state=CleanupState.ACTIVE,
            active_attempt_ref=attempt_ref(version=2),
            fence_generation=1,
            fence_revoked=False,
            version=3,
        )
        validate_invocation_transition(queued, claimed)
        with self.assertRaisesRegex(RuntimeProtocolError, "version"):
            validate_invocation_transition(created, replace(queued, version=3))
        with self.assertRaisesRegex(RuntimeProtocolError, "不得倒退"):
            failed_record = terminal_record(
                invocation_ref(version=3), ExecutionState.FAILED
            )
            validate_invocation_transition(
                claimed,
                replace(
                    claimed,
                    execution_state=ExecutionState.FAILED,
                    active_attempt_ref=None,
                    fence_generation=0,
                    fence_revoked=True,
                    terminal_record=failed_record,
                    version=4,
                ),
            )

    def test_active_attempt_snapshot_version_can_advance_without_new_fence(self):
        invocation, attempt = running_pair()
        attempt_v2 = replace(attempt, version=2)
        validate_attempt_transition(attempt, attempt_v2)
        invocation_v2 = replace(
            invocation,
            active_attempt_ref=attempt_ref(version=2),
            version=2,
        )
        validate_invocation_transition(invocation, invocation_v2)
        decision = evaluate_fenced_mutation(
            invocation_v2,
            attempt_v2,
            make_mutation(),
            observed_at=OBSERVED,
        )
        self.assertEqual(decision.code, FencedMutationDecisionCode.ACCEPT)

        with self.assertRaisesRegex(RuntimeProtocolError, "引用版本不得倒退"):
            validate_invocation_transition(
                invocation_v2,
                replace(
                    invocation_v2,
                    active_attempt_ref=attempt_ref(version=1),
                    version=3,
                ),
            )

    def test_invocation_transition_freezes_terminal_facts_and_drains_only(self):
        invocation, _ = running_pair()
        intent = TerminationIntent(
            "cancel-1",
            SCOPE,
            invocation_ref(),
            "runtime:user_cancelled",
            "2026-08-23T11:15:00+00:00",
            ScopedRef(SCOPE, "core:user", "user-1", 1),
        )
        cancelling = replace(invocation, termination_intent=intent)
        with self.assertRaisesRegex(RuntimeProtocolError, "不得新增"):
            validate_invocation_transition(
                invocation,
                replace(
                    invocation,
                    termination_intent=intent,
                    active_grant_refs=(ScopedRef(
                        SCOPE, "core:capability_grant", "late-grant", 1
                    ),),
                    version=2,
                ),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "TerminationIntent"):
            validate_invocation_transition(
                cancelling,
                replace(
                    cancelling,
                    execution_state=ExecutionState.FAILED,
                    active_attempt_ref=None,
                    active_lease_refs=(),
                    fence_revoked=True,
                    termination_intent=None,
                    terminal_record=terminal_record(
                        invocation_ref(), ExecutionState.FAILED
                    ),
                    version=2,
                ),
            )

        record = terminal_record(invocation_ref())
        terminal = make_invocation(
            execution_state=ExecutionState.SUCCEEDED,
            cleanup_state=CleanupState.ACTIVE,
            terminal_record=record,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "TerminalRecord"):
            validate_invocation_transition(
                terminal,
                replace(
                    terminal,
                    cleanup_state=CleanupState.DRAINING,
                    terminal_record=replace(record, record_id="terminal-rewritten"),
                    version=2,
                ),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "不得新增"):
            validate_invocation_transition(
                terminal,
                replace(
                    terminal,
                    cleanup_state=CleanupState.DRAINING,
                    active_resource_refs=(ScopedRef(
                        SCOPE, "core:execution_resource", "late-port", 1
                    ),),
                    version=2,
                ),
            )

    def test_attempt_aggregate_transition_freezes_signed_fence(self):
        created = make_attempt(
            execution_state=ExecutionState.CREATED,
            cleanup_state=CleanupState.ALLOCATED,
            worker_id="",
            principal_id="",
            selection_ref=None,
            fence=None,
            fence_revoked=True,
            lease=None,
            lease_active=False,
        )
        queued = replace(
            created, execution_state=ExecutionState.QUEUED, version=2
        )
        validate_attempt_transition(created, queued)
        forged_failure = replace(
            created,
            execution_state=ExecutionState.FAILED,
            worker_id="forged-worker",
            principal_id="forged-principal",
            selection_ref=ScopedRef(
                SCOPE, "core:worker_selection", "forged-selection", 1
            ),
            terminal_record=terminal_record(
                attempt_ref(), ExecutionState.FAILED
            ),
            version=2,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "进入 claimed"):
            validate_attempt_transition(created, forged_failure)
        fence = make_fence(att_ref=attempt_ref(version=3))
        claimed = replace(
            queued,
            execution_state=ExecutionState.CLAIMED,
            cleanup_state=CleanupState.ACTIVE,
            worker_id="worker-1",
            principal_id="principal-1",
            fence=fence,
            fence_revoked=False,
            lease=make_lease(fence),
            lease_active=True,
            version=3,
        )
        validate_attempt_transition(queued, claimed)
        running = replace(claimed, execution_state=ExecutionState.RUNNING, version=4)
        validate_attempt_transition(claimed, running)
        with self.assertRaisesRegex(RuntimeProtocolError, "fence 不得替换"):
            replacement_fence = make_fence(2, att_ref=attempt_ref(version=3))
            validate_attempt_transition(
                running,
                replace(
                    running,
                    fence=replacement_fence,
                    lease=make_lease(replacement_fence),
                    version=5,
                ),
            )

    def test_preclaim_attempt_cannot_own_execution_identity_or_resources(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "created/queued"):
            make_attempt(
                execution_state=ExecutionState.CREATED,
                cleanup_state=CleanupState.ALLOCATED,
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "owner_id"):
            make_attempt(
                lease=make_lease(owner_id="worker-other"),
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "cleanup_state"):
            make_attempt(cleanup_state=CleanupState.ALLOCATED)

    def test_attempt_transition_freezes_worker_lease_and_cleanup_resources(self):
        _, attempt = running_pair()
        with self.assertRaisesRegex(RuntimeProtocolError, "worker_id"):
            validate_attempt_transition(
                attempt,
                replace(attempt, worker_id="worker-other", version=2),
            )

        shorter_lease = replace(
            attempt.lease,
            expires_at="2026-08-23T11:25:00+00:00",
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "expires_at"):
            validate_attempt_transition(
                attempt,
                replace(attempt, lease=shorter_lease, version=2),
            )

        renewed_lease = replace(
            attempt.lease,
            lease_ref=ScopedRef(SCOPE, "core:lease", "lease-1", 2),
            expires_at="2026-08-23T11:40:00+00:00",
            last_heartbeat_at="2026-08-23T11:20:00+00:00",
        )
        validate_attempt_transition(
            attempt,
            replace(attempt, lease=renewed_lease, version=2),
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "version"):
            validate_attempt_transition(
                attempt,
                replace(
                    attempt,
                    lease=replace(
                        renewed_lease,
                        lease_ref=ScopedRef(
                            SCOPE, "core:lease", "lease-1", 99
                        ),
                    ),
                    version=2,
                ),
            )

        intent = TerminationIntent(
            "cancel-attempt",
            SCOPE,
            attempt_ref(),
            "runtime:user_cancelled",
            "2026-08-23T11:15:00+00:00",
            ScopedRef(SCOPE, "core:runtime_principal", "runtime", 1),
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "不得新增"):
            validate_attempt_transition(
                attempt,
                replace(
                    attempt,
                    termination_intent=intent,
                    active_resource_refs=(ScopedRef(
                        SCOPE, "core:execution_resource", "late-port", 1
                    ),),
                    version=2,
                ),
            )

        record = terminal_record(attempt_ref(), ExecutionState.FAILED)
        terminal = replace(
            attempt,
            execution_state=ExecutionState.FAILED,
            fence_revoked=True,
            lease_active=False,
            terminal_record=record,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "不得新增"):
            validate_attempt_transition(
                terminal,
                replace(
                    terminal,
                    cleanup_state=CleanupState.DRAINING,
                    active_resource_refs=(ScopedRef(
                        SCOPE, "core:execution_resource", "late-process", 1
                    ),),
                    version=2,
                ),
            )

    def test_protocols_round_trip_strict_json(self):
        invocation, attempt = running_pair()
        mutation = make_mutation()
        for value, factory in (
            (invocation, Invocation),
            (attempt, Attempt),
            (mutation, FencedMutation),
        ):
            with self.subTest(type=type(value).__name__):
                raw = json.loads(json.dumps(dict(value.to_dict())))
                self.assertEqual(factory.from_dict(raw), value)

        data = dict(invocation.to_dict())
        data["unknown"] = True
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            Invocation.from_dict(data)
        data = dict(invocation.to_dict())
        data["schema_version"] = "9.9"
        with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
            Invocation.from_dict(data)

    def test_valid_fenced_mutation_is_admitted_without_changing_state(self):
        invocation, attempt = running_pair()
        before_invocation = dict(invocation.to_dict())
        before_attempt = dict(attempt.to_dict())
        decision = evaluate_fenced_mutation(
            invocation, attempt, make_mutation(), observed_at=OBSERVED
        )
        self.assertEqual(decision.code, FencedMutationDecisionCode.ACCEPT)
        self.assertTrue(decision.may_mutate)
        self.assertEqual(dict(invocation.to_dict()), before_invocation)
        self.assertEqual(dict(attempt.to_dict()), before_attempt)

    def test_stale_and_future_fences_are_deterministically_rejected(self):
        invocation, old_attempt = running_pair(generation=2)
        stale = evaluate_fenced_mutation(
            invocation, old_attempt, make_mutation(fence=make_fence(1)),
            observed_at=OBSERVED,
        )
        self.assertEqual(stale.code, FencedMutationDecisionCode.STALE_FENCE)
        self.assertTrue(stale.audit_only)

        invocation, attempt = running_pair(generation=1)
        future = evaluate_fenced_mutation(
            invocation, attempt, make_mutation(fence=make_fence(2)),
            observed_at=OBSERVED,
        )
        self.assertEqual(future.code, FencedMutationDecisionCode.FUTURE_FENCE)

    def test_cancel_terminal_expiry_and_wrong_bindings_are_rejected(self):
        invocation, attempt = running_pair()
        intent = TerminationIntent(
            "cancel-1", SCOPE, invocation_ref(), "runtime:user_cancelled",
            "2026-08-23T11:15:00+00:00",
            ScopedRef(SCOPE, "core:user", "user-1", 1),
        )
        cancelling = replace(invocation, termination_intent=intent)
        decision = evaluate_fenced_mutation(
            cancelling, attempt, make_mutation(), observed_at=OBSERVED
        )
        self.assertEqual(
            decision.code, FencedMutationDecisionCode.TERMINATION_REQUESTED
        )

        expired = evaluate_fenced_mutation(
            invocation, attempt, make_mutation(), observed_at=LEASE_EXPIRES
        )
        self.assertEqual(expired.code, FencedMutationDecisionCode.LEASE_EXPIRED)
        deadline = evaluate_fenced_mutation(
            invocation, attempt, make_mutation(), observed_at=DEADLINE
        )
        self.assertEqual(deadline.code, FencedMutationDecisionCode.DEADLINE_EXCEEDED)

        wrong_input = evaluate_fenced_mutation(
            invocation, attempt, make_mutation(input_digest="9" * 64),
            observed_at=OBSERVED,
        )
        self.assertEqual(
            wrong_input.code, FencedMutationDecisionCode.INPUT_DIGEST_MISMATCH
        )
        wrong_policy = ScopedRef(
            SCOPE, "core:policy_snapshot", "policy-other", 1
        )
        wrong_policy_result = evaluate_fenced_mutation(
            invocation, attempt,
            make_mutation(policy_snapshot_ref=wrong_policy),
            observed_at=OBSERVED,
        )
        self.assertEqual(
            wrong_policy_result.code,
            FencedMutationDecisionCode.POLICY_SNAPSHOT_MISMATCH,
        )

        wrong_session = make_attempt(
            agent_session_ref=ScopedRef(
                SCOPE, "core:agent_session", "session-other", 1
            )
        )
        wrong_session_result = evaluate_fenced_mutation(
            invocation,
            wrong_session,
            make_mutation(),
            observed_at=OBSERVED,
        )
        self.assertEqual(
            wrong_session_result.code,
            FencedMutationDecisionCode.AGENT_SESSION_MISMATCH,
        )

        future_submission = evaluate_fenced_mutation(
            invocation,
            attempt,
            make_mutation(submitted_at="2026-08-23T13:00:00+00:00"),
            observed_at=OBSERVED,
        )
        self.assertEqual(
            future_submission.code,
            FencedMutationDecisionCode.SUBMISSION_TIME_INVALID,
        )

    def test_wrong_scope_thread_and_attempt_are_rejected(self):
        invocation, attempt = running_pair()
        wrong_thread = make_mutation(
            thread_ref=ScopedRef(SCOPE, "core:thread", "other-thread", 1)
        )
        self.assertEqual(
            evaluate_fenced_mutation(
                invocation, attempt, wrong_thread, observed_at=OBSERVED
            ).code,
            FencedMutationDecisionCode.THREAD_MISMATCH,
        )
        other_fence = make_fence(att_ref=attempt_ref("attempt-other"))
        self.assertEqual(
            evaluate_fenced_mutation(
                invocation, attempt, make_mutation(fence=other_fence),
                observed_at=OBSERVED,
            ).code,
            FencedMutationDecisionCode.ATTEMPT_MISMATCH,
        )

        scope_b = "scope-b"
        thread_b = ScopedRef(scope_b, "core:thread", "thread-1", 1)
        invocation_b = make_invocation(
            scope_id=scope_b,
            thread_ref=thread_b,
            turn_ref=ScopedRef(scope_b, "core:turn", "turn-1", 1),
            agent_instance_ref=ScopedRef(
                scope_b, "core:agent_instance", "agent-1", 1
            ),
            agent_session_ref=ScopedRef(
                scope_b, "core:agent_session", "session-1", 1
            ),
            input_refs=(InvocationInputRef(
                ScopedRef(scope_b, "core:artifact", "input-1", 1), "a" * 64
            ),),
            input_digest=digest_invocation_inputs((InvocationInputRef(
                ScopedRef(scope_b, "core:artifact", "input-1", 1), "a" * 64
            ),)),
            policy_snapshot_ref=ScopedRef(
                scope_b, "core:policy_snapshot", "policy-1", 1
            ),
            budget_reservation_ref=ScopedRef(
                scope_b, "core:budget_reservation", "budget-1", 1
            ),
        )
        self.assertEqual(
            evaluate_fenced_mutation(
                invocation_b, attempt, make_mutation(), observed_at=OBSERVED
            ).code,
            FencedMutationDecisionCode.SCOPE_MISMATCH,
        )

    def test_duplicate_is_noop_and_conflicting_payload_is_rejected(self):
        invocation, attempt = running_pair()
        mutation = make_mutation()
        duplicate = evaluate_fenced_mutation(
            invocation, attempt, mutation, observed_at=OBSERVED,
            existing_mutation_digest=mutation.idempotency_digest,
        )
        self.assertEqual(
            duplicate.code, FencedMutationDecisionCode.DUPLICATE_NOOP
        )
        self.assertFalse(duplicate.may_mutate)
        conflict = evaluate_fenced_mutation(
            invocation, attempt, mutation, observed_at=OBSERVED,
            existing_mutation_digest="c" * 64,
        )
        self.assertEqual(
            conflict.code, FencedMutationDecisionCode.IDEMPOTENCY_CONFLICT
        )
        same_payload_different_kind = make_mutation(
            mutation_kind="core:tool_side_effect"
        )
        semantic_conflict = evaluate_fenced_mutation(
            invocation,
            attempt,
            same_payload_different_kind,
            observed_at=OBSERVED,
            existing_mutation_digest=mutation.idempotency_digest,
        )
        self.assertEqual(
            semantic_conflict.code,
            FencedMutationDecisionCode.IDEMPOTENCY_CONFLICT,
        )
        restored = FencedMutationDecision.from_dict(
            json.loads(json.dumps(dict(conflict.to_dict())))
        )
        self.assertEqual(restored, conflict)


if __name__ == "__main__":
    unittest.main()
