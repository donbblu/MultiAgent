from __future__ import annotations

import gc
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import weakref
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from coding_workflow.artifacts import ArtifactStore
from coding_workflow.harness import LifecycleController
from coding_workflow.harness.lifecycle import TaskCancelledError
from coding_workflow.local_execution import (
    CLEANUP_FAILED,
    LocalExecutionError,
    SupervisedBackground,
    redact_text,
    sanitize_output,
)
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
    VisionForgeLocalExecutionApprover,
    VisionForgeSchemaError,
)
from coding_workflow.visionforge.browser import ManagedProcess, _bounded_public_text


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"


class BrowserRuntimeUnitTests(unittest.TestCase):
    class _TimedProcess:
        pid = 424243

        def __init__(self) -> None:
            self.returncode = None
            self.stdout = None
            self.stderr = None

        def communicate(self, timeout=None):
            if timeout is not None and self.returncode is None:
                time.sleep(float(timeout))
                raise subprocess.TimeoutExpired(("pnpm",), timeout)
            return "", ""

        def wait(self, timeout=None):
            del timeout
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self):
            return self.returncode

    @staticmethod
    def _killpg(pgid: int, sig: int) -> None:
        del pgid
        if sig == 0:
            raise ProcessLookupError

    def build_runner(self, root: Path) -> BrowserProcessRunner:
        return BrowserProcessRunner(
            executable_overrides={"pnpm": "/usr/bin/pnpm"},
            poll_interval=0.02,
            workspace_root=root,
        )

    @staticmethod
    def _fake_supervisor(log_path: Path) -> SupervisedBackground:
        with mock.patch.object(
            SupervisedBackground,
            "__init__",
            return_value=None,
        ):
            state = SupervisedBackground()
        state.log_path = log_path
        state.request_stop = mock.Mock()
        return state

    def test_process_timeout_terminates_command(self) -> None:
        process = self._TimedProcess()
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=self._killpg,
        ):
            root = Path(temp)
            started = time.monotonic()
            result = VisionForgeLocalExecutionApprover(True).run_browser(
                self.build_runner(root),
                BrowserProcessRunner.BUILD_COMMAND,
                cwd=root,
                timeout_seconds=0.05,
            )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)
        self.assertLess(time.monotonic() - started, 2)

    def test_public_text_uses_core_redaction_and_bounding(self) -> None:
        sensitive = (
            '{"token": "super-secret-\\\"escaped-secret-tail", '
            '"authorization": "Bearer abcdefghijklmnop==", '
            '"pem": "-----BEGIN PRIVATE KEY-----private-material'
            '-----END PRIVATE KEY-----", '
            '"ordinary": "visible text"}'
        )
        samples: tuple[object, ...] = (
            sensitive,
            sensitive.encode("utf-8"),
            "ordinary output with punctuation, quotes, and spaces",
            "ordinary-" * 2_000,
        )
        for sample in samples:
            core = sanitize_output(sample, limit_chars=128)
            browser = _bounded_public_text(sample, limit=128)
            self.assertEqual(
                browser,
                (
                    core.text,
                    core.truncated,
                    core.raw_chars,
                    core.raw_sha256,
                ),
            )

        full_text = _bounded_public_text(sensitive)[0]
        for secret in (
            "super-secret",
            "escaped-secret-tail",
            "abcdefghijklmnop",
            "private-material",
        ):
            self.assertNotIn(secret, full_text)
        self.assertIn("visible text", full_text)

    def test_process_execution_dto_redacts_all_public_representations(self) -> None:
        sensitive = (
            '{"api_key": "dto-secret-\\\"escaped-tail", '
            '"auth": "Bearer zyxwvutsrqponmlk==", '
            '"key": "-----BEGIN RSA PRIVATE KEY-----dto-private'
            '-----END RSA PRIVATE KEY-----", '
            '"ordinary": "kept"}'
        )
        execution = ProcessExecution(
            ("node", sensitive),
            1,
            sensitive,
            sensitive,
            7,
            profile_manifest={sensitive: {"detail": sensitive}},
            cleanup_evidence={"detail": [sensitive]},
            cleanup_evidence_digest="d" * 64,
        )
        core = sanitize_output(sensitive, limit_chars=10_000)
        self.assertEqual(execution.stdout, core.text)
        self.assertEqual(execution.stderr, core.text)
        self.assertEqual(execution.stdout_chars, core.raw_chars)
        self.assertEqual(execution.stdout_sha256, core.raw_sha256)
        representations = (
            repr(execution),
            json.dumps(execution.to_dict(), ensure_ascii=False),
        )
        for representation in representations:
            for secret in (
                "dto-secret",
                "escaped-tail",
                "zyxwvutsrqponmlk",
                "dto-private",
            ):
                self.assertNotIn(secret, representation)
            self.assertIn("kept", representation)

    def test_browser_exception_representation_uses_core_redaction(self) -> None:
        sensitive = (
            '{"password": "exception-secret-\\\"escaped-tail", '
            '"auth": "Bearer qwertyuiopasdfgh==", '
            '"ordinary": "kept"}'
        )
        error = BrowserRuntimeError(sensitive)
        self.assertEqual(str(error), redact_text(sensitive))
        for representation in (str(error), repr(error)):
            self.assertNotIn("exception-secret", representation)
            self.assertNotIn("escaped-tail", representation)
            self.assertNotIn("qwertyuiopasdfgh", representation)
            self.assertIn("kept", representation)

    def test_process_cancel_terminates_command(self) -> None:
        lifecycle = LifecycleController()
        lifecycle.mark_running()
        lifecycle.cancel("测试取消")
        process = self._TimedProcess()
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=self._killpg,
        ):
            root = Path(temp)
            started = time.monotonic()
            with self.assertRaisesRegex(TaskCancelledError, "测试取消"):
                VisionForgeLocalExecutionApprover(True).run_browser(
                    self.build_runner(root),
                    BrowserProcessRunner.BUILD_COMMAND,
                    cwd=root,
                    timeout_seconds=5,
                    lifecycle=lifecycle,
                )
        self.assertLess(time.monotonic() - started, 2)

    def test_managed_process_retains_live_state_until_cleanup_is_terminal(
        self,
    ) -> None:
        terminal_evidence = MappingProxyType({"verified": True})
        barrier_error = LocalExecutionError(
            CLEANUP_FAILED,
            "synthetic cleanup barrier",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self._fake_supervisor(root / "server.log")
            state.stop = mock.Mock(side_effect=(barrier_error, None))
            runner = self.build_runner(root)
            managed = ManagedProcess(state, runner)
            with mock.patch.object(
                SupervisedBackground,
                "cleanup_terminal",
                new_callable=mock.PropertyMock,
                side_effect=(False, True),
            ), mock.patch.object(
                SupervisedBackground,
                "running",
                new_callable=mock.PropertyMock,
                return_value=True,
            ), mock.patch.object(
                SupervisedBackground,
                "profile_manifest",
                new_callable=mock.PropertyMock,
                return_value=MappingProxyType({
                    "profile_id": "visionforge_dev",
                }),
            ), mock.patch.object(
                SupervisedBackground,
                "cleanup_evidence",
                new_callable=mock.PropertyMock,
                return_value=MappingProxyType({}),
            ) as cleanup_evidence, mock.patch.object(
                SupervisedBackground,
                "cleanup_evidence_digest",
                new_callable=mock.PropertyMock,
                return_value="",
            ) as cleanup_digest, mock.patch.object(
                SupervisedBackground,
                "server_log",
                new_callable=mock.PropertyMock,
                return_value=MappingProxyType({
                    "chars": 0,
                    "sha256": "0" * 64,
                    "truncated": False,
                }),
            ):
                with self.assertRaises(LocalExecutionError) as raised:
                    managed.stop()
                self.assertIs(raised.exception, barrier_error)
                self.assertTrue(managed.running)
                self.assertEqual(dict(managed.cleanup_evidence), {})
                self.assertEqual(
                    managed.local_execution_approval_state()["log_path"],
                    str(root / "server.log"),
                )

                cleanup_evidence.return_value = terminal_evidence
                cleanup_digest.return_value = "d" * 64
                managed.stop()

            self.assertFalse(managed.running)
            self.assertEqual(
                dict(managed.cleanup_evidence), dict(terminal_evidence)
            )
            self.assertEqual(managed.cleanup_evidence_digest, "d" * 64)
            cleanup_evidence.return_value = MappingProxyType({
                "verified": False,
            })
            self.assertEqual(dict(managed.cleanup_evidence), {"verified": True})
            managed.stop()

    def test_nonterminal_stop_keeps_abandonment_cleanup_armed(self) -> None:
        barrier_error = LocalExecutionError(
            CLEANUP_FAILED,
            "synthetic cleanup barrier",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = self._fake_supervisor(root / "server.log")
            state.stop = mock.Mock(side_effect=barrier_error)
            managed = ManagedProcess(state, self.build_runner(root))
            reference = weakref.ref(managed)

            with mock.patch.object(
                SupervisedBackground,
                "cleanup_terminal",
                new_callable=mock.PropertyMock,
                return_value=False,
            ):
                with self.assertRaises(LocalExecutionError):
                    managed.stop()
                del managed
                gc.collect()

            self.assertIsNone(reference())
            state.request_stop.assert_called_once_with("abandoned")

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
