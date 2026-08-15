from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_workflow.artifacts import ArtifactStore
from coding_workflow.harness import LifecycleController, TaskGraph, TaskGraphRuntime, TaskSpec
from coding_workflow.model import (
    ImageContentPart,
    ModelCapability,
    ModelCapabilityError,
    ModelClientFactory,
    ModelConfig,
    ModelMessage,
    ModelRequest,
    OpenAICompatibleClient,
    StructuredOutputMode,
    TextContentPart,
)
from coding_workflow.runtime_sqlite import RuntimeSnapshot, SQLiteRuntimeStore
from coding_workflow.visionforge import (
    ImageArtifactRef,
    ImageAssetError,
    ImageAssetStore,
    UISpec,
    VisualReview,
    VisionForgeSchemaError,
)


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"


def sample_ui_spec() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "page_type": "landing_page",
        "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
        "layout": [
            {"region_id": "hero", "role": "main", "order": 0, "children": []}
        ],
        "components": [
            {
                "component_id": "headline",
                "component_type": "heading",
                "region_id": "hero",
                "text": "VisionForge",
                "test_id": "headline",
                "properties": {"level": "1"},
            }
        ],
        "texts": ["VisionForge"],
        "interactions": [
            {
                "interaction_id": "headline-visible",
                "action": "expect_visible",
                "target": "[data-testid=headline]",
                "expected": "visible",
            }
        ],
        "acceptance_criteria": [
            {
                "criterion_id": "hero-heading",
                "kind": "dom",
                "target": "[data-testid=headline]",
                "expected": "VisionForge",
            }
        ],
        "style_tokens": {"primary_color": "#5267da"},
    }


def minimal_png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


class VisionForgeContractTests(unittest.TestCase):
    def test_ui_spec_round_trip_and_cross_reference_validation(self) -> None:
        spec = UISpec.from_dict(sample_ui_spec())
        self.assertEqual(spec.viewport.width, 1440)
        self.assertEqual(UISpec.from_dict(spec.to_dict()), spec)

        invalid = sample_ui_spec()
        invalid["components"][0]["region_id"] = "missing"
        with self.assertRaisesRegex(VisionForgeSchemaError, "不存在的布局区域"):
            UISpec.from_dict(invalid)

        nested = sample_ui_spec()
        nested["layout"][0]["children"] = ["hero-copy"]
        nested["components"][0]["region_id"] = "hero-copy"
        self.assertEqual(
            UISpec.from_dict(nested).components[0].region_id, "hero-copy"
        )

        duplicate = sample_ui_spec()
        duplicate["layout"][0]["children"] = ["hero"]
        with self.assertRaisesRegex(VisionForgeSchemaError, "children ID 不能重复"):
            UISpec.from_dict(duplicate)

    def test_ui_spec_rejects_arbitrary_browser_action(self) -> None:
        invalid = sample_ui_spec()
        invalid["interactions"][0]["action"] = "evaluate_javascript"
        with self.assertRaisesRegex(VisionForgeSchemaError, "不在允许范围"):
            UISpec.from_dict(invalid)

    def test_visual_review_blocks_p1_p2_even_if_model_claims_pass(self) -> None:
        review = VisualReview.from_dict({
            "schema_version": "1.0",
            "passed": True,
            "score": 92,
            "summary": "模型认为整体接近",
            "issues": [{
                "severity": "P2",
                "region": "hero",
                "category": "layout",
                "expected": "标题居中",
                "actual": "标题偏左",
                "evidence": "实际截图标题中心偏移 80px",
                "suggestion": "调整容器对齐方式",
            }],
        })
        self.assertFalse(review.eligible_for_runtime_pass(80))
        self.assertEqual(VisualReview.from_dict(review.to_dict()), review)

    def test_visual_review_rejects_unknown_severity(self) -> None:
        with self.assertRaises(VisionForgeSchemaError):
            VisualReview.from_dict({
                "schema_version": "1.0",
                "passed": False,
                "score": 50,
                "summary": "存在问题",
                "issues": [{
                    "severity": "critical",
                    "region": "page",
                    "category": "other",
                    "expected": "正常",
                    "actual": "异常",
                    "evidence": "截图",
                    "suggestion": "修复",
                }],
            })


