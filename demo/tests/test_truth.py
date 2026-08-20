import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from coding_workflow import (
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    ArtifactValidationState,
    Claim,
    ClaimKind,
    DEFAULT_ROLES,
    LifecycleController,
    LifecycleState,
    MemoryKind,
    MemoryManager,
    RuntimeSnapshot,
    SQLiteRuntimeStore,
    TaskContext,
    TaskGraph,
    TaskGraphExecutor,
    TaskGraphRuntime,
    TaskRunResult,
    TaskSpec,
    VerificationOutcome,
    VerificationRecord,
    WorkerRegistry,
)


class TruthProtocolTests(unittest.TestCase):
    def test_claim_distinguishes_observation_inference_and_proposal(self) -> None:
        with self.assertRaisesRegex(ValueError, "observation"):
            Claim.create(
                ClaimKind.OBSERVATION, "测试通过", "model:tester"
            )
        with self.assertRaisesRegex(ValueError, "inference"):
            Claim.create(
                ClaimKind.INFERENCE,
                "可能是缓存导致",
                "model:reviewer",
                evidence_refs=("artifact://log",),
            )

        claim = Claim.create(
            ClaimKind.INFERENCE,
            "可能是缓存导致",
            "model:reviewer",
            evidence_refs=("artifact://log",),
            uncertainty="尚未清空缓存复现",
        )
        restored = Claim.from_dict(dict(claim.to_dict()))
        self.assertEqual(restored, claim)
        self.assertEqual(restored.kind, ClaimKind.INFERENCE)

    def test_passed_or_failed_verification_requires_execution_evidence(self) -> None:
        for outcome in (
            VerificationOutcome.PASSED,
            VerificationOutcome.FAILED,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(ValueError, "执行证据"):
                    VerificationRecord.create(
                        "core:test",
                        outcome,
                        ("artifact://subject",),
                        (),
                        "模型声称通过",
                    )
        with self.assertRaisesRegex(ValueError, "自身通过证据"):
            VerificationRecord.create(
                "core:test",
                VerificationOutcome.PASSED,
                ("artifact://subject",),
                ("artifact://subject",),
                "用候选产物证明自身通过",
            )

    def test_unknown_verification_does_not_turn_artifact_into_fact(self) -> None:
        store = ArtifactStore()
        subject = store.put(Artifact.create("patch", "task", "candidate"))
        record_ref = store.mark_unknown(
            (subject,),
            validator_kind="core:test",
            summary="测试工具不可用，无法判断",
        )

        validation = store.validation(subject)
        self.assertEqual(
            validation.state, ArtifactValidationState.UNVERIFIED
        )
        self.assertEqual(validation.verification_refs, (record_ref,))
        self.assertEqual(
            store.verification(record_ref).outcome,
            VerificationOutcome.UNKNOWN,
        )

    def test_unknown_reverification_revokes_previous_verified_state(self) -> None:
        store = ArtifactStore()
        subject = store.put(Artifact.create("patch", "task", "candidate"))
        evidence = store.put(Artifact.create("test", "runtime", "passed"))
        store.mark_verified((subject,), (evidence,))
        self.assertTrue(store.is_verified(subject))

        store.mark_unknown(
            (subject,),
            validator_kind="core:test",
            summary="Workspace 已变化，旧结果无法复用",
        )
        self.assertEqual(
            store.validation(subject).state,
            ArtifactValidationState.UNVERIFIED,
        )
        self.assertFalse(store.is_verified(subject))

    def test_runtime_verification_is_atomic_and_keeps_its_evidence(self) -> None:
        store = ArtifactStore()
        subject = store.put(Artifact.create("patch", "task", "candidate"))
        evidence = store.put(Artifact.create(
            "test-result", "runtime", {"exit_code": 0}, kind="tool_result"
        ))
        record = VerificationRecord.create(
            "core:test",
            VerificationOutcome.PASSED,
            (subject,),
            (evidence,),
            "固定测试通过",
            subject_hashes={subject: store.get(subject).content_hash},
        )
        record_ref = store.record_verification(record)

        validation = store.validation(subject)
        self.assertEqual(validation.state, ArtifactValidationState.VERIFIED)
        self.assertEqual(validation.verification_refs, (record_ref,))
        self.assertEqual(store.verification(record_ref), record)

        invalid = VerificationRecord.create(
            "core:test",
            VerificationOutcome.FAILED,
            (subject,),
            ("artifact://missing",),
            "引用不存在的证据",
            subject_hashes={subject: store.get(subject).content_hash},
        )
        with self.assertRaises(KeyError):
            store.record_verification(invalid)
        self.assertEqual(
            store.validation(subject).verification_refs, (record_ref,)
        )

    def test_worker_artifact_cannot_forge_validation_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "verification_refs"):
            ArtifactDraft(
                {"passed": True},
                metadata={"verification_refs": ["made-up"]},
            )

        legitimate = ArtifactDraft({
            "passed": True,
            "validation_state": "verified",
        })
        store = ArtifactStore()
        reference = store.put(legitimate.materialize("model-result", "task"))
        self.assertEqual(
            store.validation(reference).state,
            ArtifactValidationState.UNVERIFIED,
        )

    def test_worker_success_is_execution_success_not_acceptance(self) -> None:
        class Worker:
            def run_task(self, request):
                return TaskRunResult(
                    True,
                    "模型声称完成",
                    {"candidate": ArtifactDraft({"passed": True})},
                )

        graph = TaskGraph((TaskSpec(
            "implement",
            "实现",
            "生成候选结果",
            "implementer",
            acceptance_criteria=("固定测试通过",),
            output_artifacts=("candidate",),
        ),))
        registry = WorkerRegistry()
        registry.register("implementer", Worker())
        artifacts = ArtifactStore()
        lifecycle = LifecycleController()
        memory = MemoryManager()
        result = TaskGraphExecutor(
            graph,
            registry,
            DEFAULT_ROLES,
            memory,
            artifacts=artifacts,
            lifecycle=lifecycle,
        ).run(TaskContext("task", "修复代码", ["固定测试通过"]))

        reference = result.snapshot.artifacts["candidate"]
        self.assertTrue(result.succeeded)
        self.assertFalse(result.accepted)
        self.assertEqual(
            result.acceptance_outcome, VerificationOutcome.UNKNOWN
        )
        self.assertEqual(lifecycle.state, LifecycleState.RUNNING)
        self.assertEqual(
            artifacts.validation(reference).state,
            ArtifactValidationState.UNVERIFIED,
        )
        self.assertEqual(
            memory.store.query(kinds=(MemoryKind.LONG_TERM,)), ()
        )

    def test_verification_record_round_trips_through_runtime_sqlite(self) -> None:
        graph = TaskGraph((TaskSpec(
            "implement",
            "实现",
            "生成候选结果",
            "implementer",
            acceptance_criteria=("固定测试通过",),
            output_artifacts=("candidate",),
        ),))
        artifacts = ArtifactStore()
        subject = artifacts.put(Artifact.create(
            "candidate", "task", "candidate"
        ))
        evidence = artifacts.put(Artifact.create(
            "test-result", "runtime", {"exit_code": 0}, kind="tool_result"
        ))
        record_ref = artifacts.mark_verified(
            (subject,),
            (evidence,),
            validator_kind="core:test",
            summary="固定测试通过",
        )
        runtime = TaskGraphRuntime(graph)
        runtime.claim_ready(1)
        runtime.succeed("implement", {"candidate": subject})
        lifecycle = LifecycleController()
        lifecycle.mark_running()

        with tempfile.TemporaryDirectory() as temp:
            sqlite = SQLiteRuntimeStore(Path(temp) / "runtime.sqlite3")
            sqlite.save(RuntimeSnapshot(
                "snapshot",
                "task",
                "project",
                "verified",
                graph,
                runtime.snapshot(),
                MappingProxyType({"implement": 1}),
                lifecycle.snapshot(),
                artifacts,
                MappingProxyType({}),
            ))
            restored = sqlite.load("snapshot")

        self.assertIsNotNone(restored)
        self.assertEqual(
            restored.artifacts.validation(subject).state,
            ArtifactValidationState.VERIFIED,
        )
        self.assertEqual(
            restored.artifacts.verification(record_ref).validator_kind,
            "core:test",
        )
        self.assertTrue(restored.artifacts.is_verified(subject))


if __name__ == "__main__":
    unittest.main()
