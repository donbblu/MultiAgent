from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from coding_workflow import (
    REQUIREMENT_VIDEO_KIND,
    VIDEO_BUG_EVIDENCE_KIND,
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    CandidateReproductionStep,
    ClaimKind,
    CodingRequirement,
    DEFAULT_ROLES,
    EvidenceGrant,
    EvidenceModality,
    ExpectedBasis,
    FixedCodingSuite,
    MemoryManager,
    ObservedDiscrepancy,
    RepositoryScope,
    RequirementEvidence,
    TaskContext,
    TaskGraph,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    UnreviewedVideoRange,
    ValidatorProfile,
    ValidatorSpec,
    VerificationOutcome,
    VideoAnalysisCapability,
    VideoAnalysisResponse,
    VideoBugEvidence,
    VideoEvent,
    VideoEventCertainty,
    VideoEventKind,
    VideoPerceptionWorker,
    WorkerDescriptor,
    build_video_perception_registry,
    score_video_events,
)
from coding_workflow.local_execution_approval import LocalExecutionApprover


MP4 = b"\x00\x00\x00\x18ftypisom" + b"fixed-video-bug-evidence"
VIDEO_CAPABILITIES = frozenset({
    VideoAnalysisCapability.VIDEO_UNDERSTANDING,
    VideoAnalysisCapability.TIMESTAMPS,
})


def valid_response() -> VideoAnalysisResponse:
    return VideoAnalysisResponse(
        "fake",
        "fake-video-perception",
        6000,
        (
            VideoEvent(
                "event-expected",
                0,
                800,
                VideoEventKind.SPOKEN_STATEMENT,
                "旁白说明提交后应该显示按两位小数舍入的税额",
                "audio_track",
            ),
            VideoEvent(
                "event-action",
                1000,
                2000,
                VideoEventKind.USER_ACTION,
                "用户输入金额 1.005 并点击提交",
                "form",
            ),
            VideoEvent(
                "event-error",
                2200,
                3000,
                VideoEventKind.ERROR_SIGNAL,
                "结果区域显示税额 1.00，而不是旁白所述的 1.01",
                "result_panel",
                VideoEventCertainty.UNCERTAIN,
                "结果数字较小，但可辨认为 1.00",
            ),
        ),
        (
            CandidateReproductionStep(
                "step-1",
                1,
                "输入金额 1.005 并点击提交",
                ("event-action",),
            ),
        ),
        (
            ObservedDiscrepancy(
                "rounding-mismatch",
                "提交后显示按两位小数 ROUND_HALF_UP 的税额 1.01",
                ExpectedBasis.SPOKEN,
                ("event-expected",),
                ("event-error",),
            ),
        ),
        (UnreviewedVideoRange(4000, 4500, "画面被遮挡"),),
        9,
    )


class FakeVideoPerceptionClient:
    def __init__(self, responses, capabilities=VIDEO_CAPABILITIES):
        self.responses = list(responses)
        self.capabilities = frozenset(capabilities)
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class VideoEvidenceConsumerWorker:
    def __init__(self):
        self.evidence = None
        self.input_kinds = ()

    def run_task(self, request):
        self.input_kinds = tuple(item.kind for item in request.inputs.values())
        self.evidence = VideoBugEvidence.from_dict(
            request.inputs["video_bug_evidence"].content
        )
        return TaskRunResult(
            True,
            "结构化录屏证据已交给普通 Planner",
            {"analysis": ArtifactDraft(
                {
                    "event_count": len(self.evidence.events),
                    "candidate_step_count": len(self.evidence.candidate_steps),
                },
                kind="core:analysis",
            )},
        )