class VisionForgeModelCapabilityTests(unittest.TestCase):
    def test_text_client_rejects_vision_request_before_network(self) -> None:
        client = OpenAICompatibleClient(ModelConfig(
            provider="test",
            api_key_env="UNUSED_TEST_KEY",
            base_url="https://example.invalid",
            model="text-only",
        ))
        request = ModelRequest((ModelMessage("user", (
            TextContentPart("分析参考图"),
            ImageContentPart(
                "artifact://reference-image", "image/png", minimal_png()
            ),
        )),))
        with self.assertRaisesRegex(ModelCapabilityError, "vision"):
            client.generate_structured(request)

    def test_multimodal_payload_uses_transient_data_url_and_artifact_ref(self) -> None:
        message = ModelMessage("user", (
            TextContentPart("分析参考图"),
            ImageContentPart(
                "artifact://reference-image", "image/png", minimal_png(), "high"
            ),
        ))
        payload = OpenAICompatibleClient._message_payload(message)
        image = payload["content"][1]
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("artifact://", image["image_url"]["url"])

    def test_config_exposes_declared_capabilities(self) -> None:
        capabilities = frozenset({
            ModelCapability.TEXT,
            ModelCapability.VISION,
            ModelCapability.STRUCTURED_OUTPUT,
        })
        client = OpenAICompatibleClient(ModelConfig(
            provider="vision-test",
            api_key_env="UNUSED_TEST_KEY",
            base_url="https://example.invalid",
            model="vision-model",
            capabilities=capabilities,
        ))
        self.assertEqual(client.capabilities, capabilities)

    def test_factory_routes_text_and_vision_models_independently(self) -> None:
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "deepseek",
            "MODEL_NAME": "deepseek-v4-pro",
            "VISION_MODEL_PROVIDER": "dashscope",
            "VISION_MODEL_BASE_URL": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            "VISION_MODEL_NAME": "qwen3.7-plus",
        }, clear=True):
            text = ModelClientFactory.config_from_env()
            vision = ModelClientFactory.vision_config_from_env()
        self.assertEqual(text.provider, "deepseek")
        self.assertEqual(text.api_key_env, "DEEPSEEK_API_KEY")
        self.assertNotIn(ModelCapability.VISION, text.capabilities)
        self.assertEqual(text.structured_output_mode, StructuredOutputMode.JSON_OBJECT)
        self.assertEqual(vision.provider, "dashscope")
        self.assertEqual(vision.model, "qwen3.7-plus")
        self.assertEqual(vision.api_key_env, "DASHSCOPE_API_KEY")
        self.assertIn(ModelCapability.VISION, vision.capabilities)
        self.assertEqual(
            vision.structured_output_mode, StructuredOutputMode.JSON_OBJECT
        )
        payload = OpenAICompatibleClient(vision)._request_payload(
            ModelRequest.from_text_messages([
                {"role": "user", "content": "请输出 JSON"}
            ])
        )
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("max_tokens", payload)

    def test_json_object_provider_receives_schema_as_system_instruction(self) -> None:
        client = OpenAICompatibleClient(ModelConfig(
            provider="json-object-test",
            api_key_env="UNUSED_TEST_KEY",
            base_url="https://example.invalid",
            model="json-model",
            structured_output_mode=StructuredOutputMode.JSON_OBJECT,
        ))
        request = ModelRequest.from_text_messages(
            [{"role": "user", "content": "返回 JSON"}],
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            OpenAICompatibleClient._response_format(
                request, StructuredOutputMode.JSON_OBJECT
            ),
            {"type": "json_object"},
        )
        messages = client._request_messages(request)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("JSON Schema", messages[0]["content"])
        self.assertIn('"required":["ok"]', messages[0]["content"])


