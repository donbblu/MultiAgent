import json
import unittest
from dataclasses import FrozenInstanceError

from coding_workflow.runtime_domain.acceptance import (
    AcceptanceEvidence,
    AcceptancePolicy,
    AcceptanceRecord,
    AcceptanceSubjectType,
    EvidenceRequirement,
    Outcome,
    OutcomeStatus,
)
from coding_workflow.runtime_domain.common import (
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
)


NOW = "2026-08-23T08:00:00+00:00"
RECENT = "2026-08-23T07:59:30+00:00"
OLD = "2026-08-23T06:00:00+00:00"
DIGEST = "a" * 64


def policy(*, independent: bool = True) -> AcceptancePolicy:
    return AcceptancePolicy(
        "scope-a",
        "turn-policy",
        AcceptanceSubjectType.TURN,
        (EvidenceRequirement("core:delivery_ack", max_age_seconds=60),),
        independent,
        (
            OutcomeStatus.UNKNOWN,
            OutcomeStatus.NEEDS_INPUT,
            OutcomeStatus.ACCEPTED,
            OutcomeStatus.REJECTED,
        ),
        created_at=NOW,
    )


def evidence(
    subject: ScopedRef,
    *,
    evaluator: str = "agent-reviewer",
    observed_at: str = RECENT,
    scope_id: str = "scope-a",
    version: int = 1,
) -> AcceptanceEvidence:
    return AcceptanceEvidence(
        ScopedRef(scope_id, "core:evidence", "delivery-ack", version),
        "core:delivery_ack",
        subject,
        observed_at,
        DIGEST,
        evaluator,
    )


