from __future__ import annotations

import json
from pathlib import Path

from coding_workflow.model import (
    ImageContentPart,
    ModelCapability,
    ModelClientFactory,
    ModelMessage,
    ModelRequest,
    TextContentPart,
    load_env_file,
)


ROOT = Path(__file__).parent.resolve()


def main() -> None:
    load_env_file(ROOT / ".env")
    text_client = ModelClientFactory.create(
        ModelClientFactory.config_from_env(),
        required_capabilities=frozenset({
            ModelCapability.TEXT,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STRUCTURED_OUTPUT,
        }),
    )
    vision_client = ModelClientFactory.create(
        ModelClientFactory.vision_config_from_env(),
        required_capabilities=frozenset({
            ModelCapability.TEXT,
            ModelCapability.VISION,
            ModelCapability.STRUCTURED_OUTPUT,
        }),
    )
    text_response = text_client.generate_structured(ModelRequest(
        (
            ModelMessage("system", (TextContentPart(
                "这是连接测试。只输出 JSON，不要解释。"
            ),)),
            ModelMessage("user", (TextContentPart(
                "返回 ok=true 和 kind='text'。"
            ),)),
        ),
        frozenset({
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
        }),
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok", "kind"],
            "properties": {
                "ok": {"type": "boolean"},
                "kind": {"type": "string", "const": "text"},
            },
        },
    ))
    if text_response.data.get("ok") is not True or text_response.data.get("kind") != "text":
        raise RuntimeError("DeepSeek 文本烟测响应未通过本地校验")

    image_path = ROOT / "docs" / "multi-agent-architecture.png"
    vision_response = vision_client.generate_structured(ModelRequest(
        (
            ModelMessage("system", (TextContentPart(
                "这是视觉连接测试。观察图片并只输出 JSON，不要解释。"
            ),)),
            ModelMessage("user", (
                TextContentPart("判断图片是否包含可见文字，并给出不超过 20 字的摘要。"),
                ImageContentPart(
                    "artifact://vision-smoke-reference",
                    "image/png",
                    image_path.read_bytes(),
                    "low",
                ),
            )),
        ),
        frozenset({
            ModelCapability.TEXT,
            ModelCapability.VISION,
            ModelCapability.STRUCTURED_OUTPUT,
        }),
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["image_received", "has_visible_text", "summary"],
            "properties": {
                "image_received": {"type": "boolean"},
                "has_visible_text": {"type": "boolean"},
                "summary": {"type": "string"},
            },
        },
    ))
    if (
        vision_response.data.get("image_received") is not True
        or vision_response.data.get("has_visible_text") is not True
        or not isinstance(vision_response.data.get("summary"), str)
        or not vision_response.data["summary"].strip()
    ):
        raise RuntimeError("Qwen 视觉烟测响应未通过本地校验")

    print(json.dumps({
        "text": {
            "validated": True,
            "provider": text_response.provider,
            "model": text_response.model,
            "total_tokens": text_response.usage.total_tokens,
            "latency_ms": text_response.latency_ms,
        },
        "vision": {
            "validated": True,
            "provider": vision_response.provider,
            "model": vision_response.model,
            "total_tokens": vision_response.usage.total_tokens,
            "latency_ms": vision_response.latency_ms,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
