import json
import unittest
from types import MappingProxyType

from coding_workflow.artifacts import (
    Artifact,
    ArtifactValidation,
    ArtifactValidationState,
)
from coding_workflow.coding_runtime_compat import (
    CodingTaskSnapshot,
    CodingWorkerBinding,
    agent_role_to_role_spec,
    artifact_to_invocation_input,
    artifact_to_scoped_ref,
    role_spec_to_agent_role,
    validate_artifact_input_binding,
    verification_record_to_acceptance_evidence,
    worker_descriptor_to_binding,
)
from coding_workflow.harness.registry import WorkerDescriptor
from coding_workflow.harness.task_graph import TaskSpec
from coding_workflow.models import TaskState
from coding_workflow.roles import IMPLEMENTER, PLANNER
from coding_workflow.runtime_domain.acceptance import AcceptanceEvidence
from coding_workflow.runtime_domain.common import (
    RuntimeProtocolError,
    ScopeBoundaryError,
    ScopedRef,
)
from coding_workflow.runtime_domain.interaction import AgentProfile
from coding_workflow.runtime_domain.invocation import InvocationInputRef
from coding_workflow.truth import VerificationOutcome, VerificationRecord


NOW = "2026-08-23T08:00:00+00:00"
WORKSPACE_HASH = "b" * 64


def artifact(content=None, *, artifact_id="artifact-1") -> Artifact:
    return Artifact(
        artifact_id,
        "patch",
        "task-1",
        "result",
        content if content is not None else {"secret-marker": "payload-only"},
        MappingProxyType({"source": "worker"}),
        NOW,
    )


def full_task() -> TaskSpec:
    return TaskSpec(
        "implement",
        "实现功能",
        "修改实现并保持行为",
        "implementer",
        dependencies=("plan",),
        acceptance_criteria=("固定测试通过", "不得扩大写入范围"),
        read_scopes=("src/**", "tests/**"),
        write_scopes=("src/**",),
        input_artifacts=("source-snapshot", "verified-contract"),
        output_artifacts=("patch",),
        context_queries=("相关符号",),
        risk_level="medium",
        timeout_seconds=321,
        retry_limit=2,
        priority=7,
        required_verified_inputs=("verified-contract",),
        required_capabilities=("code_generation",),
        input_protocols=("core:source_snapshot",),
        output_protocols=("core:patch",),
        required_policy_tags=("sandboxed",),
        independent_from_tasks=("plan",),
    )


