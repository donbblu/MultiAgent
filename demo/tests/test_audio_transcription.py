from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from coding_workflow import (
    AUDIO_TRANSCRIPT_KIND,
    REQUIREMENT_AUDIO_KIND,
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    AudioTranscript,
    AudioTranscriptionWorker,
    CodingRequirement,
    DEFAULT_ROLES,
    EvidenceGrant,
    EvidenceModality,
    FixedCodingSuite,
    MemoryManager,
    RepositoryScope,
    RequirementEvidence,
    TaskContext,
    TaskGraph,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    TranscriptCertainty,
    TranscriptSegment,
    TranscriptionCapability,
    TranscriptionResponse,
    UntranscribedRange,
    ValidatorProfile,
    ValidatorSpec,
    VerificationOutcome,
    WorkerDescriptor,
    build_audio_transcription_registry,
    score_audio_transcript,
)


WAV = b"RIFF" + (40).to_bytes(4, "little") + b"WAVE" + b"fixed-audio-requirement"
TRANSCRIPTION_CAPABILITIES = frozenset({
    TranscriptionCapability.TRANSCRIPTION,
    TranscriptionCapability.TIMESTAMPS,
})


def valid_response() -> TranscriptionResponse:
    return TranscriptionResponse(
        "fake",
        "fake-transcriber",
        "zh-CN",
        5200,
        (
            TranscriptSegment(
                "segment-1",
                0,
                2600,
                "税额必须使用十进制 ROUND_HALF_UP 保留两位",
            ),
            TranscriptSegment(
                "segment-2",
                3000,
                4800,
                "金额输入允许小数",
                TranscriptCertainty.UNCERTAIN,
                "末尾有背景噪声，‘小数’一词置信度较低",
                "user",
            ),
        ),
        (UntranscribedRange(2600, 3000, "静音"),),
        7,
    )


