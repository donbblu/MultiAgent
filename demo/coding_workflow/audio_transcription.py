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


AUDIO_TRANSCRIPT_PROTOCOL_VERSION = "1.0"
REQUIREMENT_AUDIO_KIND = "core:requirement_audio"
AUDIO_TRANSCRIPT_KIND = "core:audio_transcript"


class AudioTranscriptionError(RuntimeError):
    pass


class TranscriptionCapability(str, Enum):
    TRANSCRIPTION = "transcription"
    TIMESTAMPS = "timestamps"
    LANGUAGE_DETECTION = "language_detection"


class TranscriptCertainty(str, Enum):
    CLEAR = "clear"
    UNCERTAIN = "uncertain"


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


def _language(value: object) -> str:
    parsed = _nonempty(value, "language")
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|und", parsed):
        raise ValueError("language 格式无效")
    return parsed


@dataclass(frozen=True)
class TranscriptionRequest:
    source_evidence_ref: str
    mime_type: str
    audio: bytes
    language_hint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_evidence_ref",
            _artifact_ref(self.source_evidence_ref, "source_evidence_ref"),
        )
        mime_type = _nonempty(self.mime_type, "mime_type").lower()
        if not mime_type.startswith("audio/"):
            raise ValueError("TranscriptionRequest MIME 必须是音频")
        object.__setattr__(self, "mime_type", mime_type)
        if not isinstance(self.audio, bytes) or not self.audio:
            raise ValueError("TranscriptionRequest audio 不能为空")
        object.__setattr__(
            self, "language_hint", _optional(self.language_hint, "language_hint")
        )


