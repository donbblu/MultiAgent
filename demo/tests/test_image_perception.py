from __future__ import annotations

import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from coding_workflow import (
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    CodingRequirement,
    DEFAULT_ROLES,
    EvidenceGrant,
    EvidenceModality,
    FixedCodingSuite,
    IMAGE_OBSERVATION_KIND,
    ImageObservation,
    ImagePerceptionWorker,
    MemoryManager,
    ModelCapability,
    ModelResponse,
    ModelUsage,
    REQUIREMENT_IMAGE_KIND,
    RepositoryScope,
    RequirementEvidence,
    TaskContext,
    TaskGraph,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    ValidatorProfile,
    ValidatorSpec,
    VerificationOutcome,
    WorkerDescriptor,
    build_image_perception_registry,
    score_image_observation,
)


PNG = b"\x89PNG\r\n\x1a\nfixed-image-requirement"
VISION_CAPABILITIES = frozenset({
    ModelCapability.TEXT,
    ModelCapability.VISION,
    ModelCapability.STRUCTURED_OUTPUT,
})


def valid_response():
    return {
        "schema_version": "1.0",
        "summary": "识别出舍入规则",
        "observations": [{
            "statement": "税额必须使用十进制 ROUND_HALF_UP 保留两位",
            "region": "中央规格框",
            "evidence": "图片中可见 ROUND_HALF_UP 和 2 decimal places",
        }],
        "inferences": [{
            "statement": "可能需要 Decimal 类型",
            "evidence": "规格要求十进制舍入",
            "uncertainty": "图片没有规定具体语言实现",
        }],
        "unreadable_regions": ["右下角小字无法辨认"],
    }


class FakeVisionClient:
    def __init__(self, responses, capabilities=VISION_CAPABILITIES):
        self.responses = list(responses)
        self.capabilities = frozenset(capabilities)
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        return ModelResponse(
            self.responses.pop(0),
            "fake",
            "fake-vision",
            ModelUsage(20, 10, 30),
            2,
        )

    def generate_json(self, messages):
        raise AssertionError("必须使用 structured output")


class ConsumerWorker:
    def __init__(self):
        self.observation = None

    def run_task(self, request):
        artifact = request.inputs["visual_observation"]
        self.observation = ImageObservation.from_dict(artifact.content)
        self.requirement_refs = request.parent.coding_requirement.evidence_refs
        return TaskRunResult(
            True,
            "结构化视觉证据已交给普通 Planner",
            {"analysis": ArtifactDraft(
                {"claim_count": len(self.observation.claims)},
                kind="core:analysis",
            )},
        )


