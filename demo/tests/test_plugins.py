import ast
import tempfile
import unittest
from pathlib import Path

from coding_workflow.harness import (
    PluginCompatibilityError,
    PluginManifest,
    PluginRegistrationError,
    PluginRegistry,
    PluginUnavailableError,
    ScenarioRunState,
    SQLiteScenarioRunStore,
)
from coding_workflow.visionforge import (
    VISIONFORGE_ARTIFACT_KINDS,
    VISIONFORGE_PLUGIN_VERSION,
    WEB_VISUAL_REFERENCE,
    VisionForgePlugin,
    WebVisualScenario,
    create_visionforge_plugin_registry,
)


class _Profile:
    name = "sample"
    max_rework_rounds = 0

    def build_round(self, state, lifecycle):
        raise NotImplementedError

    def decide(self, state, execution, artifacts):
        raise NotImplementedError

    def finalize(self, state, artifacts, decision):
        raise NotImplementedError

    def restore_result(self, result_artifact_ref, artifacts):
        raise NotImplementedError


class _Plugin:
    def __init__(
        self,
        *,
        plugin_id: str = "sample_plugin",
        core_api_version: str = "1.0",
        scenarios: tuple[str, ...] = ("sample",),
    ) -> None:
        self.manifest = PluginManifest(
            plugin_id,
            "1.2.0",
            core_api_version,
            scenarios,
            required_capabilities=("scenario_runtime",),
        )
        self.context = None
        self.register_called = False

    def register(self, context) -> None:
        self.register_called = True
        self.context = context
        for scenario in self.manifest.scenarios:
            context.register_scenario(scenario, _Profile)


class PluginCoreTests(unittest.TestCase):
    def test_visionforge_is_an_explicit_namespaced_plugin(self) -> None:
        empty = PluginRegistry(core_api_version="1.0")
        self.assertEqual(empty.available_scenarios(), ())

        registry = create_visionforge_plugin_registry()
        self.assertEqual(
            registry.available_scenarios(), (WEB_VISUAL_REFERENCE,)
        )
        manifest = registry.manifest("visionforge")
        self.assertEqual(manifest, VisionForgePlugin.manifest)
        self.assertEqual(manifest.version, VISIONFORGE_PLUGIN_VERSION)
        registration = registry.resolve_reference(WEB_VISUAL_REFERENCE)
        self.assertIs(registration.factory, WebVisualScenario)
        self.assertTrue(VISIONFORGE_ARTIFACT_KINDS)
        self.assertTrue(all(
            kind.startswith("visionforge:")
            for kind in VISIONFORGE_ARTIFACT_KINDS
        ))

    def test_manifest_is_strict_and_namespaced_scenarios_are_stable(self) -> None:
        with self.assertRaises(ValueError):
            PluginManifest("VisionForge", "1", "1.0", ("web_visual",))
        with self.assertRaises(ValueError):
            PluginManifest("visionforge", "1", "1.0", ("web_visual", "web_visual"))
        with self.assertRaises(ValueError):
            PluginManifest(
                "visionforge", "1", "1.0", ("web_visual",),
                optional_dependencies="playwright",
            )

        registry = PluginRegistry(core_api_version="1.0")
        manifest = registry.register(_Plugin(plugin_id="visionforge"))

        self.assertEqual(manifest.plugin_id, "visionforge")
        self.assertEqual(
            registry.available_scenarios(), ("visionforge:sample",)
        )
        registration = registry.resolve_reference("visionforge:sample")
        self.assertEqual(registration.reference, "visionforge:sample")
        self.assertEqual(registration.plugin_version, "1.2.0")
        profile = registration.create()
        self.assertIsInstance(profile.profile, _Profile)
        self.assertEqual(profile.plugin_id, "visionforge")
        self.assertEqual(profile.plugin_version, "1.2.0")
        self.assertEqual(profile.name, "sample")

    def test_plugin_identity_round_trips_with_scenario_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteScenarioRunStore(Path(temp) / "scenario.sqlite3")
            state = ScenarioRunState(
                "run-1", "task-1", "project-1", "sample", "running",
                0, 0, ("snapshot-1",), "snapshot-1",
                plugin_id="sample_plugin", plugin_version="1.2.0",
            )
            store.save(state)
            restored = store.load("run-1")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.plugin_id, "sample_plugin")
        self.assertEqual(restored.plugin_version, "1.2.0")

    def test_empty_registry_reports_plugin_and_scenario_unavailable(self) -> None:
        registry = PluginRegistry(core_api_version="1.0")
        self.assertEqual(registry.available_scenarios(), ())
        with self.assertRaises(PluginUnavailableError):
            registry.manifest("visionforge")
        with self.assertRaises(PluginUnavailableError):
            registry.resolve_reference("visionforge:web_visual")
        with self.assertRaises(PluginUnavailableError):
            registry.resolve_reference("invalid-reference")

    def test_incompatible_plugin_is_rejected_before_registration(self) -> None:
        plugin = _Plugin(core_api_version="2.0")
        registry = PluginRegistry(core_api_version="1.0")
        with self.assertRaises(PluginCompatibilityError):
            registry.register(plugin)
        self.assertFalse(plugin.register_called)
        self.assertEqual(registry.manifests(), {})

    def test_registration_is_atomic_and_must_match_manifest(self) -> None:
        class IncompletePlugin(_Plugin):
            def register(self, context) -> None:
                context.register_scenario("sample", _Profile)

        plugin = IncompletePlugin(scenarios=("sample", "secondary"))
        registry = PluginRegistry(core_api_version="1.0")
        with self.assertRaises(PluginRegistrationError):
            registry.register(plugin)
        self.assertEqual(registry.manifests(), {})
        self.assertEqual(registry.available_scenarios(), ())

    def test_registration_context_cannot_be_reused_or_overridden(self) -> None:
        plugin = _Plugin()
        registry = PluginRegistry(core_api_version="1.0")
        registry.register(plugin)
        with self.assertRaises(PluginRegistrationError):
            plugin.context.register_scenario("sample", _Profile)
        with self.assertRaises(PluginRegistrationError):
            registry.register(_Plugin())

    def test_factory_must_return_declared_scenario_profile(self) -> None:
        class WrongProfile(_Profile):
            name = "other"

        class WrongFactoryPlugin(_Plugin):
            def register(self, context) -> None:
                context.register_scenario("sample", WrongProfile)

        registry = PluginRegistry(core_api_version="1.0")
        registry.register(WrongFactoryPlugin())
        with self.assertRaises(PluginRegistrationError):
            registry.resolve_reference("sample_plugin:sample").create()

    def test_core_modules_do_not_import_visionforge(self) -> None:
        package_root = Path(__file__).parents[1] / "coding_workflow"
        violations = []
        for path in package_root.rglob("*.py"):
            if "visionforge" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = tuple(item.name for item in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names = (node.module or "",)
                else:
                    continue
                if any("visionforge" in name for name in names):
                    violations.append(str(path.relative_to(package_root)))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
