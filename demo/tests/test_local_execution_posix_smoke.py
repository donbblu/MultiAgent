"""Opt-in, no-target POSIX smoke artifacts.

Actual execution is not authorized by this module.  Each case stays skipped
unless both its exact environment selector and its fully qualified unittest ID
are present.  A separate reviewed outer timeout is still required before use.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Optional

from tests._local_execution_posix import (
    ExternalProcessGuard,
    GuardCleanupResult,
    read_json_if_ready,
)


CASE_ENV = "SEC_EXEC_POSIX_SMOKE_CASE"
HARD_DEADLINE_SECONDS = 4.0
WATCHDOG_ONLY = "watchdog_only"
ARM_DISARM = "arm_disarm"
TEST_IDS = {
    WATCHDOG_ONLY: (
        "tests.test_local_execution_posix_smoke."
        "LocalExecutionPosixSmokeTests.test_watchdog_only"
    ),
    ARM_DISARM: (
        "tests.test_local_execution_posix_smoke."
        "LocalExecutionPosixSmokeTests.test_arm_disarm"
    ),
}
SELECTOR_CASES = (
    (
        "watchdog exact fully qualified ID",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        (TEST_IDS[WATCHDOG_ONLY],),
        True,
    ),
    (
        "arm/disarm exact fully qualified ID",
        ARM_DISARM,
        ARM_DISARM,
        (TEST_IDS[ARM_DISARM],),
        True,
    ),
    (
        "-k option plus fully qualified ID",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        ("-k", TEST_IDS[WATCHDOG_ONLY]),
        False,
    ),
    (
        "discover command",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        ("discover",),
        False,
    ),
    (
        "module ID",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        ("tests.test_local_execution_posix_smoke",),
        False,
    ),
    (
        "class ID",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        (
            "tests.test_local_execution_posix_smoke."
            "LocalExecutionPosixSmokeTests",
        ),
        False,
    ),
    (
        "duplicate fully qualified ID",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        (TEST_IDS[WATCHDOG_ONLY], TEST_IDS[WATCHDOG_ONLY]),
        False,
    ),
    (
        "extra positional argument",
        WATCHDOG_ONLY,
        WATCHDOG_ONLY,
        (TEST_IDS[WATCHDOG_ONLY], "extra"),
        False,
    ),
    (
        "--locals option plus fully qualified ID",
        ARM_DISARM,
        ARM_DISARM,
        ("--locals", TEST_IDS[ARM_DISARM]),
        False,
    ),
    (
        "wrong environment selector",
        WATCHDOG_ONLY,
        ARM_DISARM,
        (TEST_IDS[WATCHDOG_ONLY],),
        False,
    ),
    (
        "missing environment selector",
        WATCHDOG_ONLY,
        None,
        (TEST_IDS[WATCHDOG_ONLY],),
        False,
    ),
)

_SELECTED_CASE = os.environ.get(CASE_ENV)
_RAW_TEST_ARGUMENTS = tuple(sys.argv[1:])


def _case_is_explicitly_selected(
    case_name: str,
    selected_case: Optional[str],
    raw_arguments: tuple[str, ...],
) -> bool:
    return (
        selected_case == case_name
        and raw_arguments == (TEST_IDS[case_name],)
    )


class LocalExecutionPosixSmokeTests(unittest.TestCase):
    """Two exact opt-ins that never create a target process."""

    def _assert_exact_selection(self, case_name: str) -> None:
        self.assertTrue(
            _case_is_explicitly_selected(
                case_name,
                os.environ.get(CASE_ENV),
                tuple(sys.argv[1:]),
            )
        )
        self.assertEqual(self.id(), TEST_IDS[case_name])

    def test_selector_requires_exact_raw_fully_qualified_id(self) -> None:
        for (
            label,
            case_name,
            selected_case,
            raw_arguments,
            expected,
        ) in SELECTOR_CASES:
            with self.subTest(selector_case=label):
                self.assertIs(
                    _case_is_explicitly_selected(
                        case_name,
                        selected_case,
                        raw_arguments,
                    ),
                    expected,
                )

    def _run_case(
        self,
        case_name: str,
        exercise: Callable[[ExternalProcessGuard], None],
    ) -> None:
        root = Path(
            tempfile.mkdtemp(prefix=f"sec-exec-posix-{case_name}-")
        ).resolve()
        guard = None
        primary_failure: Optional[BaseException] = None
        fallback_close_failure: Optional[BaseException] = None
        exercise_completed = False
        remove_root = False
        try:
            try:
                guard = ExternalProcessGuard(
                    root,
                    hard_deadline_seconds=HARD_DEADLINE_SECONDS,
                )
                exercise(guard)
                exercise_completed = True
            except BaseException as exc:
                primary_failure = exc
            finally:
                if guard is not None and guard.cleanup_result is None:
                    try:
                        guard.close()
                    except BaseException as exc:
                        fallback_close_failure = exc

            if (
                primary_failure is not None
                or fallback_close_failure is not None
                or not exercise_completed
            ):
                primary_text = (
                    "none"
                    if primary_failure is None
                    else f"{type(primary_failure).__name__}: {primary_failure}"
                )
                fallback_text = (
                    "none"
                    if fallback_close_failure is None
                    else (
                        f"{type(fallback_close_failure).__name__}: "
                        f"{fallback_close_failure}"
                    )
                )
                cause = primary_failure or fallback_close_failure
                raise AssertionError(
                    f"{case_name} smoke failed; preserved_root={root}; "
                    f"primary={primary_text}; fallback_close={fallback_text}"
                ) from cause
            remove_root = True
        finally:
            if remove_root:
                shutil.rmtree(root)

    def _assert_terminal_clean(
        self,
        guard: ExternalProcessGuard,
        result: GuardCleanupResult,
    ) -> None:
        self.assertIs(guard.cleanup_result, result)
        self.assertTrue(result.clean)
        self.assertEqual(result.watchdog_exit_code, 0)
        self.assertEqual(guard._watchdog.poll(), 0)
        self.assertEqual(guard._watchdog.returncode, 0)
        self.assertIn(
            "watchdog joined before guard.close returned",
            result.observations,
        )
        self.assertIsNone(result.target_group)
        self.assertTrue(result.target_group_gone)
        self.assertTrue(result.target_pids_gone)
        self.assertEqual(result.watchdog_payload.get("target_pgid"), 0)
        self.assertEqual(result.watchdog_payload.get("target_pids"), [])
        self.assertIs(result.watchdog_payload.get("clean"), True)
        self.assertIsNone(guard._spawned_process)
        self.assertTrue(guard.control_path.is_file())
        self.assertTrue(guard.watchdog_ready_path.is_file())
        self.assertTrue(guard.watchdog_result_path.is_file())

    def _assert_paths_absent(self, *paths: Path) -> None:
        for path in paths:
            with self.subTest(path=str(path)):
                self.assertFalse(path.exists(), str(path))

    @unittest.skipUnless(
        _case_is_explicitly_selected(
            WATCHDOG_ONLY,
            _SELECTED_CASE,
            _RAW_TEST_ARGUMENTS,
        ),
        "requires exact watchdog_only selector and fully qualified test ID",
    )
    def test_watchdog_only(self) -> None:
        self._assert_exact_selection(WATCHDOG_ONLY)

        def exercise(guard: ExternalProcessGuard) -> None:
            result = guard.close()
            self._assert_terminal_clean(guard, result)
            self._assert_paths_absent(
                guard.launch_armed_manifest,
                guard.watchdog_arm_ack_path,
                guard.spawn_observed_path,
                guard.watchdog_spawn_ack_path,
                guard.leader_manifest,
                guard.grandchild_manifest,
                guard.marker_path,
            )

        self._run_case(WATCHDOG_ONLY, exercise)

    @unittest.skipUnless(
        _case_is_explicitly_selected(
            ARM_DISARM,
            _SELECTED_CASE,
            _RAW_TEST_ARGUMENTS,
        ),
        "requires exact arm_disarm selector and fully qualified test ID",
    )
    def test_arm_disarm(self) -> None:
        self._assert_exact_selection(ARM_DISARM)

        def exercise(guard: ExternalProcessGuard) -> None:
            command = guard.workload_command("stdout_short")
            self.assertIs(type(command), tuple)
            self.assertTrue(guard._watchdog_arm_is_acknowledged())
            arm_ack = read_json_if_ready(guard.watchdog_arm_ack_path)
            self.assertIsNotNone(arm_ack)
            if arm_ack is None:
                self.fail("arm ACK disappeared after acknowledgment")
            self.assertEqual(arm_ack.get("token"), guard.token)
            self.assertEqual(arm_ack.get("state"), "armed_acknowledged")
            self.assertEqual(
                arm_ack.get("watchdog_pid"), guard.watchdog_pid
            )
            del command

            guard.disarm_no_spawn()
            result = guard.close()
            self._assert_terminal_clean(guard, result)

            launch = read_json_if_ready(guard.launch_armed_manifest)
            self.assertIsNotNone(launch)
            if launch is None:
                self.fail("disarmed launch manifest disappeared before assertions")
            self.assertEqual(launch.get("token"), guard.token)
            self.assertEqual(launch.get("state"), "disarmed_no_spawn")
            self.assertTrue(guard.watchdog_arm_ack_path.is_file())
            self._assert_paths_absent(
                guard.spawn_observed_path,
                guard.watchdog_spawn_ack_path,
                guard.leader_manifest,
                guard.grandchild_manifest,
                guard.marker_path,
            )

        self._run_case(ARM_DISARM, exercise)


if __name__ == "__main__":
    unittest.main()