class ImagePerceptionTests(unittest.TestCase):
    def setup_case(self, response=None, capabilities=VISION_CAPABILITIES):
        artifacts = ArtifactStore()
        digest = sha256(PNG).hexdigest()
        image_ref = artifacts.put(Artifact.create(
            "requirement-image",
            "image-task",
            {"asset_uri": f"memory://{digest}"},
            kind=REQUIREMENT_IMAGE_KIND,
            metadata={
                "mime_type": "image/png",
                "content_hash": digest,
                "size_bytes": len(PNG),
            },
        ))
        evidence = RequirementEvidence(
            image_ref,
            EvidenceModality.IMAGE,
            "image/png",
            len(PNG),
            digest,
            "user:upload",
        )
        client = FakeVisionClient(
            [response or valid_response()], capabilities
        )
        worker = ImagePerceptionWorker(
            client, {image_ref: evidence}, lambda reference: PNG
        )
        return artifacts, image_ref, evidence, client, worker

    @staticmethod
    def contract(image_ref):
        criterion = AcceptanceCriterion(
            "fixed_tests",
            "原固定隐藏测试必须通过",
            "core:test",
            {"outcome": "passed"},
            evidence_refs=(image_ref,),
        )
        profile = ValidatorProfile(
            "image_tax_profile",
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
            "image_tax_fix",
            "根据图片中的舍入规格修复税额计算",
            ("代码 Patch",),
            ("不得修改验收",),
            RepositoryScope(("**",), ("tax.py",), ("读取隐藏测试",)),
            (criterion,),
            (image_ref,),
            profile.reference,
        )
        return requirement, profile

    @staticmethod
    def graph():
        return TaskGraph((
            TaskSpec(
                "perceive",
                "读取图片证据",
                "只生成结构化视觉 Claim",
                "planner",
                acceptance_criteria=("生成 observation",),
                input_artifacts=("requirement_image",),
                output_artifacts=("visual_observation",),
                retry_limit=0,
                required_capabilities=("vision_understanding",),
                input_protocols=(REQUIREMENT_IMAGE_KIND,),
                output_protocols=(IMAGE_OBSERVATION_KIND,),
                required_policy_tags=("multimodal",),
            ),
            TaskSpec(
                "consume",
                "分析结构化证据",
                "不再读取原图",
                "planner",
                dependencies=("perceive",),
                acceptance_criteria=("生成分析",),
                input_artifacts=("visual_observation",),
                output_artifacts=("analysis",),
                retry_limit=0,
                required_capabilities=("task_planning",),
                input_protocols=(IMAGE_OBSERVATION_KIND,),
                output_protocols=("core:analysis",),
                required_policy_tags=("text",),
            ),
        ), external_artifacts=("requirement_image",))

    def execute(self, *, response=None, operations=("read", "vision:inspect"),
                capabilities=VISION_CAPABILITIES, payload=PNG):
        artifacts, image_ref, evidence, client, worker = self.setup_case(
            response, capabilities
        )
        if payload != PNG:
            worker.payload_resolver = lambda reference: payload
        registry = build_image_perception_registry(worker)
        consumer = ConsumerWorker()
        registry.register_worker(
            WorkerDescriptor(
                "text-requirement-planner",
                "planner",
                frozenset({"task_planning"}),
                frozenset({IMAGE_OBSERVATION_KIND}),
                frozenset({"core:analysis"}),
                frozenset({"text"}),
                principal_id="text-planner-principal",
            ),
            consumer,
        )
        requirement, profile = self.contract(image_ref)
        result = TaskGraphExecutor(
            self.graph(),
            registry,
            DEFAULT_ROLES,
            MemoryManager(),
            artifacts=artifacts,
            initial_artifacts={"requirement_image": image_ref},
            evidence_grants={
                "perceive": EvidenceGrant(
                    "image-grant",
                    "perceive",
                    "planner",
                    (image_ref,),
                    operations,
                    "提取图片中的需求证据",
                )
            },
            validator_profile=profile,
            requirement_evidence=(evidence,),
        ).run(TaskContext(
            "image-task",
            requirement.objective,
            ["原固定隐藏测试必须通过"],
            allowed_paths=["tax.py"],
            prohibited_actions=["读取隐藏测试"],
            coding_requirement=requirement,
        ))
        return result, artifacts, client, consumer

    def test_image_is_perceived_once_then_downstream_uses_claim_artifact(self):
        result, artifacts, client, consumer = self.execute()

        self.assertTrue(result.succeeded)
        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertIn(ModelCapability.VISION, request.required_capabilities)
        self.assertEqual(len(request.messages[1].content), 2)
        self.assertEqual(len(consumer.observation.claims), 2)
        self.assertEqual(
            consumer.requirement_refs,
            (consumer.observation.source_evidence_ref,),
        )
        observation_ref = result.snapshot.artifacts["visual_observation"]
        self.assertFalse(artifacts.is_verified(observation_ref))
        score = score_image_observation(
            consumer.observation,
            ("税额必须使用十进制 ROUND_HALF_UP 保留两位",),
        )
        self.assertEqual(score.f1, 1.0)

    def test_vision_operation_is_required_before_model_call(self):
        result, _, client, _ = self.execute(operations=("read",))

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_vision_capability_is_checked_before_model_call(self):
        result, _, client, _ = self.execute(capabilities={
            ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT,
        })

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_payload_hash_mismatch_is_rejected_before_model_call(self):
        result, _, client, _ = self.execute(payload=PNG + b"tampered")

        self.assertFalse(result.succeeded)
        self.assertEqual(client.requests, [])

    def test_model_cannot_add_acceptance_or_passed_fields(self):
        forged = valid_response()
        forged["passed"] = True
        forged["acceptance_criteria"] = ["trust model"]

        result, _, client, _ = self.execute(response=forged)

        self.assertFalse(result.succeeded)
        self.assertEqual(len(client.requests), 1)

    def test_accuracy_score_penalizes_missing_and_hallucinated_observations(self):
        forged = valid_response()
        forged["observations"].append({
            "statement": "必须调用外部税务 API",
            "region": "不存在的脚注",
            "evidence": "模型声称看见",
        })
        result, artifacts, _, _ = self.execute(response=forged)
        observation = ImageObservation.from_dict(
            artifacts.get(
                result.snapshot.artifacts["visual_observation"]
            ).content
        )

        score = score_image_observation(observation, (
            "税额必须使用十进制 ROUND_HALF_UP 保留两位",
            "金额输入允许小数",
        ))

        self.assertEqual(score.matched, 1)
        self.assertEqual(score.precision, 0.5)
        self.assertEqual(score.recall, 0.5)
        self.assertEqual(score.f1, 0.5)

    def test_image_and_text_paths_share_identical_hidden_validator(self):
        suite_root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        task = FixedCodingSuite.load(suite_root).task("python-tax-rounding")
        outcomes = []
        validator_sets = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for modality in ("text", "image"):
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
