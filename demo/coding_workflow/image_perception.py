from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Callable, Mapping

from .artifacts import ArtifactDraft
from .harness.executor import TaskRunRequest, TaskRunResult
from .harness.registry import WorkerDescriptor, WorkerRegistry
from .model import (
    ImageContentPart,
    ModelCapability,
    ModelClient,
    ModelMessage,
    ModelRequest,
    TextContentPart,
    require_capabilities,
)
from .requirements import EvidenceModality, RequirementEvidence
from .truth import Claim, ClaimKind


IMAGE_PERCEPTION_PROTOCOL_VERSION = "1.0"
REQUIREMENT_IMAGE_KIND = "core:requirement_image"
IMAGE_OBSERVATION_KIND = "core:image_observation"


IMAGE_PERCEPTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "summary", "observations", "inferences",
        "unreadable_regions",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "summary": {"type": "string", "minLength": 1},
        "observations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "region", "evidence"],
                "properties": {
                    "statement": {"type": "string", "minLength": 1},
                    "region": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string", "minLength": 1},
                },
            },
        },
        "inferences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "evidence", "uncertainty"],
                "properties": {
                    "statement": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string", "minLength": 1},
                    "uncertainty": {"type": "string", "minLength": 1},
                },
            },
        },
        "unreadable_regions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


