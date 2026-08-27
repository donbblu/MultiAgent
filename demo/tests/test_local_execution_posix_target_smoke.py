"""One deliberately small, opt-in real POSIX fixture smoke.

This is a development smoke, not a production safety certification.  Normal
discovery never constructs the guard because the real test requires both an
exact environment selector and its fully qualified unittest ID.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from tests._local_execution_posix import (
    ExternalProcessGuard,
    GuardCleanupResult,
    _minimal_environment,
    read_json_if_ready,
)


CASE_ENV = "SEC_EXEC_POSIX_TARGET_SMOKE"
STDOUT_SHORT = "stdout_short"
TEST_ID = (
    "tests.test_local_execution_posix_target_smoke."
    "LocalExecutionPosixTargetSmokeTests.test_stdout_short_real_fixture"
)
EXPECTED_STDOUT = b"fixture-short-stdout\n"
EXPECTED_STDERR = b"fixture-short-stderr\n"

_SELECTED_CASE = os.environ.get(CASE_ENV)
_RAW_TEST_ARGUMENTS = tuple(sys.argv[1:])


def _target_case_is_explicitly_selected(
    selected_case: Optional[str],
    raw_arguments: tuple[str, ...],
) -> bool:
    return selected_case == STDOUT_SHORT and raw_arguments == (TEST_ID,)


class LocalExecutionPosixTargetSmokeTests(unittest.TestCase):
    def test_selector_is_fail_closed(self) -> None:
        cases = (
            (STDOUT_SHORT, (TEST_ID,), True),
            (None, (TEST_ID,), False),
            (STDOUT_SHORT, (), False),
            (STDOUT_SHORT, ("-k", TEST_ID), False),
            (STDOUT_SHORT, (TEST_ID, TEST_ID), False),
            ("another-mode", (TEST_ID,), False),
        )
        for selected, arguments, expected in cases:
            with self.subTest(selected=selected, arguments=arguments):
                self.assertIs(
                    _target_case_is_explicitly_selected(selected, arguments),
                    expected,
                )

    @unittest.skipUnless(
        _target_case_is_explicitly_selected(
            _SELECTED_CASE,
            _RAW_TEST_ARGUMENTS,
        ),
        "requires exact stdout_short selector and fully qualified test ID",
    )
    def test_stdout_short_real_fixture(self) -> None:
        self.assertEqual(self.id(), TEST_ID)

        root = Path(
            tempfile.mkdtemp(
                prefix="sec-exec-posix-target-stdout-short-",
                dir="/private/tmp",
            )
        ).resolve()
        guard: Optional[ExternalProcessGuard] = None
        result: Optional[GuardCleanupResult] = None
        success = False
        try:
            guard = ExternalProcessGuard(root, hard_deadline_seconds=8.0)
            command = guard.workload_command(STDOUT_SHORT)

            # Reuse the exact Popen type already used to create the reviewed
            # watchdog.  The guard wrapper is the only call site that invokes
            # it for the target and withholds the handle until spawn ACK.
            popen_type = type(guard._watchdog)
            self.assertEqual(popen_type.__module__, "subprocess")
            self.assertEqual(popen_type.__name__, "Popen")
            guarded_popen = guard.spawn_observing_popen(popen_type)
            process = guarded_popen(
                command,
                cwd=str(guard.guard_root),
                env=_minimal_environment(guard.guard_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
                umask=0o077,
            )

            stdout, stderr = process.communicate(timeout=4.0)
            result = guard.close()

            self.assertEqual(stdout, EXPECTED_STDOUT)
            self.assertEqual(stderr, EXPECTED_STDERR)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.poll(), 0)
            self.assertEqual(process.wait(timeout=0), 0)
            self.assertTrue(result.clean)
            self.assertEqual(result.errors, ())
            self.assertEqual(result.watchdog_exit_code, 0)
            self.assertTrue(result.target_group_gone)
            self.assertTrue(result.target_pids_gone)
            self.assertIsNotNone(result.target_group)
            if result.target_group is None:
                self.fail("clean target result omitted its registered group")
            self.assertEqual(result.target_group.pgid, process.pid)
            self.assertIn(process.pid, result.target_group.pids)
            self.assertIs(guard._spawned_process, process)
            self.assertEqual(guard._watchdog.poll(), 0)
            self.assertIs(result.watchdog_payload.get("clean"), True)
            self.assertTrue(result.watchdog_payload.get("target_group_gone"))
            self.assertTrue(result.watchdog_payload.get("target_pids_gone"))
            self.assertEqual(
                result.watchdog_payload.get("target_pgid"),
                process.pid,
            )
            self.assertIn(
                "watchdog joined before guard.close returned",
                result.observations,
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
            self.assertEqual(observed.get("pid"), process.pid)
            self.assertEqual(observed.get("pgid"), process.pid)
            self.assertEqual(observed.get("sid"), process.pid)
            self.assertEqual(acknowledged.get("pid"), process.pid)
            self.assertEqual(acknowledged.get("pgid"), process.pid)
            self.assertEqual(acknowledged.get("sid"), process.pid)
            self.assertEqual(leader.get("pid"), process.pid)
            self.assertEqual(leader.get("pgid"), process.pid)
            self.assertEqual(leader.get("sid"), process.pid)
            self.assertEqual(grandchild.get("pgid"), process.pid)
            self.assertEqual(grandchild.get("sid"), process.pid)
            self.assertEqual(leader.get("grandchild_pid"), grandchild.get("pid"))
            success = True
        finally:
            if guard is not None and guard.cleanup_result is None:
                guard.close()
            if success:
                shutil.rmtree(root)
            else:
                sys.stderr.write(f"preserved target smoke root: {root}\n")


if __name__ == "__main__":
    unittest.main()
