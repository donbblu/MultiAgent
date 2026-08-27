from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coding_workflow
from coding_workflow.command_validators import (
    ControlledCommandResult,
    ControlledCommandRunner,
)
from coding_workflow.models import CommandResult
from coding_workflow.policy import CommandPolicy
from coding_workflow.visionforge.browser import (
    BrowserProcessRunner,
    ProcessExecution,
)
from coding_workflow.workspace import ProjectWorkspace, WorkspaceError


ROOT = Path(__file__).resolve().parents[1]
FAKE_SECRET = "SEC_EXEC_FAKE_NOT_A_SECRET_7f24"
FROZEN_VERSION = "local_trusted_execution/v1"
FROZEN_PROFILE_IDS = frozenset({
    "core_validator",
    "legacy_workspace_verify",
    "visionforge_build",
    "visionforge_dev",
    "visionforge_browser",
})


class LocalTrustedExecutionExpectedRedTests(unittest.TestCase):
    """SEC-EXEC-01 first structural card.

    These tests intentionally avoid real credentials, model calls, external
    network access, and untrusted code.  They freeze one independently
    discoverable failure for each A-H gate before production implementation.
    The full behavioral and POSIX adversarial matrix remains required before
    SEC-EXEC-01 can become KEEP.
    """

    def test_a_admission_contract_is_public_and_runtime_owned(self) -> None:
        source = self._security_scope_text()
        required = (
            FROZEN_VERSION,
            "trusted_local",
            "SANDBOX_REQUIRED",
            "workspace_digest",
            "input_digest",
            "profile_digest",
        )
        missing = [token for token in required if token not in source]
        self.assertEqual(
            missing,
            [],
            "SEC-A: runtime-owned admission must bind trusted_local, "
            "workspace/input/profile digests, and SANDBOX_REQUIRED; "
            f"missing={missing}",
        )

    def test_b_entrypoints_do_not_inherit_parent_environment(self) -> None:
        violations: list[str] = []
        completed = subprocess.CompletedProcess(
            args=["python3", "-V"],
            returncode=0,
            stdout="Python test\n",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict(
                os.environ,
                {
                    "SEC_EXEC_PARENT_SENTINEL": FAKE_SECRET,
                    "OPENAI_API_KEY": FAKE_SECRET,
                    "HTTPS_PROXY": f"http://{FAKE_SECRET}.invalid",
                    "SSH_AUTH_SOCK": f"/tmp/{FAKE_SECRET}.sock",
                    "PYTHONPATH": f"/tmp/{FAKE_SECRET}",
                    "NODE_OPTIONS": f"--require=/tmp/{FAKE_SECRET}.js",
                    "DYLD_INSERT_LIBRARIES": f"/tmp/{FAKE_SECRET}.dylib",
                },
                clear=False,
            ):
                with mock.patch.object(
                    subprocess,
                    "run",
                    return_value=completed,
                ) as run:
                    ProjectWorkspace(root).run(["python3", "-V"])
                if run.called:
                    violations.append(
                        "legacy workspace reached a process backend without "
                        "Runtime-owned trusted_local admission"
                    )

                browser_env = BrowserProcessRunner()._environment()
                if FAKE_SECRET in repr(browser_env):
                    violations.append("browser runner copies parent environment")

                try:
                    controlled = ControlledCommandRunner(
                        root,
                        CommandPolicy(
                            allowed_executables={"python3"},
                            allowed_commands=[["python3", "-V"]],
                        ),
                        environment={"SEC_EXEC_PARENT_SENTINEL": FAKE_SECRET},
                    )
                except (TypeError, ValueError):
                    controlled = None
                if controlled is not None and FAKE_SECRET in repr(
                    dict(controlled.environment)
                ):
                    violations.append("core validator accepts arbitrary env extension")

        self.assertEqual(
            violations,
            [],
            "SEC-B: every entrypoint must receive only its versioned profile "
            f"environment; violations={violations}",
        )

    def test_c_only_absolute_registered_profile_reaches_spawn(self) -> None:
        violations: list[str] = []
        source = self._security_scope_text()
        for token in (FROZEN_VERSION, "profile_digest"):
            if token not in source:
                violations.append(f"missing command-profile token: {token}")

        completed = subprocess.CompletedProcess(
            args=["python3", "-V"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "python3"
            fake.write_text("not executable\n", encoding="utf-8")
            with mock.patch.object(
                subprocess,
                "run",
                return_value=completed,
            ) as run:
                ProjectWorkspace(root).run(["python3", "-V"])
            if run.called:
                violations.append(
                    "legacy workspace reached basename resolution without "
                    "Runtime-owned trusted_local admission"
                )

        self.assertEqual(
            violations,
            [],
            "SEC-C: exact absolute executable/argv/profile digest must be "
            f"validated before spawn; violations={violations}",
        )

    def test_d_workspace_api_rejects_reserved_paths_and_symlink_escape(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            outside = base / "outside.txt"
            outside.write_text("canary", encoding="utf-8")
            workspace = ProjectWorkspace(root)

            for value in (
                ".env",
                ".env.local",
                ".git/config",
                ".runtime/state.sqlite3",
                ".runs/result.json",
                ".verification/report.json",
                ".harness-hidden-tests/test_private.py",
                "solution/reference.py",
            ):
                try:
                    workspace._resolve(value)
                except WorkspaceError:
                    continue
                violations.append(value)

            link = root / "escape"
            link.symlink_to(outside)
            with self.assertRaises(WorkspaceError):
                workspace._resolve("escape")
            self.assertEqual(outside.read_text(encoding="utf-8"), "canary")

        self.assertEqual(
            violations,
            [],
            "SEC-D: reserved Workspace paths must fail closed before write/spawn; "
            f"accepted={violations}",
        )

    def test_e_cleanup_failure_and_quarantine_are_typed(self) -> None:
        source = self._security_scope_text()
        violations: list[str] = []
        for token in ("CLEANUP_FAILED", "SANDBOX_REQUIRED", "quarantine"):
            if token not in source:
                violations.append(f"missing lifecycle token: {token}")

        self.assertEqual(
            violations,
            [],
            "SEC-E: every terminal path needs a cleanup barrier and typed "
            f"quarantine; violations={violations}",
        )

    def test_f_result_representations_never_retain_raw_secret_text(self) -> None:
        controlled = ControlledCommandResult(
            command=("python3", "-V"),
            exit_code=0,
            stdout="[REDACTED]",
            stderr="",
            duration_ms=1,
            stdout_chars=len(FAKE_SECRET),
            stdout_sha256="0" * 64,
            _assertion_stdout=f"api_key={FAKE_SECRET}",
        )
        browser = ProcessExecution(
            ("node", "runner.mjs"),
            0,
            f"api_key={FAKE_SECRET}",
            "",
            1,
        )
        legacy = CommandResult(
            ["python3", "-V"],
            0,
            f"api_key={FAKE_SECRET}",
            "",
        )
        leaked = [
            name
            for name, value in (
                ("ControlledCommandResult.repr", repr(controlled)),
                ("ControlledCommandResult.evidence", repr(controlled.evidence())),
                ("ProcessExecution.repr", repr(browser)),
                ("ProcessExecution.to_dict", repr(browser.to_dict())),
                ("CommandResult.repr", repr(legacy)),
            )
            if FAKE_SECRET in value
        ]
        self.assertEqual(
            leaked,
            [],
            "SEC-F: stdout/stderr/server-log representations must share the "
            f"bounded redacted form; leaked={leaked}",
        )

    def test_g_frozen_profile_manifest_has_all_normal_controls(self) -> None:
        source = self._security_scope_text()
        violations = [
            token
            for token in (FROZEN_VERSION, *sorted(FROZEN_PROFILE_IDS))
            if token not in source
        ]
        self.assertEqual(
            violations,
            [],
            "SEC-G: frozen normal-path manifest tokens are missing; "
            f"missing={violations}",
        )

    def test_h_no_process_entrypoint_bypasses_the_supervisor(self) -> None:
        scope = (
            ROOT / "coding_workflow/command_validators.py",
            ROOT / "coding_workflow/workspace.py",
            ROOT / "coding_workflow/visionforge/browser.py",
        )
        calls: list[tuple[str, str, int]] = []
        for path in scope:
            if not path.exists():
                continue
            calls.extend(self._subprocess_calls(path))

        allowed: set[tuple[str, str]] = set()
        actual = {(path, name) for path, name, _ in calls}
        self.assertEqual(
            actual,
            allowed,
            "SEC-H: direct process entrypoints must collapse to the single "
            f"registered supervisor boundary; legacy_calls={calls}",
        )

    @staticmethod
    def _security_scope_text() -> str:
        paths = [
            ROOT / "coding_workflow/command_validators.py",
            ROOT / "coding_workflow/workspace.py",
            ROOT / "coding_workflow/visionforge/browser.py",
            ROOT / "coding_workflow/policy.py",
            ROOT / "coding_workflow/__init__.py",
            ROOT / "coding_agent_cli.py",
        ]
        paths.extend(sorted((ROOT / "coding_workflow").glob("*execution*.py")))
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in dict.fromkeys(paths)
            if path.exists()
        )

    @staticmethod
    def _subprocess_calls(path: Path) -> list[tuple[str, str, int]]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_aliases = {"subprocess"}
        function_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name == "subprocess":
                        module_aliases.add(item.asname or item.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for item in node.names:
                    if item.name in {"Popen", "run"}:
                        function_aliases[item.asname or item.name] = item.name

        calls: list[tuple[str, str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in module_aliases
                and node.func.attr in {"Popen", "run"}
            ):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = function_aliases.get(node.func.id)
            if name is not None:
                calls.append((str(path.relative_to(ROOT)), name, node.lineno))
        return calls


if __name__ == "__main__":
    unittest.main()
