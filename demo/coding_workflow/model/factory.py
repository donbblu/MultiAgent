from __future__ import annotations

import os
from dataclasses import dataclass

from .base import ModelClient
from .config import ModelConfig
from .openai_compatible import OpenAICompatibleClient


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    api_key_env: str
    default_model: str
    protocol: str = "openai-chat-completions"


class ModelClientFactory:
    _providers: dict[str, ProviderPreset] = {
        "deepseek": ProviderPreset(
            name="deepseek",
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-v4-pro",
        )
    }

    @classmethod
    def register(cls, preset: ProviderPreset) -> None:
        cls._providers[preset.name] = preset

    @classmethod
    def config_from_env(
        cls, provider: str | None = None, model: str | None = None
    ) -> ModelConfig:
        provider_name = provider or os.environ.get("MODEL_PROVIDER", "deepseek")
        preset = cls._providers.get(provider_name)
        if preset is None:
            required = ["MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME"]
            missing = [name for name in required if not os.environ.get(name)]
            if missing:
                raise ValueError(
                    f"未知供应商 {provider_name}，且缺少自定义配置: {', '.join(missing)}"
                )
            return ModelConfig(
                provider=provider_name,
                api_key_env="MODEL_API_KEY",
                base_url=os.environ["MODEL_BASE_URL"],
                model=model or os.environ["MODEL_NAME"],
            )

        generic_key_present = bool(os.environ.get("MODEL_API_KEY"))
        return ModelConfig(
            provider=provider_name,
            api_key_env="MODEL_API_KEY" if generic_key_present else preset.api_key_env,
            base_url=os.environ.get("MODEL_BASE_URL", preset.base_url),
            model=model
            or os.environ.get("MODEL_NAME")
            or os.environ.get("CODING_MODEL")
            or preset.default_model,
        )

    @classmethod
    def create(cls, config: ModelConfig) -> ModelClient:
        preset = cls._providers.get(config.provider)
        protocol = preset.protocol if preset else "openai-chat-completions"
        if protocol == "openai-chat-completions":
            return OpenAICompatibleClient(config)
        raise ValueError(f"不支持的模型协议: {protocol}")
