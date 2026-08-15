from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .base import ModelCapability


class StructuredOutputMode(str, Enum):
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


def load_env_file(path: Path) -> None:
    """加载简单 KEY=VALUE 文件；不覆盖已存在的进程环境变量。"""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    api_key_env: str
    base_url: str
    model: str
    timeout_seconds: int = 180
    max_tokens: int = 12000
    max_response_bytes: int = 2_000_000
    max_retries: int = 2
    capabilities: frozenset[ModelCapability] = frozenset({
        ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT,
    })
    structured_output_mode: StructuredOutputMode = StructuredOutputMode.JSON_SCHEMA
    request_options: tuple[tuple[str, object], ...] = ()
    include_max_tokens: bool = True

    def validate(self) -> None:
        if not self.provider.strip():
            raise ValueError("模型供应商不能为空")
        if not self.base_url.startswith("https://"):
            raise ValueError("模型 Base URL 必须使用 HTTPS")
        if not self.model.strip():
            raise ValueError("模型名称不能为空")
        if self.max_retries not in range(0, 6):
            raise ValueError("模型重试次数必须在 0 到 5 之间")
        if not self.capabilities:
            raise ValueError("模型至少需要声明一种能力")
        if not isinstance(self.structured_output_mode, StructuredOutputMode):
            raise ValueError("模型结构化输出模式无效")
        forbidden = {
            "model", "messages", "response_format", "temperature", "max_tokens",
            "stream",
        }
        option_names = [name for name, _ in self.request_options]
        if len(option_names) != len(set(option_names)):
            raise ValueError("模型附加请求参数不能重复")
        if forbidden.intersection(option_names):
            raise ValueError("模型附加请求参数不能覆盖 Runtime 核心字段")
        if not isinstance(self.include_max_tokens, bool):
            raise ValueError("include_max_tokens 必须是布尔值")
