from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from ..artifacts import Artifact, ArtifactStore
from ..model import (
    ImageContentPart,
    ModelCapability,
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContentPart,
    require_capabilities,
)
from ..models import FileChange, ImplementationPlan
from ..workspace import ProjectWorkspace
from .assets import ImageArtifactRef, ImageAssetStore
from .artifact_types import (
    ACTUAL_SCREENSHOT,
    BROWSER_RUN,
    REFERENCE_IMAGE,
    UI_SPEC,
    VISUAL_REVIEW,
)
from .contracts import BrowserRunResult, UISpec, VisualReview


class VisionForgeAgentError(RuntimeError):
    pass


UI_SPEC_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "page_type", "viewport", "layout", "components",
        "texts", "interactions", "acceptance_criteria", "style_tokens",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "page_type": {"type": "string", "minLength": 1},
        "viewport": {
            "type": "object",
            "additionalProperties": False,
            "required": ["width", "height", "device_scale_factor"],
            "properties": {
                "width": {"type": "integer", "minimum": 320, "maximum": 3840},
                "height": {"type": "integer", "minimum": 320, "maximum": 2160},
                "device_scale_factor": {"type": "number", "minimum": 0.5, "maximum": 3},
            },
        },
        "layout": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["region_id", "role", "order", "children"],
                "properties": {
                    "region_id": {"type": "string", "minLength": 1},
                    "role": {"type": "string", "minLength": 1},
                    "order": {"type": "integer", "minimum": 0},
                    "children": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "components": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "component_id", "component_type", "region_id", "text",
                    "test_id", "properties",
                ],
                "properties": {
                    "component_id": {"type": "string", "minLength": 1},
                    "component_type": {"type": "string", "minLength": 1},
                    "region_id": {"type": "string", "minLength": 1},
                    "text": {"type": "string"},
                    "test_id": {"type": "string"},
                    "properties": {
                        "type": "object", "additionalProperties": {"type": "string"},
                    },
                },
            },
        },
        "texts": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "interactions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["interaction_id", "action", "target", "value", "expected"],
                "properties": {
                    "interaction_id": {"type": "string", "minLength": 1},
                    "action": {
                        "type": "string",
                        "enum": ["click", "fill", "expect_visible", "expect_text", "expect_url"],
                    },
                    "target": {"type": "string", "minLength": 1},
                    "value": {"type": "string"},
                    "expected": {"type": "string"},
                },
            },
        },
        "acceptance_criteria": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["criterion_id", "kind", "target", "expected"],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1},
                    "kind": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "minLength": 1},
                },
            },
        },
        "style_tokens": {
            "type": "object", "additionalProperties": {"type": "string"},
        },
    },
}


IMPLEMENTATION_PLAN_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "changes", "suggested_checks"],
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "changes": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["path", "content", "reason"],
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "suggested_checks": {
            "type": "array",
            "items": {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
        },
    },
}


