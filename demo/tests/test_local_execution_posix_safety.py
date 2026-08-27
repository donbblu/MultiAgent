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


class FakeSpawnProcess:
    def __init__(self, pid: int = 41001) -> None:
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls: list[object] = []

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixture-child", timeout)
        return self.returncode


class ControlledWatchdog:
    def __init__(self, pid: int = 47001) -> None:
        self.pid = pid
        self.returncode = None
        self.wait_calls: list[object] = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("watchdog", timeout)
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
        guard.spawn_observed_path = paths.spawn_observed
        guard.watchdog_spawn_ack_path = paths.spawn_ack
        guard.leader_manifest = paths.leader
        guard.grandchild_manifest = paths.grandchild
        guard.watchdog_result_path = paths.result
        guard._watchdog = watchdog
        guard._closed = False
        guard._close_in_progress = False
        guard._cleanup_result = None
        guard._spawned_process = None
        guard._deferred_close_error = None
        guard.leader_payload = lambda: leader
        guard.grandchild_payload = lambda: grandchild
        guard._stop_watchdog_only = mock.Mock()
        return guard

    def guard_for_spawn(self, paths, process: FakeSpawnProcess):
        guard = object.__new__(posix.ExternalProcessGuard)
        guard.token = self.TOKEN
        guard._state_lock = threading.RLock()
        guard._closed = False
        guard._close_in_progress = False
        guard._arm_ack_timeout_seconds = 1.0
        guard._spawn_ack_timeout_seconds = 1.0
        guard._watchdog = JoinedWatchdog(40001)
        guard.launch_armed_manifest = paths.launch
        guard.watchdog_arm_ack_path = paths.arm_ack
        guard.spawn_observed_path = paths.spawn_observed
        guard.watchdog_spawn_ack_path = paths.spawn_ack
        guard.leader_manifest = paths.leader
        guard.grandchild_manifest = paths.grandchild
        guard.marker_path = paths.root / "marker.log"
        guard._spawned_process = None
        guard._expected_workload_command = ("trusted-fixture",)
        guard._spawn_factory_result = process
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
        guard.watchdog_spawn_ack_path = paths.spawn_ack
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

        def reader(path: Path):
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            return None

        with mock.patch.object(
            posix, "read_json_if_ready", side_effect=reader
        ), mock.patch.object(
            posix, "atomic_write_json", side_effect=write
        ), mock.patch.object(
            posix, "wait_until", side_effect=wait_for_ack
        ), mock.patch.object(posix.time, "monotonic", return_value=1.0):
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
        state = {
            "now": 0.0,
            "group_killed": False,
            "candidate_acknowledged": False,
        }

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "registered"}
            if path == paths.spawn_observed:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_observed",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                }
            if path == paths.leader:
                return self.leader()
            if path == paths.grandchild and state["now"] >= 0.4:
                return self.grandchild()
            return None

        def writer(path: Path, payload) -> None:
            captured[path] = dict(payload)
            if path == paths.spawn_ack:
                state["candidate_acknowledged"] = True

        def sleeper(seconds: float) -> None:
            state["now"] += seconds

        def getpgid(pid: int) -> int:
            if state["group_killed"]:
                raise ProcessLookupError
            if pid == 41001:
                if state["candidate_acknowledged"]:
                    raise ProcessLookupError
                return 41001
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
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
            if path == paths.spawn_observed:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_observed",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                }
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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        identity_calls = {"count": 0}

        def getpgid(_pid: int) -> int:
            identity_calls["count"] += 1
            if identity_calls["count"] == 1:
                return 41001
            raise ProcessLookupError

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

    def test_spawn_wrapper_publishes_identity_and_waits_for_exact_ack(self) -> None:
        paths = self.paths()
        process = FakeSpawnProcess()
        guard = self.guard_for_spawn(paths, process)
        writes: list[tuple[Path, dict[str, object]]] = []

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            if path == paths.spawn_ack and writes:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_acknowledged",
                    "owner_pid": 42001,
                    "pid": process.pid,
                    "pgid": process.pid,
                    "sid": process.pid,
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            return None

        factory = mock.Mock(return_value=process)
        with mock.patch.object(posix.os, "getpid", return_value=42001), mock.patch.object(
            posix.os, "getpgrp", return_value=42001
        ), mock.patch.object(
            posix.os, "getpgid", return_value=process.pid
        ), mock.patch.object(
            posix.os, "getsid", return_value=process.pid
        ), mock.patch.object(
            posix, "read_json_if_ready", side_effect=reader
        ), mock.patch.object(
            posix,
            "atomic_write_json",
            side_effect=lambda path, payload: writes.append((path, dict(payload))),
        ), mock.patch.object(
            posix, "wait_until", side_effect=lambda predicate, **_kwargs: predicate()
        ), mock.patch.object(posix.time, "monotonic", return_value=1.0):
            wrapped = guard.spawn_observing_popen(factory)
            returned = wrapped(("trusted-fixture",), cwd=str(paths.root))

        self.assertIs(returned, process)
        self.assertIs(guard._spawned_process, process)
        factory.assert_called_once()
        self.assertEqual([path for path, _payload in writes], [paths.spawn_observed])
        self.assertEqual(
            {
                key: writes[0][1][key]
                for key in ("token", "owner_pid", "pid", "pgid", "sid")
            },
            {
                "token": self.TOKEN,
                "owner_pid": 42001,
                "pid": process.pid,
                "pgid": process.pid,
                "sid": process.pid,
            },
        )

    def test_expired_arm_ack_rejects_before_spawn_factory(self) -> None:
        paths = self.paths()
        process = FakeSpawnProcess()
        guard = self.guard_for_spawn(paths, process)
        factory = mock.Mock(return_value=process)

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 5.0,
                }
            if path == paths.spawn_ack:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_acknowledged",
                    "owner_pid": 42001,
                    "pid": process.pid,
                    "pgid": process.pid,
                    "sid": process.pid,
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 5.0,
                }
            return None

        with mock.patch.object(posix.os, "getpid", return_value=42001), mock.patch.object(
            posix.os, "getpgrp", return_value=42001
        ), mock.patch.object(posix.os, "getpgid", return_value=process.pid), mock.patch.object(
            posix.os, "getsid", return_value=process.pid
        ), mock.patch.object(posix.time, "monotonic", return_value=5.0), mock.patch.object(
            posix, "read_json_if_ready", side_effect=reader
        ), mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix, "wait_until", side_effect=lambda predicate, **_kwargs: predicate()
        ):
            wrapped = guard.spawn_observing_popen(factory)
            with self.assertRaisesRegex(RuntimeError, "deadline|expired|fresh"):
                wrapped(("trusted-fixture",))

        factory.assert_not_called()
        self.assertIsNone(guard._spawned_process)

    def test_spawn_ack_expiring_during_wait_reaps_before_return(self) -> None:
        paths = self.paths()
        process = FakeSpawnProcess()
        guard = self.guard_for_spawn(paths, process)
        state = {"now": 4.0}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 5.0,
                }
            if path == paths.spawn_ack:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_acknowledged",
                    "owner_pid": 42001,
                    "pid": process.pid,
                    "pgid": process.pid,
                    "sid": process.pid,
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 5.0,
                }
            return None

        def wait_at_deadline(predicate, **_kwargs) -> bool:
            state["now"] = 5.0
            return predicate()

        with mock.patch.object(posix.os, "getpid", return_value=42001), mock.patch.object(
            posix.os, "getpgrp", return_value=42001
        ), mock.patch.object(posix.os, "getpgid", return_value=process.pid), mock.patch.object(
            posix.os, "getsid", return_value=process.pid
        ), mock.patch.object(
            posix.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(posix, "read_json_if_ready", side_effect=reader), mock.patch.object(
            posix, "atomic_write_json"
        ), mock.patch.object(posix, "wait_until", side_effect=wait_at_deadline):
            wrapped = guard.spawn_observing_popen(mock.Mock(return_value=process))
            with self.assertRaisesRegex(RuntimeError, "spawn acknowledgment"):
                wrapped(("trusted-fixture",))

        self.assertIs(guard._spawned_process, process)
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertIsNotNone(process.poll())

    def test_spawn_ack_timeout_reaps_strong_process_before_raise(self) -> None:
        paths = self.paths()
        process = FakeSpawnProcess()
        guard = self.guard_for_spawn(paths, process)
        writes: list[tuple[Path, dict[str, object]]] = []

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            return None

        with mock.patch.object(posix.os, "getpid", return_value=42001), mock.patch.object(
            posix.os, "getpgrp", return_value=42001
        ), mock.patch.object(
            posix.os, "getpgid", return_value=process.pid
        ), mock.patch.object(
            posix.os, "getsid", return_value=process.pid
        ), mock.patch.object(
            posix, "read_json_if_ready", side_effect=reader
        ), mock.patch.object(
            posix,
            "atomic_write_json",
            side_effect=lambda path, payload: writes.append((path, dict(payload))),
        ), mock.patch.object(posix, "wait_until", return_value=False), mock.patch.object(
            posix.time, "monotonic", return_value=1.0
        ):
            wrapped = guard.spawn_observing_popen(mock.Mock(return_value=process))
            with self.assertRaisesRegex(RuntimeError, "spawn acknowledgment"):
                wrapped(("trusted-fixture",))

        self.assertIs(guard._spawned_process, process)
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.wait_calls)
        self.assertEqual([path for path, _payload in writes], [paths.spawn_observed])
        self.assertNotIn(
            "disarmed_no_spawn", {payload.get("state") for _path, payload in writes}
        )

    def test_spawn_observation_never_overwrites_registered_launch_manifest(self) -> None:
        paths = self.paths()
        process = FakeSpawnProcess()
        guard = self.guard_for_spawn(paths, process)
        launch_state = {"value": "armed"}
        writes: list[tuple[Path, dict[str, object]]] = []

        def write(path: Path, payload) -> None:
            writes.append((path, dict(payload)))
            if path == paths.spawn_observed:
                launch_state["value"] = "registered"

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": launch_state["value"]}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            if path == paths.spawn_ack:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_acknowledged",
                    "owner_pid": 42001,
                    "pid": process.pid,
                    "pgid": process.pid,
                    "sid": process.pid,
                    "watchdog_pid": 40001,
                    "deadline_monotonic": 10.0,
                }
            return None

        with mock.patch.object(posix.os, "getpid", return_value=42001), mock.patch.object(
            posix.os, "getpgrp", return_value=42001
        ), mock.patch.object(posix.os, "getpgid", return_value=process.pid), mock.patch.object(
            posix.os, "getsid", return_value=process.pid
        ), mock.patch.object(posix, "read_json_if_ready", side_effect=reader), mock.patch.object(
            posix, "atomic_write_json", side_effect=write
        ), mock.patch.object(
            posix, "wait_until", side_effect=lambda predicate, **_kwargs: predicate()
        ), mock.patch.object(posix.time, "monotonic", return_value=1.0):
            guard.spawn_observing_popen(mock.Mock(return_value=process))(
                ("trusted-fixture",)
            )

        self.assertEqual(launch_state["value"], "registered")
        self.assertNotIn(paths.launch, [path for path, _payload in writes])

    def test_disarm_rejects_existing_spawn_observation(self) -> None:
        paths = self.paths()
        guard = self.guard_for_spawn(paths, FakeSpawnProcess())

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed"}
            if path == paths.spawn_observed:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_observed",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                }
            return None

        with mock.patch.object(posix, "read_json_if_ready", side_effect=reader), mock.patch.object(
            posix, "atomic_write_json"
        ) as write:
            with self.assertRaisesRegex(RuntimeError, "spawn observation"):
                guard.disarm_no_spawn()

        write.assert_not_called()

    def test_disarm_rejects_any_returned_spawn_factory_handle(self) -> None:
        paths = self.paths()
        guard = self.guard_for_spawn(paths, FakeSpawnProcess())
        guard._spawned_process = FakeSpawnProcess()

        with mock.patch.object(
            posix,
            "read_json_if_ready",
            side_effect=lambda path: (
                {"token": self.TOKEN, "state": "armed"}
                if path == paths.launch
                else None
            ),
        ), mock.patch.object(posix, "atomic_write_json") as write:
            with self.assertRaisesRegex(RuntimeError, "spawn factory"):
                guard.disarm_no_spawn()

        write.assert_not_called()

    def test_watchdog_candidate_cleans_without_leader_manifest(self) -> None:
        observation = {
            "token": self.TOKEN,
            "state": "spawn_observed",
            "owner_pid": 42001,
            "pid": 41001,
            "pgid": 41001,
            "sid": 41001,
        }
        with mock.patch.object(fixture.os, "getpgid", return_value=41001), mock.patch.object(
            fixture.os, "getsid", return_value=41001
        ):
            candidate, candidate_errors = fixture._validated_spawn_candidate(
                observation,
                token=self.TOKEN,
                owner_pid=42001,
                forbidden_pgids=(42001, 43001),
            )
        pgid, pids, target_errors = fixture._validated_candidate_target(
            candidate,
            None,
            None,
            token=self.TOKEN,
            forbidden_pgids=(42001, 43001),
        )

        self.assertEqual(candidate_errors, [])
        self.assertEqual(target_errors, [])
        self.assertEqual((pgid, pids), (41001, (41001,)))

    def test_watchdog_uses_candidate_to_cleanup_before_leader_manifest(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0, "killed": False}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "registered"}
            if path == paths.spawn_observed:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_observed",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                }
            return None

        def getpgid(_pid: int) -> int:
            if state["killed"]:
                raise ProcessLookupError
            return 41001

        def killpg(pgid: int, signum: int) -> None:
            self.assertEqual((pgid, signum), (41001, signal.SIGTERM))
            state["killed"] = True

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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time,
            "sleep",
            side_effect=lambda seconds: state.__setitem__("now", state["now"] + seconds),
        ), mock.patch.object(fixture, "_read_json", side_effect=reader), mock.patch.object(
            fixture,
            "_atomic_write_json",
            side_effect=lambda path, payload: captured.__setitem__(path, dict(payload)),
        ), mock.patch.object(fixture, "_pid_exists", return_value=False), mock.patch.object(
            fixture.os, "getpgid", side_effect=getpgid
        ), mock.patch.object(fixture.os, "getsid", return_value=41001), mock.patch.object(
            fixture.os, "killpg", side_effect=killpg
        ) as group_signal:
            exit_code = fixture._watchdog(args)

        self.assertEqual(exit_code, 70)
        group_signal.assert_called_once_with(41001, signal.SIGTERM)
        self.assertIn(paths.spawn_ack, captured)
        self.assertTrue(captured[paths.result]["target_group_gone"])
        self.assertEqual(captured[paths.result]["target_pids"], [41001])

    def test_watchdog_late_observation_cleans_candidate_without_ack(self) -> None:
        paths = self.paths()
        captured: dict[Path, dict[str, object]] = {}
        state = {"now": 0.0, "killed": False}

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "registered"}
            if path == paths.spawn_observed and state["now"] >= 1.0:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_observed",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                }
            return None

        def getpgid(_pid: int) -> int:
            if state["killed"]:
                raise ProcessLookupError
            return 41001

        def killpg(pgid: int, signum: int) -> None:
            self.assertEqual((pgid, signum), (41001, signal.SIGTERM))
            state["killed"] = True

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
            spawn_observed=paths.spawn_observed,
            spawn_ack=paths.spawn_ack,
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
        )
        with mock.patch.object(fixture.os, "getpid", return_value=43001), mock.patch.object(
            fixture.os, "getpgrp", return_value=43001
        ), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(
            fixture.time, "sleep", side_effect=lambda _seconds: state.__setitem__("now", 1.0)
        ), mock.patch.object(fixture, "_read_json", side_effect=reader), mock.patch.object(
            fixture,
            "_atomic_write_json",
            side_effect=lambda path, payload: captured.__setitem__(path, dict(payload)),
        ), mock.patch.object(fixture, "_pid_exists", return_value=True), mock.patch.object(
            fixture.os, "getpgid", side_effect=getpgid
        ), mock.patch.object(fixture.os, "getsid", return_value=41001), mock.patch.object(
            fixture.os, "killpg", side_effect=killpg
        ) as group_signal:
            exit_code = fixture._watchdog(args)

        self.assertEqual(exit_code, 70)
        self.assertNotIn(paths.spawn_ack, captured)
        group_signal.assert_called_once_with(41001, signal.SIGTERM)
        self.assertEqual(captured[paths.result]["target_pids"], [41001])
        self.assertTrue(captured[paths.result]["target_group_gone"])

    def test_watchdog_observation_identity_mismatch_never_reaches_killpg(self) -> None:
        observation = {
            "token": self.TOKEN,
            "state": "spawn_observed",
            "owner_pid": 42001,
            "pid": 41001,
            "pgid": 41001,
            "sid": 41001,
        }
        with mock.patch.object(fixture.os, "getpgid", return_value=51001), mock.patch.object(
            fixture.os, "getsid", return_value=41001
        ), mock.patch.object(fixture.os, "killpg") as killpg:
            candidate, errors = fixture._validated_spawn_candidate(
                observation,
                token=self.TOKEN,
                owner_pid=42001,
                forbidden_pgids=(42001, 43001),
            )
            pgid, pids, target_errors = fixture._validated_candidate_target(
                candidate,
                self.leader(),
                self.grandchild(),
                token=self.TOKEN,
                forbidden_pgids=(42001, 43001),
            )
            fixture._kill_target_group(pgid, pids)

        self.assertIsNone(candidate)
        self.assertTrue(any("identity" in item for item in errors))
        self.assertTrue(target_errors)
        killpg.assert_not_called()

    def test_watchdog_leader_must_exactly_match_observed_candidate(self) -> None:
        candidate = {
            "token": self.TOKEN,
            "owner_pid": 42001,
            "pid": 41001,
            "pgid": 41001,
            "sid": 41001,
        }
        exact = fixture._validated_candidate_target(
            candidate,
            self.leader(),
            self.grandchild(),
            token=self.TOKEN,
            forbidden_pgids=(42001, 43001),
        )
        mismatch = fixture._validated_candidate_target(
            candidate,
            self.leader(41003),
            None,
            token=self.TOKEN,
            forbidden_pgids=(42001, 43001),
        )

        self.assertEqual(exact, (41001, (41001, 41002), []))
        self.assertIsNone(mismatch[0])
        self.assertEqual(mismatch[1], ())
        self.assertTrue(any("candidate" in item for item in mismatch[2]))

    def test_fixture_spawn_ack_gate_precedes_grandchild_spawn(self) -> None:
        paths = self.paths()
        writes: list[tuple[Path, dict[str, object]]] = []
        args = SimpleNamespace(
            token=self.TOKEN,
            mode="success_orphan",
            port=0,
            output_chars=0,
            tick_seconds=0.05,
            fake_secret="fake",
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
            launch_armed_manifest=paths.launch,
            arm_ack_manifest=paths.arm_ack,
            spawn_ack_manifest=paths.spawn_ack,
            marker=paths.root / "marker.log",
        )

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed", "owner_pid": 42001}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 43001,
                    "deadline_monotonic": 10.0,
                }
            return None

        with mock.patch.object(fixture.os, "getpid", return_value=41001), mock.patch.object(
            fixture.os, "getpgrp", return_value=41001
        ), mock.patch.object(fixture.os, "getsid", return_value=41001), mock.patch.object(
            fixture.time, "monotonic", return_value=1.0
        ), mock.patch.object(fixture, "_read_json", side_effect=reader), mock.patch.object(
            fixture,
            "_atomic_write_json",
            side_effect=lambda path, payload: writes.append((path, dict(payload))),
        ), mock.patch.object(fixture, "_wait_until", return_value=False), mock.patch.object(
            fixture.os, "pipe", side_effect=AssertionError("grandchild reached before ACK")
        ) as pipe, mock.patch.object(fixture.subprocess, "Popen") as popen:
            exit_code = fixture._workload(args)

        self.assertEqual(exit_code, 61)
        pipe.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(writes[0][0], paths.leader)
        self.assertNotIn(paths.launch, [path for path, _payload in writes])

    def test_fixture_equal_deadline_ack_never_reaches_mode_side_effects(self) -> None:
        paths = self.paths()
        writes: list[tuple[Path, dict[str, object]]] = []
        state = {"now": 1.0}
        args = SimpleNamespace(
            token=self.TOKEN,
            mode="success_orphan",
            port=0,
            output_chars=0,
            tick_seconds=0.05,
            fake_secret="fake",
            leader_manifest=paths.leader,
            grandchild_manifest=paths.grandchild,
            launch_armed_manifest=paths.launch,
            arm_ack_manifest=paths.arm_ack,
            spawn_ack_manifest=paths.spawn_ack,
            marker=paths.root / "marker.log",
        )

        def reader(path: Path):
            if path == paths.launch:
                return {"token": self.TOKEN, "state": "armed", "owner_pid": 42001}
            if path == paths.arm_ack:
                return {
                    "token": self.TOKEN,
                    "state": "armed_acknowledged",
                    "watchdog_pid": 43001,
                    "deadline_monotonic": 10.0,
                }
            if path == paths.spawn_ack:
                return {
                    "token": self.TOKEN,
                    "state": "spawn_acknowledged",
                    "owner_pid": 42001,
                    "pid": 41001,
                    "pgid": 41001,
                    "sid": 41001,
                    "watchdog_pid": 43001,
                    "deadline_monotonic": 10.0,
                }
            return None

        def wait_at_deadline(predicate, _timeout_seconds) -> bool:
            state["now"] = 10.0
            return predicate()

        with mock.patch.object(fixture.os, "getpid", return_value=41001), mock.patch.object(
            fixture.os, "getpgrp", return_value=41001
        ), mock.patch.object(fixture.os, "getsid", return_value=41001), mock.patch.object(
            fixture.time, "monotonic", side_effect=lambda: state["now"]
        ), mock.patch.object(fixture, "_read_json", side_effect=reader), mock.patch.object(
            fixture,
            "_atomic_write_json",
            side_effect=lambda path, payload: writes.append((path, dict(payload))),
        ), mock.patch.object(fixture, "_wait_until", side_effect=wait_at_deadline), mock.patch.object(
            Path, "mkdir", side_effect=AssertionError("mode side effect reached")
        ) as mkdir, mock.patch.object(
            fixture.os, "pipe", side_effect=AssertionError("pipe reached")
        ) as pipe, mock.patch.object(fixture.subprocess, "Popen") as popen:
            exit_code = fixture._workload(args)

        self.assertEqual(exit_code, 61)
        mkdir.assert_not_called()
        pipe.assert_not_called()
        popen.assert_not_called()
        self.assertNotIn(paths.launch, [path for path, _payload in writes])

    def test_double_join_failure_cannot_escape_while_watchdog_is_live(self) -> None:
        paths = self.paths()
        watchdog = ControlledWatchdog()
        guard = self.guard_for_close(paths, watchdog)
        emergency_attempted = threading.Event()
        outcome: list[BaseException] = []

        def stop() -> None:
            emergency_attempted.set()
            raise RuntimeError("emergency stop failed")

        def run_close() -> None:
            try:
                guard.close()
            except BaseException as exc:
                outcome.append(exc)

        guard._stop_watchdog_only = mock.Mock(side_effect=stop)
        with mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix,
            "read_json_if_ready",
            side_effect=lambda path: (
                {"token": self.TOKEN, "state": "disarmed_no_spawn"}
                if path == paths.launch
                else None
            ),
        ):
            closer = threading.Thread(target=run_close, daemon=True)
            closer.start()
            self.assertTrue(emergency_attempted.wait(1.0))
            threading.Event().wait(0.05)
            escaped_while_live = not closer.is_alive()
            watchdog.returncode = 70
            closer.join(1.0)

        self.assertFalse(escaped_while_live)
        self.assertFalse(closer.is_alive())
        self.assertTrue(outcome)
        wait_count = len(watchdog.wait_calls)
        with self.assertRaises(AssertionError):
            guard.close()
        self.assertEqual(len(watchdog.wait_calls), wait_count)

    def test_keyboard_interrupt_is_deferred_until_watchdog_terminal(self) -> None:
        paths = self.paths()

        class InterruptingWatchdog(ControlledWatchdog):
            def __init__(self) -> None:
                super().__init__()
                self.interrupted = False

            def wait(self, timeout=None):
                self.wait_calls.append(timeout)
                if not self.interrupted:
                    self.interrupted = True
                    raise KeyboardInterrupt("defer me")
                return super().wait(timeout=timeout)

        watchdog = InterruptingWatchdog()
        guard = self.guard_for_close(paths, watchdog)
        emergency_attempted = threading.Event()
        outcome: list[BaseException] = []

        def stop() -> None:
            emergency_attempted.set()
            raise RuntimeError("emergency stop failed")

        def run_close() -> None:
            try:
                guard.close()
            except BaseException as exc:
                outcome.append(exc)

        guard._stop_watchdog_only = mock.Mock(side_effect=stop)
        with mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix,
            "read_json_if_ready",
            side_effect=lambda path: (
                {"token": self.TOKEN, "state": "disarmed_no_spawn"}
                if path == paths.launch
                else None
            ),
        ):
            closer = threading.Thread(target=run_close, daemon=True)
            closer.start()
            self.assertTrue(emergency_attempted.wait(1.0))
            threading.Event().wait(0.05)
            escaped_while_live = not closer.is_alive()
            watchdog.returncode = 0
            closer.join(1.0)

        self.assertFalse(escaped_while_live)
        self.assertFalse(closer.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], KeyboardInterrupt)

    def test_watchdog_identity_drift_never_signals_and_waits_for_terminal(self) -> None:
        paths = self.paths()
        watchdog = ControlledWatchdog()
        guard = self.guard_for_close(paths, watchdog)
        guard._stop_watchdog_only = posix.ExternalProcessGuard._stop_watchdog_only.__get__(
            guard, posix.ExternalProcessGuard
        )
        first_wait = threading.Event()
        original_wait = watchdog.wait

        def wait(timeout=None):
            first_wait.set()
            return original_wait(timeout=timeout)

        watchdog.wait = wait
        errors: list[str] = []
        observations: list[str] = []

        with mock.patch.object(posix.os, "getpgid", return_value=57001), mock.patch.object(
            posix.os, "getsid", return_value=57001
        ), mock.patch.object(posix.os, "getpgrp", return_value=42001), mock.patch.object(
            posix.os, "killpg"
        ) as killpg:
            joiner = threading.Thread(
                target=guard._join_watchdog_before_return,
                args=(errors, observations),
                daemon=True,
            )
            joiner.start()
            self.assertTrue(first_wait.wait(1.0))
            threading.Event().wait(0.05)
            self.assertTrue(joiner.is_alive())
            watchdog.returncode = 70
            joiner.join(1.0)

        self.assertFalse(joiner.is_alive())
        killpg.assert_not_called()
        self.assertTrue(any("safely stop" in item for item in errors))

    def test_guard_reads_target_manifests_only_after_watchdog_terminal(self) -> None:
        paths = self.paths()
        watchdog = ControlledWatchdog()
        guard = self.guard_for_close(paths, watchdog)
        emergency_attempted = threading.Event()
        manifest_poll_states: list[object] = []
        outcome: list[BaseException] = []

        def stop() -> None:
            emergency_attempted.set()
            raise RuntimeError("emergency stop failed")

        def payload():
            manifest_poll_states.append(watchdog.poll())
            return None

        def run_close() -> None:
            try:
                guard.close()
            except BaseException as exc:
                outcome.append(exc)

        guard._stop_watchdog_only = mock.Mock(side_effect=stop)
        guard.leader_payload = payload
        guard.grandchild_payload = payload
        with mock.patch.object(posix, "atomic_write_json"), mock.patch.object(
            posix,
            "read_json_if_ready",
            side_effect=lambda path: (
                {"token": self.TOKEN, "state": "disarmed_no_spawn"}
                if path == paths.launch
                else None
            ),
        ):
            closer = threading.Thread(target=run_close, daemon=True)
            closer.start()
            self.assertTrue(emergency_attempted.wait(1.0))
            threading.Event().wait(0.05)
            self.assertEqual(manifest_poll_states, [])
            watchdog.returncode = 70
            closer.join(1.0)

        self.assertFalse(closer.is_alive())
        self.assertTrue(manifest_poll_states)
        self.assertTrue(all(state is not None for state in manifest_poll_states))
        self.assertTrue(outcome)

    def paths(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        return SimpleNamespace(
            root=root,
            launch=root / "launch.json",
            arm_ack=root / "arm-ack.json",
            spawn_observed=root / "spawn-observed.json",
            spawn_ack=root / "spawn-ack.json",
            leader=root / "leader.json",
            grandchild=root / "grandchild.json",
            control=root / "control.json",
            ready=root / "ready.json",
            result=root / "result.json",
        )


if __name__ == "__main__":
    unittest.main()
