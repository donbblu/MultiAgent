from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

from .artifacts import Artifact, ArtifactStore
from .audio_transcription import (
    AUDIO_TRANSCRIPT_KIND,
    REQUIREMENT_AUDIO_KIND,
    AudioTranscript,
)
from .harness.executor import GraphExecutionResult, TaskGraphExecutor
from .harness.registry import WorkerDescriptor, WorkerRegistry
from .harness.task_graph import TaskExecutionState, TaskGraph, TaskSpec
from .image_perception import (
    IMAGE_OBSERVATION_KIND,
    REQUIREMENT_IMAGE_KIND,
    ImageObservation,
)
from .memory import MemoryManager
from .models import TaskContext
from .requirements import (
    CodingRequirement,
    EvidenceGrant,
    EvidenceModality,
    RepositoryScope,
    RequirementEvidence,
    ValidatorProfile,
)
from .roles import RoleRegistry
from .truth import Claim, ClaimKind
from .video_perception import (
    REQUIREMENT_VIDEO_KIND,
    VIDEO_BUG_EVIDENCE_KIND,
    VideoBugEvidence,
)


MULTIMODAL_INTAKE_PROTOCOL_VERSION = "1.0"
REQUIREMENT_TEXT_KIND = "core:requirement_text"
EVIDENCE_BUNDLE_KIND = "core:evidence_bundle"
_INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class MultimodalIntakeError(RuntimeError):
    pass


class EvidenceIntakeStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value.strip()


