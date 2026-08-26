from __future__ import annotations

import importlib.util
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import _local_execution_posix as posix


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/local_execution_process.py"
SPEC = importlib.util.spec_from_file_location(
    "local_execution_process_fixture", FIXTURE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load local execution process fixture")
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class JoinedWatchdog:
    def __init__(self, pid: int, exit_code: int = 70) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.returncode = None
        self.wait_calls: list[object] = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = self.exit_code
        return self.returncode


class LocalExecutionPosixSafetyTests(unittest.TestCase):
    TOKEN = "guard_safety_test_token"

    @staticmethod
    def leader(pid: int = 41001) -> dict[str, object]:
        return {
            "token": LocalExecutionPosixSafetyTests.TOKEN,
            "role": "leader",
            "pid": pid,
            "pgid": pid,
            "sid": pid,
            "port": 0,
        }

    @staticmethod
    def grandchild(
        pid: int = 41002, leader_pid: int = 41001
    ) -> dict[str, object]:
        return {
            "token": LocalExecutionPosixSafetyTests.TOKEN,
            "role": "grandchild",
            "pid": pid,
            "pgid": leader_pid,
            "sid": leader_pid,
            "leader_pid": leader_pid,
            "port": 0,
        }

    def guard_for_close(
        self,
        paths,
        watchdog: JoinedWatchdog,
        *,
        leader=None,
        grandchild=None,
    ):
        guard = object.__new__(posix.ExternalProcessGuard)
        guard.token = self.TOKEN
        guard.guard_root = paths.root
        root_stat = paths.root.stat()
        guard._guard_root_identity = (root_stat.st_dev, root_stat.st_ino)
        guard._hard_deadline_seconds = 1.0
        guard._state_lock = threading.RLock()
        guard.control_path = paths.control
        guard.launch_armed_manifest = paths.launch
        guard.watchdog_arm_ack_path = paths.arm_ack
        guard.leader_manifest = paths.leader
        guard.grandchild_manifest = paths.grandchild
        guard.watchdog_result_path = paths.result
        guard._watchdog = watchdog
        guard._closed = False
        guard._close_in_progress = False
        guard._cleanup_result = None
        guard.leader_payload = lambda: leader
        guard.grandchild_payload = lambda: grandchild
        guard._stop_watchdog_only = mock.Mock()
        return guard

    def test_reused_pgid_never_reaches_parent_guard_killpg(self) -> None:
        group = posix.OwnedProcessGroup(41001, 41001, (41002,))
        with mock.patch.object(
            posix.os, "getpgid", return_value=51001
        ), mock.patch.object(
            posix.os, "getsid", return_value=41001
        ), mock.patch.object(posix.os, "killpg") as killpg:
            group_gone, pids_gone, errors, observations = (
                posix._cleanup_owned_group(group)
            )

        killpg.assert_not_called()
        self.assertFalse(group_gone)
        self.assertFalse(pids_gone)
        self.assertTrue(any("pgid_may_be_reused" in item for item in errors))
        self.assertEqual(observations, [])

    def test_reused_pgid_never_reaches_watchdog_killpg(self) -> None:
        with mock.patch.object(
            fixture.os, "getpgid", return_value=51001
        ), mock.patch.object(
            fixture.os, "getsid", return_value=41001
        ), mock.patch.object(fixture.os, "killpg") as killpg:
            group_gone, pids_gone, errors, observations = (
                fixture._kill_target_group(41001, (41001, 41002))
            )

        killpg.assert_not_called()
        self.assertFalse(group_gone)
        self.assertFalse(pids_gone)
        self.assertTrue(any("pgid_may_be_reused" in item for item in errors))
        self.assertEqual(observations, [])

    def test_sid_drift_never_reaches_parent_guard_killpg(self) -> None:
        group = posix.OwnedProcessGroup(41001, 41001, (41002,))
        with mock.patch.object(
            posix.os, "getpgid", return_value=41001
        ), mock.patch.object(
            posix.os, "getsid", return_value=51001
        ), mock.patch.object(posix.os, "killpg") as killpg:
            group_gone, pids_gone, errors, _observations = (
                posix._cleanup_owned_group(group)
            )

        killpg.assert_not_called()
        self.assertFalse(group_gone)
        self.assertFalse(pids_gone)
        self.assertTrue(any("sid_drift_or_reuse" in item for item in errors))

    def test_sid_drift_never_reaches_watchdog_killpg(self) -> None:
        with mock.patch.object(
            fixture.os, "getpgid", return_value=41001
        ), mock.patch.object(
            fixture.os, "getsid", return_value=51001
        ), mock.patch.object(fixture.os, "killpg") as killpg:
            group_gone, pids_gone, errors, _observations = (
                fixture._kill_target_group(41001, (41001, 41002))
            )

        killpg.assert_not_called()
        self.assertFalse(group_gone)
        self.assertFalse(pids_gone)
        self.assertTrue(any("sid_drift_or_reuse" in item for item in errors))

    def test_workload_command_arms_then_waits_for_watchdog_ack(self) -> None:
        paths = self.paths()
        guard = object.__new__(posix.ExternalProcessGuard)
        guard.token = self.TOKEN
        guard._state_lock = mock.MagicMock()
        guard._closed = False
        guard._close_in_progress = False
        guard._arm_ack_timeout_seconds = 1.0
        guard._watchdog = JoinedWatchdog(40001)
        guard.launch_armed_manifest = paths.launch
        guard.watchdog_arm_ack_path = paths.arm_ack
        guard.leader_manifest = paths.leader
        guard.grandchild_manifest = paths.grandchild
        guard.marker_path = paths.root / "marker.log"
        guard.leader_payload = lambda: None
        guard.grandchild_payload = lambda: None
        writes: list[dict[str, object]] = []

        def write(_path: Path, payload) -> None:
            writes.append(dict(payload))

        def wait_for_ack(_predicate, **_kwargs) -> bool:
            self.assertEqual(writes[-1]["state"], "armed")
            return True

        with mock.patch.object(
            posix, "read_json_if_ready", return_value=None
        ), mock.patch.object(
            posix, "atomic_write_json", side_effect=write
        ), mock.patch.object(posix, "wait_until", side_effect=wait_for_ack):
            command = guard.workload_command("success_orphan")

        guard._state_lock.__enter__.assert_called_once()
        self.assertEqual([item["state"] for item in writes], ["armed"])
        self.assertIn(str(paths.arm_ack), command)

    def test_workload_command_rejects_after_watchdog_idle_exit(self) -> None:
        guard = object.__new__(posix.ExternalProcessGuard)
        guard.token = self.TOKEN
        guard._state_lock = threading.RLock()
        guard._closed = False
        guard._close_in_progress = False
        watchdog = JoinedWatchdog(40002, exit_code=0)
        watchdog.returncode = 0
        guard._watchdog = watchdog

        with mock.patch.object(posix, "atomic_write_json") as write:
            with self.assertRaisesRegex(RuntimeError, "idle deadline"):
                guard.workload_command("success_orphan")

        write.assert_not_called()

    def test_manifest_before_spawn_without_leader_is_not_clean(self) -> None:
        paths = self.paths()

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            return None

        leader, grandchild, errors = fixture._await_target_registration(
            paths.launch,
            paths.leader,
            paths.grandchild,
            token=self.TOKEN,
            registration_wait_seconds=0,
            reader=reader,
            clock=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )

        self.assertIsNone(leader)
        self.assertIsNone(grandchild)
        self.assertIn(
            "launch_armed_without_leader: registration deadline expired", errors
        )
        self.assertIn(
            "launch_armed_without_grandchild: registration deadline expired",
            errors,
        )

    def test_leader_and_grandchild_visible_after_trigger_are_observed(self) -> None:
        paths = self.paths()
        state = {"now": 0.0, "leader": None, "grandchild": None}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.leader:
                return state["leader"]
            if path == paths.grandchild:
                return state["grandchild"]
            return None

        def sleeper(seconds: float) -> None:
            state["now"] += max(seconds, 0.01)
            state["leader"] = self.leader()
            state["grandchild"] = self.grandchild()

        leader, grandchild, errors = fixture._await_target_registration(
            paths.launch,
            paths.leader,
            paths.grandchild,
            token=self.TOKEN,
            registration_wait_seconds=0.2,
            reader=reader,
            clock=lambda: state["now"],
            sleeper=sleeper,
        )

        self.assertEqual(leader, self.leader())
        self.assertEqual(grandchild, self.grandchild())
        self.assertEqual(errors, [])

    def test_grandchild_before_leader_waits_for_leader_registration(self) -> None:
        paths = self.paths()
        child = self.grandchild()
        state = {"now": 0.0, "leader": None}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.leader:
                return state["leader"]
            if path == paths.grandchild:
                return child
            return None

        def sleeper(seconds: float) -> None:
            state["now"] += max(seconds, 0.01)
            state["leader"] = self.leader()

        leader, grandchild, errors = fixture._await_target_registration(
            paths.launch,
            paths.leader,
            paths.grandchild,
            token=self.TOKEN,
            registration_wait_seconds=0.2,
            reader=reader,
            clock=lambda: state["now"],
            sleeper=sleeper,
        )

        self.assertEqual(leader, self.leader())
        self.assertEqual(grandchild, child)
        self.assertEqual(errors, [])

    def test_watchdog_owner_exit_armed_without_leader_is_unclean(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(fixture, "_read_json", side_effect=reader), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", return_value=False
        ), mock.patch.object(fixture.os, "killpg") as killpg:
            exit_code = fixture._watchdog(args)

        killpg.assert_not_called()
        self.assertEqual(exit_code, 70)
        self.assertEqual(captured[paths.result]["reason"], "owner_exit")
        self.assertFalse(captured[paths.result]["clean"])
        self.assertGreaterEqual(state["now"], 1.0)
        self.assertIn(
            "launch_armed_without_leader: registration deadline expired",
            captured[paths.result]["errors"],
        )

    def test_watchdog_arm_ack_starts_deadline_after_idle(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def reader(path: Path):
            if path == paths.launch and state["now"] >= 0.5:
                return {"token": self.TOKEN, "state": "armed"}
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )

        def owner_exists(_pid: int) -> bool:
            return state["now"] < 0.5

        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(
            fixture, "_read_json", side_effect=reader
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", side_effect=owner_exists
        ), mock.patch.object(fixture.os, "killpg") as group_signal:
            exit_code = fixture._watchdog(args)

        group_signal.assert_not_called()
        self.assertEqual(exit_code, 70)
        ack = captured[paths.arm_ack]
        self.assertGreaterEqual(ack["acknowledged_monotonic"], 0.5)
        self.assertAlmostEqual(
            ack["deadline_monotonic"] - ack["acknowledged_monotonic"],
            1.0,
        )
        self.assertEqual(
            captured[paths.result]["arm_deadline_monotonic"],
            ack["deadline_monotonic"],
        )
        self.assertGreaterEqual(state["now"], ack["deadline_monotonic"])

    def test_watchdog_owner_alive_without_arm_exits_at_idle_deadline(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(
            fixture, "_read_json", return_value=None
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", return_value=True
        ), mock.patch.object(fixture.os, "killpg") as group_signal:
            exit_code = fixture._watchdog(args)

        group_signal.assert_not_called()
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured[paths.result]["reason"], "idle_deadline")
        self.assertTrue(captured[paths.result]["clean"])
        self.assertGreaterEqual(state["now"], 1.0)
        self.assertNotIn(paths.arm_ack, captured)

    def test_watchdog_cleanup_control_armed_waits_until_hard_deadline(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def reader(path: Path):
            if path == paths.control:
                return {"token": self.TOKEN, "action": "cleanup"}
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(
            fixture, "_read_json", side_effect=reader
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(fixture.os, "killpg") as group_signal:
            exit_code = fixture._watchdog(args)

        group_signal.assert_not_called()
        self.assertEqual(exit_code, 70)
        self.assertEqual(captured[paths.result]["reason"], "cleanup_control")
        self.assertFalse(captured[paths.result]["clean"])
        self.assertGreaterEqual(state["now"], 1.0)

    def test_watchdog_disarmed_no_spawn_finishes_before_hard_deadline(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "disarmed_no_spawn"}
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep"
        ) as sleeper, mock.patch.object(
            fixture, "_read_json", side_effect=reader
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", return_value=False
        ), mock.patch.object(fixture.os, "killpg") as group_signal:
            exit_code = fixture._watchdog(args)

        sleeper.assert_not_called()
        group_signal.assert_not_called()
        self.assertEqual(exit_code, 0)
        self.assertTrue(captured[paths.result]["clean"])
        self.assertEqual(state["now"], 0.0)

    def test_watchdog_leader_gone_delayed_grandchild_is_cleaned(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0, "group_killed": False}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "registered"}
            if path == paths.leader:
                return self.leader()
            if path == paths.grandchild and state["now"] >= 0.4:
                return self.grandchild()
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        def getpgid(pid: int) -> int:
            if pid == 41001 or state["group_killed"]:
                raise ProcessLookupError
            self.assertEqual(pid, 41002)
            return 41001

        def killpg(pgid: int, signum: int) -> None:
            self.assertEqual((pgid, signum), (41001, signal.SIGTERM))
            state["group_killed"] = True

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(
            fixture, "_read_json", side_effect=reader
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", return_value=False
        ), mock.patch.object(
            fixture.os, "getpgid", side_effect=getpgid
        ), mock.patch.object(
            fixture.os, "getsid", return_value=41001
        ), mock.patch.object(fixture.os, "killpg", side_effect=killpg) as group_signal:
            exit_code = fixture._watchdog(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(group_signal.call_count, 1)
        self.assertTrue(captured[paths.result]["clean"])
        self.assertEqual(captured[paths.result]["target_pids"], [41001, 41002])
        self.assertGreaterEqual(state["now"], 0.4)
        self.assertLess(state["now"], 1.0)

    def test_watchdog_leader_gone_missing_grandchild_is_not_clean(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "registered"}
            if path == paths.leader:
                return self.leader()
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        args = SimpleNamespace(
            token=self.TOKEN,
            owner_pid=42001,
            owner_pgid=42001,
            hard_deadline_seconds=1.0,
            registration_wait_seconds=0.0,
            ready=paths.ready,
            result=paths.result,
            control=paths.control,
            launch_armed_manifest=paths.launch,
            arm_ack=paths.arm_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=sleeper
        ), mock.patch.object(
            fixture, "_read_json", side_effect=reader
        ), mock.patch.object(
            fixture, "_atomic_write_json", side_effect=writer
        ), mock.patch.object(
            fixture, "_pid_exists", return_value=False
        ), mock.patch.object(
            fixture.os, "getpgid", side_effect=ProcessLookupError
        ), mock.patch.object(fixture.os, "killpg") as group_signal:
            exit_code = fixture._watchdog(args)

        group_signal.assert_not_called()
        self.assertEqual(exit_code, 70)
        self.assertFalse(captured[paths.result]["clean"])
        self.assertGreaterEqual(state["now"], 1.0)
        self.assertIn(
            "launch_armed_without_grandchild: registration deadline expired",
            captured[paths.result]["errors"],
        )

    def test_disarmed_no_spawn_is_a_clean_registration_state(self) -> None:
        paths = self.paths()

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "disarmed_no_spawn"}
            return None

        leader, grandchild, errors = fixture._await_target_registration(
            paths.launch,
            paths.leader,
            paths.grandchild,
            token=self.TOKEN,
            registration_wait_seconds=0,
            reader=reader,
            clock=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )
        self.assertIsNone(leader)
        self.assertIsNone(grandchild)
        self.assertEqual(errors, [])

    def test_close_exceptions_join_watchdog_and_are_idempotent(self) -> None:
        paths = self.paths()
        watchdog = JoinedWatchdog(44001)
        guard = self.guard_for_close(
            paths,
            watchdog,
            leader=self.leader(),
            grandchild=self.grandchild(),
        )
        group = posix.OwnedProcessGroup(41001, 41001, (41002,))

        with mock.patch.object(
            posix, "atomic_write_json", side_effect=OSError("control failed")
        ) as write, mock.patch.object(
            posix,
            "read_json_if_ready",
            side_effect=lambda path: (
                {"token": self.TOKEN, "state": "registered"}
                if path == paths.launch
                else None
            ),
        ), mock.patch.object(
            posix, "_validated_owned_group", return_value=(group, [])
        ), mock.patch.object(
            posix, "_cleanup_owned_group", side_effect=OSError("cleanup failed")
        ):
            with self.assertRaisesRegex(AssertionError, "watchdog join"):
                guard.close()
            with self.assertRaisesRegex(AssertionError, "watchdog join"):
                guard.close()

        self.assertEqual(write.call_count, 1)
        guard._stop_watchdog_only.assert_not_called()
        self.assertEqual(len(watchdog.wait_calls), 1)
        self.assertEqual(watchdog.poll(), 70)
        self.assertIsNotNone(guard.cleanup_result)
        assert guard.cleanup_result is not None
        self.assertIn(
            "watchdog joined before guard.close returned",
            guard.cleanup_result.observations,
        )

    def test_close_armed_without_registration_joins_watchdog(self) -> None:
        paths = self.paths()
        watchdog = JoinedWatchdog(45001)
        guard = self.guard_for_close(paths, watchdog)

        def read(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            return None

        with mock.patch.object(
            posix, "atomic_write_json", side_effect=OSError("control failed")
        ) as write, mock.patch.object(
            posix, "read_json_if_ready", side_effect=read
        ), mock.patch.object(posix, "_cleanup_owned_group") as cleanup:
            with self.assertRaisesRegex(AssertionError, "registration unresolved"):
                guard.close()
            with self.assertRaisesRegex(AssertionError, "registration unresolved"):
                guard.close()

        self.assertEqual(write.call_count, 1)
        cleanup.assert_not_called()
        guard._stop_watchdog_only.assert_not_called()
        self.assertEqual(len(watchdog.wait_calls), 1)
        self.assertEqual(watchdog.poll(), 70)
        self.assertIsNotNone(guard.cleanup_result)
        assert guard.cleanup_result is not None
        self.assertFalse(guard.cleanup_result.target_group_gone)
        self.assertFalse(guard.cleanup_result.target_pids_gone)
        self.assertIn(
            "watchdog joined before guard.close returned",
            guard.cleanup_result.observations,
        )

    def test_close_detects_temporary_directory_ordering_violation(self) -> None:
        paths = self.paths()
        watchdog = JoinedWatchdog(46001, exit_code=0)
        guard = self.guard_for_close(paths, watchdog)
        guard.guard_root = paths.root / "already-removed-guard-root"

        with mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix, "read_json_if_ready", return_value=None
        ):
            with self.assertRaisesRegex(
                AssertionError, "TemporaryDirectory exit first"
            ):
                guard.close()

        self.assertEqual(len(watchdog.wait_calls), 1)
        self.assertEqual(watchdog.poll(), 0)
        self.assertIn(
            "TemporaryDirectory",
            posix.ExternalProcessGuard.__doc__ or "",
        )
        self.assertIn("inner", posix.ExternalProcessGuard.__doc__ or "")

    def test_close_timeout_emergency_stops_and_joins_watchdog(self) -> None:
        paths = self.paths()

        class TimedOutWatchdog:
            pid = 47001

            def __init__(self) -> None:
                self.returncode = None
                self.wait_calls: list[object] = []

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.wait_calls.append(timeout)
                raise subprocess.TimeoutExpired("watchdog", timeout)

        watchdog = TimedOutWatchdog()
        guard = self.guard_for_close(paths, watchdog)

        def emergency_stop() -> None:
            watchdog.returncode = -signal.SIGKILL

        guard._stop_watchdog_only = mock.Mock(side_effect=emergency_stop)
        with mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix, "read_json_if_ready", return_value=None
        ):
            with self.assertRaisesRegex(AssertionError, "completion buffer"):
                guard.close()

        guard._stop_watchdog_only.assert_called_once()
        self.assertEqual(watchdog.poll(), -signal.SIGKILL)
        self.assertEqual(len(watchdog.wait_calls), 1)
        self.assertLessEqual(
            watchdog.wait_calls[0],
            guard._hard_deadline_seconds
            + posix.WATCHDOG_CLOSE_BUFFER_SECONDS,
        )

    def paths(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        return SimpleNamespace(
            root=root,
            launch=root / "launch.json",
            arm_ack=root / "arm-ack.json",
            leader=root / "leader.json",
            grandchild=root / "grandchild.json",
            control=root / "control.json",
            ready=root / "ready.json",
            result=root / "result.json",
        )


if __name__ == "__main__":
    unittest.main()