class FakeTranscriptionClient:
    def __init__(self, responses, capabilities=TRANSCRIPTION_CAPABILITIES):
        self.responses = list(responses)
        self.capabilities = frozenset(capabilities)
        self.requests = []

    def transcribe(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class TranscriptConsumerWorker:
    def __init__(self):
        self.transcript = None
        self.input_kinds = ()

    def run_task(self, request):
        self.input_kinds = tuple(item.kind for item in request.inputs.values())
        self.transcript = AudioTranscript.from_dict(
            request.inputs["audio_transcript"].content
        )
        return TaskRunResult(
            True,
            "结构化转录已交给普通 Planner",
            {"analysis": ArtifactDraft(
                {"claim_count": len(self.transcript.claims)},
                kind="core:analysis",
            )},
        )


class AudioTranscriptionTests(unittest.TestCase):
    def setup_case(
        self,
        response=None,
        capabilities=TRANSCRIPTION_CAPABILITIES,
        *,
        source=WAV,
    ):
        artifacts = ArtifactStore()
        digest = sha256(source).hexdigest()
        audio_ref = artifacts.put(Artifact.create(
            "requirement-audio",
            "audio-task",
            {"asset_uri": f"memory://{digest}"},
            kind=REQUIREMENT_AUDIO_KIND,
            metadata={
                "mime_type": "audio/wav",
                "content_hash": digest,
                "size_bytes": len(source),
            },
        ))
        evidence = RequirementEvidence(
            audio_ref,
            EvidenceModality.AUDIO,
            "audio/wav",
            len(source),
            digest,
            "user:recording",
        )
        client = FakeTranscriptionClient(
            [response or valid_response()], capabilities
        )
        worker = AudioTranscriptionWorker(
            client, {audio_ref: evidence}, lambda reference: source
        )
        return artifacts, audio_ref, evidence, client, worker

    @staticmethod
    def contract(audio_ref):
        criterion = AcceptanceCriterion(
            "fixed_tests",
            "原固定隐藏测试必须通过",
            "core:test",
            {"outcome": "passed"},
            evidence_refs=(audio_ref,),
        )
        profile = ValidatorProfile(
            "audio_tax_profile",
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
            "audio_tax_fix",
            "根据录音中的舍入规格修复税额计算",
            ("代码 Patch",),
            ("不得修改验收",),
            RepositoryScope(("**",), ("tax.py",), ("读取隐藏测试",)),
            (criterion,),
            (audio_ref,),
            profile.reference,
        )
        return requirement, profile

    @staticmethod
    def graph():
        return TaskGraph((
            TaskSpec(
                "transcribe",
                "转录音频证据",
                "只生成带时间戳的结构化转录",
                "planner",
                acceptance_criteria=("生成 transcript",),
                input_artifacts=("requirement_audio",),
                output_artifacts=("audio_transcript",),
                retry_limit=0,
                required_capabilities=("audio_transcription",),
                input_protocols=(REQUIREMENT_AUDIO_KIND,),
                output_protocols=(AUDIO_TRANSCRIPT_KIND,),
                required_policy_tags=("multimodal",),
            ),
            TaskSpec(
                "consume",
                "分析结构化转录",
                "不再读取原音频",
                "planner",
                dependencies=("transcribe",),
                acceptance_criteria=("生成分析",),
                input_artifacts=("audio_transcript",),
                output_artifacts=("analysis",),
                retry_limit=0,
                required_capabilities=("task_planning",),
                input_protocols=(AUDIO_TRANSCRIPT_KIND,),
                output_protocols=("core:analysis",),
                required_policy_tags=("text",),
            ),
        ), external_artifacts=("requirement_audio",))

    def execute(
        self,
        *,
        response=None,
        operations=("read", "audio:transcribe"),
        capabilities=TRANSCRIPTION_CAPABILITIES,
        source=WAV,
        payload=None,
    ):
        artifacts, audio_ref, evidence, client, worker = self.setup_case(
            response, capabilities, source=source
        )
        if payload is not None:
            worker.payload_resolver = lambda reference: payload
        registry = build_audio_transcription_registry(worker)
        consumer = TranscriptConsumerWorker()
        registry.register_worker(
            WorkerDescriptor(
                "text-requirement-planner",
                "planner",
                frozenset({"task_planning"}),
                frozenset({AUDIO_TRANSCRIPT_KIND}),
                frozenset({"core:analysis"}),
                frozenset({"text"}),
                principal_id="text-planner-principal",
            ),
            consumer,
        )
        requirement, profile = self.contract(audio_ref)
        result = TaskGraphExecutor(
            self.graph(),
            registry,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"requirement_audio": audio_ref},
            evidence_grants={
                "transcribe": EvidenceGrant(
                    "audio-grant",
                    "transcribe",
                    "planner",
                    (audio_ref,),
                    operations,
                    "转录音频中的需求证据",
                )
            },
            validator_profile=profile,
            requirement_evidence=(evidence,),
        ).run(TaskContext(
            "audio-task",
            requirement.objective,
            ["原固定隐藏测试必须通过"],
            allowed_paths=["tax.py"],
            prohibited_actions=["读取隐藏测试"],
            coding_requirement=requirement,
        ))
        return result, artifacts, client, consumer

    def test_audio_is_transcribed_once_then_downstream_uses_only_transcript(self):
        result, artifacts, client, consumer = self.execute()

        self.assertTrue(result.succeeded)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(consumer.input_kinds, (AUDIO_TRANSCRIPT_KIND,))
        self.assertEqual(len(consumer.transcript.claims), 2)
        self.assertEqual(
            consumer.transcript.claims[1].uncertainty,
            "末尾有背景噪声，‘小数’一词置信度较低",
        )
        transcript_ref = result.snapshot.artifacts["audio_transcript"]
        transcript_artifact = artifacts.get(transcript_ref)
        self.assertFalse(artifacts.is_verified(transcript_ref))
        self.assertEqual(
            transcript_artifact.metadata["source_evidence_ref"],
            consumer.transcript.source_evidence_ref,
        )
        self.assertEqual(
            score_audio_transcript(
                consumer.transcript,
                (
                    "税额必须使用十进制 ROUND_HALF_UP 保留两位",
                    "金额输入允许小数",
                ),
            ).f1,
            1.0,
        )

    def test_audio_operation_is_required_before_client_call(self):
        result, _, client, _ = self.execute(operations=("read",))

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_timestamp_capability_is_required_before_client_call(self):
        result, _, client, _ = self.execute(capabilities={
            TranscriptionCapability.TRANSCRIPTION,
        })

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_payload_integrity_and_signature_are_checked_before_client_call(self):
        tampered = WAV[:-1] + b"x"
        result, _, client, _ = self.execute(payload=tampered)
        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

        invalid = b"NOPE" + WAV[4:]
        result, _, client, _ = self.execute(source=invalid)
        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_timeline_and_uncertainty_are_strict(self):
        with self.assertRaisesRegex(ValueError, "uncertainty"):
            TranscriptSegment(
                "unclear", 0, 10, "可能是修复税额",
                TranscriptCertainty.UNCERTAIN,
            )
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            TranscriptionResponse(
                "fake",
                "fake-transcriber",
                "zh-CN",
                100,
                (
                    TranscriptSegment("a", 0, 60, "第一段"),
                    TranscriptSegment("b", 50, 80, "第二段"),
                ),
            )
        with self.assertRaisesRegex(ValueError, "未转录区间不能"):
            TranscriptionResponse(
                "fake",
                "fake-transcriber",
                "zh-CN",
                100,
                (TranscriptSegment("a", 0, 60, "第一段"),),
                (UntranscribedRange(50, 80, "听不清"),),
            )

    def test_transcription_response_cannot_add_acceptance_fields(self):
        value = {
            "provider": "fake",
            "model": "fake-transcriber",
            "language": "zh-CN",
            "duration_ms": 100,
            "segments": [{
                "segment_id": "a",
                "start_ms": 0,
                "end_ms": 100,
                "text": "修复税额",
                "certainty": "clear",
                "uncertainty": "",
                "speaker": "",
            }],
            "untranscribed_ranges": [],
            "latency_ms": 1,
            "passed": True,
        }

        with self.assertRaisesRegex(ValueError, "字段无效"):
            TranscriptionResponse.from_dict(value)

    def test_audio_and_text_paths_share_identical_hidden_validator(self):
        suite_root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        task = FixedCodingSuite.load(suite_root).task("python-tax-rounding")
        outcomes = []
        validator_sets = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for modality in ("text", "audio"):
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
