from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from coding_workflow.artifacts import ArtifactStore
from coding_workflow.harness import LifecycleController
from coding_workflow.harness.lifecycle import TaskCancelledError
from coding_workflow.visionforge import (
    ACTUAL_SCREENSHOT,
    BROWSER_RUN,
    BrowserProcessRunner,
    BrowserProjectConfig,
    BrowserRunResult,
    BrowserRuntimeError,
    ImageAssetStore,
    PlaywrightBrowserTester,
    ProcessExecution,
    UISpec,
    VisionForgeSchemaError,
)


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"


class BrowserRuntimeUnitTests(unittest.TestCase):
    def python_runner(self) -> BrowserProcessRunner:
        return BrowserProcessRunner(
            allowed_executables=frozenset({"python"}),
            executable_overrides={"python": sys.executable},
            poll_interval=0.02,
        )

    def test_process_timeout_terminates_command(self) -> None:
        started = time.monotonic()
        result = self.python_runner().run(
            ("python", "-c", "import time; time.sleep(10)"),
            cwd=ROOT,
            timeout_seconds=0.1,
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)
        self.assertLess(time.monotonic() - started, 2)

    def test_process_cancel_terminates_command(self) -> None:
        lifecycle = LifecycleController()
        lifecycle.mark_running()

        def cancel() -> None:
            time.sleep(0.1)
            lifecycle.cancel("测试取消")

        thread = threading.Thread(target=cancel)
        thread.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(TaskCancelledError, "测试取消"):
                self.python_runner().run(
                    ("python", "-c", "import time; time.sleep(10)"),
                    cwd=ROOT,
                    timeout_seconds=5,
                    lifecycle=lifecycle,
                )
        finally:
            thread.join()
        self.assertLess(time.monotonic() - started, 2)

    def test_project_config_rejects_external_origin_and_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = root / ".visionforge" / "browser-runner.mjs"
            runner.parent.mkdir()
            runner.write_text("", encoding="utf-8")
            config = {
                "origin": "https://example.com:4173",
                "entry_route": "/",
                "viewport": {"width": 1440, "height": 900},
                "commands": {
                    "build": ["pnpm", "run", "build"],
                    "dev": ["pnpm", "run", "dev", "--port", "4173"],
                },
                "browser_runner": ".visionforge/browser-runner.mjs",
            }
            path = root / "visionforge.template.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(BrowserRuntimeError, "本地 HTTP"):
                BrowserProjectConfig.load(root)
            config["origin"] = "http://127.0.0.1:4173"
            config["commands"]["build"] = ["pnpm", "install"]
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(BrowserRuntimeError, "禁止参数"):
                BrowserProjectConfig.load(root)

    def test_browser_result_recomputes_passed_from_evidence(self) -> None:
        payload = {
            "schema_version": "1.0",
            "passed": True,
            "url": "http://127.0.0.1:4173/",
            "viewport": {"width": 1440, "height": 900},
            "assertions": [{
                "interaction_id": "visible",
                "action": "expect_visible",
                "target": "[data-testid=page-shell]",
                "passed": True,
                "evidence": "可见",
                "error": "",
                "duration_ms": 2,
            }],
            "console_messages": [{"level": "error", "message": "boom"}],
            "page_errors": [],
            "network_errors": [],
            "duration_ms": 10,
        }
        with self.assertRaisesRegex(VisionForgeSchemaError, "证据不一致"):
            BrowserRunResult.from_runner_payload(
                payload, "artifact://screenshot"
            )

    def test_build_failure_becomes_fixable_browser_evidence(self) -> None:
        class FailingBuildRunner:
            def run(self, *args, **kwargs):
                return ProcessExecution(
                    ("pnpm", "run", "build"), 1, "", "Vue syntax error", 9
                )

        ui_spec = UISpec.from_dict(json.loads(
            (TEMPLATE / "visionforge.ui-spec.json").read_text(encoding="utf-8")
        ))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactStore()
            result = PlaywrightBrowserTester(
                TEMPLATE,
                FailingBuildRunner(),
                artifacts,
                ImageAssetStore(root / "assets"),
                root / "runtime",
            ).run(task_id="vf-build-failure", ui_spec=ui_spec)
            self.assertFalse(result.result.passed)
            self.assertIn("Vue syntax error", result.result.page_errors[0])
            self.assertFalse(result.result.assertions[0].passed)
            self.assertTrue(
                artifacts.get(result.screenshot_artifact_ref).content["width"]
                == ui_spec.viewport.width
            )


@unittest.skipUnless(
    os.environ.get("VISIONFORGE_E2E") == "1",
    "设置 VISIONFORGE_E2E=1 后运行真实 Playwright 集成测试",
)
class BrowserRuntimeIntegrationTests(unittest.TestCase):
    def test_fixed_vue_page_builds_runs_interacts_and_cleans_up(self) -> None:
        node = os.environ.get("VISIONFORGE_NODE", "")
        pnpm = os.environ.get("VISIONFORGE_PNPM", "")
        if not Path(node).is_file() or not Path(pnpm).is_file():
            self.fail("真实浏览器测试需要 VISIONFORGE_NODE 和 VISIONFORGE_PNPM")
        ui_spec = UISpec.from_dict(json.loads(
            (TEMPLATE / "visionforge.ui-spec.json").read_text(encoding="utf-8")
        ))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = ArtifactStore()
            tester = PlaywrightBrowserTester(
                TEMPLATE,
                BrowserProcessRunner(
                    executable_overrides={"node": node, "pnpm": pnpm},
                    environment={
                        "PATH": f"{Path(node).parent}:/usr/bin:/bin",
                    },
                ),
                artifacts,
                ImageAssetStore(root / "assets"),
                root / "runtime",
            )
            result = tester.run(task_id="vf-browser-e2e", ui_spec=ui_spec)
            self.assertTrue(result.result.passed)
            self.assertEqual(len(result.result.assertions), 4)
            self.assertTrue(all(item.passed for item in result.result.assertions))
            self.assertFalse(result.result.page_errors)
            self.assertFalse(result.result.network_errors)
            screenshot = artifacts.get(result.screenshot_artifact_ref)
            browser_run = artifacts.get(result.browser_run_artifact_ref)
            build = artifacts.get(result.build_artifact_ref)
            self.assertEqual(screenshot.kind, ACTUAL_SCREENSHOT)
            self.assertEqual(browser_run.kind, BROWSER_RUN)
            self.assertEqual(build.kind, "build_result")
            self.assertEqual(
                browser_run.content["screenshot_artifact_ref"],
                result.screenshot_artifact_ref,
            )
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", 4173)), 0)


if __name__ == "__main__":
    unittest.main()
