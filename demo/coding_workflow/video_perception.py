from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from .artifacts import ArtifactDraft
from .harness.executor import TaskRunRequest, TaskRunResult
from .harness.registry import WorkerDescriptor, WorkerRegistry
from .requirements import EvidenceModality, RequirementEvidence
from .truth import Claim, ClaimKind


VIDEO_EVIDENCE_PROTOCOL_VERSION = "1.0"
REQUIREMENT_VIDEO_KIND = "core:requirement_video"
VIDEO_BUG_EVIDENCE_KIND = "core:video_bug_evidence"


class VideoPerceptionError(RuntimeError):
    pass


class VideoAnalysisCapability(str, Enum):
    VIDEO_UNDERSTANDING = "video_understanding"
    TIMESTAMPS = "timestamps"
    AUDIO_TRACK = "audio_track"


class VideoEventKind(str, Enum):
    USER_ACTION = "user_action"
    SYSTEM_RESPONSE = "system_response"
    OBSERVABLE_STATE = "observable_state"
    ERROR_SIGNAL = "error_signal"
    SPOKEN_STATEMENT = "spoken_statement"


class VideoEventCertainty(str, Enum):
    CLEAR = "clear"
    UNCERTAIN = "uncertain"


class ExpectedBasis(str, Enum):
    VISIBLE = "visible"
    SPOKEN = "spoken"
    INFERRED = "inferred"


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value.strip()


