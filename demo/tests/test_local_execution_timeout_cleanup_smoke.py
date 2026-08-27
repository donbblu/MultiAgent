"""One default-off Runtime timeout cleanup smoke under ExternalProcessGuard.

The real test is deliberately narrower than a Runtime acceptance test.  It
proves that one trusted ``hang_ignore_term`` fixture is timed out, escalated,
reaped, and independently observed absent.  Quarantine behavior is covered by
pure mocks in ``test_local_execution_supervisor``.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

import coding_workflow.local_execution as local_execution_module
from coding_workflow.local_execution import (
    PROFILE_CORE,
    issue_trusted_local_confirmation,
    prepare_execution,
    run_prepared,
)
from tests._local_execution_posix import (
    ExternalProcessGuard,
    GuardCleanupResult,
    read_json_if_ready,
    wait_until,
)


CASE_ENV = "SEC_EXEC_REAL_TIMEOUT_CLEANUP"
TIMEOUT_CASE = "guarded_hang_ignore_term"
TEST_ID = (
    "tests.test_local_execution_timeout_cleanup_smoke."
    "LocalExecutionTimeoutCleanupSmokeTests."
    "test_guarded_real_timeout_cleanup"
)

_SELECTED_CASE = os.environ.get(CASE_ENV)
_RAW_TEST_ARGUMENTS = tuple(sys.argv[1:])


def _real_case_is_explicitly_selected(
    selected_case: Optional[str],
    raw_arguments: tuple[str, ...],
) -> bool:
    return selected_case == TIMEOUT_CASE and raw_arguments == (TEST_ID,)


def _lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class LocalExecutionTimeoutCleanupSmokeTests(unittest.TestCase):
    def test_selector_is_fail_closed(self) -> None:
        cases = (
            (TIMEOUT_CASE, (TEST_ID,), True),
            (None, (TEST_ID,), False),
            (TIMEOUT_CASE, (), False),
            (TIMEOUT_CASE, ("-k", TEST_ID), False),
            (TIMEOUT_CASE, (TEST_ID, TEST_ID), False),
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
        "requires exact timeout selector and fully qualified test ID",
    )
    def test_guarded_real_timeout_cleanup(self) -> None:
        self.assertEqual(self.id(), TEST_ID)
        self.assertTrue(_real_case_is_explicitly_selected(
            os.environ.get(CASE_ENV),
            tuple(sys.argv[1:]),
        ))
        root = Path(tempfile.mkdtemp(
            prefix="sec-exec-runtime-timeout-",
            dir="/private/tmp",
        )).resolve()
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        guard: ExternalProcessGuard | None = None
        guard_result: GuardCleanupResult | None = None
        runtime_process = None
        runtime_private_roots: list[Path] = []
        runtime_spawn_count = 0
        success = False
        real_spawn = local_execution_module._spawn
        real_private_environment = local_execution_module._private_environment

        try:
            guard = ExternalProcessGuard(root, hard_deadline_seconds=16.0)
            guarded_command = guard.workload_command(
                "hang_ignore_term",
                tick_seconds=0.02,
            )
            requested_command = (
                Path(guarded_command[0]).name,
                *guarded_command[1:],
            )
            prepared = prepare_execution(
                profile_id=PROFILE_CORE,
                workspace_root=workspace,
                executable=guarded_command[0],
                command=requested_command,
                wall_deadline_seconds=2.5,
                output_limit_chars=10_000,
                python_profile=True,
            )
            self.assertEqual(prepared.execution_argv, guarded_command)
            confirmation = issue_trusted_local_confirmation(
                workspace_digest=prepared.workspace_digest,
                input_digest=prepared.input_digest,
                profile_digest=prepared.profile_digest,
                expires_at_monotonic=time.monotonic() + 5.0,
            )

            def capture_private_environment(*, python_profile: bool):
                private = real_private_environment(
                    python_profile=python_profile,
                )
                runtime_private_roots.append(private.root)
                return private

            def guarded_runtime_spawn(
                candidate,
                environment,
                *,
                background: bool,
            ):
                nonlocal runtime_process, runtime_spawn_count
                self.assertEqual(candidate.execution_argv, prepared.execution_argv)
                self.assertEqual(candidate.workspace_root, prepared.workspace_root)
                self.assertEqual(candidate.input_digest, prepared.input_digest)
                self.assertEqual(candidate.profile_digest, prepared.profile_digest)
                self.assertFalse(background)
                self.assertEqual(runtime_spawn_count, 0)
                runtime_spawn_count += 1
                guarded_popen = guard.spawn_observing_popen(
                    local_execution_module.subprocess.Popen
                )
                with mock.patch.object(
                    local_execution_module.subprocess,
                    "Popen",
                    new=guarded_popen,
                ):
                    runtime_process = real_spawn(
                        candidate,
                        environment,
                        background=background,
                    )
                self.assertTrue(wait_until(
                    lambda: (
                        guard.leader_payload() or {}
                    ).get("grandchild_pid") is not None,
                    timeout_seconds=2.0,
                    poll_interval_seconds=0.02,
                ))
                self.assertTrue(wait_until(
                    lambda: (
                        guard.grandchild_payload() or {}
                    ).get("state") == "ready",
                    timeout_seconds=2.0,
                    poll_interval_seconds=0.02,
                ))
                return runtime_process

            with mock.patch.object(
                local_execution_module,
                "_spawn",
                new=guarded_runtime_spawn,
            ), mock.patch.object(
                local_execution_module,
                "_private_environment",
                new=capture_private_environment,
            ):
                outcome = run_prepared(
                    prepared,
                    trusted_local=confirmation,
                )

            guard_result = guard.close()
            actions = {
                item["phase"]: item
                for item in outcome.cleanup_evidence["actions"]
            }
            resources = outcome.cleanup_evidence["owned_resource_outcomes"]

            self.assertEqual(runtime_spawn_count, 1)
            self.assertTrue(outcome.timed_out)
            self.assertIsNotNone(runtime_process)
            self.assertIsNotNone(runtime_process.returncode)
            self.assertIsNotNone(runtime_process.poll())
            self.assertEqual(runtime_process.wait(timeout=0), runtime_process.returncode)
            self.assertTrue(actions["term"]["attempted"])
            self.assertEqual(actions["term"]["outcome"], "signal_sent")
            self.assertTrue(actions["kill"]["attempted"])
            self.assertEqual(actions["kill"]["outcome"], "signal_sent")
            self.assertTrue(actions["wait_reap"]["attempted"])
            self.assertEqual(actions["verify"]["outcome"], "process_group_absent")
            self.assertTrue(outcome.cleanup_evidence["direct_child_reaped"])
            self.assertTrue(outcome.cleanup_evidence["verified"])
            self.assertEqual(resources["streams"]["outcome"], "closed")
            self.assertEqual(resources["private_environment"]["outcome"], "closed")
            self.assertTrue(_lower_sha256(outcome.cleanup_evidence_digest))
            self.assertEqual(len(runtime_private_roots), 1)
            self.assertFalse(runtime_private_roots[0].exists())
            self.assertTrue(runtime_process.stdout.closed)
            self.assertTrue(runtime_process.stderr.closed)

            self.assertTrue(guard_result.clean)
            self.assertEqual(guard_result.errors, ())
            self.assertEqual(guard_result.watchdog_exit_code, 0)
            self.assertTrue(guard_result.target_group_gone)
            self.assertTrue(guard_result.target_pids_gone)
            self.assertIsNotNone(guard_result.target_group)
            if guard_result.target_group is None:
                self.fail("clean timeout result omitted its target group")
            self.assertEqual(guard_result.target_group.leader_pid, runtime_process.pid)
            self.assertEqual(len(guard_result.target_group.descendant_pids), 1)
            self.assertEqual(len(guard_result.target_group.pids), 2)
            self.assertIs(guard._spawned_process, runtime_process)
            self.assertEqual(guard._watchdog.poll(), 0)
            self.assertIs(guard_result.watchdog_payload.get("clean"), True)
            self.assertEqual(
                guard_result.watchdog_payload.get("reason"),
                "cleanup_control",
            )

            observed = read_json_if_ready(guard.spawn_observed_path)
            acknowledged = read_json_if_ready(guard.watchdog_spawn_ack_path)
            leader = guard.leader_payload()
            grandchild = guard.grandchild_payload()
            for name, payload in (
                ("spawn observation", observed),
                ("spawn acknowledgment", acknowledged),
                ("leader manifest", leader),
                ("grandchild manifest", grandchild),
            ):
                self.assertIsNotNone(payload, name)
                if payload is None:
                    self.fail(f"{name} disappeared before verification")
                self.assertEqual(payload.get("token"), guard.token, name)
            self.assertEqual(observed.get("pid"), runtime_process.pid)
            self.assertEqual(acknowledged.get("pid"), runtime_process.pid)
            self.assertEqual(leader.get("pid"), runtime_process.pid)
            self.assertEqual(leader.get("grandchild_pid"), grandchild.get("pid"))

            marker_size = guard.marker_path.stat().st_size
            time.sleep(0.1)
            self.assertEqual(guard.marker_path.stat().st_size, marker_size)
            success = True
        finally:
            if guard is not None and guard.cleanup_result is None:
                try:
                    armed = read_json_if_ready(guard.launch_armed_manifest)
                    if (
                        guard._spawned_process is None
                        and armed is not None
                        and armed.get("state") == "armed"
                    ):
                        guard.disarm_no_spawn()
                finally:
                    guard.close()
            if success:
                shutil.rmtree(root)
            else:
                sys.stderr.write(f"preserved timeout smoke root: {root}\n")


if __name__ == "__main__":
    unittest.main()
