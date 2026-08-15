import json
import tempfile
import unittest
from pathlib import Path

from coding_workflow.artifacts import Artifact, ArtifactStore
from coding_workflow.models import FileChange, ImplementationPlan
from coding_workflow.visionforge import (
    ImageArtifactRef,
    VisionForgeCycle,
    VisionForgeRunResult,
    VisionForgeWebError,
    VisionForgeWebRuntime,
)
from web_server import (
    finalize_node_states,
    initial_nodes,
    parse_visionforge_task_payload,
    public_event,
)


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"


def minimal_png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def fake_visionforge_executor(
    task_id,
    requirement,
    reference_image_artifact_ref,
    task_root,
    project_root,
    artifacts,
    image_assets,
):
    ui_ref = artifacts.put(Artifact.create(
        "ui-spec", task_id, {"schema_version": "1.0", "page_type": "landing"},
        kind="ui_spec",
    ))
    plan_ref = artifacts.put(Artifact.create(
        "implementation", task_id,
        ImplementationPlan(
            "实现页面",
            [FileChange("src/App.vue", "<template>ok</template>", "实现 UI")],
        ),
        kind="implementation_plan",
    ))
    integration_ref = artifacts.put(Artifact.create(
        "integration", task_id, {"changed_files": ["src/App.vue"]},
        kind="integration_result",
    ))
    build_ref = artifacts.put(Artifact.create(
        "build", task_id, {"passed": True, "exit_code": 0, "duration_ms": 4},
        kind="build_result",
    ))
    reference_image = ImageArtifactRef.from_dict(
        artifacts.get(reference_image_artifact_ref).content
    )
    screenshot_ref, _ = image_assets.create_artifact(
        artifacts,
        name="actual",
        task_id=task_id,
        data=image_assets.read(reference_image),
        kind="actual_screenshot",
    )
    browser_ref = artifacts.put(Artifact.create(
        "browser", task_id,
        {"passed": True, "screenshot_artifact_ref": screenshot_ref},
        kind="browser_run",
    ))
    review_ref = artifacts.put(Artifact.create(
        "review", task_id,
        {"schema_version": "1.0", "passed": True, "score": 94, "summary": "通过", "issues": []},
        kind="visual_review",
    ))
    gate_ref = artifacts.put(Artifact.create(
        "gate", task_id,
        {"passed": True, "failures": [], "checks": {"no_blocking_issues": True}},
        kind="quality_gate",
    ))
    cycle = VisionForgeCycle(
        0, plan_ref, integration_ref, build_ref, screenshot_ref,
        browser_ref, review_ref, gate_ref, True,
    )
    run_ref = artifacts.put(Artifact.create(
        "run", task_id,
        {"status": "completed", "artifact_chain": {"quality_gate": gate_ref}},
        kind="visionforge_run",
    ))
    return VisionForgeRunResult(
        task_id,
        reference_image_artifact_ref,
        ui_ref,
        plan_ref,
        integration_ref,
        build_ref,
        screenshot_ref,
        browser_ref,
        review_ref,
        run_ref,
        ("src/App.vue",),
        True,
        94,
        False,
        "completed",
        0,
        gate_ref,
        (cycle,),
    )