def _optional(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value.strip()


def _artifact_ref(value: object, field_name: str) -> str:
    parsed = _nonempty(value, field_name)
    if not parsed.startswith("artifact://"):
        raise ValueError(f"{field_name} 必须使用 artifact:// 引用")
    return parsed


def _milliseconds(value: object, field_name: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是整数")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{field_name} 范围无效")
    return value


def _identifier_refs(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{field_name} 必须是字符串数组")
    result = tuple(_nonempty(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} 不能重复")
    return result


@dataclass(frozen=True)
class VideoAnalysisRequest:
    source_evidence_ref: str
    mime_type: str
    video: bytes

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_evidence_ref",
            _artifact_ref(self.source_evidence_ref, "source_evidence_ref"),
        )
        mime_type = _nonempty(self.mime_type, "mime_type").lower()
        if not mime_type.startswith("video/"):
            raise ValueError("VideoAnalysisRequest MIME 必须是视频")
        object.__setattr__(self, "mime_type", mime_type)
        if not isinstance(self.video, bytes) or not self.video:
            raise ValueError("VideoAnalysisRequest video 不能为空")


@dataclass(frozen=True)
class VideoEvent:
    event_id: str
    start_ms: int
    end_ms: int
    kind: VideoEventKind
    description: str
    region: str
    certainty: VideoEventCertainty = VideoEventCertainty.CLEAR
    uncertainty: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _nonempty(self.event_id, "event_id"))
        start = _milliseconds(self.start_ms, "start_ms")
        end = _milliseconds(self.end_ms, "end_ms", positive=True)
        if end <= start:
            raise ValueError("VideoEvent end_ms 必须大于 start_ms")
        try:
            kind = self.kind if isinstance(self.kind, VideoEventKind) else VideoEventKind(
                self.kind
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("VideoEvent kind 无效") from exc
        try:
            certainty = (
                self.certainty
                if isinstance(self.certainty, VideoEventCertainty)
                else VideoEventCertainty(self.certainty)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("VideoEvent certainty 无效") from exc
        uncertainty = _optional(self.uncertainty, "uncertainty")
        if certainty is VideoEventCertainty.UNCERTAIN and not uncertainty:
            raise ValueError("不确定视频事件必须说明 uncertainty")
        if certainty is VideoEventCertainty.CLEAR and uncertainty:
            raise ValueError("清晰视频事件不能携带 uncertainty")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "description", _nonempty(
            self.description, "description"
        ))
        object.__setattr__(self, "region", _nonempty(self.region, "region"))
        object.__setattr__(self, "certainty", certainty)
        object.__setattr__(self, "uncertainty", uncertainty)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "event_id": self.event_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "kind": self.kind.value,
            "description": self.description,
            "region": self.region,
            "certainty": self.certainty.value,
            "uncertainty": self.uncertainty,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VideoEvent":
        expected = {
            "event_id", "start_ms", "end_ms", "kind", "description",
            "region", "certainty", "uncertainty",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("VideoEvent 字段无效")
        return cls(
            value["event_id"], value["start_ms"], value["end_ms"],
            value["kind"], value["description"], value["region"],
            value["certainty"], value["uncertainty"],
        )


@dataclass(frozen=True)
class CandidateReproductionStep:
    step_id: str
    sequence: int
    instruction: str
    supporting_event_ids: tuple[str, ...]
    uncertainty: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _nonempty(self.step_id, "step_id"))
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise ValueError("sequence 必须是整数")
        if self.sequence <= 0:
            raise ValueError("sequence 必须大于 0")
        object.__setattr__(self, "instruction", _nonempty(
            self.instruction, "instruction"
        ))
        refs = _identifier_refs(self.supporting_event_ids, "supporting_event_ids")
        if not refs:
            raise ValueError("候选复现步骤必须引用视频事件")
        object.__setattr__(self, "supporting_event_ids", refs)
        object.__setattr__(
            self, "uncertainty", _optional(self.uncertainty, "uncertainty")
        )

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "step_id": self.step_id,
            "sequence": self.sequence,
            "instruction": self.instruction,
            "supporting_event_ids": self.supporting_event_ids,
            "uncertainty": self.uncertainty,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateReproductionStep":
        expected = {
            "step_id", "sequence", "instruction", "supporting_event_ids",
            "uncertainty",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("CandidateReproductionStep 字段无效")
        return cls(
            value["step_id"], value["sequence"], value["instruction"],
            value["supporting_event_ids"], value["uncertainty"],
        )


@dataclass(frozen=True)
class ObservedDiscrepancy:
    discrepancy_id: str
    expected: str
    expected_basis: ExpectedBasis
    expected_event_ids: tuple[str, ...]
    actual_event_ids: tuple[str, ...]
    uncertainty: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "discrepancy_id",
            _nonempty(self.discrepancy_id, "discrepancy_id"),
        )
        object.__setattr__(self, "expected", _nonempty(self.expected, "expected"))
        try:
            basis = (
                self.expected_basis
                if isinstance(self.expected_basis, ExpectedBasis)
                else ExpectedBasis(self.expected_basis)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_basis 无效") from exc
        expected_refs = _identifier_refs(
            self.expected_event_ids, "expected_event_ids"
        )
        actual_refs = _identifier_refs(self.actual_event_ids, "actual_event_ids")
        if not actual_refs:
            raise ValueError("差异必须引用实际视频事件")
        uncertainty = _optional(self.uncertainty, "uncertainty")
        if basis is ExpectedBasis.INFERRED:
            if expected_refs or not uncertainty:
                raise ValueError("推测预期不能伪造来源，且必须说明 uncertainty")
        elif not expected_refs or uncertainty:
            raise ValueError("画面/旁白预期必须引用事件且不能标成模型推测")
        object.__setattr__(self, "expected_basis", basis)
        object.__setattr__(self, "expected_event_ids", expected_refs)
        object.__setattr__(self, "actual_event_ids", actual_refs)
        object.__setattr__(self, "uncertainty", uncertainty)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "discrepancy_id": self.discrepancy_id,
            "expected": self.expected,
            "expected_basis": self.expected_basis.value,
            "expected_event_ids": self.expected_event_ids,
            "actual_event_ids": self.actual_event_ids,
            "uncertainty": self.uncertainty,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ObservedDiscrepancy":
        expected = {
            "discrepancy_id", "expected", "expected_basis",
            "expected_event_ids", "actual_event_ids", "uncertainty",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("ObservedDiscrepancy 字段无效")
        return cls(
            value["discrepancy_id"], value["expected"],
            value["expected_basis"], value["expected_event_ids"],
            value["actual_event_ids"], value["uncertainty"],
        )


@dataclass(frozen=True)
class UnreviewedVideoRange:
    start_ms: int
    end_ms: int
    reason: str

    def __post_init__(self) -> None:
        start = _milliseconds(self.start_ms, "start_ms")
        end = _milliseconds(self.end_ms, "end_ms", positive=True)
        if end <= start:
            raise ValueError("UnreviewedVideoRange end_ms 必须大于 start_ms")
        object.__setattr__(self, "reason", _nonempty(self.reason, "reason"))

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "reason": self.reason,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "UnreviewedVideoRange":
        if not isinstance(value, Mapping) or set(value) != {
            "start_ms", "end_ms", "reason",
        }:
            raise ValueError("UnreviewedVideoRange 字段无效")
        return cls(value["start_ms"], value["end_ms"], value["reason"])


def _validate_video_evidence(
    events: tuple[VideoEvent, ...],
    steps: tuple[CandidateReproductionStep, ...],
    discrepancies: tuple[ObservedDiscrepancy, ...],
    unreviewed: tuple[UnreviewedVideoRange, ...],
    duration_ms: int,
) -> None:
    if not events or not all(isinstance(item, VideoEvent) for item in events):
        raise ValueError("events 必须是非空 VideoEvent 数组")
    if not all(isinstance(item, CandidateReproductionStep) for item in steps):
        raise ValueError("candidate_steps 类型无效")
    if not all(isinstance(item, ObservedDiscrepancy) for item in discrepancies):
        raise ValueError("discrepancies 类型无效")
    if not all(isinstance(item, UnreviewedVideoRange) for item in unreviewed):
        raise ValueError("unreviewed_ranges 类型无效")
    event_ids = tuple(item.event_id for item in events)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("VideoEvent ID 不能重复")
    if tuple(events) != tuple(sorted(
        events, key=lambda item: (item.start_ms, item.end_ms, item.event_id)
    )):
        raise ValueError("视频事件必须按时间排序")
    if any(item.end_ms > duration_ms for item in events):
        raise ValueError("视频事件超出视频时长")
    step_ids = tuple(item.step_id for item in steps)
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("候选复现步骤 ID 不能重复")
    if tuple(item.sequence for item in steps) != tuple(range(1, len(steps) + 1)):
        raise ValueError("候选复现步骤 sequence 必须从 1 连续递增")
    known = set(event_ids)
    for step in steps:
        if not set(step.supporting_event_ids).issubset(known):
            raise ValueError("候选复现步骤引用未知视频事件")
    discrepancy_ids = tuple(item.discrepancy_id for item in discrepancies)
    if len(discrepancy_ids) != len(set(discrepancy_ids)):
        raise ValueError("差异 ID 不能重复")
    for item in discrepancies:
        if not set(item.expected_event_ids + item.actual_event_ids).issubset(known):
            raise ValueError("差异引用未知视频事件")
        expected_kinds = {
            event.kind for event in events if event.event_id in item.expected_event_ids
        }
        if item.expected_basis is ExpectedBasis.SPOKEN and expected_kinds != {
            VideoEventKind.SPOKEN_STATEMENT
        }:
            raise ValueError("spoken 预期必须只引用旁白事件")
        if item.expected_basis is ExpectedBasis.VISIBLE and any(
            kind is VideoEventKind.SPOKEN_STATEMENT for kind in expected_kinds
        ):
            raise ValueError("visible 预期不能引用旁白事件")
    previous_end = 0
    for item in unreviewed:
        if item.start_ms < previous_end:
            raise ValueError("未审查区间必须按时间排序且不能重叠")
        if item.end_ms > duration_ms:
            raise ValueError("未审查区间超出视频时长")
        if any(
            item.start_ms < event.end_ms and event.start_ms < item.end_ms
            for event in events
        ):
            raise ValueError("未审查区间不能包含已报告事件")
        previous_end = item.end_ms


@dataclass(frozen=True)
class VideoAnalysisResponse:
    provider: str
    model: str
    duration_ms: int
    events: tuple[VideoEvent, ...]
    candidate_steps: tuple[CandidateReproductionStep, ...] = ()
    discrepancies: tuple[ObservedDiscrepancy, ...] = ()
    unreviewed_ranges: tuple[UnreviewedVideoRange, ...] = ()
    latency_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _nonempty(self.provider, "provider"))
        object.__setattr__(self, "model", _nonempty(self.model, "model"))
        duration = _milliseconds(self.duration_ms, "duration_ms", positive=True)
        events = tuple(self.events)
        steps = tuple(self.candidate_steps)
        discrepancies = tuple(self.discrepancies)
        unreviewed = tuple(self.unreviewed_ranges)
        _validate_video_evidence(
            events, steps, discrepancies, unreviewed, duration
        )
        _milliseconds(self.latency_ms, "latency_ms")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "candidate_steps", steps)
        object.__setattr__(self, "discrepancies", discrepancies)
        object.__setattr__(self, "unreviewed_ranges", unreviewed)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VideoAnalysisResponse":
        expected = {
            "provider", "model", "duration_ms", "events", "candidate_steps",
            "discrepancies", "unreviewed_ranges", "latency_ms",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("VideoAnalysisResponse 字段无效")
        arrays = (
            value["events"], value["candidate_steps"],
            value["discrepancies"], value["unreviewed_ranges"],
        )
        if not all(isinstance(item, (tuple, list)) for item in arrays):
            raise ValueError("VideoAnalysisResponse 时间线字段必须是数组")
        return cls(
            value["provider"], value["model"], value["duration_ms"],
            tuple(VideoEvent.from_dict(item) for item in value["events"]),
            tuple(CandidateReproductionStep.from_dict(item)
                  for item in value["candidate_steps"]),
            tuple(ObservedDiscrepancy.from_dict(item)
                  for item in value["discrepancies"]),
            tuple(UnreviewedVideoRange.from_dict(item)
                  for item in value["unreviewed_ranges"]),
            value["latency_ms"],
        )


class VideoPerceptionClient(Protocol):
    """供应商无关的视频时间理解边界。"""

    @property
    def capabilities(self) -> frozenset[VideoAnalysisCapability]: ...

    def analyze(self, request: VideoAnalysisRequest) -> VideoAnalysisResponse: ...


def _event_statement(event: VideoEvent) -> str:
    return (
        f"[{event.start_ms}-{event.end_ms}ms][{event.kind.value}] "
        f"{event.description}；区域：{event.region}"
    )


def _discrepancy_statement(
    discrepancy: ObservedDiscrepancy,
    events: Mapping[str, VideoEvent],
) -> str:
    actual = "；".join(events[item].description for item in discrepancy.actual_event_ids)
    return f"预期：{discrepancy.expected}；实际：{actual}"


def _discrepancy_uncertainty(discrepancy: ObservedDiscrepancy) -> str:
    if discrepancy.expected_basis is ExpectedBasis.INFERRED:
        return discrepancy.uncertainty
    return (
        f"预期来自录像中的 {discrepancy.expected_basis.value} 证据，"
        "仍需 Runtime 复现验证"
    )


@dataclass(frozen=True)
class VideoBugEvidence:
    source_evidence_ref: str
    duration_ms: int
    events: tuple[VideoEvent, ...]
    candidate_steps: tuple[CandidateReproductionStep, ...]
    discrepancies: tuple[ObservedDiscrepancy, ...]
    claims: tuple[Claim, ...]
    unreviewed_ranges: tuple[UnreviewedVideoRange, ...] = ()
    schema_version: str = VIDEO_EVIDENCE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        source = _artifact_ref(self.source_evidence_ref, "source_evidence_ref")
        duration = _milliseconds(self.duration_ms, "duration_ms", positive=True)
        events = tuple(self.events)
        steps = tuple(self.candidate_steps)
        discrepancies = tuple(self.discrepancies)
        claims = tuple(self.claims)
        unreviewed = tuple(self.unreviewed_ranges)
        _validate_video_evidence(
            events, steps, discrepancies, unreviewed, duration
        )
        expected_count = len(events) + len(discrepancies) + len(steps)
        if len(claims) != expected_count or not all(
            isinstance(item, Claim) for item in claims
        ):
            raise ValueError("视频事件、差异和复现步骤必须一一对应 Claim")
        event_map = {item.event_id: item for item in events}
        expected_claims: list[tuple[ClaimKind, str, str]] = []
        expected_claims.extend(
            (ClaimKind.OBSERVATION, _event_statement(item), item.uncertainty)
            for item in events
        )
        expected_claims.extend(
            (
                ClaimKind.INFERENCE,
                _discrepancy_statement(item, event_map),
                _discrepancy_uncertainty(item),
            )
            for item in discrepancies
        )
        expected_claims.extend(
            (
                ClaimKind.PROPOSAL,
                f"候选复现步骤 {item.sequence}：{item.instruction}",
                item.uncertainty,
            )
            for item in steps
        )
        for claim, expected in zip(claims, expected_claims):
            kind, statement, uncertainty = expected
            if (
                claim.kind is not kind
                or claim.statement != statement
                or claim.uncertainty != uncertainty
                or source not in claim.evidence_refs
            ):
                raise ValueError("视频 Claim 与结构化证据不一致")
        if self.schema_version != VIDEO_EVIDENCE_PROTOCOL_VERSION:
            raise ValueError("VideoBugEvidence schema_version 无效")
        object.__setattr__(self, "source_evidence_ref", source)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "candidate_steps", steps)
        object.__setattr__(self, "discrepancies", discrepancies)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "unreviewed_ranges", unreviewed)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "source_evidence_ref": self.source_evidence_ref,
            "duration_ms": self.duration_ms,
            "events": [dict(item.to_dict()) for item in self.events],
            "candidate_steps": [
                dict(item.to_dict()) for item in self.candidate_steps
            ],
            "discrepancies": [
                dict(item.to_dict()) for item in self.discrepancies
            ],
            "claims": [dict(item.to_dict()) for item in self.claims],
            "unreviewed_ranges": [
                dict(item.to_dict()) for item in self.unreviewed_ranges
            ],
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VideoBugEvidence":
        expected = {
            "schema_version", "source_evidence_ref", "duration_ms", "events",
            "candidate_steps", "discrepancies", "claims", "unreviewed_ranges",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("VideoBugEvidence 字段无效")
        arrays = tuple(value[name] for name in (
            "events", "candidate_steps", "discrepancies", "claims",
            "unreviewed_ranges",
        ))
        if not all(isinstance(item, (tuple, list)) for item in arrays):
            raise ValueError("VideoBugEvidence 数组字段无效")
        return cls(
            value["source_evidence_ref"],
            value["duration_ms"],
            tuple(VideoEvent.from_dict(item) for item in value["events"]),
            tuple(CandidateReproductionStep.from_dict(item)
                  for item in value["candidate_steps"]),
            tuple(ObservedDiscrepancy.from_dict(item)
                  for item in value["discrepancies"]),
            tuple(Claim.from_dict(item) for item in value["claims"]),
            tuple(UnreviewedVideoRange.from_dict(item)
                  for item in value["unreviewed_ranges"]),
            value["schema_version"],
        )


def _video_signature_is_valid(mime_type: str, data: bytes) -> bool:
    if mime_type == "video/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if mime_type == "video/webm":
        return data.startswith(b"\x1aE\xdf\xa3")
    return False


class VideoPerceptionWorker:
    """把获授权录屏变成时间线和候选复现证据，不修改验收。"""

    REQUIRED_CAPABILITIES = frozenset({
        VideoAnalysisCapability.VIDEO_UNDERSTANDING,
        VideoAnalysisCapability.TIMESTAMPS,
    })

    def __init__(
        self,
        client: VideoPerceptionClient,
        evidence: Mapping[str, RequirementEvidence],
        payload_resolver: Callable[[str], bytes],
        *,
        max_video_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if max_video_bytes <= 0:
            raise ValueError("max_video_bytes 必须大于 0")
        self.client = client
        self.evidence = MappingProxyType(dict(evidence))
        self.payload_resolver = payload_resolver
        self.max_video_bytes = max_video_bytes
        self.requests: list[VideoAnalysisRequest] = []

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        if request.task.role != "planner":
            raise PermissionError("VideoPerceptionWorker 只能执行 planner Role")
        if len(request.inputs) != 1 or len(request.task.output_artifacts) != 1:
            raise VideoPerceptionError("视频感知节点必须恰好有一个输入和输出")
        artifact = next(iter(request.inputs.values()))
        reference = f"artifact://{artifact.artifact_id}"
        if artifact.kind != REQUIREMENT_VIDEO_KIND:
            raise VideoPerceptionError("输入必须是 core:requirement_video")
        if artifact.task_id != request.parent.task_id:
            raise PermissionError("视频 Artifact 不属于当前任务")
        evidence = self.evidence.get(reference)
        if evidence is None:
            raise PermissionError("视频缺少 RequirementEvidence 声明")
        if evidence.modality is not EvidenceModality.VIDEO:
            raise VideoPerceptionError("RequirementEvidence 不是视频")
        evidence.validate_artifact(artifact)
        grant = request.evidence_grant
        if grant is None or not grant.allows(
            task_id=request.task.task_id,
            role=request.task.role,
            evidence_ref=reference,
            operation="video:inspect",
        ):
            raise PermissionError("视频缺少 video:inspect 授权")
        data = self.payload_resolver(reference)
        if not isinstance(data, bytes) or not data:
            raise VideoPerceptionError("视频 Payload 为空")
        if len(data) > self.max_video_bytes or len(data) != evidence.size_bytes:
            raise VideoPerceptionError("视频 Payload 大小无效")
        if sha256(data).hexdigest() != evidence.content_hash:
            raise VideoPerceptionError("视频 Payload 哈希不匹配")
        if not _video_signature_is_valid(evidence.mime_type, data):
            raise VideoPerceptionError("视频格式或文件签名不受支持")
        capabilities = frozenset(self.client.capabilities)
        missing = self.REQUIRED_CAPABILITIES - capabilities
        if missing:
            raise VideoPerceptionError(
                f"视频客户端缺少能力: {sorted(item.value for item in missing)}"
            )
        video_request = VideoAnalysisRequest(reference, evidence.mime_type, data)
        self.requests.append(video_request)
        response = self.client.analyze(video_request)
        if not isinstance(response, VideoAnalysisResponse):
            raise VideoPerceptionError("视频客户端必须返回 VideoAnalysisResponse")
        source = f"video_perception:{response.provider}/{response.model}"
        event_map = {item.event_id: item for item in response.events}
        claims: list[Claim] = [
            Claim.create(
                ClaimKind.OBSERVATION,
                _event_statement(event),
                source,
                evidence_refs=(reference,),
                uncertainty=event.uncertainty,
            )
            for event in response.events
        ]
        claims.extend(
            Claim.create(
                ClaimKind.INFERENCE,
                _discrepancy_statement(item, event_map),
                source,
                evidence_refs=(reference,),
                uncertainty=_discrepancy_uncertainty(item),
            )
            for item in response.discrepancies
        )
        claims.extend(
            Claim.create(
                ClaimKind.PROPOSAL,
                f"候选复现步骤 {item.sequence}：{item.instruction}",
                source,
                evidence_refs=(reference,),
                uncertainty=item.uncertainty,
            )
            for item in response.candidate_steps
        )
        result = VideoBugEvidence(
            reference,
            response.duration_ms,
            response.events,
            response.candidate_steps,
            response.discrepancies,
            tuple(claims),
            response.unreviewed_ranges,
        )
        output_name = request.task.output_artifacts[0]
        return TaskRunResult(
            True,
            f"已生成 {len(response.events)} 个视频事件和 "
            f"{len(response.candidate_steps)} 个候选复现步骤",
            {output_name: ArtifactDraft(
                result.to_dict(),
                kind=VIDEO_BUG_EVIDENCE_KIND,
                metadata={
                    "source_evidence_ref": reference,
                    "source_content_hash": evidence.content_hash,
                    "protocol_version": VIDEO_EVIDENCE_PROTOCOL_VERSION,
                    "provider": response.provider,
                    "model": response.model,
                    "duration_ms": response.duration_ms,
                    "latency_ms": response.latency_ms,
                },
            )},
        )


def build_video_perception_registry(worker: VideoPerceptionWorker) -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.register_worker(
        WorkerDescriptor(
            "core-video-perception",
            "planner",
            frozenset({"video_temporal_understanding"}),
            frozenset({REQUIREMENT_VIDEO_KIND}),
            frozenset({VIDEO_BUG_EVIDENCE_KIND}),
            frozenset({"multimodal"}),
            principal_id="core-video-perception-principal",
        ),
        worker,
    )
    return registry


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


@dataclass(frozen=True)
class VideoEvidenceScore:
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


def score_video_events(
    evidence: VideoBugEvidence,
    expected_event_descriptions: tuple[str, ...],
) -> VideoEvidenceScore:
    expected = {_normalized_text(item) for item in expected_event_descriptions}
    if not expected or "" in expected:
        raise ValueError("expected_event_descriptions 不能为空")
    reported = {_normalized_text(item.description) for item in evidence.events}
    matched = len(expected.intersection(reported))
    precision = matched / len(reported) if reported else 0.0
    recall = matched / len(expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    return VideoEvidenceScore(
        len(expected), len(reported), matched, precision, recall, f1
    )
