from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping

from .artifacts import Artifact, ArtifactDraft
from .coding_ablation import (
    AblationUsage,
    AblationWorkerRequest,
    AblationWorkerResponse,
    UsageSource,
)
from .harness.registry import WorkerDescriptor, WorkerRegistry
from .model import (
    ModelCapability,
    ModelClient,
    ModelMessage,
    ModelRequest,
    TextContentPart,
    require_capabilities,
)
from .models import FileChange, ImplementationPlan


CODING_MODEL_PROTOCOL_VERSION = "1.0"
CODING_PROMPT_VERSION = "core-coding-ablation-1.0"


PLAN_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "summary", "steps", "risks"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "summary": {"type": "string", "minLength": 1},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "step_id", "objective", "target_paths", "acceptance_notes",
                ],
                "properties": {
                    "step_id": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "target_paths": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "acceptance_notes": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


PATCH_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "summary", "changes", "assumptions"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "summary": {"type": "string", "minLength": 1},
        "changes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "content", "reason"],
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


DIAGNOSIS_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "summary", "root_causes",
        "recommended_changes", "uncertainty",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "summary": {"type": "string", "minLength": 1},
        "root_causes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence", "hypothesis", "affected_paths"],
                "properties": {
                    "evidence": {"type": "string", "minLength": 1},
                    "hypothesis": {"type": "string", "minLength": 1},
                    "affected_paths": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "recommended_changes": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "uncertainty": {"type": "string", "minLength": 1},
    },
}


class CodingModelWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactDisclosure:
    name: str
    kind: str
    content_hash: str
    original_chars: int
    disclosed_chars: int
    truncated: bool
    files: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "name": self.name,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "original_chars": self.original_chars,
            "disclosed_chars": self.disclosed_chars,
            "truncated": self.truncated,
            "files": [dict(item) for item in self.files],
        })


@dataclass(frozen=True)
class PreparedModelInvocation:
    request: ModelRequest
    request_sha256: str
    prompt_version: str
    payload_chars: int
    disclosures: tuple[ArtifactDisclosure, ...]

    def audit_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "request_sha256": self.request_sha256,
            "prompt_version": self.prompt_version,
            "payload_chars": self.payload_chars,
            "disclosures": [dict(item.to_dict()) for item in self.disclosures],
        })


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise CodingModelWorkerError(
            f"Artifact 内容不能转换为模型 JSON: {exc}"
        ) from exc


