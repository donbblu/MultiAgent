from __future__ import annotations

from ..harness import (
    PluginManifest,
    PluginRegistrationContext,
    PluginRegistry,
)
from .scenario import WebVisualScenario


VISIONFORGE_PLUGIN_ID = "visionforge"
VISIONFORGE_PLUGIN_VERSION = "1.0.0"
VISIONFORGE_CORE_API_VERSION = "1.0"
WEB_VISUAL_SCENARIO = "web_visual"
WEB_VISUAL_REFERENCE = f"{VISIONFORGE_PLUGIN_ID}:{WEB_VISUAL_SCENARIO}"


class VisionForgePlugin:
    """由 Composition Root 显式启用的可信内置场景插件。"""

    manifest = PluginManifest(
        VISIONFORGE_PLUGIN_ID,
        VISIONFORGE_PLUGIN_VERSION,
        VISIONFORGE_CORE_API_VERSION,
        (WEB_VISUAL_SCENARIO,),
        required_capabilities=(
            "scenario_runtime",
            "image_artifacts",
            "browser_testing",
            "vision_models",
            "structured_output",
        ),
        optional_dependencies=("playwright", "vue3"),
    )

    def register(self, context: PluginRegistrationContext) -> None:
        context.register_scenario(WEB_VISUAL_SCENARIO, WebVisualScenario)


def create_visionforge_plugin_registry() -> PluginRegistry:
    """产品 Composition Root 使用；Core 本身不会自动加载插件。"""
    registry = PluginRegistry(core_api_version=VISIONFORGE_CORE_API_VERSION)
    registry.register(VisionForgePlugin())
    return registry
