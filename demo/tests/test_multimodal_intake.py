from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from threading import Barrier

from coding_workflow import (
    AUDIO_TRANSCRIPT_KIND,
    EVIDENCE_BUNDLE_KIND,
    IMAGE_OBSERVATION_KIND,
    REQUIREMENT_AUDIO_KIND,
    REQUIREMENT_IMAGE_KIND,
    REQUIREMENT_TEXT_KIND,
    REQUIREMENT_VIDEO_KIND,
    VIDEO_BUG_EVIDENCE_KIND,
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    AudioTranscriptionWorker,
    CodingRequirement,
    DEFAULT_ROLES,
    EvidenceBundleEntry,
    EvidenceGrant,
    EvidenceIntakeStatus,
    EvidenceModality,
    FixedCodingSuite,
    ImagePerceptionWorker,
    MemoryManager,
    ModelCapability,
    ModelResponse,
    ModelUsage,
    MultimodalEvidenceBundle,
    MultimodalIntakeError,
    MultimodalIntakeRunner,
    RepositoryScope,
    RequirementEvidence,
    TaskContext,
    TaskRunResult,
    TranscriptSegment,
    TranscriptionCapability,
    TranscriptionResponse,
    ValidatorProfile,
    ValidatorSpec,
    VerificationOutcome,
    VideoAnalysisCapability,
    VideoAnalysisResponse,
    VideoEvent,
    VideoEventKind,
    VideoPerceptionWorker,
    build_multimodal_intake_plan,
    build_multimodal_intake_registry,
)
from coding_workflow.local_execution_approval import LocalExecutionApprover


TEXT = "税额必须使用十进制 ROUND_HALF_UP 保留两位"
PNG = b"\x89PNG\r\n\x1a\nmultimodal-image"
WAV = b"RIFF" + (40).to_bytes(4, "little") + b"WAVE" + b"multimodal-audio"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"multimodal-video"


class FakeVisionClient:
    capabilities = frozenset({
        ModelCapability.TEXT,
        ModelCapability.VISION,
        ModelCapability.STRUCTURED_OUTPUT,
    })

    def __init__(self, barrier=None):
        self.barrier = barrier
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        if self.barrier:
            self.barrier.wait(3)
        return ModelResponse(
            {
                "schema_version": "1.0",
                "summary": "图片说明十进制舍入规则",
                "observations": [{
                    "statement": "截图显示 ROUND_HALF_UP",
                    "region": "specification",
                    "evidence": "可见 ROUND_HALF_UP 文本",
                }],
                "inferences": [],
                "unreadable_regions": [],
            },
            "fake",
            "fake-vision",
            ModelUsage(10, 5, 15),
            1,
        )


class FakeTranscriptionClient:
    capabilities = frozenset({
        TranscriptionCapability.TRANSCRIPTION,
        TranscriptionCapability.TIMESTAMPS,
    })

    def __init__(self, barrier=None):
        self.barrier = barrier
        self.requests = []

    def transcribe(self, request):
        self.requests.append(request)
        if self.barrier:
            self.barrier.wait(3)
        return TranscriptionResponse(
            "fake",
            "fake-transcriber",
            "zh-CN",
            2000,
            (TranscriptSegment(
                "audio-1", 0, 1800,
                "录音要求税额使用 ROUND_HALF_UP",
            ),),
            latency_ms=1,
        )


class FakeVideoClient:
    capabilities = frozenset({
        VideoAnalysisCapability.VIDEO_UNDERSTANDING,
        VideoAnalysisCapability.TIMESTAMPS,
    })

    def __init__(self, barrier=None):
        self.barrier = barrier
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        if self.barrier:
            self.barrier.wait(3)
        return VideoAnalysisResponse(
            "fake",
            "fake-video",
            2500,
            (VideoEvent(
                "video-1",
                500,
                1800,
                VideoEventKind.ERROR_SIGNAL,
                "录屏显示 1.005 被错误舍入为 1.00",
                "result_panel",
            ),),
            latency_ms=1,
        )


class BundlePlanner:
    def __init__(self):
        self.requests = []
        self.bundle = None

    def run_task(self, request):
        self.requests.append(request)
        self.bundle = MultimodalEvidenceBundle.from_dict(
            request.inputs["evidence_bundle"].content
        )
        return TaskRunResult(
            True,
            "已根据统一 Bundle 生成需求分析",
            {"analysis": ArtifactDraft(
                {"claim_count": len(self.bundle.claims)},
                kind="core:analysis",
            )},
        )


