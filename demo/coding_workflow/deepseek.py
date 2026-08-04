"""DeepSeek 兼容层；新代码应使用 coding_workflow.model 与 backends。"""

from dataclasses import dataclass

from .backends import StructuredCodingBackend
from .model import ModelConfig, ModelError, OpenAICompatibleClient, load_env_file


@dataclass(frozen=True)
class DeepSeekConfig(ModelConfig):
    provider: str = "deepseek"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"


DeepSeekError = ModelError
DeepSeekClient = OpenAICompatibleClient
DeepSeekCodingBackend = StructuredCodingBackend

__all__ = [
    "DeepSeekClient",
    "DeepSeekCodingBackend",
    "DeepSeekConfig",
    "DeepSeekError",
    "load_env_file",
]