@dataclass(frozen=True)
class TranscriptSegment:
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    certainty: TranscriptCertainty = TranscriptCertainty.CLEAR
    uncertainty: str = ""
    speaker: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_id", _nonempty(self.segment_id, "segment_id"))
        start = _milliseconds(self.start_ms, "start_ms")
        end = _milliseconds(self.end_ms, "end_ms", positive=True)
        if end <= start:
            raise ValueError("TranscriptSegment end_ms 必须大于 start_ms")
        object.__setattr__(self, "text", _nonempty(self.text, "text"))
        try:
            certainty = (
                self.certainty
                if isinstance(self.certainty, TranscriptCertainty)
                else TranscriptCertainty(self.certainty)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("TranscriptSegment certainty 无效") from exc
        uncertainty = _optional(self.uncertainty, "uncertainty")
        if certainty is TranscriptCertainty.UNCERTAIN and not uncertainty:
            raise ValueError("不确定转录片段必须说明 uncertainty")
        if certainty is TranscriptCertainty.CLEAR and uncertainty:
            raise ValueError("清晰转录片段不能携带 uncertainty")
        object.__setattr__(self, "certainty", certainty)
        object.__setattr__(self, "uncertainty", uncertainty)
        object.__setattr__(self, "speaker", _optional(self.speaker, "speaker"))

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "segment_id": self.segment_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "certainty": self.certainty.value,
            "uncertainty": self.uncertainty,
            "speaker": self.speaker,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TranscriptSegment":
        expected = {
            "segment_id", "start_ms", "end_ms", "text", "certainty",
            "uncertainty", "speaker",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("TranscriptSegment 字段无效")
        return cls(
            value["segment_id"], value["start_ms"], value["end_ms"],
            value["text"], value["certainty"], value["uncertainty"],
            value["speaker"],
        )


@dataclass(frozen=True)
class UntranscribedRange:
    start_ms: int
    end_ms: int
    reason: str

    def __post_init__(self) -> None:
        start = _milliseconds(self.start_ms, "start_ms")
        end = _milliseconds(self.end_ms, "end_ms", positive=True)
        if end <= start:
            raise ValueError("UntranscribedRange end_ms 必须大于 start_ms")
        object.__setattr__(self, "reason", _nonempty(self.reason, "reason"))

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "reason": self.reason,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "UntranscribedRange":
        if not isinstance(value, Mapping) or set(value) != {
            "start_ms", "end_ms", "reason",
        }:
            raise ValueError("UntranscribedRange 字段无效")
        return cls(value["start_ms"], value["end_ms"], value["reason"])


def _validate_timeline(
    segments: tuple[TranscriptSegment, ...],
    ranges: tuple[UntranscribedRange, ...],
    duration_ms: int,
) -> None:
    if not all(isinstance(item, TranscriptSegment) for item in segments):
        raise ValueError("segments 类型无效")
    if not all(isinstance(item, UntranscribedRange) for item in ranges):
        raise ValueError("untranscribed_ranges 类型无效")
    if not segments:
        raise ValueError("转录至少需要一个片段")
    ids = tuple(item.segment_id for item in segments)
    if len(ids) != len(set(ids)):
        raise ValueError("TranscriptSegment ID 不能重复")
    previous_end = 0
    for segment in segments:
        if segment.start_ms < previous_end:
            raise ValueError("转录片段必须按时间排序且不能重叠")
        if segment.end_ms > duration_ms:
            raise ValueError("转录片段超出音频时长")
        previous_end = segment.end_ms
    previous_end = 0
    for item in ranges:
        if item.start_ms < previous_end:
            raise ValueError("未转录区间必须按时间排序且不能重叠")
        if item.end_ms > duration_ms:
            raise ValueError("未转录区间超出音频时长")
        if any(
            item.start_ms < segment.end_ms and segment.start_ms < item.end_ms
            for segment in segments
        ):
            raise ValueError("未转录区间不能与转录片段重叠")
        previous_end = item.end_ms


@dataclass(frozen=True)
class TranscriptionResponse:
    provider: str
    model: str
    language: str
    duration_ms: int
    segments: tuple[TranscriptSegment, ...]
    untranscribed_ranges: tuple[UntranscribedRange, ...] = ()
    latency_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _nonempty(self.provider, "provider"))
        object.__setattr__(self, "model", _nonempty(self.model, "model"))
        language = _language(self.language)
        object.__setattr__(self, "language", language)
        duration = _milliseconds(self.duration_ms, "duration_ms", positive=True)
        segments = tuple(self.segments)
        ranges = tuple(self.untranscribed_ranges)
        _validate_timeline(segments, ranges, duration)
        _milliseconds(self.latency_ms, "latency_ms")
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "untranscribed_ranges", ranges)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TranscriptionResponse":
        expected = {
            "provider", "model", "language", "duration_ms", "segments",
            "untranscribed_ranges", "latency_ms",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("TranscriptionResponse 字段无效")
        segments = value["segments"]
        ranges = value["untranscribed_ranges"]
        if not isinstance(segments, (tuple, list)) or not isinstance(
            ranges, (tuple, list)
        ):
            raise ValueError("TranscriptionResponse 时间线必须是数组")
        return cls(
            value["provider"], value["model"], value["language"],
            value["duration_ms"],
            tuple(TranscriptSegment.from_dict(item) for item in segments),
            tuple(UntranscribedRange.from_dict(item) for item in ranges),
            value["latency_ms"],
        )


class TranscriptionClient(Protocol):
    """供应商无关的音频转录边界；它不是文本 ModelClient。"""

    @property
    def capabilities(self) -> frozenset[TranscriptionCapability]: ...

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse: ...


@dataclass(frozen=True)
class AudioTranscript:
    source_evidence_ref: str
    language: str
    duration_ms: int
    segments: tuple[TranscriptSegment, ...]
    claims: tuple[Claim, ...]
    untranscribed_ranges: tuple[UntranscribedRange, ...] = ()
    schema_version: str = AUDIO_TRANSCRIPT_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        source = _artifact_ref(self.source_evidence_ref, "source_evidence_ref")
        language = _language(self.language)
        duration = _milliseconds(self.duration_ms, "duration_ms", positive=True)
        segments = tuple(self.segments)
        claims = tuple(self.claims)
        ranges = tuple(self.untranscribed_ranges)
        _validate_timeline(segments, ranges, duration)
        if len(claims) != len(segments) or not all(
            isinstance(item, Claim) for item in claims
        ):
            raise ValueError("每个转录片段必须对应一个 Claim")
        for segment, claim in zip(segments, claims):
            expected = f"[{segment.start_ms}-{segment.end_ms}ms] {segment.text}"
            if claim.kind is not ClaimKind.OBSERVATION or claim.statement != expected:
                raise ValueError("转录 Claim 必须忠实对应片段和时间范围")
            if source not in claim.evidence_refs:
                raise ValueError("转录 Claim 必须引用原始音频")
            if claim.uncertainty != segment.uncertainty:
                raise ValueError("转录 Claim uncertainty 与片段不一致")
        if self.schema_version != AUDIO_TRANSCRIPT_PROTOCOL_VERSION:
            raise ValueError("AudioTranscript schema_version 无效")
        object.__setattr__(self, "source_evidence_ref", source)
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "untranscribed_ranges", ranges)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "source_evidence_ref": self.source_evidence_ref,
            "language": self.language,
            "duration_ms": self.duration_ms,
            "segments": [dict(item.to_dict()) for item in self.segments],
            "claims": [dict(item.to_dict()) for item in self.claims],
            "untranscribed_ranges": [
                dict(item.to_dict()) for item in self.untranscribed_ranges
            ],
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AudioTranscript":
        expected = {
            "schema_version", "source_evidence_ref", "language", "duration_ms",
            "segments", "claims", "untranscribed_ranges",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("AudioTranscript 字段无效")
        segments = value["segments"]
        claims = value["claims"]
        ranges = value["untranscribed_ranges"]
        if not all(isinstance(item, (tuple, list)) for item in (
            segments, claims, ranges
        )):
            raise ValueError("AudioTranscript 数组字段无效")
        return cls(
            value["source_evidence_ref"], value["language"], value["duration_ms"],
            tuple(TranscriptSegment.from_dict(item) for item in segments),
            tuple(Claim.from_dict(item) for item in claims),
            tuple(UntranscribedRange.from_dict(item) for item in ranges),
            value["schema_version"],
        )


def _audio_signature_is_valid(mime_type: str, data: bytes) -> bool:
    if mime_type in {"audio/wav", "audio/x-wav"}:
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if mime_type == "audio/mpeg":
        return data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        )
    return False