def _exact_object(
    value: object, required: set[str], field_name: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CodingModelWorkerError(f"{field_name} 必须是对象")
    keys = set(value)
    if keys != required:
        raise CodingModelWorkerError(
            f"{field_name} 字段不匹配，缺少 {sorted(required - keys)}，"
            f"多出 {sorted(keys - required)}"
        )
    return value


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodingModelWorkerError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _strings(
    value: object, field_name: str, *, allow_empty: bool = True
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CodingModelWorkerError(f"{field_name} 必须是字符串数组")
    parsed = tuple(_nonempty(item, field_name) for item in value)
    if not allow_empty and not parsed:
        raise CodingModelWorkerError(f"{field_name} 不能为空")
    if len(parsed) != len(set(parsed)):
        raise CodingModelWorkerError(f"{field_name} 不能重复")
    return parsed


def _safe_allowed_path(path: str, allowed_paths: tuple[str, ...]) -> str:
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise CodingModelWorkerError(f"模型输出不安全路径: {path}")
    if (
        parsed.parts[0] in {
            ".git", ".runs", ".runtime", ".verification",
            ".harness-hidden-tests", "solution",
        }
        or parsed.name.startswith(".env")
    ):
        raise CodingModelWorkerError(f"模型输出受保护路径: {path}")
    if not any(fnmatch.fnmatch(normalized, pattern) for pattern in allowed_paths):
        raise CodingModelWorkerError(f"模型输出路径未获授权: {path}")
    return normalized


def _parse_plan(
    value: Mapping[str, object], allowed_paths: tuple[str, ...]
) -> Mapping[str, object]:
    root = _exact_object(
        value, {"schema_version", "summary", "steps", "risks"}, "Plan"
    )
    if root["schema_version"] != CODING_MODEL_PROTOCOL_VERSION:
        raise CodingModelWorkerError("Plan schema_version 无效")
    summary = _nonempty(root["summary"], "Plan.summary")
    raw_steps = root["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise CodingModelWorkerError("Plan.steps 必须是非空数组")
    steps: list[Mapping[str, object]] = []
    step_ids: set[str] = set()
    for index, raw in enumerate(raw_steps):
        item = _exact_object(
            raw,
            {"step_id", "objective", "target_paths", "acceptance_notes"},
            f"Plan.steps[{index}]",
        )
        step_id = _nonempty(item["step_id"], "step_id")
        if step_id in step_ids:
            raise CodingModelWorkerError("Plan.step_id 不能重复")
        step_ids.add(step_id)
        targets = tuple(
            _safe_allowed_path(path, allowed_paths)
            for path in _strings(
                item["target_paths"], "target_paths", allow_empty=False
            )
        )
        steps.append(MappingProxyType({
            "step_id": step_id,
            "objective": _nonempty(item["objective"], "objective"),
            "target_paths": targets,
            "acceptance_notes": _strings(
                item["acceptance_notes"], "acceptance_notes"
            ),
        }))
    return MappingProxyType({
        "schema_version": CODING_MODEL_PROTOCOL_VERSION,
        "summary": summary,
        "steps": tuple(steps),
        "risks": _strings(root["risks"], "Plan.risks"),
    })


def _parse_patch(
    value: Mapping[str, object], allowed_paths: tuple[str, ...]
) -> tuple[ImplementationPlan, tuple[str, ...]]:
    root = _exact_object(
        value,
        {"schema_version", "summary", "changes", "assumptions"},
        "Patch",
    )
    if root["schema_version"] != CODING_MODEL_PROTOCOL_VERSION:
        raise CodingModelWorkerError("Patch schema_version 无效")
    raw_changes = root["changes"]
    if not isinstance(raw_changes, list) or not raw_changes:
        raise CodingModelWorkerError("Patch.changes 必须是非空数组")
    changes: list[FileChange] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_changes):
        item = _exact_object(
            raw, {"path", "content", "reason"}, f"Patch.changes[{index}]"
        )
        path = _safe_allowed_path(
            _nonempty(item["path"], "change.path"), allowed_paths
        )
        if path in paths:
            raise CodingModelWorkerError("Patch 不能重复修改同一路径")
        paths.add(path)
        content = item["content"]
        if not isinstance(content, str):
            raise CodingModelWorkerError("change.content 必须是字符串")
        changes.append(FileChange(
            path, content, _nonempty(item["reason"], "change.reason")
        ))
    assumptions = _strings(root["assumptions"], "Patch.assumptions")
    return ImplementationPlan(
        _nonempty(root["summary"], "Patch.summary"), changes, []
    ), assumptions


def _parse_diagnosis(
    value: Mapping[str, object], allowed_paths: tuple[str, ...]
) -> Mapping[str, object]:
    root = _exact_object(
        value,
        {
            "schema_version", "summary", "root_causes",
            "recommended_changes", "uncertainty",
        },
        "Diagnosis",
    )
    if root["schema_version"] != CODING_MODEL_PROTOCOL_VERSION:
        raise CodingModelWorkerError("Diagnosis schema_version 无效")
    raw_causes = root["root_causes"]
    if not isinstance(raw_causes, list) or not raw_causes:
        raise CodingModelWorkerError("Diagnosis.root_causes 必须是非空数组")
    causes: list[Mapping[str, object]] = []
    for index, raw in enumerate(raw_causes):
        item = _exact_object(
            raw,
            {"evidence", "hypothesis", "affected_paths"},
            f"Diagnosis.root_causes[{index}]",
        )
        causes.append(MappingProxyType({
            "evidence": _nonempty(item["evidence"], "evidence"),
            "hypothesis": _nonempty(item["hypothesis"], "hypothesis"),
            "affected_paths": tuple(
                _safe_allowed_path(path, allowed_paths)
                for path in _strings(item["affected_paths"], "affected_paths")
            ),
        }))
    return MappingProxyType({
        "schema_version": CODING_MODEL_PROTOCOL_VERSION,
        "summary": _nonempty(root["summary"], "Diagnosis.summary"),
        "root_causes": tuple(causes),
        "recommended_changes": _strings(
            root["recommended_changes"],
            "Diagnosis.recommended_changes",
            allow_empty=False,
        ),
        "uncertainty": _nonempty(root["uncertainty"], "Diagnosis.uncertainty"),
    })


_STAGE_PROTOCOLS: Mapping[str, tuple[str, Mapping[str, object]]] = MappingProxyType({
    "plan": (
        "你是 Planner。只根据已披露的 Artifact 形成可执行计划；不得声称代码或测试已经通过。",
        PLAN_SCHEMA,
    ),
    "implement": (
        "你是 Developer。输出完整文件内容形式的候选补丁；不得修改授权范围外文件，也不得声称补丁已通过验证。",
        PATCH_SCHEMA,
    ),
    "diagnose": (
        "你是 Tester。根据 Runtime 的失败证据提出可证伪的诊断假设；不得把推测写成已验证事实，也不得声明任务通过。",
        DIAGNOSIS_SCHEMA,
    ),
    "fix": (
        "你是 Fixer。根据失败证据和诊断输出候选修复；不得修改授权范围外文件，也不得声称修复已通过验证。",
        PATCH_SCHEMA,
    ),
})

_STAGE_ROLES = MappingProxyType({
    "plan": "planner",
    "implement": "implementer",
    "diagnose": "tester",
    "fix": "fixer",
})

_PROTECTED_SOURCE_ROOTS = frozenset({
    ".git", ".runs", ".runtime", ".verification",
    ".harness-hidden-tests", "solution",
})


def _json_value(value: object, field_name: str = "Artifact.content") -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, f"{field_name}.{key}")
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [
            _json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise CodingModelWorkerError(
        f"{field_name} 含不可披露类型: {type(value).__qualname__}"
    )


def _content_digest(value: object) -> str:
    return sha256(_canonical(_json_value(value)).encode("utf-8")).hexdigest()


def _is_protected_source(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)
    return (
        parsed.is_absolute()
        or ".." in parsed.parts
        or not parsed.parts
        or parsed.parts[0] in _PROTECTED_SOURCE_ROOTS
        or parsed.name.startswith(".env")
    )


class ModelAblationWorker:
    """Provider-neutral model adapter; Runtime still owns validation and pass/fail."""

    def __init__(
        self,
        role: str,
        client: ModelClient,
        *,
        prompt_version: str = CODING_PROMPT_VERSION,
        max_context_chars: int = 60_000,
        max_file_chars: int = 20_000,
        usage_source: UsageSource = UsageSource.MODEL,
    ) -> None:
        if role not in set(_STAGE_ROLES.values()):
            raise ValueError(f"不支持的模型 Worker Role: {role}")
        if max_context_chars < 2_000 or max_file_chars <= 0:
            raise ValueError("模型上下文和单文件披露上限无效")
        if not prompt_version.strip():
            raise ValueError("prompt_version 不能为空")
        self.role = role
        self.client = client
        self.prompt_version = prompt_version
        self.max_context_chars = max_context_chars
        self.max_file_chars = max_file_chars
        self.usage_source = UsageSource(usage_source)
        self.prepared_invocations: list[PreparedModelInvocation] = []

    def _project_artifacts(
        self, request: AblationWorkerRequest
    ) -> tuple[dict[str, object], tuple[ArtifactDisclosure, ...]]:
        required = request.stage.required_kinds
        actual = frozenset(item.kind for item in request.visible_artifacts.values())
        missing = required - actual
        unexpected = actual - request.stage.visible_kinds
        if missing or unexpected:
            raise CodingModelWorkerError(
                f"可见 Artifact 协议不匹配，缺少 {sorted(missing)}，"
                f"越界 {sorted(unexpected)}"
            )

        projected: dict[str, object] = {}
        disclosures: list[ArtifactDisclosure] = []
        source_items: list[tuple[str, Artifact]] = []
        for name, artifact in sorted(request.visible_artifacts.items()):
            if artifact.kind == "core:source_snapshot":
                source_items.append((name, artifact))
                continue
            content = _json_value(artifact.content)
            serialized = _canonical(content)
            projected[name] = {"kind": artifact.kind, "content": content}
            disclosures.append(ArtifactDisclosure(
                name,
                artifact.kind,
                _content_digest(artifact.content),
                len(serialized),
                len(serialized),
                False,
            ))

        if len(source_items) != 1:
            raise CodingModelWorkerError("每次模型调用必须且只能披露一个源码快照")
        source_name, source_artifact = source_items[0]
        if not isinstance(source_artifact.content, Mapping):
            raise CodingModelWorkerError("源码快照必须是 path -> content 对象")
        source_files: list[tuple[str, str]] = []
        for raw_path, raw_content in sorted(
            source_artifact.content.items(), key=lambda pair: str(pair[0])
        ):
            path = str(raw_path).replace("\\", "/")
            if _is_protected_source(path):
                raise CodingModelWorkerError(f"源码快照包含受保护路径: {path}")
            if not isinstance(raw_content, str):
                raise CodingModelWorkerError(f"源码文件必须是文本: {path}")
            source_files.append((path, raw_content))
        if not source_files:
            raise CodingModelWorkerError("源码快照不能为空")

        disclosed_source: dict[str, str] = {}
        projected[source_name] = {
            "kind": source_artifact.kind,
            "content": disclosed_source,
        }
        base = {
            "task_id": request.task_id,
            "strategy": request.strategy.value,
            "stage_id": request.stage.stage_id,
            "allowed_paths": list(request.allowed_paths),
            "artifacts": projected,
        }
        if len(_canonical(base)) >= self.max_context_chars:
            raise CodingModelWorkerError("非源码 Artifact 已超过模型上下文披露上限")

        file_audit: list[Mapping[str, object]] = []
        for path, content in source_files:
            candidate = content[:self.max_file_chars]
            disclosed_source[path] = candidate
            while candidate and len(_canonical(base)) > self.max_context_chars:
                overflow = len(_canonical(base)) - self.max_context_chars
                candidate = candidate[:max(0, len(candidate) - overflow - 1)]
                disclosed_source[path] = candidate
            if len(_canonical(base)) > self.max_context_chars:
                disclosed_source.pop(path, None)
                candidate = ""
            file_audit.append(MappingProxyType({
                "path": path,
                "sha256": sha256(content.encode("utf-8")).hexdigest(),
                "original_chars": len(content),
                "disclosed_chars": len(candidate),
                "truncated": len(candidate) != len(content),
            }))
        if not any(item["disclosed_chars"] for item in file_audit):
            raise CodingModelWorkerError("模型上下文上限不足以披露任何源码")

        source_serialized = _canonical(_json_value(source_artifact.content))
        disclosed_serialized = _canonical(disclosed_source)
        disclosures.append(ArtifactDisclosure(
            source_name,
            source_artifact.kind,
            _content_digest(source_artifact.content),
            len(source_serialized),
            len(disclosed_serialized),
            any(bool(item["truncated"]) for item in file_audit),
            tuple(file_audit),
        ))
        return base, tuple(disclosures)

    def prepare(self, request: AblationWorkerRequest) -> PreparedModelInvocation:
        expected_role = _STAGE_ROLES.get(request.stage.stage_id)
        if expected_role is None or expected_role != self.role:
            raise CodingModelWorkerError(
                f"{self.role} 不能执行 stage {request.stage.stage_id}"
            )
        system_prompt, schema = _STAGE_PROTOCOLS[request.stage.stage_id]
        payload, disclosures = self._project_artifacts(request)
        user_payload = _canonical({
            "protocol_version": CODING_MODEL_PROTOCOL_VERSION,
            "prompt_version": self.prompt_version,
            "input": payload,
        })
        required_capabilities = frozenset({
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
        })
        if request.stage.stage_id in {"implement", "fix"}:
            required_capabilities |= frozenset({ModelCapability.TOOL_CALLING})
        model_request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    f"协议 {CODING_MODEL_PROTOCOL_VERSION}；Prompt {self.prompt_version}。"
                    f"{system_prompt} 输出必须严格符合 JSON Schema。"
                ),)),
                ModelMessage("user", (TextContentPart(user_payload),)),
            ),
            required_capabilities,
            schema,
        )
        request_material = {
            "messages": [
                {
                    "role": message.role,
                    "content": [part.text for part in message.content],
                }
                for message in model_request.messages
            ],
            "required_capabilities": sorted(
                item.value for item in required_capabilities
            ),
            "response_schema": schema,
        }
        return PreparedModelInvocation(
            model_request,
            sha256(_canonical(request_material).encode("utf-8")).hexdigest(),
            self.prompt_version,
            len(user_payload),
            disclosures,
        )

    def run_experiment(
        self, request: AblationWorkerRequest
    ) -> AblationWorkerResponse:
        prepared = self.prepare(request)
        require_capabilities(
            self.client.capabilities,
            prepared.request.required_capabilities,
        )
        self.prepared_invocations.append(prepared)
        response = self.client.generate_structured(prepared.request)
        metadata: dict[str, object] = {
            "protocol_version": CODING_MODEL_PROTOCOL_VERSION,
            "prompt_version": self.prompt_version,
            "request_sha256": prepared.request_sha256,
            "source_disclosure": dict(prepared.audit_dict()),
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
            "latency_ms": response.latency_ms,
        }
        stage_id = request.stage.stage_id
        if stage_id == "plan":
            parsed = _parse_plan(response.data, request.allowed_paths)
            draft = ArtifactDraft(parsed, kind="core:plan", metadata=metadata)
            summary = str(parsed["summary"])
        elif stage_id == "diagnose":
            parsed = _parse_diagnosis(response.data, request.allowed_paths)
            draft = ArtifactDraft(
                parsed, kind="core:test_diagnosis", metadata=metadata
            )
            summary = str(parsed["summary"])
        else:
            plan, assumptions = _parse_patch(response.data, request.allowed_paths)
            metadata["assumptions"] = assumptions
            draft = ArtifactDraft(plan, kind="core:patch", metadata=metadata)
            summary = plan.summary
        return AblationWorkerResponse(
            draft,
            summary,
            AblationUsage(
                self.usage_source,
                response.usage.input_tokens,
                response.usage.output_tokens,
            ),
        )