class WebVisualizationTests(unittest.TestCase):
    def test_initial_nodes_expose_safe_visualization_metadata(self) -> None:
        nodes = initial_nodes()
        self.assertEqual(
            set(nodes), {"planner", "implementer", "tester", "reviewer", "fixer"}
        )
        self.assertTrue(all(node["status"] == "pending" for node in nodes.values()))
        self.assertTrue(all("permissions" in node for node in nodes.values()))

    def test_public_message_is_linked_to_agent_node(self) -> None:
        event = public_event(
            {
                "event": "agent_message",
                "timestamp": "2026-08-07T00:00:00+00:00",
                "payload": {
                    "sender": "tester",
                    "recipient": "coordinator",
                    "message_type": "result",
                    "summary": "测试通过",
                    "payload": {"passed": True},
                },
            },
            3,
        )
        self.assertEqual(event["id"], "event-3")
        self.assertEqual(event["node_id"], "tester")
        self.assertEqual(event["title"], "tester → coordinator · result")

    def test_unused_fixer_is_marked_as_not_triggered(self) -> None:
        nodes = initial_nodes()
        finalize_node_states(nodes, "completed")
        self.assertEqual(nodes["fixer"]["status"], "skipped")
        self.assertIn("无需返工", nodes["fixer"]["last_summary"])

    def test_fixer_explains_exhausted_attempt_budget(self) -> None:
        nodes = initial_nodes()
        finalize_node_states(nodes, "failed")
        self.assertEqual(nodes["fixer"]["status"], "skipped")
        self.assertIn("尝试上限", nodes["fixer"]["last_summary"])


class VisionForgeWebRuntimeTests(unittest.TestCase):
    def runtime(self, root: Path) -> VisionForgeWebRuntime:
        return VisionForgeWebRuntime(
            root / "runtime",
            TEMPLATE,
            executor=fake_visionforge_executor,
            project_preparer=lambda destination: destination.mkdir(parents=True),
        )

    def test_upload_is_content_addressed_and_catalog_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.runtime(root)
            uploaded = first.upload_image(minimal_png(8, 6), "image/png")
            self.assertEqual(len(uploaded["asset_id"]), 64)
            self.assertNotIn("base64", json.dumps(uploaded).lower())
            second = self.runtime(root)
            data, mime_type = second.read_asset(uploaded["asset_id"])
            self.assertEqual(data, minimal_png(8, 6))
            self.assertEqual(mime_type, "image/png")

    def test_upload_rejects_declared_type_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.runtime(Path(temp))
            with self.assertRaisesRegex(
                VisionForgeWebError, "Content-Type.*真实格式"
            ):
                runtime.upload_image(minimal_png(), "image/jpeg")

    def test_task_uses_only_asset_id_and_exposes_artifact_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = self.runtime(root)
            uploaded = runtime.upload_image(minimal_png(), "image/png")
            task_id = runtime.submit_task("实现参考图中的页面", uploaded["asset_id"])
            self.assertTrue(runtime.wait(task_id, 2))
            snapshot = runtime.task_snapshot(task_id)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["reference_image"]["asset_id"], uploaded["asset_id"])
            self.assertEqual(snapshot["result"]["fix_attempts"], 0)
            kinds = {item["kind"] for item in snapshot["artifacts"]}
            self.assertTrue({
                "reference_image", "ui_spec", "implementation_plan",
                "actual_screenshot", "browser_run", "visual_review",
                "quality_gate", "visionforge_run",
            }.issubset(kinds))
            serialized = json.dumps(snapshot, ensure_ascii=False)
            self.assertNotIn("base64", serialized.lower())
            self.assertNotIn(str(root / "runtime" / "tasks"), serialized)

    def test_task_payload_rejects_base64_or_project_path_fields(self) -> None:
        with self.assertRaisesRegex(VisionForgeWebError, "未知字段"):
            parse_visionforge_task_payload({
                "requirement": "实现页面",
                "asset_id": "a" * 64,
                "reference_image_base64": "ignored",
            })
        with self.assertRaisesRegex(VisionForgeWebError, "未知字段"):
            parse_visionforge_task_payload({
                "requirement": "实现页面",
                "asset_id": "a" * 64,
                "project_root": "/tmp/untrusted",
            })

    def test_web_page_is_visionforge_entry_not_old_generic_form(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("VisionForge", html)
        self.assertIn('id="reference-file"', html)
        self.assertIn("/api/visionforge/assets", script)
        self.assertIn("/api/visionforge/tasks", script)
        self.assertNotIn("reference_image_base64", script)


if __name__ == "__main__":
    unittest.main()
