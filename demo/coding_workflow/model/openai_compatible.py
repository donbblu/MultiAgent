from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .base import ModelError
from .config import ModelConfig


class OpenAICompatibleClient:
    """面向 OpenAI Chat Completions 兼容接口的供应商无关客户端。"""

    def __init__(self, config: ModelConfig) -> None:
        config.validate()
        self.config = config

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise ModelError(f"缺少模型凭据环境变量 {self.config.api_key_env}")
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": self.config.max_tokens,
                "stream": False,
            },
            ensure_ascii=False,
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
                return result
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