def build_model_ablation_registry(
    clients: Mapping[str, ModelClient],
    *,
    usage_source: UsageSource = UsageSource.MODEL,
    prompt_version: str = CODING_PROMPT_VERSION,
    max_context_chars: int = 60_000,
    max_file_chars: int = 20_000,
) -> tuple[WorkerRegistry, Mapping[str, ModelAblationWorker]]:
    required_roles = frozenset(_STAGE_ROLES.values())
    if set(clients) != required_roles:
        raise ValueError(
            f"模型客户端 Role 必须恰好为 {sorted(required_roles)}"
        )
    definitions = (
        (
            "planner", "model-planner", "model-planner-principal",
            {"task_planning"},
            {"core:coding_requirement", "core:source_snapshot"},
            {"core:plan"},
        ),
        (
            "implementer", "model-implementer", "model-implementer-principal",
            {"code_generation"},
            {"core:coding_requirement", "core:source_snapshot", "core:plan"},
            {"core:patch"},
        ),
        (
            "tester", "model-tester", "model-tester-principal",
            {"failure_analysis"},
            {
                "core:coding_requirement", "core:source_snapshot", "core:plan",
                "core:validator_feedback",
            },
            {"core:test_diagnosis"},
        ),
        (
            "fixer", "model-fixer", "model-fixer-principal",
            {"code_repair"},
            {
                "core:coding_requirement", "core:source_snapshot", "core:plan",
                "core:validator_feedback", "core:test_diagnosis",
            },
            {"core:patch"},
        ),
    )
    registry = WorkerRegistry()
    workers: dict[str, ModelAblationWorker] = {}
    for role, worker_id, principal, capabilities, inputs, outputs in definitions:
        worker = ModelAblationWorker(
            role,
            clients[role],
            prompt_version=prompt_version,
            max_context_chars=max_context_chars,
            max_file_chars=max_file_chars,
            usage_source=usage_source,
        )
        registry.register_worker(
            WorkerDescriptor(
                worker_id,
                role,
                frozenset(capabilities),
                frozenset(inputs),
                frozenset(outputs),
                frozenset({"model-eval"}),
                principal_id=principal,
            ),
            worker,
        )
        workers[role] = worker
    return registry, MappingProxyType(workers)
