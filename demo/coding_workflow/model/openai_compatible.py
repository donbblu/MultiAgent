from __future__ import annotations

import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .base import (
    ImageContentPart,
    ModelCapability,
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    TextContentPart,
    require_capabilities,
)
from .config import ModelConfig, StructuredOutputMode


class OpenAICompatibleClient:
    """面向 OpenAI Chat Completions 兼容接口的供应商无关客户端。"""

    def __init__(self, config: ModelConfig) -> None:
        config.validate()
        self.config = config

    @property
    def capabilities(self) -> frozenset[ModelCapability]:
        return self.config.capabilities

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return dict(self.generate_structured(ModelRequest.from_text_messages(messages)).data)

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        required = set(request.required_capabilities)
        if any(
            isinstance(part, ImageContentPart)
            for message in request.messages for part in message.content
        ):
            required.add(ModelCapability.VISION)
        require_capabilities(self.capabilities, frozenset(required))
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise ModelError(f"缺少模型凭据环境变量 {self.config.api_key_env}")
        payload = json.dumps(
            self._request_payload(request), ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        started = time.monotonic()
        for attempt in range(self.config.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    raw = response.read(self.config.max_response_bytes + 1)
                if len(raw) > self.config.max_response_bytes:
                    raise ModelError("模型响应超过大小限制")
                envelope = json.loads(raw.decode("utf-8"))
                content = envelope["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ModelError("模型返回空内容")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ModelError("模型 JSON 顶层必须是对象")
                usage = envelope.get("usage", {})
                if not isinstance(usage, dict):
                    usage = {}
                return ModelResponse(
                    result,
                    self.config.provider,
                    self.config.model,
                    ModelUsage(
                        int(usage.get("prompt_tokens", 0) or 0),
                        int(usage.get("completion_tokens", 0) or 0),
                        int(usage.get("total_tokens", 0) or 0),
                    ),
                    int((time.monotonic() - started) * 1000),
                )
            except urllib.error.HTTPError as exc:
                last_error = ModelError(
                    f"模型供应商 {self.config.provider} 返回 HTTP {exc.code}"
                )
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                json.JSONDecodeError,
                KeyError,
                IndexError,
                ModelError,
            ) as exc:
                last_error = exc
            if attempt < self.config.max_retries:
                time.sleep(2**attempt)
        raise ModelError(
            f"模型供应商 {self.config.provider} 请求失败: {last_error}"
        )

    def _request_payload(self, request: ModelRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": self._request_messages(request),
            "response_format": self._response_format(
                request, self.config.structured_output_mode
            ),
            "temperature": self.config.temperature,
            "stream": False,
            **dict(self.config.request_options),
        }
        if self.config.include_max_tokens:
            payload["max_tokens"] = self.config.max_tokens
        return payload

    @staticmethod
    def _response_format(
        request: ModelRequest,
        mode: StructuredOutputMode = StructuredOutputMode.JSON_SCHEMA,
    ) -> dict[str, object]:
        if not request.response_schema or mode is StructuredOutputMode.JSON_OBJECT:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "strict": True,
                "schema": dict(request.response_schema),
            },
        }

    def _request_messages(self, request: ModelRequest) -> list[dict[str, object]]:
        messages = [self._message_payload(message) for message in request.messages]
        if (
            request.response_schema
            and self.config.structured_output_mode is StructuredOutputMode.JSON_OBJECT
        ):
            instruction = (
                "只输出一个 JSON 对象，不要使用 Markdown 或添加自然语言前后缀。"
                "输出必须符合以下 JSON Schema；Runtime 会再次严格校验："
                + json.dumps(
                    dict(request.response_schema),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            messages.insert(0, {"role": "system", "content": instruction})
        return messages

    @staticmethod
    def _message_payload(message: ModelMessage) -> dict[str, object]:
        if len(message.content) == 1 and isinstance(message.content[0], TextContentPart):
            return {"role": message.role, "content": message.content[0].text}
        content: list[dict[str, object]] = []
        for part in message.content:
            if isinstance(part, TextContentPart):
                content.append({"type": "text", "text": part.text})
            else:
                encoded = base64.b64encode(part.data).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part.mime_type};base64,{encoded}",
                        "detail": part.detail,
                    },
                })
        return {"role": message.role, "content": content}
