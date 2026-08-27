"""One opt-in real happy-path smoke for the production Workspace adapter."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Optional
from unittest import mock

import coding_workflow.local_execution as local_execution_module
import coding_workflow.local_execution_approval as approval_module
from coding_workflow.local_execution import (
    CONTRACT_VERSION,
    PROFILE_LEGACY,
    SANDBOX_REQUIRED,
    LocalExecutionError,
)
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.models import CommandResult
from coding_workflow.workspace import ProjectWorkspace


CASE_ENV = "SEC_EXEC_WORKSPACE_REAL_SMOKE"
PYTHON_VERSION = "python_version"
TEST_ID = (
    "tests.test_project_workspace_production_smoke."
    "ProjectWorkspaceProductionSmokeTests.test_real_python_version"
)
COMMAND = ["python3", "-V"]
EXPECTED_STDOUT = "Python 3.9.6\n"
EXPECTED_STDERR = ""

_SELECTED_CASE = os.environ.get(CASE_ENV)
_RAW_TEST_ARGUMENTS = tuple(sys.argv[1:])


def _real_case_is_explicitly_selected(
    selected_case: Optional[str],
    raw_arguments: tuple[str, ...],
) -> bool:
    return selected_case == PYTHON_VERSION and raw_arguments == (TEST_ID,)


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class ProjectWorkspaceProductionSmokeTests(unittest.TestCase):
    def test_default_unapproved_call_is_zero_spawn(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="sec-exec-workspace-denied-",
            dir="/private/tmp",
        ) as temporary:
            workspace = ProjectWorkspace(Path(temporary), command_timeout=5)
            with mock.patch(
                "coding_workflow.local_execution._spawn"
            ) as spawn:
                rejection = workspace.run(list(COMMAND))

        self.assertIs(type(rejection), LocalExecutionError)
        self.assertEqual(rejection.code, SANDBOX_REQUIRED)
        self.assertEqual(
            set(rejection.confirmation_request),
            {"workspace_digest", "input_digest", "profile_digest"},
        )
        self.assertTrue(all(
            _is_lower_sha256(value)
            for value in rejection.confirmation_request.values()
        ))
        spawn.assert_not_called()

    def test_selector_is_fail_closed(self) -> None:
        cases = (
            (PYTHON_VERSION, (TEST_ID,), True),
            (None, (TEST_ID,), False),
            (PYTHON_VERSION, (), False),
            (PYTHON_VERSION, ("-k", TEST_ID), False),
            (PYTHON_VERSION, (TEST_ID, TEST_ID), False),
            ("another-case", (TEST_ID,), False),
        )
        for selected, arguments, expected in cases:
            with self.subTest(selected=selected, arguments=arguments):
                self.assertIs(
                    _real_case_is_explicitly_selected(selected, arguments),
                    expected,
                )

    @unittest.skipUnless(
        _real_case_is_explicitly_selected(
            _SELECTED_CASE,
            _RAW_TEST_ARGUMENTS,
        ),
        "requires exact python_version selector and fully qualified test ID",
    )
    def test_real_python_version(self) -> None:
        self.assertEqual(self.id(), TEST_ID)
        root = Path(
            tempfile.mkdtemp(
                prefix="sec-exec-workspace-python-version-",
                dir="/private/tmp",
            )
        ).resolve()
        success = False
        try:
            workspace = ProjectWorkspace(root, command_timeout=5)
            approver = LocalExecutionApprover(True, ttl_seconds=5)
            spawn_count = 0
            issue_count = 0
            real_spawn = local_execution_module._spawn
            real_issue = approval_module.issue_trusted_local_confirmation

            def counted_spawn(*args, **kwargs):
                nonlocal spawn_count
                spawn_count += 1
                return real_spawn(*args, **kwargs)

            def counted_issue(**kwargs):
                nonlocal issue_count
                issue_count += 1
                return real_issue(**kwargs)

            with mock.patch.object(
                local_execution_module,
                "_spawn",
                new=counted_spawn,
            ), mock.patch.object(
                approval_module,
                "issue_trusted_local_confirmation",
                new=counted_issue,
            ):
                result = approver.run_workspace(workspace, list(COMMAND))

            self.assertIs(type(result), CommandResult)
            self.assertEqual(issue_count, 1)
            self.assertEqual(spawn_count, 1)
            self.assertEqual(result.command, COMMAND)
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(result.stdout, EXPECTED_STDOUT)
            self.assertEqual(result.stderr, EXPECTED_STDERR)
            self.assertEqual(result.stdout_chars, len(EXPECTED_STDOUT))
            self.assertEqual(result.stderr_chars, 0)
            self.assertEqual(
                result.stdout_sha256,
                sha256(EXPECTED_STDOUT.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                result.stderr_sha256,
                sha256(b"").hexdigest(),
            )
            self.assertFalse(result.stdout_truncated)
            self.assertFalse(result.stderr_truncated)

            manifest = result.profile_manifest
            self.assertIsInstance(manifest, Mapping)
            if not isinstance(manifest, Mapping):
                self.fail("production result omitted its profile manifest")
            self.assertEqual(manifest.get("contract_version"), CONTRACT_VERSION)
            self.assertEqual(manifest.get("profile_id"), PROFILE_LEGACY)
            self.assertEqual(manifest.get("executable"), "/usr/bin/python3")
            self.assertEqual(tuple(manifest.get("argv", ())), (
                "/usr/bin/python3",
                "-V",
            ))
            self.assertEqual(manifest.get("cwd"), str(root))
            self.assertTrue(_is_lower_sha256(manifest.get("profile_digest")))
            limits = manifest.get("limits")
            self.assertIsInstance(limits, Mapping)
            if not isinstance(limits, Mapping):
                self.fail("profile manifest omitted limits")
            self.assertEqual(limits.get("wall_deadline_seconds"), 5)
            self.assertEqual(limits.get("cleanup_barrier_seconds"), 5)
            self.assertEqual(limits.get("stdout_limit_chars"), 10_000)
            self.assertEqual(limits.get("stderr_limit_chars"), 10_000)

            cleanup = result.cleanup_evidence
            self.assertIsInstance(cleanup, Mapping)
            if not isinstance(cleanup, Mapping):
                self.fail("production result omitted cleanup evidence")
            self.assertEqual(cleanup.get("status"), "terminal")
            self.assertIs(cleanup.get("direct_child_reaped"), True)
            self.assertIs(cleanup.get("verified"), True)
            resources = cleanup.get("resources")
            self.assertIsInstance(resources, Mapping)
            if not isinstance(resources, Mapping):
                self.fail("cleanup evidence omitted owned process identity")
            self.assertIsInstance(resources.get("pid"), int)
            self.assertEqual(resources.get("pgid"), resources.get("pid"))
            actions = cleanup.get("actions")
            self.assertIsInstance(actions, (tuple, list))
            self.assertEqual(
                tuple(action.get("phase") for action in actions),
                ("term", "kill", "wait_reap", "verify"),
            )
            owned = cleanup.get("owned_resource_outcomes")
            self.assertIsInstance(owned, Mapping)
            if not isinstance(owned, Mapping):
                self.fail("cleanup evidence omitted owned resource outcomes")
            self.assertEqual(owned["streams"].get("outcome"), "closed")
            self.assertEqual(
                owned["private_environment"].get("outcome"),
                "closed",
            )
            self.assertTrue(_is_lower_sha256(result.cleanup_evidence_digest))
            self.assertEqual(workspace.list_files(), [])

            with self.assertRaisesRegex(
                LocalExecutionError,
                "already consumed",
            ):
                approver.run_workspace(workspace, list(COMMAND))
            success = True
        finally:
            if success:
                shutil.rmtree(root)
            else:
                sys.stderr.write(f"preserved workspace smoke root: {root}\n")


if __name__ == "__main__":
    unittest.main()