VISUAL_REVIEW_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "passed", "score", "summary", "issues"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "passed": {"type": "boolean"},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "summary": {"type": "string", "minLength": 1},
        "issues": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "severity", "region", "category", "expected", "actual",
                    "evidence", "suggestion",
                ],
                "properties": {
                    "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "region": {"type": "string", "minLength": 1},
                    "category": {
                        "type": "string",
                        "enum": [
                            "layout", "visual_hierarchy", "missing_component", "overflow",
                            "typography", "color", "spacing", "usability", "other",
                        ],
                    },
                    "expected": {"type": "string", "minLength": 1},
                    "actual": {"type": "string", "minLength": 1},
                    "evidence": {"type": "string", "minLength": 1},
                    "suggestion": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def _model_metadata(response: ModelResponse) -> dict[str, object]:
    return {
        "provider": response.provider,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.total_tokens,
        "latency_ms": response.latency_ms,
    }


def _require_task_artifact(
    artifacts: ArtifactStore,
    reference: str,
    *,
    task_id: str,
    kind: str,
) -> Artifact:
    artifact = artifacts.get(reference)
    if artifact.task_id != task_id or artifact.kind != kind:
        raise VisionForgeAgentError(
            f"Artifact {reference} 必须属于任务 {task_id} 且类型为 {kind}"
        )
    return artifact


def _image_part(
    artifacts: ArtifactStore,
    image_assets: ImageAssetStore,
    reference: str,
    *,
    task_id: str,
    kind: str,
) -> ImageContentPart:
    artifact = _require_task_artifact(
        artifacts, reference, task_id=task_id, kind=kind
    )
    image = ImageArtifactRef.from_dict(artifact.content)
    return ImageContentPart(
        reference,
        image.mime_type,
        image_assets.read(image),
        "high",
    )


@dataclass(frozen=True)
class AgentArtifactResult:
    artifact_ref: str
    response: ModelResponse


class RequirementAnalyst:
    def __init__(
        self,
        client: ModelClient,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
    ) -> None:
        self.client = client
        self.artifacts = artifacts
        self.image_assets = image_assets

    def analyze(
        self,
        *,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
    ) -> AgentArtifactResult:
        if not requirement.strip():
            raise ValueError("页面需求不能为空")
        image = _image_part(
            self.artifacts,
            self.image_assets,
            reference_image_artifact_ref,
            task_id=task_id,
            kind=REFERENCE_IMAGE,
        )
        request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    "你是 VisionForge Requirement Analyst。根据页面需求和参考图输出 UI Spec 1.0。"
                    "只描述可见结构、文本、布局、样式 token、受控交互和可验证验收标准；"
                    "不得输出代码、命令或自然语言前后缀。"
                ),)),
                ModelMessage("user", (
                    TextContentPart(f"页面需求：{requirement.strip()}\n参考图如下。"),
                    image,
                )),
            ),
            frozenset({
                ModelCapability.TEXT,
                ModelCapability.VISION,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            UI_SPEC_SCHEMA,
        )
        require_capabilities(self.client.capabilities, request.required_capabilities)
        response = self.client.generate_structured(request)
        ui_spec = UISpec.from_dict(dict(response.data))
        artifact_ref = self.artifacts.put(Artifact.create(
            "visionforge-ui-spec",
            task_id,
            ui_spec.to_dict(),
            kind=UI_SPEC,
            metadata={
                "reference_image_artifact_ref": reference_image_artifact_ref,
                **_model_metadata(response),
            },
        ))
        return AgentArtifactResult(artifact_ref, response)


class VisionForgeDeveloper:
    TEXT_SUFFIXES = frozenset({
        ".css", ".html", ".js", ".json", ".md", ".svg", ".ts", ".txt", ".vue",
    })

    def __init__(
        self,
        client: ModelClient,
        artifacts: ArtifactStore,
        *,
        max_context_chars: int = 200_000,
    ) -> None:
        if max_context_chars <= 0:
            raise ValueError("max_context_chars 必须大于 0")
        self.client = client
        self.artifacts = artifacts
        self.max_context_chars = max_context_chars

    def develop(
        self,
        *,
        task_id: str,
        requirement: str,
        ui_spec_artifact_ref: str,
        workspace: ProjectWorkspace,
        allowed_paths: Iterable[str],
        runtime_acceptance_spec: UISpec | None = None,
    ) -> AgentArtifactResult:
        ui_spec_artifact = _require_task_artifact(
            self.artifacts,
            ui_spec_artifact_ref,
            task_id=task_id,
            kind=UI_SPEC,
        )
        ui_spec = UISpec.from_dict(ui_spec_artifact.content)
        patterns = tuple(allowed_paths)
        files = self._source_snapshot(workspace, patterns)
        payload: dict[str, object] = {
            "requirement": requirement,
            "ui_spec": ui_spec.to_dict(),
            "allowed_paths": patterns,
            "project_files": files,
        }
        if runtime_acceptance_spec is not None:
            payload["runtime_acceptance"] = {
                "immutable": True,
                "interactions": [
                    item.to_dict() for item in runtime_acceptance_spec.interactions
                ],
                "acceptance_criteria": [
                    item.to_dict()
                    for item in runtime_acceptance_spec.acceptance_criteria
                ],
            }
        request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    "你是 VisionForge Developer。只输出 ImplementationPlan JSON。"
                    "changes 中每项必须是允许路径内的相对路径和完整文件内容；"
                    "runtime_acceptance 若存在则是不可修改且必须满足的测试契约；"
                    "不得执行命令，不得修改依赖、Runtime、.env、.git 或受保护文件。"
                ),)),
                ModelMessage("user", (TextContentPart(json.dumps(
                    payload, ensure_ascii=False
                )),)),
            ),
            frozenset({
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            IMPLEMENTATION_PLAN_SCHEMA,
        )
        require_capabilities(self.client.capabilities, request.required_capabilities)
        response = self.client.generate_structured(request)
        plan = self._implementation_plan(response.data)
        artifact_ref = self.artifacts.put(Artifact.create(
            "visionforge-implementation-plan",
            task_id,
            plan,
            kind="implementation_plan",
            metadata={
                "ui_spec_artifact_ref": ui_spec_artifact_ref,
                **_model_metadata(response),
            },
        ))
        return AgentArtifactResult(artifact_ref, response)

    def _source_snapshot(
        self,
        workspace: ProjectWorkspace,
        allowed_paths: tuple[str, ...],
    ) -> list[dict[str, str]]:
        remaining = self.max_context_chars
        result: list[dict[str, str]] = []
        for path in workspace.list_files():
            if Path(path).suffix.lower() not in self.TEXT_SUFFIXES:
                continue
            if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed_paths):
                continue
            if remaining <= 0:
                break
            content = workspace.read_text(path, max_chars=min(remaining, 80_000))
            result.append({"path": path, "content": content})
            remaining -= len(content)
        if not result:
            raise VisionForgeAgentError("允许路径内没有可提供给 Developer 的文本项目文件")
        return result

    @staticmethod
    def _implementation_plan(value: Mapping[str, object]) -> ImplementationPlan:
        try:
            summary = value["summary"]
            changes = value["changes"]
            checks = value.get("suggested_checks", [])
            if not isinstance(summary, str) or not summary.strip():
                raise TypeError("summary 必须是非空字符串")
            if not isinstance(changes, list) or not changes:
                raise TypeError("changes 必须是非空数组")
            if not isinstance(checks, list):
                raise TypeError("suggested_checks 必须是数组")
            parsed_changes: list[FileChange] = []
            for item in changes:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(key), str) for key in ("path", "content", "reason")
                ):
                    raise TypeError("每个 change 必须包含字符串 path/content/reason")
                if not item["path"].strip() or not item["reason"].strip():
                    raise TypeError("change.path/reason 不能为空")
                parsed_changes.append(FileChange(
                    item["path"], item["content"], item["reason"]
                ))
            if not all(
                isinstance(command, list)
                and command
                and all(isinstance(part, str) and part for part in command)
                for command in checks
            ):
                raise TypeError("每个 suggested_check 必须是非空字符串数组")
            return ImplementationPlan(summary.strip(), parsed_changes, checks)
        except (KeyError, TypeError) as exc:
            raise VisionForgeAgentError(
                f"Developer 输出不符合 ImplementationPlan: {exc}"
            ) from exc