class RuntimeAcceptanceProtocolTests(unittest.TestCase):
    def test_scoped_ref_is_immutable_and_rejects_cross_scope(self) -> None:
        reference = ScopedRef("scope-a", "core:artifact", "artifact-1", 2)
        with self.assertRaises(FrozenInstanceError):
            reference.scope_id = "scope-b"
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            reference.assert_scope("scope-b")
        restored = ScopedRef.from_dict(
            json.loads(json.dumps(dict(reference.to_dict())))
        )
        self.assertEqual(restored, reference)

    def test_policy_hash_and_json_round_trip_are_deterministic(self) -> None:
        original = policy()
        encoded = json.loads(json.dumps(dict(original.to_dict())))
        restored = AcceptancePolicy.from_dict(encoded)
        self.assertEqual(restored, original)
        self.assertEqual(restored.policy_hash, original.policy_hash)

        encoded["allowed_outcomes"] = ["accepted"]
        with self.assertRaisesRegex(RuntimeProtocolError, "hash"):
            AcceptancePolicy.from_dict(encoded)

    def test_policy_protocol_rejects_unknown_fields_and_versions(self) -> None:
        encoded = dict(policy().to_dict())
        encoded["agent_can_override"] = True
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            AcceptancePolicy.from_dict(encoded)

        encoded = dict(policy().to_dict())
        encoded["schema_version"] = "2.0"
        with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
            AcceptancePolicy.from_dict(encoded)

        with self.assertRaisesRegex(RuntimeProtocolError, "EvidenceRequirement"):
            AcceptancePolicy(
                "scope-a",
                "unsafe-policy",
                AcceptanceSubjectType.TURN,
                (),
                False,
                (OutcomeStatus.ACCEPTED,),
                created_at=NOW,
            )

    def test_accepted_requires_matching_fresh_evidence(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 3)
        with self.assertRaisesRegex(RuntimeProtocolError, "Evidence 不足"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-1",
            )

        with self.assertRaisesRegex(RuntimeProtocolError, "Evidence 不足"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(subject, observed_at=OLD),),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-1",
            )

        wrong_version = ScopedRef("scope-a", "core:turn", "turn-1", 2)
        with self.assertRaisesRegex(RuntimeProtocolError, "Evidence 不足"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(wrong_version),),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-1",
            )

        identity_only_policy = AcceptancePolicy(
            "scope-a",
            "identity-policy",
            AcceptanceSubjectType.TURN,
            (EvidenceRequirement(
                "core:delivery_ack",
                bind_subject_version=False,
            ),),
            False,
            (OutcomeStatus.ACCEPTED,),
            created_at=NOW,
        )
        other_subject = ScopedRef("scope-a", "core:turn", "turn-2", 3)
        with self.assertRaisesRegex(RuntimeProtocolError, "Evidence 不足"):
            AcceptanceRecord.issue(
                identity_only_policy,
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(other_subject),),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-identity",
            )

    def test_multiple_versions_of_one_evidence_entity_count_only_once(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 3)
        two_evidence_policy = AcceptancePolicy(
            "scope-a",
            "two-evidence-policy",
            AcceptanceSubjectType.TURN,
            (EvidenceRequirement("core:delivery_ack", min_count=2),),
            False,
            (OutcomeStatus.ACCEPTED,),
            created_at=NOW,
        )

        with self.assertRaisesRegex(RuntimeProtocolError, "同一 evidence 实体"):
            AcceptanceRecord.issue(
                two_evidence_policy,
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(subject, version=1), evidence(subject, version=2)),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-reused-evidence",
            )

    def test_decision_time_cannot_predate_policy(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 3)
        with self.assertRaisesRegex(RuntimeProtocolError, "Policy created_at"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(subject),),
                producer_principal_id="agent-author",
                evaluated_at="2026-08-23T07:59:45+00:00",
                record_id="record-before-policy",
            )

    def test_independent_evaluator_is_enforced_by_policy(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 3)
        with self.assertRaisesRegex(RuntimeProtocolError, "独立 Evaluator"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(subject, evaluator="agent-author"),),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-1",
            )

        record = AcceptanceRecord.issue(
            policy(),
            subject,
            OutcomeStatus.ACCEPTED,
            (evidence(subject),),
            producer_principal_id="agent-author",
            evaluated_at=NOW,
            record_id="record-1",
        )
        self.assertEqual(record.issued_by, "runtime")
        self.assertEqual(record.evaluator_principal_ids, ("agent-reviewer",))
        record.validate_against(policy())

    def test_cross_scope_evidence_and_thread_subject_fail_closed(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 1)
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            AcceptanceRecord.issue(
                policy(),
                subject,
                OutcomeStatus.ACCEPTED,
                (evidence(subject, scope_id="scope-b"),),
                producer_principal_id="agent-author",
                evaluated_at=NOW,
                record_id="record-1",
            )

        with self.assertRaisesRegex(RuntimeProtocolError, "Thread"):
            Outcome(
                "scope-a",
                "outcome-1",
                ScopedRef("scope-a", "core:thread", "thread-1", 1),
                OutcomeStatus.UNKNOWN,
                updated_at=NOW,
            )

    def test_record_round_trip_rejects_forged_issuer(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 1)
        record = AcceptanceRecord.issue(
            policy(),
            subject,
            OutcomeStatus.ACCEPTED,
            (evidence(subject),),
            producer_principal_id="agent-author",
            evaluated_at=NOW,
            record_id="record-1",
        )
        encoded = json.loads(json.dumps(dict(record.to_dict())))
        self.assertEqual(AcceptanceRecord.from_dict(encoded), record)
        encoded["issued_by"] = "agent"
        with self.assertRaisesRegex(RuntimeProtocolError, "Runtime"):
            AcceptanceRecord.from_dict(encoded)

    def test_unknown_is_normal_and_continue_is_not_an_outcome(self) -> None:
        subject = ScopedRef("scope-a", "core:turn", "turn-1", 1)
        current = Outcome(
            "scope-a",
            "outcome-1",
            subject,
            OutcomeStatus.UNKNOWN,
            updated_at=NOW,
        )
        restored = Outcome.from_dict(
            json.loads(json.dumps(dict(current.to_dict())))
        )
        self.assertEqual(restored, current)
        with self.assertRaisesRegex(RuntimeProtocolError, "无效"):
            Outcome(
                "scope-a",
                "outcome-2",
                subject,
                "continue",
                updated_at=NOW,
            )
        for lifecycle_state in ("blocked", "cancelled"):
            with self.subTest(lifecycle_state=lifecycle_state):
                with self.assertRaisesRegex(RuntimeProtocolError, "无效"):
                    Outcome(
                        "scope-a",
                        f"outcome-{lifecycle_state}",
                        subject,
                        lifecycle_state,
                        updated_at=NOW,
                    )

    def test_non_unknown_outcome_requires_runtime_record(self) -> None:
        subject = ScopedRef("scope-a", "core:task", "task-1", 1)
        for status in (
            OutcomeStatus.NEEDS_INPUT,
            OutcomeStatus.ACCEPTED,
            OutcomeStatus.REJECTED,
        ):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeProtocolError, "AcceptanceRecord"):
                    Outcome(
                        "scope-a",
                        f"outcome-{status.value}",
                        subject,
                        status,
                        updated_at=NOW,
                    )


if __name__ == "__main__":
    unittest.main()