class AudioTranscriptionWorker:
    """把获授权音频变成带时间戳的 Claim Artifact，不修改验收。"""

    REQUIRED_CAPABILITIES = frozenset({
        TranscriptionCapability.TRANSCRIPTION,
        TranscriptionCapability.TIMESTAMPS,
    })

    def __init__(
        self,
        client: TranscriptionClient,
        evidence: Mapping[str, RequirementEvidence],
        payload_resolver: Callable[[str], bytes],
        *,
        max_audio_bytes: int = 25 * 1024 * 1024,
        language_hint: str = "",
    ) -> None:
        if max_audio_bytes <= 0:
            raise ValueError("max_audio_bytes 必须大于 0")
        self.client = client
        self.evidence = MappingProxyType(dict(evidence))
        self.payload_resolver = payload_resolver
        self.max_audio_bytes = max_audio_bytes
        self.language_hint = _optional(language_hint, "language_hint")
        self.requests: list[TranscriptionRequest] = []

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        if request.task.role != "planner":
            raise PermissionError("AudioTranscriptionWorker 只能执行 planner Role")
        if len(request.inputs) != 1 or len(request.task.output_artifacts) != 1:
            raise AudioTranscriptionError("转录节点必须恰好有一个输入和输出")
        artifact = next(iter(request.inputs.values()))
        reference = f"artifact://{artifact.artifact_id}"
        if artifact.kind != REQUIREMENT_AUDIO_KIND:
            raise AudioTranscriptionError("输入必须是 core:requirement_audio")
        if artifact.task_id != request.parent.task_id:
            raise PermissionError("音频 Artifact 不属于当前任务")
        evidence = self.evidence.get(reference)
        if evidence is None:
            raise PermissionError("音频缺少 RequirementEvidence 声明")
        if evidence.modality is not EvidenceModality.AUDIO:
            raise AudioTranscriptionError("RequirementEvidence 不是音频")
        evidence.validate_artifact(artifact)
        grant = request.evidence_grant
        if grant is None or not grant.allows(
            task_id=request.task.task_id,
            role=request.task.role,
            evidence_ref=reference,
            operation="audio:transcribe",
        ):
            raise PermissionError("音频缺少 audio:transcribe 授权")
        data = self.payload_resolver(reference)
        if not isinstance(data, bytes) or not data:
            raise AudioTranscriptionError("音频 Payload 为空")
        if len(data) > self.max_audio_bytes or len(data) != evidence.size_bytes:
            raise AudioTranscriptionError("音频 Payload 大小无效")
        if sha256(data).hexdigest() != evidence.content_hash:
            raise AudioTranscriptionError("音频 Payload 哈希不匹配")
        if not _audio_signature_is_valid(evidence.mime_type, data):
            raise AudioTranscriptionError("音频格式或文件签名不受支持")
        capabilities = frozenset(self.client.capabilities)
        missing = self.REQUIRED_CAPABILITIES - capabilities
        if missing:
            raise AudioTranscriptionError(
                f"转录客户端缺少能力: {sorted(item.value for item in missing)}"
            )
        transcription_request = TranscriptionRequest(
            reference, evidence.mime_type, data, self.language_hint
        )
        self.requests.append(transcription_request)
        response = self.client.transcribe(transcription_request)
        if not isinstance(response, TranscriptionResponse):
            raise AudioTranscriptionError("转录客户端必须返回 TranscriptionResponse")
        source = f"transcription:{response.provider}/{response.model}"
        claims = tuple(
            Claim.create(
                ClaimKind.OBSERVATION,
                f"[{segment.start_ms}-{segment.end_ms}ms] {segment.text}",
                source,
                evidence_refs=(reference,),
                uncertainty=segment.uncertainty,
            )
            for segment in response.segments
        )
        transcript = AudioTranscript(
            reference,
            response.language,
            response.duration_ms,
            response.segments,
            claims,
            response.untranscribed_ranges,
        )
        output_name = request.task.output_artifacts[0]
        return TaskRunResult(
            True,
            f"已生成 {len(response.segments)} 个带时间戳的转录片段",
            {output_name: ArtifactDraft(
                transcript.to_dict(),
                kind=AUDIO_TRANSCRIPT_KIND,
                metadata={
                    "source_evidence_ref": reference,
                    "source_content_hash": evidence.content_hash,
                    "protocol_version": AUDIO_TRANSCRIPT_PROTOCOL_VERSION,
                    "provider": response.provider,
                    "model": response.model,
                    "duration_ms": response.duration_ms,
                    "latency_ms": response.latency_ms,
                },
            )},
        )


def build_audio_transcription_registry(
    worker: AudioTranscriptionWorker,
) -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.register_worker(
        WorkerDescriptor(
            "core-audio-transcription",
            "planner",
            frozenset({"audio_transcription"}),
            frozenset({REQUIREMENT_AUDIO_KIND}),
            frozenset({AUDIO_TRANSCRIPT_KIND}),
            frozenset({"multimodal"}),
            principal_id="core-audio-transcription-principal",
        ),
        worker,
    )
    return registry


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


@dataclass(frozen=True)
class TranscriptScore:
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


def score_audio_transcript(
    transcript: AudioTranscript,
    expected_segment_texts: tuple[str, ...],
) -> TranscriptScore:
    expected = {_normalized_text(item) for item in expected_segment_texts}
    if not expected or "" in expected:
        raise ValueError("expected_segment_texts 不能为空")
    reported = {_normalized_text(item.text) for item in transcript.segments}
    matched = len(expected.intersection(reported))
    precision = matched / len(reported) if reported else 0.0
    recall = matched / len(expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    return TranscriptScore(
        len(expected), len(reported), matched, precision, recall, f1
    )