class ImagePerceptionError(RuntimeError):
    pass


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImagePerceptionError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _exact(
    value: object, fields: set[str], field_name: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ImagePerceptionError(f"{field_name} 必须是对象")
    keys = set(value)
    if keys != fields:
        raise ImagePerceptionError(
            f"{field_name} 字段不匹配，缺少 {sorted(fields - keys)}，"
            f"多出 {sorted(keys - fields)}"
        )
    return value


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ImagePerceptionError(f"{field_name} 必须是字符串数组")
    result = tuple(_nonempty(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ImagePerceptionError(f"{field_name} 不能重复")
    return result


@dataclass(frozen=True)
class ImageObservation:
    source_evidence_ref: str
    summary: str
    claims: tuple[Claim, ...]
    unreadable_regions: tuple[str, ...] = ()
    schema_version: str = IMAGE_PERCEPTION_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.source_evidence_ref, str) or not (
            self.source_evidence_ref.startswith("artifact://")
        ):
            raise ValueError("ImageObservation 必须引用原始 Artifact")
        summary = self.summary.strip() if isinstance(self.summary, str) else ""
        claims = tuple(self.claims) if isinstance(self.claims, (tuple, list)) else ()
        if not summary or not claims:
            raise ValueError("ImageObservation 摘要和 claims 不能为空")
        if not all(isinstance(item, Claim) for item in claims):
            raise ValueError("ImageObservation claims 类型无效")
        if any(
            self.source_evidence_ref not in item.evidence_refs
            for item in claims
        ):
            raise ValueError("每个视觉 Claim 都必须引用原始图片")
        if not isinstance(self.unreadable_regions, (tuple, list)):
            raise ValueError("unreadable_regions 必须是数组")
        regions = tuple(
            item.strip() if isinstance(item, str) else ""
            for item in self.unreadable_regions
        )
        if any(not item for item in regions) or len(regions) != len(set(regions)):
            raise ValueError("unreadable_regions 不能包含空值或重复项")
        if self.schema_version != IMAGE_PERCEPTION_PROTOCOL_VERSION:
            raise ValueError("ImageObservation schema_version 无效")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "unreadable_regions", regions)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "source_evidence_ref": self.source_evidence_ref,
            "summary": self.summary,
            "claims": [dict(item.to_dict()) for item in self.claims],
            "unreadable_regions": self.unreadable_regions,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ImageObservation":
        if set(value) != {
            "schema_version", "source_evidence_ref", "summary", "claims",
            "unreadable_regions",
        }:
            raise ValueError("ImageObservation 字段无效")
        claims = value["claims"]
        if not isinstance(claims, (tuple, list)):
            raise ValueError("ImageObservation claims 必须是数组")
        return cls(
            value["source_evidence_ref"],
            value["summary"],
            tuple(Claim.from_dict(item) for item in claims),
            value["unreadable_regions"],
            value["schema_version"],
        )


def _parse_model_observation(
    value: Mapping[str, object],
    *,
    source_evidence_ref: str,
    source: str,
) -> ImageObservation:
    root = _exact(
        value,
        {
            "schema_version", "summary", "observations", "inferences",
            "unreadable_regions",
        },
        "ImagePerception",
    )
    if root["schema_version"] != IMAGE_PERCEPTION_PROTOCOL_VERSION:
        raise ImagePerceptionError("ImagePerception schema_version 无效")
    raw_observations = root["observations"]
    if not isinstance(raw_observations, list) or not raw_observations:
        raise ImagePerceptionError("observations 必须是非空数组")
    claims: list[Claim] = []
    for index, raw in enumerate(raw_observations):
        item = _exact(
            raw, {"statement", "region", "evidence"},
            f"observations[{index}]",
        )
        statement = _nonempty(item["statement"], "statement")
        region = _nonempty(item["region"], "region")
        evidence = _nonempty(item["evidence"], "evidence")
        claims.append(Claim.create(
            ClaimKind.OBSERVATION,
            f"[{region}] {statement}；可见依据：{evidence}",
            source,
            evidence_refs=(source_evidence_ref,),
        ))
    raw_inferences = root["inferences"]
    if not isinstance(raw_inferences, list):
        raise ImagePerceptionError("inferences 必须是数组")
    for index, raw in enumerate(raw_inferences):
        item = _exact(
            raw, {"statement", "evidence", "uncertainty"},
            f"inferences[{index}]",
        )
        claims.append(Claim.create(
            ClaimKind.INFERENCE,
            _nonempty(item["statement"], "statement"),
            source,
            evidence_refs=(source_evidence_ref,),
            uncertainty=(
                f"依据：{_nonempty(item['evidence'], 'evidence')}；"
                f"不确定性：{_nonempty(item['uncertainty'], 'uncertainty')}"
            ),
        ))
    return ImageObservation(
        source_evidence_ref,
        _nonempty(root["summary"], "summary"),
        tuple(claims),
        _strings(root["unreadable_regions"], "unreadable_regions"),
    )


class ImagePerceptionWorker:
    """将获授权图片转换为通用 Claim Artifact，不修改需求或验收。"""

    def __init__(
        self,
        client: ModelClient,
        evidence: Mapping[str, RequirementEvidence],
        payload_resolver: Callable[[str], bytes],
        *,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes 必须大于 0")
        self.client = client
        self.evidence = MappingProxyType(dict(evidence))
        self.payload_resolver = payload_resolver
        self.max_image_bytes = max_image_bytes
        self.requests: list[ModelRequest] = []

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        if request.task.role != "planner":
            raise PermissionError("ImagePerceptionWorker 只能执行 planner Role")
        if len(request.inputs) != 1 or len(request.task.output_artifacts) != 1:
            raise ImagePerceptionError("视觉感知节点必须恰好有一个输入和输出")
        artifact = next(iter(request.inputs.values()))
        reference = f"artifact://{artifact.artifact_id}"
        if artifact.kind != REQUIREMENT_IMAGE_KIND:
            raise ImagePerceptionError("输入必须是 core:requirement_image")
        if artifact.task_id != request.parent.task_id:
            raise PermissionError("图片 Artifact 不属于当前任务")
        evidence = self.evidence.get(reference)
        if evidence is None:
            raise PermissionError("图片缺少 RequirementEvidence 声明")
        if evidence.modality is not EvidenceModality.IMAGE:
            raise ImagePerceptionError("RequirementEvidence 不是图片")
        evidence.validate_artifact(artifact)
        grant = request.evidence_grant
        if grant is None or not grant.allows(
            task_id=request.task.task_id,
            role=request.task.role,
            evidence_ref=reference,
            operation="vision:inspect",
        ):
            raise PermissionError("图片缺少 vision:inspect 授权")
        data = self.payload_resolver(reference)
        if not isinstance(data, bytes) or not data:
            raise ImagePerceptionError("图片 Payload 为空")
        if len(data) > self.max_image_bytes or len(data) != evidence.size_bytes:
            raise ImagePerceptionError("图片 Payload 大小无效")
        if sha256(data).hexdigest() != evidence.content_hash:
            raise ImagePerceptionError("图片 Payload 哈希不匹配")
        if evidence.mime_type == "image/png":
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ImagePerceptionError("PNG 签名无效")
        elif evidence.mime_type == "image/jpeg":
            if not data.startswith(b"\xff\xd8"):
                raise ImagePerceptionError("JPEG 签名无效")
        else:
            raise ImagePerceptionError("视觉模型输入只支持 PNG/JPEG")

        model_request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    "你是通用 Coding Harness 的图片感知 Worker。只提取图片中直接可见的"
                    "文本、规格、错误、架构节点和关系；推测必须放入 inferences 并明确"
                    "不确定性。不得生成代码、文件路径、命令、权限、验收条件或 passed/"
                    "verified 字段。看不清的内容写入 unreadable_regions。"
                ),)),
                ModelMessage("user", (
                    TextContentPart(
                        "把这张需求或问题证据图片转换成结构化 observation Artifact。"
                    ),
                    ImageContentPart(
                        reference, evidence.mime_type, data, "high"
                    ),
                )),
            ),
            frozenset({
                ModelCapability.TEXT,
                ModelCapability.VISION,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            IMAGE_PERCEPTION_SCHEMA,
        )
        require_capabilities(
            self.client.capabilities, model_request.required_capabilities
        )
        self.requests.append(model_request)
        response = self.client.generate_structured(model_request)
        observation = _parse_model_observation(
            response.data,
            source_evidence_ref=reference,
            source=f"model:{response.provider}/{response.model}:image_perception",
        )
        output_name = request.task.output_artifacts[0]
        return TaskRunResult(
            True,
            observation.summary,
            {output_name: ArtifactDraft(
                observation.to_dict(),
                kind=IMAGE_OBSERVATION_KIND,
                metadata={
                    "source_evidence_ref": reference,
                    "protocol_version": IMAGE_PERCEPTION_PROTOCOL_VERSION,
                    "provider": response.provider,
                    "model": response.model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "latency_ms": response.latency_ms,
                },
            )},
        )


def build_image_perception_registry(
    worker: ImagePerceptionWorker,
) -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.register_worker(
        WorkerDescriptor(
            "core-image-perception",
            "planner",
            frozenset({"vision_understanding"}),
            frozenset({REQUIREMENT_IMAGE_KIND}),
            frozenset({IMAGE_OBSERVATION_KIND}),
            frozenset({"multimodal"}),
            principal_id="core-image-perception-principal",
        ),
        worker,
    )
    return registry


def _normalized_statement(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _claim_visible_statement(value: str) -> str:
    match = re.fullmatch(r"\[[^]]+\] (.+)；可见依据：.+", value)
    return match.group(1) if match else value


@dataclass(frozen=True)
class PerceptionScore:
    expected: int
    reported: int
    matched: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "expected": self.expected,
            "reported": self.reported,
            "matched": self.matched,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        })


def score_image_observation(
    observation: ImageObservation,
    expected_visible_statements: tuple[str, ...],
) -> PerceptionScore:
    expected = {_normalized_statement(item) for item in expected_visible_statements}
    if not expected or "" in expected:
        raise ValueError("expected_visible_statements 不能为空")
    reported = {
        _normalized_statement(_claim_visible_statement(item.statement))
        for item in observation.claims
        if item.kind is ClaimKind.OBSERVATION
    }
    matched = len(expected.intersection(reported))
    precision = matched / len(reported) if reported else 0.0
    recall = matched / len(expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    return PerceptionScore(
        len(expected), len(reported), matched, precision, recall, f1
    )
