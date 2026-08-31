from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from coding_workflow.change_approval import (
    ChangeApplicationReceipt,
    ChangeApprovalRejected,
    ChangeApprovalRuntime,
    ChangeApprovalStatus,
    ChangeSet,
    ChangeTargetKind,
    UserChangeApprovalConfirmation,
)
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteChangeApprovalStore,
    SQLiteRuntimeDatabase,
)


class ChangeApprovalRuntimeTests(unittest.TestCase):
    @staticmethod
    def _database(path: Path) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/change-approval-test-v1",
                destination="core:runtime_events",
                expected_sink_id="core:test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000, 5_000, 30_000),
            ),
        )

    @staticmethod
    def _change_set() -> ChangeSet:
        return ChangeSet(
            proposal_id="proposal-runtime-policy-1",
            scope_id="scope-a",
            target_kind=ChangeTargetKind.RUNTIME_POLICY,
            target_ref="runtime:communication_policy",
            reason="避免Agent形成无限自动对话。",
            exact_change={"max_auto_hops": 1},
            affected_refs=("runtime:communication_policy",),
            requested_capabilities=("runtime:policy_write",),
            dependency_digests=("b" * 64,),
            evidence_refs=("artifact:loop-regression",),
            risk="可能过早终止需要多轮讨论的任务。",
            verification="运行乒乓与正常一跳对照测试。",
            base_state_digest="a" * 64,
        )

    def test_exact_user_approval_allows_one_apply_after_restart(self) -> None:
        change_set = self._change_set()
        effects: list[ChangeSet] = []

        def apply_effect(value: ChangeSet) -> ChangeApplicationReceipt:
            effects.append(value)
            return ChangeApplicationReceipt(result_digest="d" * 64)

        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            runtime = ChangeApprovalRuntime(
                store=SQLiteChangeApprovalStore(database)
            )
            proposal = runtime.propose(change_set)

            self.assertIs(proposal.status, ChangeApprovalStatus.PROPOSED)
            self.assertEqual("PENDING_USER_REVIEW", proposal.review_status)
            self.assertEqual(change_set.change_digest, proposal.change_digest)
            with self.assertRaises(ChangeApprovalRejected) as unapproved:
                runtime.apply(
                    change_set,
                    current_state_digest="a" * 64,
                    effect=apply_effect,
                )
            self.assertEqual("user_approval_required", unapproved.exception.code)
            self.assertEqual([], effects)

            approval = runtime.approve(UserChangeApprovalConfirmation(
                proposal_id=change_set.proposal_id,
                change_digest=change_set.change_digest,
                base_state_digest=change_set.base_state_digest,
                user_id="local-user",
            ))
            self.assertIs(
                approval.status,
                ChangeApprovalStatus.USER_APPROVED,
            )

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_runtime = ChangeApprovalRuntime(
                store=SQLiteChangeApprovalStore(reopened)
            )
            application = reopened_runtime.apply(
                change_set,
                current_state_digest="a" * 64,
                effect=apply_effect,
            )
            self.assertIs(
                application.status,
                ChangeApprovalStatus.APPLIED,
            )
            self.assertEqual("d" * 64, application.result_digest)
            self.assertEqual([change_set], effects)

            with self.assertRaises(ChangeApprovalRejected) as replayed:
                reopened_runtime.apply(
                    change_set,
                    current_state_digest="a" * 64,
                    effect=apply_effect,
                )
            self.assertEqual("change_already_applied", replayed.exception.code)
            self.assertEqual([change_set], effects)

    def test_any_change_or_state_drift_invalidates_previous_approval(
        self,
    ) -> None:
        original = self._change_set()
        with tempfile.TemporaryDirectory() as temporary:
            database = self._database(Path(temporary) / "runtime.sqlite3")
            database.initialize()
            runtime = ChangeApprovalRuntime(
                store=SQLiteChangeApprovalStore(database)
            )
            runtime.propose(original)
            runtime.approve(UserChangeApprovalConfirmation(
                proposal_id=original.proposal_id,
                change_digest=original.change_digest,
                base_state_digest=original.base_state_digest,
                user_id="local-user",
            ))
            effects: list[ChangeSet] = []

            changed_values = (
                replace(original, exact_change={"max_auto_hops": 2}),
                replace(
                    original,
                    target_ref="runtime:termination_policy",
                ),
                replace(
                    original,
                    requested_capabilities=("runtime:route_write",),
                ),
                replace(original, dependency_digests=("c" * 64,)),
                replace(original, evidence_refs=("artifact:new-evidence",)),
                replace(original, risk="新的风险说明。"),
                replace(original, verification="新的验证计划。"),
                replace(original, base_state_digest="e" * 64),
            )
            for changed in changed_values:
                with self.subTest(change_digest=changed.change_digest):
                    self.assertNotEqual(
                        original.change_digest,
                        changed.change_digest,
                    )
                    with self.assertRaises(ChangeApprovalRejected) as rejected:
                        runtime.apply(
                            changed,
                            current_state_digest=changed.base_state_digest,
                            effect=lambda value: (
                                effects.append(value)
                                or ChangeApplicationReceipt(
                                    result_digest="d" * 64
                                )
                            ),
                        )
                    self.assertEqual(
                        "change_digest_mismatch", rejected.exception.code
                    )

            with self.assertRaises(ChangeApprovalRejected) as stale_state:
                runtime.apply(
                    original,
                    current_state_digest="f" * 64,
                    effect=lambda value: (
                        effects.append(value)
                        or ChangeApplicationReceipt(result_digest="d" * 64)
                    ),
                )
            self.assertEqual(
                "state_digest_mismatch", stale_state.exception.code
            )
            self.assertEqual([], effects)

    def test_failed_effect_leaves_one_claim_and_cannot_auto_retry(self) -> None:
        change_set = self._change_set()
        effects = 0

        def failing_effect(value: ChangeSet) -> ChangeApplicationReceipt:
            nonlocal effects
            effects += 1
            raise RuntimeError(f"failed for {value.proposal_id}")

        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            runtime = ChangeApprovalRuntime(
                store=SQLiteChangeApprovalStore(database)
            )
            runtime.propose(change_set)
            runtime.approve(UserChangeApprovalConfirmation(
                proposal_id=change_set.proposal_id,
                change_digest=change_set.change_digest,
                base_state_digest=change_set.base_state_digest,
                user_id="local-user",
            ))
            with self.assertRaises(ChangeApprovalRejected) as failed:
                runtime.apply(
                    change_set,
                    current_state_digest=change_set.base_state_digest,
                    effect=failing_effect,
                )
            self.assertEqual("change_application_failed", failed.exception.code)

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_runtime = ChangeApprovalRuntime(
                store=SQLiteChangeApprovalStore(reopened)
            )
            with self.assertRaises(ChangeApprovalRejected) as retried:
                reopened_runtime.apply(
                    change_set,
                    current_state_digest=change_set.base_state_digest,
                    effect=failing_effect,
                )
            self.assertEqual(
                "change_application_unresolved", retried.exception.code
            )
            self.assertEqual(1, effects)


if __name__ == "__main__":
    unittest.main()