class RuntimeCodingCompatibilityTests(unittest.TestCase):
    def test_role_spec_maps_to_namespaced_generic_role_losslessly(self) -> None:
        role = role_spec_to_agent_role(
            IMPLEMENTER,
            scope_id="scope-a",
            created_at=NOW,
        )
        self.assertEqual(role.role_id, "coding:implementer")
        self.assertEqual(
            set(role.capability_ceiling),
            {
                "coding:read_project",
                "coding:propose_changes",
                "coding:write_project",
            },
        )
        self.assertEqual(agent_role_to_role_spec(role), IMPLEMENTER)
        self.assertNotIn("provider", role.to_dict())
        self.assertNotIn("model", role.to_dict())

    def test_worker_descriptor_becomes_binding_not_agent_instance(self) -> None:
        role = role_spec_to_agent_role(
            IMPLEMENTER, scope_id="scope-a", created_at=NOW
        )
        profile = AgentProfile(
            "coding-implementer-profile",
            "scope-a",
            role.reference,
            created_at=NOW,
        )
        descriptor = WorkerDescriptor(
            "worker-1",
            "implementer",
            frozenset({"code_generation"}),
            frozenset({"core:source_snapshot"}),
            frozenset({"core:patch"}),
            frozenset({"sandboxed"}),
            principal_id="principal-worker-1",
            priority=4,
        )
        binding = worker_descriptor_to_binding(
            descriptor, scope_id="scope-a", profile=profile
        )
        payload = json.loads(json.dumps(dict(binding.to_dict())))
        self.assertEqual(CodingWorkerBinding.from_dict(payload), binding)
        self.assertEqual(binding.role_ref, role.reference)
        self.assertEqual(binding.profile_ref, profile.reference)
        self.assertEqual(binding.capabilities, ("coding:code_generation",))
        self.assertEqual(binding.policy_tags, ("coding:sandboxed",))
        self.assertNotIn("agent_instance_id", payload)
        self.assertNotIn("agent_session_id", payload)

        payload["accepted"] = True
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            CodingWorkerBinding.from_dict(payload)

        payload = json.loads(json.dumps(dict(binding.to_dict())))
        payload["schema_version"] = "2.0"
        with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
            CodingWorkerBinding.from_dict(payload)

    def test_worker_binding_rejects_cross_scope_and_wrong_role(self) -> None:
        implementer = role_spec_to_agent_role(
            IMPLEMENTER, scope_id="scope-a", created_at=NOW
        )
        foreign_profile = AgentProfile(
            "foreign-profile",
            "scope-b",
            ScopedRef("scope-b", "core:agent_role", "coding:implementer", 1),
            created_at=NOW,
        )
        descriptor = WorkerDescriptor("worker-1", "implementer")
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            worker_descriptor_to_binding(
                descriptor, scope_id="scope-a", profile=foreign_profile
            )

        planner = role_spec_to_agent_role(
            PLANNER, scope_id="scope-a", created_at=NOW
        )
        wrong_profile = AgentProfile(
            "planner-profile", "scope-a", planner.reference, created_at=NOW
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "role"):
            worker_descriptor_to_binding(
                descriptor, scope_id="scope-a", profile=wrong_profile
            )

        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            CodingWorkerBinding(
                "scope-a",
                "worker-1",
                "principal-1",
                implementer.reference,
                ScopedRef("scope-b", "core:agent_profile", "profile-1", 1),
            )

    def test_task_spec_full_snapshot_round_trip_keeps_logical_slots(self) -> None:
        task = full_task()
        snapshot = CodingTaskSnapshot.from_task_spec(
            task, scope_id="scope-a", version=3
        )
        encoded = json.loads(json.dumps(dict(snapshot.to_dict())))
        restored = CodingTaskSnapshot.from_dict(encoded)
        self.assertEqual(restored, snapshot)
        self.assertEqual(restored.to_task_spec(), task)
        self.assertEqual(restored.role_id, "coding:implementer")
        self.assertEqual(
            restored.input_artifacts,
            ("source-snapshot", "verified-contract"),
        )
        self.assertTrue(all(
            not item.startswith("artifact://") for item in restored.input_artifacts
        ))
        self.assertEqual(restored.invocation_input.content_hash, snapshot.snapshot_hash)
        self.assertEqual(restored.invocation_input.ref, snapshot.reference)
        self.assertNotIn("acceptance_policy", encoded)
        self.assertNotIn("outcome", encoded)
        self.assertEqual(
            encoded["acceptance_criteria"],
            ["固定测试通过", "不得扩大写入范围"],
        )

    def test_task_snapshot_is_strict_and_content_addressed(self) -> None:
        snapshot = CodingTaskSnapshot.from_task_spec(
            full_task(), scope_id="scope-a"
        )
        encoded = dict(snapshot.to_dict())
        encoded["unexpected"] = "accepted"
        with self.assertRaisesRegex(RuntimeProtocolError, "未知字段"):
            CodingTaskSnapshot.from_dict(encoded)

        encoded = dict(snapshot.to_dict())
        encoded["schema_version"] = "2.0"
        with self.assertRaisesRegex(RuntimeProtocolError, "schema_version"):
            CodingTaskSnapshot.from_dict(encoded)

        encoded = dict(snapshot.to_dict())
        encoded["title"] = "被篡改"
        with self.assertRaisesRegex(RuntimeProtocolError, "hash"):
            CodingTaskSnapshot.from_dict(encoded)

        other_scope = CodingTaskSnapshot.from_task_spec(
            full_task(), scope_id="scope-b"
        )
        self.assertNotEqual(snapshot.snapshot_hash, other_scope.snapshot_hash)
        self.assertNotEqual(snapshot.reference, other_scope.reference)

    def test_artifact_mapping_requires_explicit_scope_and_never_copies_content(self) -> None:
        source = artifact()
        with self.assertRaises(TypeError):
            artifact_to_invocation_input(source)  # type: ignore[call-arg]

        reference = artifact_to_scoped_ref(source, scope_id="scope-a")
        input_ref = artifact_to_invocation_input(source, scope_id="scope-a")
        self.assertEqual(reference.entity_type, "core:artifact")
        self.assertEqual(reference.entity_id, source.artifact_id)
        self.assertEqual(input_ref.ref, reference)
        self.assertEqual(input_ref.content_hash, source.content_hash)
        encoded = json.dumps(dict(input_ref.to_dict()), ensure_ascii=False)
        self.assertNotIn("secret-marker", encoded)
        self.assertNotIn("payload-only", encoded)
        validate_artifact_input_binding(source, input_ref, scope_id="scope-a")

    def test_artifact_binding_rejects_scope_identity_and_content_drift(self) -> None:
        source = artifact()
        current = artifact_to_invocation_input(source, scope_id="scope-a")
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            validate_artifact_input_binding(source, current, scope_id="scope-b")

        wrong_id = InvocationInputRef(
            ScopedRef("scope-a", "core:artifact", "artifact-other", 1),
            source.content_hash,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "错误 Artifact"):
            validate_artifact_input_binding(source, wrong_id, scope_id="scope-a")

        changed = artifact({"changed": True}, artifact_id=source.artifact_id)
        with self.assertRaisesRegex(RuntimeProtocolError, "哈希已过期"):
            validate_artifact_input_binding(changed, current, scope_id="scope-a")

    def test_verification_record_maps_only_to_bound_acceptance_evidence(self) -> None:
        source = artifact()
        source_input = artifact_to_invocation_input(source, scope_id="scope-a")
        record = VerificationRecord(
            "verification-1",
            "core:test",
            VerificationOutcome.PASSED,
            (f"artifact://{source.artifact_id}",),
            ("evidence://command-1",),
            "测试通过",
            NOW,
            MappingProxyType({
                f"artifact://{source.artifact_id}": source.content_hash
            }),
            WORKSPACE_HASH,
        )
        subject = ScopedRef("scope-a", "core:task", "task-1", 4)
        evidence = verification_record_to_acceptance_evidence(
            record,
            scope_id="scope-a",
            acceptance_subject_ref=subject,
            subject_inputs=(source_input,),
            evaluator_principal_id="tester-principal",
            current_workspace_hash=WORKSPACE_HASH,
        )
        self.assertIsInstance(evidence, AcceptanceEvidence)
        self.assertEqual(evidence.evidence_kind, "core:test_passed")
        self.assertEqual(evidence.subject_ref, subject)
        self.assertEqual(evidence.evidence_ref.entity_type, "core:verification_record")
        self.assertFalse(hasattr(evidence, "outcome"))
        self.assertFalse(hasattr(evidence, "acceptance_record_ref"))
        self.assertNotEqual(evidence.evidence_kind, "core:accepted")

        failed = VerificationRecord(
            "verification-2",
            "core:test",
            VerificationOutcome.FAILED,
            record.subject_refs,
            record.evidence_refs,
            "测试失败",
            NOW,
            record.subject_hashes,
            WORKSPACE_HASH,
        )
        failed_evidence = verification_record_to_acceptance_evidence(
            failed,
            scope_id="scope-a",
            acceptance_subject_ref=subject,
            subject_inputs=(source_input,),
            evaluator_principal_id="tester-principal",
            current_workspace_hash=WORKSPACE_HASH,
        )
        self.assertEqual(failed_evidence.evidence_kind, "core:test_failed")

    def test_verification_mapping_fails_closed_on_wrong_binding(self) -> None:
        source = artifact()
        source_input = artifact_to_invocation_input(source, scope_id="scope-a")
        record = VerificationRecord(
            "verification-1",
            "core:test",
            VerificationOutcome.PASSED,
            (f"artifact://{source.artifact_id}",),
            ("evidence://command-1",),
            "测试通过",
            NOW,
            MappingProxyType({
                f"artifact://{source.artifact_id}": source.content_hash
            }),
            WORKSPACE_HASH,
        )
        subject = ScopedRef("scope-a", "core:task", "task-1", 1)
        common = dict(
            scope_id="scope-a",
            acceptance_subject_ref=subject,
            subject_inputs=(source_input,),
            evaluator_principal_id="tester-principal",
            current_workspace_hash=WORKSPACE_HASH,
        )
        with self.assertRaisesRegex(ScopeBoundaryError, "跨 Scope"):
            verification_record_to_acceptance_evidence(
                record,
                **{**common, "acceptance_subject_ref": ScopedRef(
                    "scope-b", "core:task", "task-1", 1
                )},
            )
        stale = InvocationInputRef(source_input.ref, "0" * 64)
        with self.assertRaisesRegex(RuntimeProtocolError, "subject_hashes"):
            verification_record_to_acceptance_evidence(
                record, **{**common, "subject_inputs": (stale,)}
            )
        with self.assertRaisesRegex(RuntimeProtocolError, "Workspace"):
            verification_record_to_acceptance_evidence(
                record, **{**common, "current_workspace_hash": "c" * 64}
            )

        unbound = VerificationRecord(
            "verification-unbound",
            "core:test",
            VerificationOutcome.PASSED,
            record.subject_refs,
            record.evidence_refs,
            "旧验证没有 Workspace 绑定",
            NOW,
            record.subject_hashes,
        )
        with self.assertRaisesRegex(RuntimeProtocolError, "绑定当前 Workspace"):
            verification_record_to_acceptance_evidence(
                unbound,
                **{**common, "current_workspace_hash": ""},
            )

    def test_verified_or_completed_legacy_values_do_not_become_accepted(self) -> None:
        subject = ScopedRef("scope-a", "core:task", "task-1", 1)
        validation = ArtifactValidation(ArtifactValidationState.VERIFIED)
        for legacy_value in (
            validation,
            VerificationOutcome.PASSED,
            TaskState.COMPLETED,
        ):
            with self.subTest(value=legacy_value):
                with self.assertRaises(TypeError):
                    verification_record_to_acceptance_evidence(
                        legacy_value,  # type: ignore[arg-type]
                        scope_id="scope-a",
                        acceptance_subject_ref=subject,
                        subject_inputs=(),
                        evaluator_principal_id="tester-principal",
                    )


if __name__ == "__main__":
    unittest.main()