def _optional(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value.strip()


def _artifact_ref(value: object, field_name: str, *, optional: bool = False) -> str:
    if optional and value == "":
        return ""
    parsed = _nonempty(value, field_name)
    if not parsed.startswith("artifact://"):
        raise ValueError(f"{field_name} 必须使用 artifact:// 引用")
    return parsed


def _protocol_for(modality: EvidenceModality) -> tuple[str, str, str, str]:
    return {
        EvidenceModality.TEXT: (
            REQUIREMENT_TEXT_KIND,
            REQUIREMENT_TEXT_KIND,
            "text_intake",
            "read",
        ),
        EvidenceModality.IMAGE: (
            REQUIREMENT_IMAGE_KIND,
            IMAGE_OBSERVATION_KIND,
            "vision_understanding",
            "vision:inspect",
        ),
        EvidenceModality.AUDIO: (
            REQUIREMENT_AUDIO_KIND,
            AUDIO_TRANSCRIPT_KIND,
            "audio_transcription",
            "audio:transcribe",
        ),
        EvidenceModality.VIDEO: (
            REQUIREMENT_VIDEO_KIND,
            VIDEO_BUG_EVIDENCE_KIND,
            "video_temporal_understanding",
            "video:inspect",
        ),
    }[modality]


@dataclass(frozen=True)
class IntakeBinding:
    input_name: str
    evidence: RequirementEvidence

    def __post_init__(self) -> None:
        name = _nonempty(self.input_name, "input_name")
        if not _INPUT_NAME.fullmatch(name):
            raise ValueError("input_name 只能使用小写字母、数字和下划线")
        if not isinstance(self.evidence, RequirementEvidence):
            raise ValueError("evidence 必须是 RequirementEvidence")
        object.__setattr__(self, "input_name", name)

    @property
    def task_id(self) -> str:
        return f"intake_{self.input_name}_{self.evidence.modality.value}"

    @property
    def normalized_name(self) -> str:
        return f"normalized_{self.input_name}"

    @property
    def source_kind(self) -> str:
        return _protocol_for(self.evidence.modality)[0]

    @property
    def normalized_kind(self) -> str:
        return _protocol_for(self.evidence.modality)[1]

    @property
    def operation(self) -> str:
        return _protocol_for(self.evidence.modality)[3]


@dataclass(frozen=True)
class MultimodalIntakePlan:
    bindings: tuple[IntakeBinding, ...]

    def __post_init__(self) -> None:
        bindings = tuple(self.bindings)
        if not bindings or not all(isinstance(item, IntakeBinding) for item in bindings):
            raise ValueError("MultimodalIntakePlan 至少需要一个 binding")
        if tuple(item.input_name for item in bindings) != tuple(sorted(
            item.input_name for item in bindings
        )):
            raise ValueError("bindings 必须按 input_name 排序")
        names = tuple(item.input_name for item in bindings)
        refs = tuple(item.evidence.artifact_ref for item in bindings)
        if len(names) != len(set(names)):
            raise ValueError("Intake input_name 不能重复")
        if len(refs) != len(set(refs)):
            raise ValueError("同一原始 Evidence 不能重复处理")
        object.__setattr__(self, "bindings", bindings)

    @property
    def evidence(self) -> tuple[RequirementEvidence, ...]:
        return tuple(item.evidence for item in self.bindings)

    def media_graph(self, input_names: tuple[str, ...]) -> TaskGraph | None:
        selected = tuple(
            item for item in self.bindings
            if item.input_name in input_names
            and item.evidence.modality is not EvidenceModality.TEXT
        )
        if not selected:
            return None
        tasks = []
        for item in selected:
            _, output_kind, capability, _ = _protocol_for(item.evidence.modality)
            tasks.append(TaskSpec(
                item.task_id,
                f"处理 {item.evidence.modality.value} Evidence",
                "只把原始媒体转换成可追踪的结构化 Claim Artifact",
                "planner",
                acceptance_criteria=("生成结构化 Evidence",),
                input_artifacts=(item.input_name,),
                output_artifacts=(item.normalized_name,),
                retry_limit=0,
                required_capabilities=(capability,),
                input_protocols=(item.source_kind,),
                output_protocols=(output_kind,),
                required_policy_tags=("multimodal",),
            ))
        return TaskGraph(
            tuple(tasks),
            external_artifacts=tuple(item.input_name for item in selected),
        )


def build_multimodal_intake_plan(
    bindings: Mapping[str, RequirementEvidence],
) -> MultimodalIntakePlan:
    if not isinstance(bindings, Mapping):
        raise ValueError("bindings 必须是映射")
    return MultimodalIntakePlan(tuple(
        IntakeBinding(name, evidence)
        for name, evidence in sorted(bindings.items())
    ))


def build_multimodal_intake_registry(
    *,
    image_worker: object | None = None,
    audio_worker: object | None = None,
    video_worker: object | None = None,
    planner_worker: object | None = None,
    planner_output_kind: str = "core:analysis",
) -> WorkerRegistry:
    registry = WorkerRegistry()
    registrations = (
        (
            image_worker,
            WorkerDescriptor(
                "core-image-perception",
                "planner",
                frozenset({"vision_understanding"}),
                frozenset({REQUIREMENT_IMAGE_KIND}),
                frozenset({IMAGE_OBSERVATION_KIND}),
                frozenset({"multimodal"}),
                principal_id="core-image-perception-principal",
            ),
        ),
        (
            audio_worker,
            WorkerDescriptor(
                "core-audio-transcription",
                "planner",
                frozenset({"audio_transcription"}),
                frozenset({REQUIREMENT_AUDIO_KIND}),
                frozenset({AUDIO_TRANSCRIPT_KIND}),
                frozenset({"multimodal"}),
                principal_id="core-audio-transcription-principal",
            ),
        ),
        (
            video_worker,
            WorkerDescriptor(
                "core-video-perception",
                "planner",
                frozenset({"video_temporal_understanding"}),
                frozenset({REQUIREMENT_VIDEO_KIND}),
                frozenset({VIDEO_BUG_EVIDENCE_KIND}),
                frozenset({"multimodal"}),
                principal_id="core-video-perception-principal",
            ),
        ),
        (
            planner_worker,
            WorkerDescriptor(
                "core-evidence-bundle-planner",
                "planner",
                frozenset({"task_planning"}),
                frozenset({EVIDENCE_BUNDLE_KIND}),
                frozenset({_nonempty(planner_output_kind, "planner_output_kind")}),
                frozenset({"text"}),
                principal_id="core-evidence-bundle-planner-principal",
            ),
        ),
    )
    for worker, descriptor in registrations:
        if worker is not None:
            registry.register_worker(descriptor, worker)
    return registry


@dataclass(frozen=True)
class EvidenceBundleEntry:
    source_evidence_ref: str
    modality: EvidenceModality
    status: EvidenceIntakeStatus
    normalized_kind: str
    normalized_artifact_ref: str = ""
    claims: tuple[Claim, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        source = _artifact_ref(self.source_evidence_ref, "source_evidence_ref")
        try:
            modality = (
                self.modality
                if isinstance(self.modality, EvidenceModality)
                else EvidenceModality(self.modality)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("EvidenceBundleEntry modality 无效") from exc
        try:
            status = (
                self.status
                if isinstance(self.status, EvidenceIntakeStatus)
                else EvidenceIntakeStatus(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("EvidenceBundleEntry status 无效") from exc
        kind = _nonempty(self.normalized_kind, "normalized_kind")
        if ":" not in kind:
            raise ValueError("normalized_kind 必须使用命名空间")
        normalized_ref = _artifact_ref(
            self.normalized_artifact_ref,
            "normalized_artifact_ref",
            optional=True,
        )
        claims = tuple(self.claims)
        if not all(isinstance(item, Claim) for item in claims):
            raise ValueError("EvidenceBundleEntry claims 类型无效")
        detail = _optional(self.detail, "detail")
        if status is EvidenceIntakeStatus.READY:
            if not normalized_ref or not claims or detail:
                raise ValueError("ready Evidence 必须有 Artifact 和 Claim，且不能有错误")
            if any(source not in item.evidence_refs for item in claims):
                raise ValueError("Bundle Claim 必须引用原始 Evidence")
        elif normalized_ref or claims or not detail:
            raise ValueError("未就绪 Evidence 只能保存失败状态和原因")
        object.__setattr__(self, "source_evidence_ref", source)
        object.__setattr__(self, "modality", modality)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "normalized_kind", kind)
        object.__setattr__(self, "normalized_artifact_ref", normalized_ref)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "detail", detail)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "source_evidence_ref": self.source_evidence_ref,
            "modality": self.modality.value,
            "status": self.status.value,
            "normalized_kind": self.normalized_kind,
            "normalized_artifact_ref": self.normalized_artifact_ref,
            "claims": [dict(item.to_dict()) for item in self.claims],
            "detail": self.detail,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceBundleEntry":
        expected = {
            "source_evidence_ref", "modality", "status", "normalized_kind",
            "normalized_artifact_ref", "claims", "detail",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("EvidenceBundleEntry 字段无效")
        claims = value["claims"]
        if not isinstance(claims, (tuple, list)):
            raise ValueError("EvidenceBundleEntry claims 必须是数组")
        return cls(
            value["source_evidence_ref"], value["modality"], value["status"],
            value["normalized_kind"], value["normalized_artifact_ref"],
            tuple(Claim.from_dict(item) for item in claims), value["detail"],
        )


@dataclass(frozen=True)
class MultimodalEvidenceBundle:
    requirement_id: str
    entries: tuple[EvidenceBundleEntry, ...]
    ready: bool
    schema_version: str = MULTIMODAL_INTAKE_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        requirement_id = _nonempty(self.requirement_id, "requirement_id")
        entries = tuple(self.entries)
        if not entries or not all(isinstance(item, EvidenceBundleEntry) for item in entries):
            raise ValueError("Evidence Bundle entries 不能为空")
        refs = tuple(item.source_evidence_ref for item in entries)
        if len(refs) != len(set(refs)):
            raise ValueError("Evidence Bundle 不能重复原始引用")
        actual_ready = all(
            item.status is EvidenceIntakeStatus.READY for item in entries
        )
        if not isinstance(self.ready, bool) or self.ready != actual_ready:
            raise ValueError("Evidence Bundle ready 必须由条目状态决定")
        if self.schema_version != MULTIMODAL_INTAKE_PROTOCOL_VERSION:
            raise ValueError("MultimodalEvidenceBundle schema_version 无效")
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "entries", entries)

    @property
    def claims(self) -> tuple[Claim, ...]:
        return tuple(claim for entry in self.entries for claim in entry.claims)

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": self.schema_version,
            "requirement_id": self.requirement_id,
            "ready": self.ready,
            "entries": [dict(item.to_dict()) for item in self.entries],
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MultimodalEvidenceBundle":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "requirement_id", "ready", "entries",
        }:
            raise ValueError("MultimodalEvidenceBundle 字段无效")
        entries = value["entries"]
        if not isinstance(entries, (tuple, list)):
            raise ValueError("MultimodalEvidenceBundle entries 必须是数组")
        return cls(
            value["requirement_id"],
            tuple(EvidenceBundleEntry.from_dict(item) for item in entries),
            value["ready"],
            value["schema_version"],
        )


@dataclass(frozen=True)
class MultimodalIntakeResult:
    bundle_ref: str
    bundle: MultimodalEvidenceBundle
    perception_result: GraphExecutionResult | None = None
    planner_result: GraphExecutionResult | None = None

    @property
    def succeeded(self) -> bool:
        return bool(
            self.bundle.ready
            and self.planner_result is not None
            and self.planner_result.succeeded
        )


def _task_context(parent: TaskContext, requirement: CodingRequirement | None) -> TaskContext:
    return TaskContext(
        parent.task_id,
        parent.objective,
        list(parent.acceptance_criteria),
        verification_commands=[list(item) for item in parent.verification_commands],
        user_request=parent.user_request,
        project_root=parent.project_root,
        project_id=parent.project_id,
        tech_stack=dict(parent.tech_stack),
        constraints=list(parent.constraints),
        allowed_paths=list(parent.allowed_paths),
        prohibited_actions=list(parent.prohibited_actions),
        assumptions=list(parent.assumptions),
        attempt=parent.attempt,
        feedback=list(parent.feedback),
        coding_requirement=requirement,
    )


def _failure_detail(value: object) -> str:
    detail = re.sub(r"\s+", " ", str(value or "处理失败")).strip()
    return detail[:500]


class MultimodalIntakeRunner:
    """Runtime 编排原始媒体感知、状态汇总和后续文本 Planner。"""

    PLANNER_TASK_ID = "plan_from_evidence_bundle"

    def __init__(
        self,
        plan: MultimodalIntakePlan,
        workers: WorkerRegistry,
        roles: RoleRegistry,
        memory: MemoryManager,
        artifacts: ArtifactStore,
        validator_profile: ValidatorProfile,
        *,
        planner_output_name: str = "analysis",
        planner_output_kind: str = "core:analysis",
        max_workers: int = 4,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self.plan = plan
        self.workers = workers
        self.roles = roles
        self.memory = memory
        self.artifacts = artifacts
        self.validator_profile = validator_profile
        self.planner_output_name = _nonempty(
            planner_output_name, "planner_output_name"
        )
        self.planner_output_kind = _nonempty(
            planner_output_kind, "planner_output_kind"
        )
        self.max_workers = max_workers

    def _preflight(
        self,
        parent: TaskContext,
        grants: Mapping[str, EvidenceGrant],
    ) -> tuple[dict[str, EvidenceBundleEntry], tuple[str, ...]]:
        entries: dict[str, EvidenceBundleEntry] = {}
        runnable: list[str] = []
        for binding in self.plan.bindings:
            evidence = binding.evidence
            grant = grants.get(binding.task_id)
            allowed = grant is not None and grant.allows(
                task_id=binding.task_id,
                role="planner",
                evidence_ref=evidence.artifact_ref,
                operation="read",
            )
            if allowed and binding.operation != "read":
                allowed = grant.allows(
                    task_id=binding.task_id,
                    role="planner",
                    evidence_ref=evidence.artifact_ref,
                    operation=binding.operation,
                )
            if not allowed:
                entries[binding.input_name] = EvidenceBundleEntry(
                    evidence.artifact_ref,
                    evidence.modality,
                    EvidenceIntakeStatus.BLOCKED,
                    binding.normalized_kind,
                    detail=f"缺少 {binding.operation} 授权",
                )
                continue
            try:
                artifact = self.artifacts.get(evidence.artifact_ref)
                if artifact.task_id != parent.task_id:
                    raise PermissionError("Evidence Artifact 不属于当前任务")
                if artifact.kind != binding.source_kind:
                    raise ValueError("Evidence Artifact kind 与模态不匹配")
                evidence.validate_artifact(artifact)
                if evidence.modality is EvidenceModality.TEXT:
                    text = self._text_content(artifact)
                    encoded = text.encode("utf-8")
                    if len(encoded) != evidence.size_bytes:
                        raise ValueError("文本 Payload 大小不匹配")
                    if sha256(encoded).hexdigest() != evidence.content_hash:
                        raise ValueError("文本 Payload 哈希不匹配")
                    claim = Claim.create(
                        ClaimKind.OBSERVATION,
                        f"用户文本需求：{text}",
                        "runtime:multimodal_intake:text",
                        evidence_refs=(evidence.artifact_ref,),
                    )
                    entries[binding.input_name] = EvidenceBundleEntry(
                        evidence.artifact_ref,
                        evidence.modality,
                        EvidenceIntakeStatus.READY,
                        binding.normalized_kind,
                        evidence.artifact_ref,
                        (claim,),
                    )
                else:
                    runnable.append(binding.input_name)
            except (KeyError, PermissionError, ValueError) as exc:
                entries[binding.input_name] = EvidenceBundleEntry(
                    evidence.artifact_ref,
                    evidence.modality,
                    EvidenceIntakeStatus.FAILED,
                    binding.normalized_kind,
                    detail=_failure_detail(exc),
                )
        return entries, tuple(runnable)

    @staticmethod
    def _text_content(artifact: Artifact) -> str:
        content = artifact.content
        if isinstance(content, str):
            return _nonempty(content, "文本 Evidence")
        if isinstance(content, Mapping) and set(content) == {"text"}:
            return _nonempty(content["text"], "文本 Evidence")
        raise ValueError("core:requirement_text 内容必须是字符串或仅含 text 的对象")

    @staticmethod
    def _normalized_claims(
        binding: IntakeBinding,
        artifact: Artifact,
    ) -> tuple[Claim, ...]:
        if artifact.kind != binding.normalized_kind:
            raise ValueError("感知 Worker 输出协议不匹配")
        if binding.evidence.modality is EvidenceModality.IMAGE:
            value = ImageObservation.from_dict(artifact.content)
            source_ref = value.source_evidence_ref
            claims = value.claims
        elif binding.evidence.modality is EvidenceModality.AUDIO:
            value = AudioTranscript.from_dict(artifact.content)
            source_ref = value.source_evidence_ref
            claims = value.claims
        elif binding.evidence.modality is EvidenceModality.VIDEO:
            value = VideoBugEvidence.from_dict(artifact.content)
            source_ref = value.source_evidence_ref
            claims = value.claims
        else:
            raise ValueError("文本不应进入媒体输出解析")
        if source_ref != binding.evidence.artifact_ref:
            raise ValueError("派生 Artifact 引用了错误的原始 Evidence")
        return tuple(claims)

    def _bundle_artifact(
        self,
        parent: TaskContext,
        entries: Mapping[str, EvidenceBundleEntry],
    ) -> tuple[str, MultimodalEvidenceBundle]:
        ordered = tuple(entries[item.input_name] for item in self.plan.bindings)
        bundle = MultimodalEvidenceBundle(
            parent.coding_requirement.requirement_id,
            ordered,
            all(item.status is EvidenceIntakeStatus.READY for item in ordered),
        )
        artifact = Artifact.create(
            "evidence_bundle",
            parent.task_id,
            bundle.to_dict(),
            kind=EVIDENCE_BUNDLE_KIND,
            metadata={
                "protocol_version": MULTIMODAL_INTAKE_PROTOCOL_VERSION,
                "runtime_provenance": {
                    "worker_id": "runtime-multimodal-intake",
                    "principal_id": "runtime-multimodal-intake",
                    "role": "runtime",
                    "task_id": "bundle_evidence",
                },
            },
        )
        return self.artifacts.put(artifact), bundle

    def _planner_context(
        self,
        parent: TaskContext,
        bundle_ref: str,
    ) -> TaskContext:
        value = dict(parent.coding_requirement.to_dict())
        value["extension_refs"] = tuple(
            parent.coding_requirement.extension_refs
        ) + (bundle_ref,)
        return _task_context(parent, CodingRequirement.from_dict(value))

    def run(
        self,
        parent: TaskContext,
        *,
        evidence_grants: Mapping[str, EvidenceGrant],
    ) -> MultimodalIntakeResult:
        requirement = parent.coding_requirement
        if requirement is None:
            raise MultimodalIntakeError("统一 Intake 需要 CodingRequirement")
        plan_refs = {item.evidence.artifact_ref for item in self.plan.bindings}
        if plan_refs != set(requirement.evidence_refs):
            raise MultimodalIntakeError("Intake Plan 必须覆盖且只能覆盖需求 Evidence")
        requirement.enforce_runtime_boundaries(
            runtime_scope=RepositoryScope(
                ("**",), tuple(parent.allowed_paths), tuple(parent.prohibited_actions)
            ),
            validator_profile=self.validator_profile,
            available_evidence=self.plan.evidence,
        )
        entries, runnable = self._preflight(parent, evidence_grants)
        perception_result = None
        graph = self.plan.media_graph(runnable)
        if graph is not None:
            selected = {
                item.input_name: item for item in self.plan.bindings
                if item.input_name in runnable
            }
            perception_result = TaskGraphExecutor(
                graph,
                self.workers,
                self.roles,
                self.memory,
                artifacts=self.artifacts,
                max_workers=self.max_workers,
                initial_artifacts={
                    name: item.evidence.artifact_ref
                    for name, item in selected.items()
                },
                evidence_grants={
                    item.task_id: evidence_grants[item.task_id]
                    for item in selected.values()
                },
            ).run(_task_context(parent, None))
            snapshot = perception_result.snapshot
            for name, binding in selected.items():
                state = snapshot.states[binding.task_id]
                if state is TaskExecutionState.SUCCEEDED:
                    try:
                        normalized_ref = snapshot.artifacts[binding.normalized_name]
                        normalized = self.artifacts.get(normalized_ref)
                        claims = self._normalized_claims(binding, normalized)
                        entries[name] = EvidenceBundleEntry(
                            binding.evidence.artifact_ref,
                            binding.evidence.modality,
                            EvidenceIntakeStatus.READY,
                            binding.normalized_kind,
                            normalized_ref,
                            claims,
                        )
                    except (KeyError, ValueError) as exc:
                        entries[name] = EvidenceBundleEntry(
                            binding.evidence.artifact_ref,
                            binding.evidence.modality,
                            EvidenceIntakeStatus.FAILED,
                            binding.normalized_kind,
                            detail=_failure_detail(exc),
                        )
                else:
                    status = (
                        EvidenceIntakeStatus.BLOCKED
                        if state is TaskExecutionState.BLOCKED
                        else EvidenceIntakeStatus.FAILED
                    )
                    entries[name] = EvidenceBundleEntry(
                        binding.evidence.artifact_ref,
                        binding.evidence.modality,
                        status,
                        binding.normalized_kind,
                        detail=_failure_detail(
                            snapshot.failures.get(binding.task_id, state.value)
                        ),
                    )
        bundle_ref, bundle = self._bundle_artifact(parent, entries)
        if not bundle.ready:
            return MultimodalIntakeResult(
                bundle_ref, bundle, perception_result, None
            )
        planner_context = self._planner_context(parent, bundle_ref)
        planner_graph = TaskGraph((TaskSpec(
            self.PLANNER_TASK_ID,
            "基于统一 Evidence Bundle 分析需求",
            "只读取结构化 Evidence Bundle，不直接读取原始媒体",
            "planner",
            acceptance_criteria=("生成结构化需求分析",),
            input_artifacts=("evidence_bundle",),
            output_artifacts=(self.planner_output_name,),
            retry_limit=0,
            required_capabilities=("task_planning",),
            input_protocols=(EVIDENCE_BUNDLE_KIND,),
            output_protocols=(self.planner_output_kind,),
            required_policy_tags=("text",),
        ),), external_artifacts=("evidence_bundle",))
        planner_result = TaskGraphExecutor(
            planner_graph,
            self.workers,
            self.roles,
            self.memory,
            artifacts=self.artifacts,
            initial_artifacts={"evidence_bundle": bundle_ref},
            evidence_grants={
                self.PLANNER_TASK_ID: EvidenceGrant(
                    "runtime-bundle-grant",
                    self.PLANNER_TASK_ID,
                    "planner",
                    (bundle_ref,),
                    ("read",),
                    "读取 Runtime 汇总的 Evidence Bundle",
                )
            },
            validator_profile=self.validator_profile,
            requirement_evidence=self.plan.evidence,
        ).run(planner_context)
        return MultimodalIntakeResult(
            bundle_ref, bundle, perception_result, planner_result
        )
