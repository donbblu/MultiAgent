from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class VisionForgeSchemaError(ValueError):
    """模型或 API 提交的数据不满足 VisionForge 稳定协议。"""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisionForgeSchemaError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _string_mapping(value: object, field_name: str) -> Mapping[str, str]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise VisionForgeSchemaError(f"{field_name} 必须是字符串到字符串的对象")
    return MappingProxyType(dict(value))


def _object_list(value: object, field_name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise VisionForgeSchemaError(f"{field_name} 必须是对象数组")
    return value


@dataclass(frozen=True)
class ViewportSpec:
    width: int
    height: int
    device_scale_factor: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not 320 <= self.width <= 3840:
            raise VisionForgeSchemaError("viewport.width 必须在 320 到 3840 之间")
        if isinstance(self.height, bool) or not 320 <= self.height <= 2160:
            raise VisionForgeSchemaError("viewport.height 必须在 320 到 2160 之间")
        if isinstance(self.device_scale_factor, bool) or not 0.5 <= self.device_scale_factor <= 3:
            raise VisionForgeSchemaError("device_scale_factor 必须在 0.5 到 3 之间")

    def to_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "device_scale_factor": self.device_scale_factor,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ViewportSpec":
        if not isinstance(value, dict):
            raise VisionForgeSchemaError("viewport 必须是对象")
        try:
            return cls(
                int(value["width"]),
                int(value["height"]),
                float(value.get("device_scale_factor", 1.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VisionForgeSchemaError(f"viewport 字段无效: {exc}") from exc


@dataclass(frozen=True)
class LayoutRegion:
    region_id: str
    role: str
    order: int
    children: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "region_id", _required_text(self.region_id, "region_id"))
        object.__setattr__(self, "role", _required_text(self.role, "region.role"))
        if isinstance(self.order, bool) or self.order < 0:
            raise VisionForgeSchemaError("region.order 不能小于 0")
        if any(not isinstance(item, str) or not item.strip() for item in self.children):
            raise VisionForgeSchemaError("region.children 必须是非空字符串数组")

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "role": self.role,
            "order": self.order,
            "children": list(self.children),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "LayoutRegion":
        children = value.get("children", [])
        if not isinstance(children, list) or not all(isinstance(item, str) for item in children):
            raise VisionForgeSchemaError("region.children 必须是字符串数组")
        try:
            return cls(
                _required_text(value.get("region_id"), "region_id"),
                _required_text(value.get("role"), "region.role"),
                int(value.get("order", 0)),
                tuple(children),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, VisionForgeSchemaError):
                raise
            raise VisionForgeSchemaError(f"layout 字段无效: {exc}") from exc


@dataclass(frozen=True)
class UIComponent:
    component_id: str
    component_type: str
    region_id: str
    text: str = ""
    test_id: str = ""
    properties: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_id", _required_text(self.component_id, "component_id"))
        object.__setattr__(self, "component_type", _required_text(self.component_type, "component_type"))
        object.__setattr__(self, "region_id", _required_text(self.region_id, "component.region_id"))
        if not isinstance(self.text, str) or not isinstance(self.test_id, str):
            raise VisionForgeSchemaError("component.text 和 test_id 必须是字符串")
        object.__setattr__(
            self, "properties", _string_mapping(dict(self.properties), "component.properties")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "component_type": self.component_type,
            "region_id": self.region_id,
            "text": self.text,
            "test_id": self.test_id,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "UIComponent":
        return cls(
            _required_text(value.get("component_id"), "component_id"),
            _required_text(value.get("component_type"), "component_type"),
            _required_text(value.get("region_id"), "component.region_id"),
            str(value.get("text", "")),
            str(value.get("test_id", "")),
            _string_mapping(value.get("properties", {}), "component.properties"),
        )


class InteractionAction(str, Enum):
    CLICK = "click"
    FILL = "fill"
    EXPECT_VISIBLE = "expect_visible"
    EXPECT_TEXT = "expect_text"
    EXPECT_URL = "expect_url"


@dataclass(frozen=True)
class InteractionSpec:
    interaction_id: str
    action: InteractionAction
    target: str
    value: str = ""
    expected: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "interaction_id", _required_text(self.interaction_id, "interaction_id"))
        object.__setattr__(self, "target", _required_text(self.target, "interaction.target"))
        if not isinstance(self.action, InteractionAction):
            raise VisionForgeSchemaError("interaction.action 不在允许范围")
        if not isinstance(self.value, str) or not isinstance(self.expected, str):
            raise VisionForgeSchemaError("interaction.value/expected 必须是字符串")

    def to_dict(self) -> dict[str, object]:
        return {
            "interaction_id": self.interaction_id,
            "action": self.action.value,
            "target": self.target,
            "value": self.value,
            "expected": self.expected,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "InteractionSpec":
        try:
            action = InteractionAction(str(value.get("action", "")))
        except ValueError as exc:
            raise VisionForgeSchemaError("interaction.action 不在允许范围") from exc
        return cls(
            _required_text(value.get("interaction_id"), "interaction_id"),
            action,
            _required_text(value.get("target"), "interaction.target"),
            str(value.get("value", "")),
            str(value.get("expected", "")),
        )


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    kind: str
    target: str
    expected: str

    def __post_init__(self) -> None:
        for field_name in ("criterion_id", "kind", "target", "expected"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "criterion_id": self.criterion_id,
            "kind": self.kind,
            "target": self.target,
            "expected": self.expected,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "AcceptanceCriterion":
        return cls(*(
            _required_text(value.get(key), key)
            for key in ("criterion_id", "kind", "target", "expected")
        ))


@dataclass(frozen=True)
class UISpec:
    schema_version: str
    page_type: str
    viewport: ViewportSpec
    layout: tuple[LayoutRegion, ...]
    components: tuple[UIComponent, ...]
    texts: tuple[str, ...]
    interactions: tuple[InteractionSpec, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    style_tokens: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    CURRENT_VERSION = "1.0"

    def __post_init__(self) -> None:
        if self.schema_version != self.CURRENT_VERSION:
            raise VisionForgeSchemaError(
                f"不支持的 UI Spec 版本: {self.schema_version}"
            )
        object.__setattr__(self, "page_type", _required_text(self.page_type, "page_type"))
        if not self.layout or not self.components or not self.acceptance_criteria:
            raise VisionForgeSchemaError("UI Spec 必须包含布局、组件和验收标准")
        region_ids = [item.region_id for item in self.layout]
        if len(region_ids) != len(set(region_ids)):
            raise VisionForgeSchemaError("layout.region_id 不能重复")
        child_region_ids = [
            child.strip() for region in self.layout for child in region.children
        ]
        all_region_ids = [*region_ids, *child_region_ids]
        if len(all_region_ids) != len(set(all_region_ids)):
            raise VisionForgeSchemaError("布局区域及其 children ID 不能重复")
        component_ids = [item.component_id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise VisionForgeSchemaError("component_id 不能重复")
        unknown_regions = {
            item.region_id
            for item in self.components
            if item.region_id not in all_region_ids
        }
        if unknown_regions:
            raise VisionForgeSchemaError(
                f"组件引用不存在的布局区域: {sorted(unknown_regions)}"
            )
        if any(not isinstance(item, str) or not item.strip() for item in self.texts):
            raise VisionForgeSchemaError("texts 必须是非空字符串数组")
        object.__setattr__(
            self, "style_tokens", _string_mapping(dict(self.style_tokens), "style_tokens")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "page_type": self.page_type,
            "viewport": self.viewport.to_dict(),
            "layout": [item.to_dict() for item in self.layout],
            "components": [item.to_dict() for item in self.components],
            "texts": list(self.texts),
            "interactions": [item.to_dict() for item in self.interactions],
            "acceptance_criteria": [
                item.to_dict() for item in self.acceptance_criteria
            ],
            "style_tokens": dict(self.style_tokens),
        }

    @classmethod
    def from_dict(cls, value: object) -> "UISpec":
        if not isinstance(value, dict):
            raise VisionForgeSchemaError("UI Spec 必须是对象")
        texts = value.get("texts", [])
        if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
            raise VisionForgeSchemaError("texts 必须是字符串数组")
        return cls(
            _required_text(value.get("schema_version"), "schema_version"),
            _required_text(value.get("page_type"), "page_type"),
            ViewportSpec.from_dict(value.get("viewport")),
            tuple(LayoutRegion.from_dict(item) for item in _object_list(value.get("layout"), "layout")),
            tuple(UIComponent.from_dict(item) for item in _object_list(value.get("components"), "components")),
            tuple(texts),
            tuple(InteractionSpec.from_dict(item) for item in _object_list(value.get("interactions", []), "interactions")),
            tuple(AcceptanceCriterion.from_dict(item) for item in _object_list(value.get("acceptance_criteria"), "acceptance_criteria")),
            _string_mapping(value.get("style_tokens", {}), "style_tokens"),
        )


class VisualSeverity(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class VisualCategory(str, Enum):
    LAYOUT = "layout"
    VISUAL_HIERARCHY = "visual_hierarchy"
    MISSING_COMPONENT = "missing_component"
    OVERFLOW = "overflow"
    TYPOGRAPHY = "typography"
    COLOR = "color"
    SPACING = "spacing"
    USABILITY = "usability"
    OTHER = "other"


@dataclass(frozen=True)
class VisualIssue:
    severity: VisualSeverity
    region: str
    category: VisualCategory
    expected: str
    actual: str
    evidence: str
    suggestion: str

    def __post_init__(self) -> None:
        if not isinstance(self.severity, VisualSeverity):
            raise VisionForgeSchemaError("issue.severity 不在允许范围")
        if not isinstance(self.category, VisualCategory):
            raise VisionForgeSchemaError("issue.category 不在允许范围")
        for field_name in (
            "region", "expected", "actual", "evidence", "suggestion"
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), f"issue.{field_name}")
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity.value,
            "region": self.region,
            "category": self.category.value,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "VisualIssue":
        try:
            severity = VisualSeverity(str(value.get("severity", "")))
            category = VisualCategory(str(value.get("category", "")))
        except ValueError as exc:
            raise VisionForgeSchemaError("Visual Issue 枚举值无效") from exc
        return cls(
            severity,
            _required_text(value.get("region"), "issue.region"),
            category,
            _required_text(value.get("expected"), "issue.expected"),
            _required_text(value.get("actual"), "issue.actual"),
            _required_text(value.get("evidence"), "issue.evidence"),
            _required_text(value.get("suggestion"), "issue.suggestion"),
        )


@dataclass(frozen=True)
class VisualReview:
    schema_version: str
    passed: bool
    score: float
    summary: str
    issues: tuple[VisualIssue, ...] = ()

    CURRENT_VERSION = "1.0"

    def __post_init__(self) -> None:
        if self.schema_version != self.CURRENT_VERSION:
            raise VisionForgeSchemaError(
                f"不支持的 Visual Review 版本: {self.schema_version}"
            )
        if not isinstance(self.passed, bool):
            raise VisionForgeSchemaError("visual_review.passed 必须是布尔值")
        if isinstance(self.score, bool) or not 0 <= self.score <= 100:
            raise VisionForgeSchemaError("visual_review.score 必须在 0 到 100 之间")
        object.__setattr__(self, "summary", _required_text(self.summary, "visual_review.summary"))

    @property
    def blocking_issues(self) -> tuple[VisualIssue, ...]:
        return tuple(
            item for item in self.issues
            if item.severity in {VisualSeverity.P1, VisualSeverity.P2}
        )

    def eligible_for_runtime_pass(self, minimum_score: float) -> bool:
        """模型的 passed 只是证据；Runtime 仍需组合其他确定性门禁。"""
        return self.passed and self.score >= minimum_score and not self.blocking_issues

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "score": self.score,
            "summary": self.summary,
            "issues": [item.to_dict() for item in self.issues],
        }

    @classmethod
    def from_dict(cls, value: object) -> "VisualReview":
        if not isinstance(value, dict):
            raise VisionForgeSchemaError("Visual Review 必须是对象")
        passed = value.get("passed")
        score = value.get("score")
        if not isinstance(passed, bool):
            raise VisionForgeSchemaError("visual_review.passed 必须是布尔值")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise VisionForgeSchemaError("visual_review.score 必须是数字")
        return cls(
            _required_text(value.get("schema_version"), "schema_version"),
            passed,
            float(score),
            _required_text(value.get("summary"), "visual_review.summary"),
            tuple(VisualIssue.from_dict(item) for item in _object_list(value.get("issues", []), "issues")),
        )


@dataclass(frozen=True)
class BrowserAssertion:
    interaction_id: str
    action: InteractionAction
    target: str
    passed: bool
    evidence: str = ""
    error: str = ""
    duration_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "interaction_id", _required_text(self.interaction_id, "browser_assertion.interaction_id")
        )
        object.__setattr__(self, "target", _required_text(self.target, "browser_assertion.target"))
        if not isinstance(self.action, InteractionAction):
            raise VisionForgeSchemaError("browser_assertion.action 不在允许范围")
        if not isinstance(self.passed, bool):
            raise VisionForgeSchemaError("browser_assertion.passed 必须是布尔值")
        if not isinstance(self.evidence, str) or not isinstance(self.error, str):
            raise VisionForgeSchemaError("browser_assertion evidence/error 必须是字符串")
        if self.duration_ms < 0:
            raise VisionForgeSchemaError("browser_assertion.duration_ms 不能小于 0")
        if self.passed and self.error:
            raise VisionForgeSchemaError("通过的 Browser Assertion 不能包含错误")
        if not self.passed and not self.error:
            raise VisionForgeSchemaError("失败的 Browser Assertion 必须包含错误")

    def to_dict(self) -> dict[str, object]:
        return {
            "interaction_id": self.interaction_id,
            "action": self.action.value,
            "target": self.target,
            "passed": self.passed,
            "evidence": self.evidence,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BrowserAssertion":
        try:
            action = InteractionAction(str(value.get("action", "")))
        except ValueError as exc:
            raise VisionForgeSchemaError("browser_assertion.action 不在允许范围") from exc
        passed = value.get("passed")
        if not isinstance(passed, bool):
            raise VisionForgeSchemaError("browser_assertion.passed 必须是布尔值")
        return cls(
            _required_text(value.get("interaction_id"), "browser_assertion.interaction_id"),
            action,
            _required_text(value.get("target"), "browser_assertion.target"),
            passed,
            str(value.get("evidence", "")),
            str(value.get("error", "")),
            int(value.get("duration_ms", 0)),
        )


@dataclass(frozen=True)
class BrowserConsoleMessage:
    level: str
    message: str

    ALLOWED_LEVELS = frozenset({"debug", "log", "info", "warning", "warn", "error"})

    def __post_init__(self) -> None:
        if self.level not in self.ALLOWED_LEVELS:
            raise VisionForgeSchemaError(f"未知浏览器控制台级别: {self.level}")
        object.__setattr__(self, "message", _required_text(self.message, "console.message"))

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "message": self.message}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "BrowserConsoleMessage":
        return cls(
            _required_text(value.get("level"), "console.level"),
            _required_text(value.get("message"), "console.message"),
        )


@dataclass(frozen=True)
class BrowserRunResult:
    schema_version: str
    passed: bool
    url: str
    viewport: ViewportSpec
    assertions: tuple[BrowserAssertion, ...]
    console_messages: tuple[BrowserConsoleMessage, ...]
    page_errors: tuple[str, ...]
    network_errors: tuple[str, ...]
    screenshot_artifact_ref: str
    duration_ms: int

    CURRENT_VERSION = "1.0"

    def __post_init__(self) -> None:
        if self.schema_version != self.CURRENT_VERSION:
            raise VisionForgeSchemaError(
                f"不支持的 Browser Run 版本: {self.schema_version}"
            )
        if not isinstance(self.passed, bool):
            raise VisionForgeSchemaError("browser_run.passed 必须是布尔值")
        if not self.url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise VisionForgeSchemaError("Browser Run 只接受本地 HTTP URL")
        if not self.assertions:
            raise VisionForgeSchemaError("Browser Run 至少需要一个受控交互或断言")
        if any(not isinstance(item, str) or not item for item in (*self.page_errors, *self.network_errors)):
            raise VisionForgeSchemaError("Browser Run 错误必须是非空字符串")
        if not self.screenshot_artifact_ref.startswith("artifact://"):
            raise VisionForgeSchemaError("Browser Run 必须引用截图 Artifact")
        if self.duration_ms < 0:
            raise VisionForgeSchemaError("browser_run.duration_ms 不能小于 0")
        effective = (
            all(item.passed for item in self.assertions)
            and not any(item.level == "error" for item in self.console_messages)
            and not self.page_errors
            and not self.network_errors
        )
        if self.passed != effective:
            raise VisionForgeSchemaError("Browser Run passed 与确定性证据不一致")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "url": self.url,
            "viewport": self.viewport.to_dict(),
            "assertions": [item.to_dict() for item in self.assertions],
            "console_messages": [item.to_dict() for item in self.console_messages],
            "page_errors": list(self.page_errors),
            "network_errors": list(self.network_errors),
            "screenshot_artifact_ref": self.screenshot_artifact_ref,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_runner_payload(
        cls, value: object, screenshot_artifact_ref: str
    ) -> "BrowserRunResult":
        if not isinstance(value, dict):
            raise VisionForgeSchemaError("Browser Run 必须是对象")
        passed = value.get("passed")
        if not isinstance(passed, bool):
            raise VisionForgeSchemaError("browser_run.passed 必须是布尔值")
        page_errors = value.get("page_errors", [])
        network_errors = value.get("network_errors", [])
        if not isinstance(page_errors, list) or not all(isinstance(item, str) for item in page_errors):
            raise VisionForgeSchemaError("browser_run.page_errors 必须是字符串数组")
        if not isinstance(network_errors, list) or not all(isinstance(item, str) for item in network_errors):
            raise VisionForgeSchemaError("browser_run.network_errors 必须是字符串数组")
        return cls(
            _required_text(value.get("schema_version"), "browser_run.schema_version"),
            passed,
            _required_text(value.get("url"), "browser_run.url"),
            ViewportSpec.from_dict(value.get("viewport")),
            tuple(BrowserAssertion.from_dict(item) for item in _object_list(value.get("assertions"), "assertions")),
            tuple(BrowserConsoleMessage.from_dict(item) for item in _object_list(value.get("console_messages", []), "console_messages")),
            tuple(page_errors),
            tuple(network_errors),
            screenshot_artifact_ref,
            int(value.get("duration_ms", 0)),
        )
