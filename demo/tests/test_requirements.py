from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from coding_workflow import (
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    CodingRequirement,
    DEFAULT_ROLES,
    EvidenceAccess,
    EvidenceGrant,
    EvidenceModality,
    LifecycleController,
    MemoryManager,
    RepositoryScope,
    RequirementEvidence,
    RuntimeSnapshot,
    SQLiteRuntimeStore,
    TaskContext,
    TaskGraph,
    TaskGraphExecutor,
    TaskGraphRuntime,
    TaskRunResult,
    TaskSpec,
    ValidatorProfile,
    ValidatorProfileRunner,
    ValidatorRegistry,
    ValidatorRunResult,
    ValidatorSpec,
    VerificationOutcome,
    VerificationRecord,
    WorkerRegistry,
    workspace_digest,
)


HASH = "a" * 64


def criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(
        "unit_tests",
        "固定单元测试通过",
        "core:test",
        {"exit_code": 0, "commands": [["python3", "-m", "unittest"]]},
        evidence_refs=("artifact://requirement-text",),
    )


def profile(*, command: str = "unittest") -> ValidatorProfile:
    acceptance = criterion()
    return ValidatorProfile("coding_default", (
        ValidatorSpec(
            "unit_test_runner",
            "core:test",
            ("unit_tests",),
            {"command": ["python3", "-m", command]},
            bind_workspace=True,
        ),
    ), {acceptance.criterion_id: acceptance.digest})


def requirement(
    validator_profile: ValidatorProfile,
    *,
    scope: RepositoryScope | None = None,
    evidence_refs: tuple[str, ...] = ("artifact://requirement-text",),
) -> CodingRequirement:
    return CodingRequirement(
        "fix_parser",
        "修复解析器边界错误",
        ("代码 Patch", "固定测试结果"),
        ("保持公开 API 兼容",),
        scope or RepositoryScope(
            ("src/**", "tests/**"),
            ("src/parser.py",),
            ("修改 .git",),
        ),
        (criterion(),),
        evidence_refs,
        validator_profile.reference,
        assumptions=("Python 版本不变",),
        open_questions=("是否需要兼容空输入",),
        extension_refs=("artifact://plugin-extension",),
    )