class VisionForgeImageAssetTests(unittest.TestCase):
    def test_image_is_content_addressed_and_artifact_contains_only_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ImageAssetStore(Path(temp) / "assets")
            artifacts = ArtifactStore()
            artifact_ref, image = store.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-1",
                data=minimal_png(3, 2),
            )
            self.assertEqual((image.width, image.height), (3, 2))
            self.assertEqual(store.read(image), minimal_png(3, 2))
            content = artifacts.get(artifact_ref).content
            self.assertEqual(ImageArtifactRef.from_dict(content), image)
            serialized = json.dumps(content)
            self.assertNotIn("base64", serialized)
            self.assertLess(len(serialized), 1000)

    def test_image_artifact_reference_survives_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_store = ImageAssetStore(root / "assets")
            artifacts = ArtifactStore()
            artifact_ref, expected = image_store.create_artifact(
                artifacts,
                name="reference-image",
                task_id="vf-2",
                data=minimal_png(),
            )
            graph = TaskGraph((TaskSpec(
                "analysis",
                "分析参考图",
                "生成 UI Spec",
                "requirement_analyst",
                acceptance_criteria=("输出结构化 UI Spec",),
                output_artifacts=("ui-spec",),
            ),))
            lifecycle = LifecycleController()
            runtime = SQLiteRuntimeStore(root / "runtime.sqlite3")
            runtime.save(RuntimeSnapshot(
                "vf-snapshot",
                "vf-2",
                "project-1",
                "received",
                graph,
                TaskGraphRuntime(graph).snapshot(),
                {"analysis": 0},
                lifecycle.snapshot(),
                artifacts,
                {},
            ))
            restored = runtime.load("vf-snapshot")
            self.assertIsNotNone(restored)
            actual = ImageArtifactRef.from_dict(
                restored.artifacts.get(artifact_ref).content
            )
            self.assertEqual(actual, expected)
            self.assertEqual(image_store.read(actual), minimal_png())

    def test_image_store_rejects_invalid_and_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ImageAssetStore(Path(temp), max_size_bytes=32)
            with self.assertRaises(ImageAssetError):
                store.put(b"not-an-image")
            with self.assertRaisesRegex(ImageAssetError, "大小限制"):
                store.put(minimal_png() + b"x" * 64)


class VisionForgeVueTemplateTests(unittest.TestCase):
    def test_template_has_fixed_dependencies_and_protected_runtime_config(self) -> None:
        package = json.loads((TEMPLATE / "package.json").read_text(encoding="utf-8"))
        config = json.loads(
            (TEMPLATE / "visionforge.template.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["dependencies"]["vue"], "3.5.40")
        self.assertEqual(package["devDependencies"]["vite"], "7.3.6")
        self.assertEqual(package["devDependencies"]["@vitejs/plugin-vue"], "6.0.8")
        self.assertEqual(package["devDependencies"]["playwright"], "1.62.0")
        self.assertEqual(package["packageManager"], "pnpm@11.19.0")
        self.assertEqual(config["allowed_paths"], ["src/**", "public/**"])
        self.assertIn("package.json", config["protected_paths"])
        self.assertIn(".visionforge/**", config["protected_paths"])
        runner = (TEMPLATE / config["browser_runner"]).read_text(encoding="utf-8")
        self.assertIn("VISIONFORGE_BROWSER_EXECUTABLE", runner)
        self.assertEqual(
            config["commands"]["dev"],
            ["pnpm", "run", "dev", "--port", "4173"],
        )
        self.assertNotIn("install", " ".join(package["scripts"].values()))
        self.assertTrue((TEMPLATE / "pnpm-lock.yaml").is_file())

    def test_template_exposes_stable_dom_hooks(self) -> None:
        app = (TEMPLATE / "src" / "App.vue").read_text(encoding="utf-8")
        for test_id in (
            "page-shell", "signup-form", "email-input", "submit-button",
            "success-message",
        ):
            self.assertIn(f'data-testid="{test_id}"', app)
        self.assertTrue((TEMPLATE / "vite.config.js").is_file())
        ignored = (TEMPLATE / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("node_modules/", ignored)
        self.assertIn("dist/", ignored)


if __name__ == "__main__":
    unittest.main()