class VisionForgeFixer(VisionForgeDeveloper):
    def fix(
        self,
        *,
        task_id: str,
        round_index: int,
        ui_spec_artifact_ref: str,
        browser_run_artifact_ref: str,
        visual_review_artifact_ref: str | None,
        current_implementation_artifact_ref: str,
        workspace: ProjectWorkspace,
        allowed_paths: Iterable[str],
    ) -> AgentArtifactResult:
        if round_index <= 0:
            raise ValueError("Fixer round_index 必须大于 0")
        ui_artifact = _require_task_artifact(
            self.artifacts, ui_spec_artifact_ref, task_id=task_id, kind=UI_SPEC
        )
        browser_artifact = _require_task_artifact(
            self.artifacts,
            browser_run_artifact_ref,
            task_id=task_id,
            kind=BROWSER_RUN,
        )
        visual_artifact = None
        if visual_review_artifact_ref is not None:
            visual_artifact = _require_task_artifact(
                self.artifacts,
                visual_review_artifact_ref,
                task_id=task_id,
                kind=VISUAL_REVIEW,
            )
        implementation = self.artifacts.get(current_implementation_artifact_ref)
        if implementation.task_id != task_id or not isinstance(
            implementation.content, ImplementationPlan
        ):
            raise VisionForgeAgentError("当前 Patch Artifact 无效")
        ui_spec = UISpec.from_dict(ui_artifact.content)
        if not isinstance(browser_artifact.content, dict):
            raise VisionForgeAgentError("Browser Run Artifact 内容必须是对象")
        screenshot_ref = browser_artifact.content.get("screenshot_artifact_ref")
        if not isinstance(screenshot_ref, str):
            raise VisionForgeAgentError("Browser Run 缺少截图 Artifact 引用")
        browser_run = BrowserRunResult.from_runner_payload(
            browser_artifact.content, screenshot_ref
        )
        visual_review = (
            VisualReview.from_dict(visual_artifact.content)
            if visual_artifact is not None
            else None
        )
        patterns = tuple(allowed_paths)
        files = self._source_snapshot(workspace, patterns)
        feedback_sources = ["browser_run"]
        payload: dict[str, object] = {
            "round_index": round_index,
            "ui_spec": ui_spec.to_dict(),
            "browser_run": browser_run.to_dict(),
            "previous_changed_files": [
                item.path for item in implementation.content.changes
            ],
            "allowed_paths": patterns,
            "project_files": files,
        }
        if visual_review is not None:
            feedback_sources.append("visual_review")
            payload["visual_review"] = visual_review.to_dict()
        request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    "你是 VisionForge Fixer。只根据请求中明确提供的结构化反馈 "
                    "生成最小局部 ImplementationPlan。只修复已报告问题，不改变需求、测试、"
                    "依赖或 Runtime；不得推测或使用未提供的视觉审查；"
                    "changes 必须包含允许路径内的完整文件内容。"
                ),)),
                ModelMessage("user", (TextContentPart(json.dumps(
                    payload, ensure_ascii=False
                )),)),
            ),
            frozenset({
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            IMPLEMENTATION_PLAN_SCHEMA,
        )
        require_capabilities(self.client.capabilities, request.required_capabilities)
        response = self.client.generate_structured(request)
        plan = self._implementation_plan(response.data)
        artifact_ref = self.artifacts.put(Artifact.create(
            f"visionforge-fix-plan-{round_index}",
            task_id,
            plan,
            kind="fix_plan",
            metadata={
                "round_index": round_index,
                "ui_spec_artifact_ref": ui_spec_artifact_ref,
                "browser_run_artifact_ref": browser_run_artifact_ref,
                "feedback_sources": feedback_sources,
                "replaces_artifact_ref": current_implementation_artifact_ref,
                **_model_metadata(response),
            },
        ))
        return AgentArtifactResult(artifact_ref, response)


class VisualReviewer:
    def __init__(
        self,
        client: ModelClient,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
    ) -> None:
        self.client = client
        self.artifacts = artifacts
        self.image_assets = image_assets

    def review(
        self,
        *,
        task_id: str,
        reference_image_artifact_ref: str,
        actual_screenshot_artifact_ref: str,
        ui_spec_artifact_ref: str,
        browser_run_artifact_ref: str,
        artifact_name: str = "visionforge-visual-review",
    ) -> AgentArtifactResult:
        ui_spec_artifact = _require_task_artifact(
            self.artifacts, ui_spec_artifact_ref, task_id=task_id, kind=UI_SPEC
        )
        browser_artifact = _require_task_artifact(
            self.artifacts,
            browser_run_artifact_ref,
            task_id=task_id,
            kind=BROWSER_RUN,
        )
        ui_spec = UISpec.from_dict(ui_spec_artifact.content)
        if not isinstance(browser_artifact.content, dict):
            raise VisionForgeAgentError("Browser Run Artifact 内容必须是对象")
        screenshot_ref = browser_artifact.content.get("screenshot_artifact_ref")
        if screenshot_ref != actual_screenshot_artifact_ref:
            raise VisionForgeAgentError("Browser Run 与实际截图 Artifact 引用不一致")
        browser_run = BrowserRunResult.from_runner_payload(
            browser_artifact.content,
            actual_screenshot_artifact_ref,
        )
        reference_image = _image_part(
            self.artifacts,
            self.image_assets,
            reference_image_artifact_ref,
            task_id=task_id,
            kind=REFERENCE_IMAGE,
        )
        actual_image = _image_part(
            self.artifacts,
            self.image_assets,
            actual_screenshot_artifact_ref,
            task_id=task_id,
            kind=ACTUAL_SCREENSHOT,
        )
        request = ModelRequest(
            (
                ModelMessage("system", (TextContentPart(
                    "你是 VisionForge Visual Reviewer。比较参考图和实际截图，"
                    "只输出 Visual Review 1.0 JSON。功能事实以 Browser Run 为准；"
                    "你只评价布局、层级、组件缺失、溢出、字体、颜色、间距和可用性。"
                ),)),
                ModelMessage("user", (
                    TextContentPart(json.dumps({
                        "ui_spec": ui_spec.to_dict(),
                        "browser_run": browser_run.to_dict(),
                        "image_order": ["reference", "actual"],
                    }, ensure_ascii=False)),
                    TextContentPart("参考图："),
                    reference_image,
                    TextContentPart("实际运行截图："),
                    actual_image,
                )),
            ),
            frozenset({
                ModelCapability.TEXT,
                ModelCapability.VISION,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            VISUAL_REVIEW_SCHEMA,
        )
        require_capabilities(self.client.capabilities, request.required_capabilities)
        response = self.client.generate_structured(request)
        visual_review = VisualReview.from_dict(dict(response.data))
        artifact_ref = self.artifacts.put(Artifact.create(
            artifact_name,
            task_id,
            visual_review.to_dict(),
            kind=VISUAL_REVIEW,
            metadata={
                "reference_image_artifact_ref": reference_image_artifact_ref,
                "actual_screenshot_artifact_ref": actual_screenshot_artifact_ref,
                "ui_spec_artifact_ref": ui_spec_artifact_ref,
                "browser_run_artifact_ref": browser_run_artifact_ref,
                **_model_metadata(response),
            },
        ))
        return AgentArtifactResult(artifact_ref, response)