class RequirementProtocolTests(unittest.TestCase):
    def test_requirement_evidence_validates_modality_hash_and_round_trip(self) -> None:
        evidence = RequirementEvidence(
            "artifact://reference",
            EvidenceModality.IMAGE,
            "image/png",
            128,
            HASH,
            "user:upload",
            access=EvidenceAccess.RESTRICTED,
        )
        restored = RequirementEvidence.from_dict(
            json.loads(json.dumps(dict(evidence.to_dict())))
        )
        self.assertEqual(restored, evidence)

        artifact = Artifact.create(
            "reference", "task", {"byte_size": 128},
            metadata={"mime_type": "image/png", "sha256": HASH},
        )
        matching = RequirementEvidence(
            f"artifact://{artifact.artifact_id}",
            EvidenceModality.IMAGE,
            "image/png",
            128,
            HASH,
            "user:upload",
        )
        matching.validate_artifact(artifact)
        with self.assertRaisesRegex(ValueError, "内容哈希"):
            RequirementEvidence(
                f"artifact://{artifact.artifact_id}",
                EvidenceModality.IMAGE,
                "image/png",
                128,
                "b" * 64,
                "user:upload",
            ).validate_artifact(artifact)

        with self.assertRaisesRegex(ValueError, "不匹配"):
            RequirementEvidence(
                "artifact://reference",
                EvidenceModality.AUDIO,
                "image/png",
                128,
                HASH,
                "user:upload",
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            RequirementEvidence(
                "artifact://reference",
                EvidenceModality.TEXT,
                "text/plain",
                10,
                "not-a-hash",
                "user:request",
            )
        with self.assertRaisesRegex(ValueError, "NaN"):
            AcceptanceCriterion(
                "invalid_number", "拒绝非 JSON 数字", "core:test",
                {"threshold": float("nan")},
            )

    def test_repository_scope_cannot_expand_runtime_authority(self) -> None:
        runtime_scope = RepositoryScope(
            ("src/**", "tests/**"),
            ("src/**",),
            ("修改 .git", "读取 .env"),
        )
        RepositoryScope(
            ("src/parser.py",),
            ("src/parser.py",),
            ("修改 .git", "读取 .env", "删除测试"),
        ).assert_within(runtime_scope)

        with self.assertRaisesRegex(PermissionError, "扩大写入范围"):
            RepositoryScope(
                ("src/parser.py",),
                ("docs/README.md",),
                ("修改 .git", "读取 .env"),
            ).assert_within(runtime_scope)
        with self.assertRaisesRegex(PermissionError, "删除了禁止操作"):
            RepositoryScope(
                ("src/parser.py",),
                ("src/parser.py",),
                ("修改 .git",),
            ).assert_within(runtime_scope)

    def test_coding_requirement_and_validator_profile_are_frozen(self) -> None:
        validator_profile = profile()
        coding_requirement = requirement(validator_profile)
        evidence = RequirementEvidence(
            "artifact://requirement-text",
            EvidenceModality.TEXT,
            "text/plain",
            64,
            HASH,
            "user:request",
        )
        runtime_scope = RepositoryScope(
            ("src/**", "tests/**"),
            ("src/**",),
            ("修改 .git",),
        )
        coding_requirement.enforce_runtime_boundaries(
            runtime_scope=runtime_scope,
            validator_profile=validator_profile,
            available_evidence=(evidence,),
        )
        restored = CodingRequirement.from_dict(json.loads(json.dumps(
            dict(coding_requirement.to_dict()), ensure_ascii=False
        )))
        self.assertEqual(restored.digest, coding_requirement.digest)
        json.dumps(TaskContext(
            "task", "修复", ["测试通过"],
            coding_requirement=coding_requirement,
        ).model_input(), ensure_ascii=False)

        weakened = profile(command="compileall")
        with self.assertRaisesRegex(PermissionError, "修改或降低"):
            weakened.assert_frozen(coding_requirement.validator_profile_ref)
        lowered_criterion = AcceptanceCriterion(
            "unit_tests",
            "只要命令执行即可",
            "core:test",
            {"exit_code": [0, 1]},
            evidence_refs=("artifact://requirement-text",),
        )
        lowered_requirement = CodingRequirement(
            "fix_parser", "修复解析器边界错误", ("代码 Patch",), (),
            coding_requirement.repository_scope,
            (lowered_criterion,),
            coding_requirement.evidence_refs,
            validator_profile.reference,
        )
        with self.assertRaisesRegex(PermissionError, "AcceptanceCriterion"):
            lowered_requirement.enforce_runtime_boundaries(
                runtime_scope=runtime_scope,
                validator_profile=validator_profile,
                available_evidence=(evidence,),
            )
        with self.assertRaisesRegex(PermissionError, "未授权证据"):
            requirement(
                validator_profile,
                evidence_refs=(
                    "artifact://requirement-text",
                    "artifact://secret",
                ),
            ).enforce_runtime_boundaries(
                runtime_scope=runtime_scope,
                validator_profile=validator_profile,
                available_evidence=(evidence,),
            )

    def test_evidence_grant_is_role_task_reference_and_time_bound(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        grant = EvidenceGrant(
            "grant-1",
            "analyze",
            "planner",
            ("artifact://reference",),
            ("read", "vision:inspect"),
            "分析用户提供的参考证据",
            future,
        )
        self.assertTrue(grant.allows(
            task_id="analyze",
            role="planner",
            evidence_ref="artifact://reference",
        ))
        self.assertFalse(grant.allows(
            task_id="analyze",
            role="implementer",
            evidence_ref="artifact://reference",
        ))
        self.assertFalse(grant.allows(
            task_id="analyze",
            role="planner",
            evidence_ref="artifact://reference",
            now=datetime.now(timezone.utc) + timedelta(hours=1),
        ))

    def test_validator_profile_uses_all_required_three_state_gate(self) -> None:
        validator_profile = profile()
        subject_hashes = {"artifact://patch": HASH}
        bound_workspace = workspace_digest({"app.py": "1" * 64})
        passed = VerificationRecord.create(
            "core:test",
            VerificationOutcome.PASSED,
            ("artifact://patch",),
            ("evidence://test-run",),
            "固定测试通过",
            subject_hashes=subject_hashes,
            workspace_hash=bound_workspace,
        )
        failed = VerificationRecord.create(
            "core:test",
            VerificationOutcome.FAILED,
            ("artifact://patch",),
            ("evidence://test-run",),
            "固定测试失败",
            subject_hashes=subject_hashes,
            workspace_hash=bound_workspace,
        )
        self.assertEqual(
            validator_profile.decide(()), VerificationOutcome.UNKNOWN
        )
        self.assertEqual(
            validator_profile.decide((passed,)), VerificationOutcome.PASSED
        )
        self.assertEqual(
            validator_profile.decide((failed,)), VerificationOutcome.FAILED
        )
        unbound = VerificationRecord.create(
            "core:test",
            VerificationOutcome.PASSED,
            ("artifact://patch",),
            ("evidence://test-run",),
            "没有绑定 Workspace",
            subject_hashes=subject_hashes,
        )
        self.assertEqual(
            validator_profile.decide((unbound,)), VerificationOutcome.UNKNOWN
        )

    def test_executor_enforces_external_evidence_grant_when_enabled(self) -> None:
        class Worker:
            def __init__(self) -> None:
                self.calls = 0

            def run_task(self, request):
                self.calls += 1
                self.grant_id = request.evidence_grant.grant_id
                return TaskRunResult(True, "完成", {"analysis": "ok"})

        graph = TaskGraph((TaskSpec(
            "analyze",
            "分析",
            "分析证据",
            "planner",
            acceptance_criteria=("产生分析",),
            input_artifacts=("reference",),
            output_artifacts=("analysis",),
        ),), external_artifacts=("reference",))
        artifacts = ArtifactStore()
        reference = artifacts.put(Artifact.create(
            "reference", "task", "evidence", kind="requirement_evidence"
        ))
        worker = Worker()
        workers = WorkerRegistry()
        workers.register("planner", worker)
        grant = EvidenceGrant(
            "grant-1", "analyze", "planner", (reference,), ("read",),
            "分析需求证据",
        )
        accepted = TaskGraphExecutor(
            graph,
            workers,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"reference": reference},
            evidence_grants={"analyze": grant},
        ).run(TaskContext("task", "分析", ["产生分析"]))
        self.assertTrue(accepted.succeeded)
        self.assertEqual(worker.calls, 1)
        self.assertEqual(worker.grant_id, "grant-1")

        denied_worker = Worker()
        denied_workers = WorkerRegistry()
        denied_workers.register("planner", denied_worker)
        denied_artifacts = ArtifactStore()
        denied_reference = denied_artifacts.put(Artifact.create(
            "reference", "task-2", "evidence", kind="requirement_evidence"
        ))
        denied = TaskGraphExecutor(
            graph,
            denied_workers,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=denied_artifacts,
            initial_artifacts={"reference": denied_reference},
            evidence_grants={},
        )
        result = denied.run(TaskContext("task-2", "分析", ["产生分析"]))
        self.assertFalse(result.succeeded)
        self.assertEqual(denied_worker.calls, 0)

    def test_required_verified_input_rejects_stale_workspace_proof(self) -> None:
        class Worker:
            def __init__(self) -> None:
                self.calls = 0

            def run_task(self, request):
                self.calls += 1
                return TaskRunResult(True, "使用已验证输入", {"result": "ok"})

        graph = TaskGraph((TaskSpec(
            "consume",
            "消费",
            "使用已验证输入",
            "implementer",
            acceptance_criteria=("输入仍然有效",),
            input_artifacts=("contract",),
            output_artifacts=("result",),
            required_verified_inputs=("contract",),
        ),), external_artifacts=("contract",))
        artifacts = ArtifactStore()
        contract = artifacts.put(Artifact.create(
            "contract", "runtime", {"version": 1}
        ))
        evidence = artifacts.put(Artifact.create(
            "test-result", "runtime", {"exit_code": 0}
        ))
        old_hashes = {"app.py": "1" * 64}
        new_hashes = {"app.py": "2" * 64}
        artifacts.mark_verified(
            (contract,),
            (evidence,),
            validator_kind="core:test",
            workspace_hash=workspace_digest(old_hashes),
        )
        self.assertTrue(artifacts.is_verified(
            contract, workspace_hash=workspace_digest(old_hashes)
        ))
        self.assertFalse(artifacts.is_verified(
            contract, workspace_hash=workspace_digest(new_hashes)
        ))

        worker = Worker()
        workers = WorkerRegistry()
        workers.register("implementer", worker)
        result = TaskGraphExecutor(
            graph,
            workers,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"contract": contract},
            workspace_hashes_provider=lambda: new_hashes,
        ).run(TaskContext("task", "消费", ["输入仍然有效"]))
        self.assertFalse(result.succeeded)
        self.assertEqual(worker.calls, 0)

        mutable = artifacts.get(contract).content
        mutable["version"] = 2
        self.assertFalse(artifacts.is_verified(
            contract, workspace_hash=workspace_digest(old_hashes)
        ))

    def test_structured_requirement_activates_profile_and_grant_enforcement(self) -> None:
        class Worker:
            def __init__(self) -> None:
                self.calls = 0

            def run_task(self, request):
                self.calls += 1
                return TaskRunResult(True, "完成", {"analysis": "ok"})

        artifacts = ArtifactStore()
        reference = artifacts.put(Artifact.create(
            "reference", "task", "用户需求", kind="requirement_evidence",
            metadata={
                "mime_type": "text/plain",
                "size_bytes": 12,
                "content_hash": HASH,
            },
        ))
        acceptance = AcceptanceCriterion(
            "contract", "固定测试通过", "core:test",
            {"exit_code": 0}, evidence_refs=(reference,),
        )
        validator_profile = ValidatorProfile("structured", (
            ValidatorSpec(
                "contract_check", "core:test", ("contract",),
                {"command": ["python3", "-m", "unittest"]},
            ),
        ), {acceptance.criterion_id: acceptance.digest})
        coding_requirement = CodingRequirement(
            "structured_task", "分析并修改代码", ("分析结果",), (),
            RepositoryScope(("**",), ("**",), ()),
            (acceptance,), (reference,), validator_profile.reference,
        )
        evidence = RequirementEvidence(
            reference, EvidenceModality.TEXT, "text/plain", 12, HASH,
            "user:request",
        )
        graph = TaskGraph((TaskSpec(
            "analyze", "分析", "分析需求", "planner",
            acceptance_criteria=("输出分析",),
            input_artifacts=("reference",),
            output_artifacts=("analysis",),
        ),), external_artifacts=("reference",))
        worker = Worker()
        workers = WorkerRegistry()
        workers.register("planner", worker)
        task = TaskContext(
            "task", "分析", ["输出分析"],
            coding_requirement=coding_requirement,
        )

        with self.assertRaisesRegex(PermissionError, "ValidatorProfile"):
            TaskGraphExecutor(
                graph, workers, DEFAULT_ROLES, MemoryManager(),
                artifacts=artifacts,
                initial_artifacts={"reference": reference},
            ).run(task)

        denied = TaskGraphExecutor(
            graph, workers, DEFAULT_ROLES, MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"reference": reference},
            validator_profile=validator_profile,
            requirement_evidence=(evidence,),
        ).run(task)
        self.assertFalse(denied.succeeded)
        self.assertEqual(worker.calls, 0)

        grant = EvidenceGrant(
            "grant-structured", "analyze", "planner", (reference,),
            ("read",), "执行结构化需求分析",
        )
        accepted = TaskGraphExecutor(
            graph, workers, DEFAULT_ROLES, MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"reference": reference},
            validator_profile=validator_profile,
            requirement_evidence=(evidence,),
            evidence_grants={"analyze": grant},
        ).run(task)
        self.assertTrue(accepted.succeeded)
        self.assertEqual(worker.calls, 1)

    def test_runtime_snapshot_preserves_required_verified_inputs(self) -> None:
        graph = TaskGraph((TaskSpec(
            "consume",
            "消费",
            "使用已验证输入",
            "implementer",
            acceptance_criteria=("输入有效",),
            input_artifacts=("contract",),
            output_artifacts=("result",),
            required_verified_inputs=("contract",),
        ),), external_artifacts=("contract",))
        artifacts = ArtifactStore()
        contract = artifacts.put(Artifact.create("contract", "runtime", "v1"))
        runtime = TaskGraphRuntime(graph, initial_artifacts={"contract": contract})
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRuntimeStore(Path(temp) / "runtime.sqlite3")
            store.save(RuntimeSnapshot(
                "snapshot", "task", "project", "created", graph,
                runtime.snapshot(), MappingProxyType({"consume": 0}),
                LifecycleController().snapshot(),
                artifacts, MappingProxyType({}),
            ))
            restored = store.load("snapshot")
        self.assertEqual(
            restored.graph.tasks["consume"].required_verified_inputs,
            ("contract",),
        )

    def test_profile_runner_selects_runtime_validator_and_records_report(self) -> None:
        class PassingValidator:
            def validate(self, request):
                self.kind = request.spec.validator_kind
                return ValidatorRunResult(
                    VerificationOutcome.PASSED,
                    "固定测试通过",
                    (ArtifactDraft(
                        {"command": ["python3", "-m", "unittest"], "exit_code": 0},
                        kind="tool_result",
                    ),),
                )

        artifacts = ArtifactStore()
        subject = artifacts.put(Artifact.create("patch", "task", "candidate"))
        registry = ValidatorRegistry()
        validator = PassingValidator()
        registry.register("core:test", validator)
        result = ValidatorProfileRunner(
            profile(), registry, artifacts
        ).run(
            task_id="task",
            subject_refs=(subject,),
            workspace_hashes={"app.py": "1" * 64},
        )

        self.assertEqual(result.outcome, VerificationOutcome.PASSED)
        self.assertEqual(validator.kind, "core:test")
        self.assertTrue(artifacts.is_verified(
            subject,
            workspace_hash=workspace_digest({"app.py": "1" * 64}),
        ))
        report = artifacts.get(result.report_artifact_ref)
        self.assertEqual(report.kind, "validator_profile_report")
        self.assertEqual(report.content["outcome"], "passed")
        self.assertEqual(
            artifacts.verification(result.verification_ref).validator_kind,
            "core:profile_gate",
        )

    def test_profile_runner_missing_validator_is_unknown_not_passed(self) -> None:
        artifacts = ArtifactStore()
        subject = artifacts.put(Artifact.create("patch", "task", "candidate"))
        result = ValidatorProfileRunner(
            profile(), ValidatorRegistry(), artifacts
        ).run(
            task_id="task",
            subject_refs=(subject,),
            workspace_hashes={"app.py": "1" * 64},
        )

        self.assertEqual(result.outcome, VerificationOutcome.UNKNOWN)
        self.assertFalse(artifacts.is_verified(
            subject,
            workspace_hash=workspace_digest({"app.py": "1" * 64}),
        ))
        self.assertIn(
            "不可用", result.validator_records[0].summary
        )


if __name__ == "__main__":
    unittest.main()