class MultimodalIntakeTests(unittest.TestCase):
    @staticmethod
    def _artifact(
        artifacts,
        *,
        name,
        task_id,
        kind,
        modality,
        mime_type,
        payload,
        content=None,
        metadata_payload=None,
    ):
        digest_payload = payload if metadata_payload is None else metadata_payload
        digest = sha256(digest_payload).hexdigest()
        reference = artifacts.put(Artifact.create(
            name,
            task_id,
            payload.decode("utf-8") if content is None and modality is EvidenceModality.TEXT else (
                content if content is not None else {"asset_uri": f"memory://{digest}"}
            ),
            kind=kind,
            metadata={
                "mime_type": mime_type,
                "content_hash": digest,
                "size_bytes": len(digest_payload),
            },
        ))
        return RequirementEvidence(
            reference,
            modality,
            mime_type,
            len(digest_payload),
            digest,
            f"user:{name}",
        )

    def setup_case(
        self,
        modalities=("text", "image", "audio", "video"),
        *,
        barrier=None,
        include_video_worker=True,
        corrupt_text=False,
    ):
        artifacts = ArtifactStore()
        evidence = {}
        payloads = {}
        if "text" in modalities:
            text_payload = TEXT.encode("utf-8")
            evidence["text"] = self._artifact(
                artifacts,
                name="text",
                task_id="mixed-task",
                kind=REQUIREMENT_TEXT_KIND,
                modality=EvidenceModality.TEXT,
                mime_type="text/plain",
                payload=text_payload,
                content=TEXT + (" 已被篡改" if corrupt_text else ""),
                metadata_payload=text_payload,
            )
        if "image" in modalities:
            evidence["image"] = self._artifact(
                artifacts,
                name="image",
                task_id="mixed-task",
                kind=REQUIREMENT_IMAGE_KIND,
                modality=EvidenceModality.IMAGE,
                mime_type="image/png",
                payload=PNG,
            )
            payloads[evidence["image"].artifact_ref] = PNG
        if "audio" in modalities:
            evidence["audio"] = self._artifact(
                artifacts,
                name="audio",
                task_id="mixed-task",
                kind=REQUIREMENT_AUDIO_KIND,
                modality=EvidenceModality.AUDIO,
                mime_type="audio/wav",
                payload=WAV,
            )
            payloads[evidence["audio"].artifact_ref] = WAV
        if "video" in modalities:
            evidence["video"] = self._artifact(
                artifacts,
                name="video",
                task_id="mixed-task",
                kind=REQUIREMENT_VIDEO_KIND,
                modality=EvidenceModality.VIDEO,
                mime_type="video/mp4",
                payload=MP4,
            )
            payloads[evidence["video"].artifact_ref] = MP4

        criterion = AcceptanceCriterion(
            "fixed_tests",
            "固定隐藏测试必须通过",
            "core:test",
            {"outcome": "passed"},
            evidence_refs=tuple(item.artifact_ref for item in evidence.values()),
        )
        profile = ValidatorProfile(
            "multimodal_tax_profile",
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
            "mixed_tax_fix",
            "根据多模态证据修复税额舍入",
            ("代码 Patch",),
            ("不得修改验收",),
            RepositoryScope(("**",), ("tax.py",), ("读取隐藏测试",)),
            (criterion,),
            tuple(item.artifact_ref for item in evidence.values()),
            profile.reference,
        )
        parent = TaskContext(
            "mixed-task",
            requirement.objective,
            ["固定隐藏测试必须通过"],
            allowed_paths=["tax.py"],
            prohibited_actions=["读取隐藏测试"],
            coding_requirement=requirement,
        )
        plan = build_multimodal_intake_plan(evidence)
        vision = FakeVisionClient(barrier)
        audio = FakeTranscriptionClient(barrier)
        video = FakeVideoClient(barrier)
        planner = BundlePlanner()
        image_worker = ImagePerceptionWorker(
            vision,
            {item.artifact_ref: item for item in evidence.values()},
            lambda reference: payloads[reference],
        ) if "image" in evidence else None
        audio_worker = AudioTranscriptionWorker(
            audio,
            {item.artifact_ref: item for item in evidence.values()},
            lambda reference: payloads[reference],
        ) if "audio" in evidence else None
        video_worker = VideoPerceptionWorker(
            video,
            {item.artifact_ref: item for item in evidence.values()},
            lambda reference: payloads[reference],
        ) if "video" in evidence and include_video_worker else None
        registry = build_multimodal_intake_registry(
            image_worker=image_worker,
            audio_worker=audio_worker,
            video_worker=video_worker,
            planner_worker=planner,
        )
        grants = {}
        for binding in plan.bindings:
            operations = ["read"]
            if binding.operation != "read":
                operations.append(binding.operation)
            grants[binding.task_id] = EvidenceGrant(
                f"grant-{binding.input_name}",
                binding.task_id,
                "planner",
                (binding.evidence.artifact_ref,),
                tuple(operations),
                "统一多模态 Intake",
            )
        runner = MultimodalIntakeRunner(
            plan,
            registry,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts,
            profile,
        )
        clients = {"image": vision, "audio": audio, "video": video}
        return runner, parent, grants, artifacts, planner, clients, evidence

    def test_mixed_media_runs_in_parallel_once_and_planner_sees_only_bundle(self):
        barrier = Barrier(3)
        runner, parent, grants, artifacts, planner, clients, evidence = (
            self.setup_case(barrier=barrier)
        )

        result = runner.run(parent, evidence_grants=grants)

        self.assertTrue(result.succeeded)
        self.assertTrue(result.bundle.ready)
        self.assertEqual(
            {entry.modality for entry in result.bundle.entries},
            set(EvidenceModality),
        )
        self.assertEqual(len(result.bundle.claims), 4)
        self.assertTrue(all(len(client.requests) == 1 for client in clients.values()))
        self.assertEqual(len(planner.requests), 1)
        self.assertEqual(
            tuple(planner.requests[0].inputs),
            ("evidence_bundle",),
        )
        self.assertEqual(
            set(entry.source_evidence_ref for entry in result.bundle.entries),
            set(item.artifact_ref for item in evidence.values()),
        )
        self.assertFalse(artifacts.is_verified(result.bundle_ref))

    def test_missing_video_worker_is_recorded_and_planner_is_not_called(self):
        runner, parent, grants, _, planner, clients, _ = self.setup_case(
            include_video_worker=False
        )

        result = runner.run(parent, evidence_grants=grants)

        self.assertFalse(result.succeeded)
        self.assertFalse(result.bundle.ready)
        by_modality = {item.modality: item for item in result.bundle.entries}
        self.assertEqual(
            by_modality[EvidenceModality.VIDEO].status,
            EvidenceIntakeStatus.BLOCKED,
        )
        self.assertEqual(planner.requests, [])
        self.assertEqual(len(clients["image"].requests), 1)
        self.assertEqual(len(clients["audio"].requests), 1)
        self.assertEqual(clients["video"].requests, [])

    def test_corrupt_text_is_failed_but_other_media_are_still_processed(self):
        runner, parent, grants, _, planner, clients, _ = self.setup_case(
            modalities=("text", "image"), corrupt_text=True
        )

        result = runner.run(parent, evidence_grants=grants)

        by_modality = {item.modality: item for item in result.bundle.entries}
        self.assertEqual(
            by_modality[EvidenceModality.TEXT].status,
            EvidenceIntakeStatus.FAILED,
        )
        self.assertEqual(
            by_modality[EvidenceModality.IMAGE].status,
            EvidenceIntakeStatus.READY,
        )
        self.assertEqual(len(clients["image"].requests), 1)
        self.assertEqual(planner.requests, [])

    def test_bundle_status_and_claim_source_cannot_be_forged(self):
        runner, parent, grants, _, _, _, _ = self.setup_case(
            modalities=("text",)
        )
        result = runner.run(parent, evidence_grants=grants)
        entry = result.bundle.entries[0]

        with self.assertRaisesRegex(ValueError, "ready 必须由"):
            MultimodalEvidenceBundle(
                result.bundle.requirement_id,
                (entry,),
                False,
            )
        with self.assertRaisesRegex(ValueError, "未就绪 Evidence"):
            EvidenceBundleEntry(
                entry.source_evidence_ref,
                entry.modality,
                EvidenceIntakeStatus.FAILED,
                entry.normalized_kind,
                entry.normalized_artifact_ref,
                entry.claims,
                "伪造失败",
            )

    def test_text_only_skips_media_graph_and_still_uses_bundle_planner(self):
        runner, parent, grants, _, planner, _, _ = self.setup_case(
            modalities=("text",)
        )

        result = runner.run(parent, evidence_grants=grants)

        self.assertTrue(result.succeeded)
        self.assertIsNone(result.perception_result)
        self.assertEqual(len(planner.requests), 1)
        self.assertEqual(result.bundle.entries[0].normalized_kind, REQUIREMENT_TEXT_KIND)

    def test_plan_rejects_duplicate_or_omitted_requirement_evidence(self):
        runner, parent, grants, _, _, _, evidence = self.setup_case(
            modalities=("text", "image")
        )
        with self.assertRaisesRegex(ValueError, "不能重复处理"):
            build_multimodal_intake_plan({
                "first": evidence["text"],
                "second": evidence["text"],
            })

        reduced = build_multimodal_intake_plan({"text": evidence["text"]})
        runner.plan = reduced
        with self.assertRaisesRegex(MultimodalIntakeError, "必须覆盖"):
            runner.run(parent, evidence_grants=grants)

    def test_text_and_multimodal_paths_keep_the_same_hidden_validator(self):
        suite_root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        task = FixedCodingSuite.load(suite_root).task("python-tax-rounding")
        outcomes = []
        validators = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for input_kind in ("text", "multimodal"):
                workspace = task.prepare_workspace(root / input_kind / "workspace")
                task.apply_reference_solution(workspace)
                artifacts = ArtifactStore()
                subject = artifacts.put(Artifact.create(
                    "candidate", input_kind, {"input_kind": input_kind}
                ))
                result = task.validate_candidate(
                    workspace=workspace,
                    validation_workspace=root / input_kind / "validation",
                    artifacts=artifacts,
                    subject_refs=(subject,),
                    task_id=f"tax-{input_kind}",
                    approver_factory=lambda: LocalExecutionApprover(True),
                )
                outcomes.append(result.outcome)
                validators.append(tuple(
                    item.validator_kind for item in result.validator_records
                ))

        self.assertEqual(outcomes, [
            VerificationOutcome.PASSED,
            VerificationOutcome.PASSED,
        ])
        self.assertEqual(validators[0], validators[1])


if __name__ == "__main__":
    unittest.main()