class VideoPerceptionTests(unittest.TestCase):
    def setup_case(
        self,
        response=None,
        capabilities=VIDEO_CAPABILITIES,
        *,
        source=MP4,
    ):
        artifacts = ArtifactStore()
        digest = sha256(source).hexdigest()
        video_ref = artifacts.put(Artifact.create(
            "requirement-video",
            "video-task",
            {"asset_uri": f"memory://{digest}"},
            kind=REQUIREMENT_VIDEO_KIND,
            metadata={
                "mime_type": "video/mp4",
                "content_hash": digest,
                "size_bytes": len(source),
            },
        ))
        evidence = RequirementEvidence(
            video_ref,
            EvidenceModality.VIDEO,
            "video/mp4",
            len(source),
            digest,
            "user:screen_recording",
        )
        client = FakeVideoPerceptionClient(
            [response or valid_response()], capabilities
        )
        worker = VideoPerceptionWorker(
            client, {video_ref: evidence}, lambda reference: source
        )
        return artifacts, video_ref, evidence, client, worker

    @staticmethod
    def contract(video_ref):
        criterion = AcceptanceCriterion(
            "fixed_tests",
            "原固定隐藏回归必须通过",
            "core:test",
            {"outcome": "passed"},
            evidence_refs=(video_ref,),
        )
        profile = ValidatorProfile(
            "video_tax_profile",
            (ValidatorSpec(
                "fixed_test_runner",
                "core:test",
                (criterion.criterion_id,),
                {"suite": "python-tax-rounding"},
                bind_workspace=True,
            ),),
            {criterion.criterion_id: criterion.digest},
        )
        requirement = CodingRequirement(
            "video_tax_fix",
            "根据录屏中的复现证据修复税额舍入错误",
            ("代码 Patch",),
            ("不得修改验收",),
            RepositoryScope(("**",), ("tax.py",), ("读取隐藏测试",)),
            (criterion,),
            (video_ref,),
            profile.reference,
        )
        return requirement, profile

    @staticmethod
    def graph():
        return TaskGraph((
            TaskSpec(
                "perceive_video",
                "读取录屏证据",
                "只生成结构化时间线和候选复现步骤",
                "planner",
                acceptance_criteria=("生成 video bug evidence",),
                input_artifacts=("requirement_video",),
                output_artifacts=("video_bug_evidence",),
                retry_limit=0,
                required_capabilities=("video_temporal_understanding",),
                input_protocols=(REQUIREMENT_VIDEO_KIND,),
                output_protocols=(VIDEO_BUG_EVIDENCE_KIND,),
                required_policy_tags=("multimodal",),
            ),
            TaskSpec(
                "consume",
                "分析结构化录屏证据",
                "不再读取原视频",
                "planner",
                dependencies=("perceive_video",),
                acceptance_criteria=("生成分析",),
                input_artifacts=("video_bug_evidence",),
                output_artifacts=("analysis",),
                retry_limit=0,
                required_capabilities=("task_planning",),
                input_protocols=(VIDEO_BUG_EVIDENCE_KIND,),
                output_protocols=("core:analysis",),
                required_policy_tags=("text",),
            ),
        ), external_artifacts=("requirement_video",))

    def execute(
        self,
        *,
        response=None,
        operations=("read", "video:inspect"),
        capabilities=VIDEO_CAPABILITIES,
        source=MP4,
        payload=None,
    ):
        artifacts, video_ref, evidence, client, worker = self.setup_case(
            response, capabilities, source=source
        )
        if payload is not None:
            worker.payload_resolver = lambda reference: payload
        registry = build_video_perception_registry(worker)
        consumer = VideoEvidenceConsumerWorker()
        registry.register_worker(
            WorkerDescriptor(
                "text-requirement-planner",
                "planner",
                frozenset({"task_planning"}),
                frozenset({VIDEO_BUG_EVIDENCE_KIND}),
                frozenset({"core:analysis"}),
                frozenset({"text"}),
                principal_id="text-planner-principal",
            ),
            consumer,
        )
        requirement, profile = self.contract(video_ref)
        result = TaskGraphExecutor(
            self.graph(),
            registry,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"requirement_video": video_ref},
            evidence_grants={
                "perceive_video": EvidenceGrant(
                    "video-grant",
                    "perceive_video",
                    "planner",
                    (video_ref,),
                    operations,
                    "提取录屏中的 Bug 证据",
                )
            },
            validator_profile=profile,
            requirement_evidence=(evidence,),
        ).run(TaskContext(
            "video-task",
            requirement.objective,
            ["原固定隐藏回归必须通过"],
            allowed_paths=["tax.py"],
            prohibited_actions=["读取隐藏测试"],
            coding_requirement=requirement,
        ))
        return result, artifacts, client, consumer

    def test_video_is_read_once_then_downstream_uses_only_structured_evidence(self):
        result, artifacts, client, consumer = self.execute()

        self.assertTrue(result.succeeded)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(consumer.input_kinds, (VIDEO_BUG_EVIDENCE_KIND,))
        self.assertEqual(len(consumer.evidence.events), 3)
        self.assertEqual(len(consumer.evidence.candidate_steps), 1)
        kinds = tuple(item.kind for item in consumer.evidence.claims)
        self.assertEqual(kinds, (
            ClaimKind.OBSERVATION,
            ClaimKind.OBSERVATION,
            ClaimKind.OBSERVATION,
            ClaimKind.INFERENCE,
            ClaimKind.PROPOSAL,
        ))
        evidence_ref = result.snapshot.artifacts["video_bug_evidence"]
        self.assertFalse(artifacts.is_verified(evidence_ref))
        self.assertEqual(
            score_video_events(
                consumer.evidence,
                tuple(item.description for item in valid_response().events),
            ).f1,
            1.0,
        )

    def test_video_operation_is_required_before_client_call(self):
        result, _, client, _ = self.execute(operations=("read",))

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_timestamp_capability_is_required_before_client_call(self):
        result, _, client, _ = self.execute(capabilities={
            VideoAnalysisCapability.VIDEO_UNDERSTANDING,
        })

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_payload_integrity_and_signature_are_checked_before_client_call(self):
        tampered = MP4[:-1] + b"x"
        result, _, client, _ = self.execute(payload=tampered)
        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

        invalid = MP4[:4] + b"NOPE" + MP4[8:]
        result, _, client, _ = self.execute(source=invalid)
        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_timeline_references_and_inferred_expectations_are_strict(self):
        with self.assertRaisesRegex(ValueError, "uncertainty"):
            VideoEvent(
                "event", 0, 10, VideoEventKind.ERROR_SIGNAL,
                "可能出现错误", "dialog", VideoEventCertainty.UNCERTAIN,
            )
        with self.assertRaisesRegex(ValueError, "必须说明 uncertainty"):
            ObservedDiscrepancy(
                "difference",
                "应该成功",
                ExpectedBasis.INFERRED,
                (),
                ("event",),
            )
        with self.assertRaisesRegex(ValueError, "未知视频事件"):
            VideoAnalysisResponse(
                "fake",
                "fake-video-perception",
                100,
                (VideoEvent(
                    "event", 0, 10, VideoEventKind.USER_ACTION,
                    "点击提交", "form",
                ),),
                (CandidateReproductionStep(
                    "step", 1, "点击不存在的按钮", ("missing",),
                ),),
            )

    def test_video_response_cannot_add_acceptance_fields(self):
        value = {
            "provider": "fake",
            "model": "fake-video-perception",
            "duration_ms": 100,
            "events": [{
                "event_id": "event",
                "start_ms": 0,
                "end_ms": 100,
                "kind": "error_signal",
                "description": "显示错误",
                "region": "dialog",
                "certainty": "clear",
                "uncertainty": "",
            }],
            "candidate_steps": [],
            "discrepancies": [],
            "unreviewed_ranges": [],
            "latency_ms": 1,
            "passed": True,
        }

        with self.assertRaisesRegex(ValueError, "字段无效"):
            VideoAnalysisResponse.from_dict(value)

    def test_video_and_text_paths_share_identical_hidden_validator(self):
        suite_root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        task = FixedCodingSuite.load(suite_root).task("python-tax-rounding")
        outcomes = []
        validator_sets = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for modality in ("text", "video"):
                workspace = task.prepare_workspace(root / modality / "workspace")
                task.apply_reference_solution(workspace)
                artifacts = ArtifactStore()
                subject = artifacts.put(Artifact.create(
                    "candidate", modality, {"input_modality": modality}
                ))
                result = task.validate_candidate(
                    workspace=workspace,
                    validation_workspace=root / modality / "validation",
                    artifacts=artifacts,
                    subject_refs=(subject,),
                    task_id=f"tax-{modality}",
                    approver_factory=lambda: LocalExecutionApprover(True),
                )
                outcomes.append(result.outcome)
                validator_sets.append(tuple(
                    item.validator_kind for item in result.validator_records
                ))

        self.assertEqual(outcomes, [
            VerificationOutcome.PASSED, VerificationOutcome.PASSED,
        ])
        self.assertEqual(validator_sets[0], validator_sets[1])


if __name__ == "__main__":
    unittest.main()
