from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Union


class ModelError(RuntimeError):
    pass


class ModelCapabilityError(ModelError):
    pass


class ModelCapability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"


@dataclass(frozen=True)
class TextContentPart:
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("文本输入不能为空")


@dataclass(frozen=True)
class ImageContentPart:
    artifact_ref: str
    mime_type: str
    data: bytes
    detail: str = "high"

    def __post_init__(self) -> None:
        if not self.artifact_ref.startswith("artifact://"):
            raise ValueError("图片输入必须引用 Artifact")
        if self.mime_type not in {"image/png", "image/jpeg"}:
            raise ValueError("图片输入只支持 PNG 或 JPEG")
        if not self.data:
            raise ValueError("图片输入不能为空")
        if self.detail not in {"low", "high", "auto"}:
            raise ValueError("图片 detail 必须是 low、high 或 auto")


ModelContentPart = Union[TextContentPart, ImageContentPart]


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: tuple[ModelContentPart, ...]

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError(f"不支持的模型消息角色: {self.role}")
        if not self.content:
            raise ValueError("模型消息内容不能为空")


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    required_capabilities: frozenset[ModelCapability] = frozenset({
        ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT,
    })
    response_schema: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("模型请求至少需要一条消息")
        object.__setattr__(
            self, "response_schema", MappingProxyType(dict(self.response_schema))
        )

    @classmethod
    def from_text_messages(
        cls,
        messages: list[dict[str, str]],
        *,
        response_schema: Mapping[str, object] | None = None,
    ) -> "ModelRequest":
        parsed: list[ModelMessage] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                raise ValueError("文本消息必须包含字符串 role/content")
            parsed.append(ModelMessage(role, (TextContentPart(content),)))
        return cls(
            tuple(parsed),
            response_schema=response_schema or MappingProxyType({}),
        )


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("模型 Token 使用量不能小于 0")


@dataclass(frozen=True)
class ModelResponse:
    data: Mapping[str, Any]
    provider: str
    model: str
    usage: ModelUsage = ModelUsage()
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("模型响应必须记录 provider 和 model")
        if self.latency_ms < 0:
            raise ValueError("模型延迟不能小于 0")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


def require_capabilities(
    available: frozenset[ModelCapability],
    required: frozenset[ModelCapability],
) -> None:
    missing = required - available
    if missing:
        raise ModelCapabilityError(
            "模型缺少能力: " + ", ".join(sorted(item.value for item in missing))
        )


class ModelClient(Protocol):
    @property
    def capabilities(self) -> frozenset[ModelCapability]: ...

    def generate_structured(self, request: ModelRequest) -> ModelResponse: ...

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, Any]: ...
