from __future__ import annotations

import os
from dataclasses import dataclass

from .base import ModelCapability, ModelClient, require_capabilities
from .config import ModelConfig, StructuredOutputMode
from .openai_compatible import OpenAICompatibleClient


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    api_key_env: str
    default_model: str
    protocol: str = "openai-chat-completions"
    capabilities: frozenset[ModelCapability] = frozenset({
        ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT,
    })
    structured_output_mode: StructuredOutputMode = StructuredOutputMode.JSON_SCHEMA
    request_options: tuple[tuple[str, object], ...] = ()
    include_max_tokens: bool = True


class ModelClientFactory:
    _providers: dict[str, ProviderPreset] = {
        "deepseek": ProviderPreset(
            name="deepseek",
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-v4-pro",
            capabilities=frozenset({
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            }),
            structured_output_mode=StructuredOutputMode.JSON_OBJECT,
        ),
        "dashscope": ProviderPreset(
            name="dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="DASHSCOPE_API_KEY",
            default_model="qwen3.7-plus",
            capabilities=frozenset(ModelCapability),
            structured_output_mode=StructuredOutputMode.JSON_OBJECT,
            request_options=(("enable_thinking", False),),
            include_max_tokens=False,
        ),
    }

    @classmethod
    def register(cls, preset: ProviderPreset) -> None:
        cls._providers[preset.name] = preset

    @classmethod
    def config_for_provider(
        cls,
        provider: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = 4_000,
        max_retries: int = 0,
        temperature: float = 0.0,
        enforce_max_tokens: bool = True,
    ) -> ModelConfig:
        """不读取环境变量，供可审计 preflight 冻结公开配置。"""
        preset = cls._providers.get(provider)
        if preset is None:
            if not all((model, base_url, api_key_env)):
                raise ValueError(
                    "自定义供应商必须显式提供 model、base_url 和 api_key_env"
                )
            capabilities = frozenset({
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            })
            return ModelConfig(
                provider,
                str(api_key_env),
                str(base_url),
                str(model),
                max_tokens=max_tokens,
                max_retries=max_retries,
                temperature=temperature,
                capabilities=capabilities,
                include_max_tokens=enforce_max_tokens,
            )
        return ModelConfig(
            provider,
            api_key_env or preset.api_key_env,
            base_url or preset.base_url,
            model or preset.default_model,
            max_tokens=max_tokens,
            max_retries=max_retries,
            temperature=temperature,
            capabilities=preset.capabilities,
            structured_output_mode=preset.structured_output_mode,
            request_options=preset.request_options,
            include_max_tokens=enforce_max_tokens,
        )

    @classmethod
    def config_from_env(
        cls, provider: str | None = None, model: str | None = None
    ) -> ModelConfig:
        return cls._config_from_env(
            "MODEL",
            provider=provider,
            model=model,
            default_provider="deepseek",
            legacy_model_env="CODING_MODEL",
        )

    @classmethod
    def vision_config_from_env(
        cls, provider: str | None = None, model: str | None = None
    ) -> ModelConfig:
        return cls._config_from_env(
            "VISION_MODEL",
            provider=provider,
            model=model,
            default_provider="dashscope",
        )

    @classmethod
    def _config_from_env(
        cls,
        prefix: str,
        *,
        provider: str | None,
        model: str | None,
        default_provider: str,
        legacy_model_env: str = "",
    ) -> ModelConfig:
        provider_name = provider or os.environ.get(
            f"{prefix}_PROVIDER", default_provider
        )
        preset = cls._providers.get(provider_name)
        if preset is None:
            required = [
                f"{prefix}_BASE_URL", f"{prefix}_API_KEY", f"{prefix}_NAME"
            ]
            missing = [name for name in required if not os.environ.get(name)]
            if missing:
                raise ValueError(
                    f"未知供应商 {provider_name}，且缺少自定义配置: {', '.join(missing)}"
                )
            return ModelConfig(
                provider=provider_name,
                api_key_env=f"{prefix}_API_KEY",
                base_url=os.environ[f"{prefix}_BASE_URL"],
                model=model or os.environ[f"{prefix}_NAME"],
                capabilities=cls._capabilities_from_env(
                    f"{prefix}_CAPABILITIES"
                ),
                structured_output_mode=cls._structured_output_mode_from_env(
                    f"{prefix}_STRUCTURED_OUTPUT_MODE"
                ),
            )

        generic_key_env = f"{prefix}_API_KEY"
        generic_key_present = bool(os.environ.get(generic_key_env))
        return ModelConfig(
            provider=provider_name,
            api_key_env=generic_key_env if generic_key_present else preset.api_key_env,
            base_url=os.environ.get(f"{prefix}_BASE_URL", preset.base_url),
            model=model
            or os.environ.get(f"{prefix}_NAME")
            or (os.environ.get(legacy_model_env) if legacy_model_env else None)
            or preset.default_model,
            capabilities=cls._capabilities_from_env(
                f"{prefix}_CAPABILITIES", preset.capabilities
            ),
            structured_output_mode=cls._structured_output_mode_from_env(
                f"{prefix}_STRUCTURED_OUTPUT_MODE",
                preset.structured_output_mode,
            ),
            request_options=preset.request_options,
            include_max_tokens=preset.include_max_tokens,
        )

    @classmethod
    def create(
        cls,
        config: ModelConfig,
        *,
        required_capabilities: frozenset[ModelCapability] = frozenset(),
    ) -> ModelClient:
        require_capabilities(config.capabilities, required_capabilities)
        preset = cls._providers.get(config.provider)
        protocol = preset.protocol if preset else "openai-chat-completions"
        if protocol == "openai-chat-completions":
            return OpenAICompatibleClient(config)
        raise ValueError(f"不支持的模型协议: {protocol}")

    @staticmethod
    def _capabilities_from_env(
        variable_name: str = "MODEL_CAPABILITIES",
        default: frozenset[ModelCapability] = frozenset({
            ModelCapability.TEXT, ModelCapability.STRUCTURED_OUTPUT,
        }),
    ) -> frozenset[ModelCapability]:
        raw = os.environ.get(variable_name, "").strip()
        if not raw:
            return default
        try:
            capabilities = frozenset(
                ModelCapability(item.strip()) for item in raw.split(",") if item.strip()
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in ModelCapability)
            raise ValueError(
                f"{variable_name} 包含未知值，允许: {allowed}"
            ) from exc
        if not capabilities:
            raise ValueError(f"{variable_name} 不能为空")
        return capabilities

    @staticmethod
    def _structured_output_mode_from_env(
        variable_name: str,
        default: StructuredOutputMode = StructuredOutputMode.JSON_SCHEMA,
    ) -> StructuredOutputMode:
        raw = os.environ.get(variable_name, "").strip()
        if not raw:
            return default
        try:
            return StructuredOutputMode(raw)
        except ValueError as exc:
            raise ValueError(
                f"{variable_name} 只允许 json_object 或 json_schema"
            ) from exc
