from __future__ import annotations

import errno
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Tuple
from uuid import uuid4


LOOPBACK_HOST = "127.0.0.1"
FROZEN_TEST_PATH = (
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
)
FIXTURE_SCRIPT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "local_execution_process.py"
)
WORKLOAD_MODES = frozenset({
    "success_orphan",
    "nonzero_orphan",
    "hang_ignore_term",
    "hang_leader_exits_on_term",
    "server_ready",
    "server_never_ready",
    "exit_before_ready",
    "stdout_short",
    "stdout_long",
})
WATCHDOG_NORMAL_COMPLETION_BUFFER_SECONDS = 2.0
# Normal completion (2s), emergency watchdog stop (2s), and final parent-side
# cleanup (at most 3.25s) fit inside this post-hard-deadline close budget.
WATCHDOG_CLOSE_BUFFER_SECONDS = 8.0


def _positive_number(value: object, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    parsed = float(value)
    minimum = 0.0 if allow_zero else 0.0
    if parsed < minimum or (not allow_zero and parsed == 0):
        raise ValueError(f"{name} must be positive")
    return parsed


def _positive_pid(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 1:
        raise ValueError(f"{name} must be an integer greater than one")
    return value


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.02,
) -> bool:
    """Poll a side-effect-free predicate against one monotonic deadline."""

    timeout = _positive_number(timeout_seconds, "timeout_seconds", allow_zero=True)
    interval = _positive_number(
        poll_interval_seconds, "poll_interval_seconds"
    )
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


def pid_exists(pid: int) -> bool:
    pid = _positive_pid(pid, "pid")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise


def pgid_exists(pgid: int, *, registered_pids: Sequence[int]) -> bool:
    """Return whether a recorded member still belongs to the recorded PGID.

    This intentionally does not use ``killpg(pgid, 0)``: a numeric PGID can be
    reused after every owned PID disappears.
    """

    pgid = _positive_pid(pgid, "pgid")
    if not registered_pids:
        raise ValueError("registered_pids cannot be empty")
    for pid_value in registered_pids:
        pid = _positive_pid(pid_value, "registered pid")
        try:
            actual_pgid = os.getpgid(pid)
        except ProcessLookupError:
            continue
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                continue
            raise
        try:
            actual_sid = os.getsid(pid)
        except ProcessLookupError:
            continue
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                continue
            raise
        if actual_pgid == pgid and actual_sid == pgid:
            return True
    return False


def wait_pid_gone(
    pid: int,
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
) -> bool:
    return wait_until(
        lambda: not pid_exists(pid),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def wait_pgid_gone(
    pgid: int,
    *,
    registered_pids: Sequence[int],
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
) -> bool:
    return wait_until(
        lambda: not pgid_exists(pgid, registered_pids=registered_pids),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def port_is_open(port: int, *, timeout_seconds: float = 0.05) -> bool:
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("port must be between 1 and 65535")
    timeout = _positive_number(timeout_seconds, "timeout_seconds")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        return connection.connect_ex((LOOPBACK_HOST, port)) == 0


def wait_port_open(
    port: int,
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
) -> bool:
    return wait_until(
        lambda: port_is_open(port),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def wait_port_closed(
    port: int,
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
) -> bool:
    return wait_until(
        lambda: not port_is_open(port),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    try:
        temporary.write_text(encoded + "\n", encoding="utf-8")
        os.chmod(str(temporary), 0o600)
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json_if_ready(path: Path) -> Optional[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


@dataclass(frozen=True)
class MarkerSnapshot:
    exists: bool
    size: int = 0
    mtime_ns: int = 0


def marker_snapshot(path: Path) -> MarkerSnapshot:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return MarkerSnapshot(False)
    return MarkerSnapshot(True, stat.st_size, stat.st_mtime_ns)


def wait_marker_stable(
    path: Path,
    *,
    quiet_seconds: float = 0.15,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
) -> bool:
    quiet = _positive_number(quiet_seconds, "quiet_seconds")
    timeout = _positive_number(timeout_seconds, "timeout_seconds")
    interval = _positive_number(
        poll_interval_seconds, "poll_interval_seconds"
    )
    deadline = time.monotonic() + timeout
    current = marker_snapshot(path)
    unchanged_since = time.monotonic()
    while True:
        now = time.monotonic()
        observed = marker_snapshot(path)
        if observed != current:
            current = observed
            unchanged_since = now
        if now - unchanged_since >= quiet:
            return True
        remaining = deadline - now
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


@dataclass(frozen=True)
class OwnedProcessGroup:
    leader_pid: int
    pgid: int
    descendant_pids: Tuple[int, ...] = ()
    port: int = 0

    @property
    def pids(self) -> Tuple[int, ...]:
        return (self.leader_pid, *self.descendant_pids)


@dataclass(frozen=True)
class GuardCleanupResult:
    watchdog_exit_code: Optional[int]
    target_group: Optional[OwnedProcessGroup]
    target_group_gone: bool
    target_pids_gone: bool
    watchdog_payload: Mapping[str, object]
    observations: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return (
            self.watchdog_exit_code == 0
            and self.target_group_gone
            and self.target_pids_gone
            and not self.errors
        )


def _manifest_int(
    payload: Mapping[str, object], name: str, *, allow_zero: bool = False
) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"manifest {name} must be an integer")
    if allow_zero:
        if value < 0:
            raise ValueError(f"manifest {name} cannot be negative")
    elif value <= 1:
        raise ValueError(f"manifest {name} must be greater than one")
    return value


def _validated_owned_group(
    leader: Optional[Mapping[str, object]],
    grandchild: Optional[Mapping[str, object]],
    *,
    token: str,
    forbidden_pgids: Sequence[int],
) -> tuple[Optional[OwnedProcessGroup], list[str]]:
    if leader is None:
        if grandchild is None:
            return None, []
        return None, ["grandchild manifest exists without a leader manifest"]

    errors: list[str] = []
    try:
        if leader.get("token") != token or leader.get("role") != "leader":
            raise ValueError("leader manifest identity mismatch")
        leader_pid = _manifest_int(leader, "pid")
        pgid = _manifest_int(leader, "pgid")
        sid = _manifest_int(leader, "sid")
        if leader_pid != pgid:
            raise ValueError("leader must be its own process-group leader")
        if sid != pgid:
            raise ValueError("leader session ID must match its process group")
        if pgid in set(forbidden_pgids):
            raise ValueError("target PGID overlaps a protected process group")
        port = _manifest_int(leader, "port", allow_zero=True)
    except ValueError as exc:
        return None, [str(exc)]

    descendants: list[int] = []
    if grandchild is not None:
        try:
            if (
                grandchild.get("token") != token
                or grandchild.get("role") != "grandchild"
            ):
                raise ValueError("grandchild manifest identity mismatch")
            child_pid = _manifest_int(grandchild, "pid")
            child_pgid = _manifest_int(grandchild, "pgid")
            child_sid = _manifest_int(grandchild, "sid")
            child_leader = _manifest_int(grandchild, "leader_pid")
            if child_pgid != pgid or child_leader != leader_pid:
                raise ValueError("grandchild escaped its recorded process group")
            if child_sid != pgid:
                raise ValueError("grandchild escaped the recorded session")
            if child_pid == leader_pid:
                raise ValueError("grandchild PID collides with leader PID")
            descendants.append(child_pid)
            child_port = _manifest_int(grandchild, "port", allow_zero=True)
            if child_port:
                port = child_port
        except ValueError as exc:
            errors.append(str(exc))

    return OwnedProcessGroup(leader_pid, pgid, tuple(descendants), port), errors


def _target_registration_unresolved(
    launch_armed: Optional[Mapping[str, object]],
    leader: Optional[Mapping[str, object]],
    grandchild: Optional[Mapping[str, object]],
    *,
    token: str,
) -> tuple[bool, list[str]]:
    """Return whether an armed fixture launch still has unregistered members."""

    if launch_armed is None:
        if leader is None and grandchild is None:
            return False, []
        return True, [
            "process manifests exist without a launch-armed manifest"
        ]

    if launch_armed.get("token") != token:
        return True, ["launch-armed manifest identity mismatch"]
    state = launch_armed.get("state")
    if state == "disarmed_no_spawn":
        if leader is None and grandchild is None:
            return False, []
        return True, ["process manifest exists after explicit disarm_no_spawn"]
    if state not in {"armed", "registered"}:
        return True, [f"launch-armed manifest has invalid state: {state}"]

    missing: list[str] = []
    if leader is None:
        missing.append("leader")
    if grandchild is None:
        missing.append("grandchild")
    if not missing:
        return False, []
    return True, [
        "target registration unresolved for launch state "
        f"{state}: missing {', '.join(missing)}"
    ]


@dataclass(frozen=True)
class _RegisteredMembership:
    status: str
    matching_pids: Tuple[int, ...] = ()
    missing_pids: Tuple[int, ...] = ()
    mismatched_pids: Tuple[Tuple[int, int, int], ...] = ()


def _registered_membership(group: OwnedProcessGroup) -> _RegisteredMembership:
    matching: list[int] = []
    missing: list[int] = []
    mismatched: list[tuple[int, int, int]] = []
    for pid in group.pids:
        try:
            actual_pgid = os.getpgid(pid)
        except ProcessLookupError:
            missing.append(pid)
            continue
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                missing.append(pid)
                continue
            return _RegisteredMembership("membership_unknown")
        try:
            actual_sid = os.getsid(pid)
        except ProcessLookupError:
            missing.append(pid)
            continue
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                missing.append(pid)
                continue
            return _RegisteredMembership("membership_unknown")
        if actual_pgid == group.pgid and actual_sid == group.pgid:
            matching.append(pid)
        else:
            mismatched.append((pid, actual_pgid, actual_sid))
    if matching:
        return _RegisteredMembership(
            "owned_member_present",
            tuple(matching),
            tuple(missing),
            tuple(mismatched),
        )
    if mismatched:
        status = (
            "sid_drift_or_reuse"
            if all(
                actual_pgid == group.pgid and actual_sid != group.pgid
                for _pid, actual_pgid, actual_sid in mismatched
            )
            else "pgid_may_be_reused"
        )
        return _RegisteredMembership(
            status, (), tuple(missing), tuple(mismatched)
        )
    return _RegisteredMembership("target_members_disappeared", (), tuple(missing))


def _send_group_signal(
    group: OwnedProcessGroup,
    signum: int,
    errors: list[str],
    observations: list[str],
) -> bool:
    membership = _registered_membership(group)
    if membership.status == "target_members_disappeared":
        observations.append(
            "target_members_disappeared: refused unnecessary group signal "
            f"for PGID {group.pgid}"
        )
        return False
    if membership.status == "pgid_may_be_reused":
        errors.append(
            "pgid_may_be_reused: refused group signal for PGID "
            f"{group.pgid}; mismatched={membership.mismatched_pids}"
        )
        return False
    if membership.status == "sid_drift_or_reuse":
        errors.append(
            "sid_drift_or_reuse: refused group signal for PGID "
            f"{group.pgid}; mismatched={membership.mismatched_pids}"
        )
        return False
    if membership.status != "owned_member_present":
        errors.append(
            "membership_unknown: refused group signal for PGID "
            f"{group.pgid}"
        )
        return False
    try:
        os.killpg(group.pgid, signum)
        return True
    except ProcessLookupError:
        observations.append(
            f"target_members_disappeared during killpg({group.pgid}, {signum})"
        )
        return False
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            errors.append(f"killpg({group.pgid}, {signum}) failed: {exc}")
        return False


def _cleanup_owned_group(
    group: Optional[OwnedProcessGroup],
    *,
    term_wait_seconds: float = 0.25,
    kill_wait_seconds: float = 1.5,
) -> tuple[bool, bool, list[str], list[str]]:
    if group is None:
        return True, True, [], []

    errors: list[str] = []
    observations: list[str] = []
    membership = _registered_membership(group)
    if membership.status == "owned_member_present":
        _send_group_signal(group, signal.SIGTERM, errors, observations)
        wait_until(
            lambda: _registered_membership(group).status
            != "owned_member_present",
            timeout_seconds=term_wait_seconds,
            poll_interval_seconds=0.02,
        )
    membership = _registered_membership(group)
    if membership.status == "owned_member_present":
        _send_group_signal(group, signal.SIGKILL, errors, observations)
        wait_until(
            lambda: _registered_membership(group).status
            != "owned_member_present",
            timeout_seconds=kill_wait_seconds,
            poll_interval_seconds=0.02,
        )

    final_membership = _registered_membership(group)
    group_gone = final_membership.status == "target_members_disappeared"
    pids_gone = group_gone
    if final_membership.status == "pgid_may_be_reused":
        errors.append(
            "pgid_may_be_reused: cleanup cannot prove ownership after PID/PGID drift"
        )
    elif final_membership.status == "sid_drift_or_reuse":
        errors.append(
            "sid_drift_or_reuse: cleanup cannot prove target session ownership"
        )
    elif final_membership.status == "membership_unknown":
        errors.append("membership_unknown: cleanup cannot prove target disappearance")
    elif final_membership.status == "target_members_disappeared":
        observations.append(
            f"target_members_disappeared: PGID {group.pgid} was not signalled again"
        )
    else:
        wait_until(
            lambda: _registered_membership(group).status
            != "owned_member_present",
            timeout_seconds=kill_wait_seconds,
            poll_interval_seconds=0.02,
        )
        final_membership = _registered_membership(group)
        group_gone = final_membership.status == "target_members_disappeared"
        pids_gone = group_gone
    if not group_gone:
        errors.append(f"target PGID {group.pgid} ownership remains unresolved")
    if not pids_gone:
        errors.append(f"target PIDs remain unresolved: {group.pids}")
    return group_gone, pids_gone, errors, observations


def _minimal_environment(private_root: Path) -> dict[str, str]:
    home = private_root / "home"
    temporary = private_root / "tmp"
    for directory in (home, temporary):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(directory), 0o700)
    return {
        "PATH": FROZEN_TEST_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


class ExternalProcessGuard:
    """Test-owned process guard with an out-of-group hard-deadline watchdog.

    Cleanup ordering is part of this API's safety contract.  The guard must be
    the *inner* context when its root belongs to ``TemporaryDirectory``::

        with TemporaryDirectory() as temporary:
            with ExternalProcessGuard(Path(temporary)) as guard:
                ...

    With ``unittest.TestCase.addCleanup``, register the temporary-directory
    cleanup first and ``guard.close`` immediately after guard construction so
    LIFO cleanup closes the guard first.  Never let ``TemporaryDirectory`` exit
    before ``guard.close``.  ``close`` is idempotent, joins the watchdog before
    returning or raising, and refuses to signal an unverified process group.
    """

    def __init__(
        self,
        root: Path,
        *,
        token: Optional[str] = None,
        hard_deadline_seconds: float = 8.0,
        watchdog_ready_timeout_seconds: float = 2.0,
        registration_wait_seconds: float = 0.75,
        python_executable: Optional[Path] = None,
    ) -> None:
        hard_deadline = _positive_number(
            hard_deadline_seconds, "hard_deadline_seconds"
        )
        ready_timeout = _positive_number(
            watchdog_ready_timeout_seconds,
            "watchdog_ready_timeout_seconds",
        )
        registration_wait = _positive_number(
            registration_wait_seconds, "registration_wait_seconds"
        )
        if hard_deadline <= ready_timeout:
            raise ValueError("watchdog hard deadline must exceed its ready timeout")
        if hard_deadline > 60:
            raise ValueError("watchdog hard deadline cannot exceed 60 seconds")
        if registration_wait >= hard_deadline:
            raise ValueError(
                "registration wait must be shorter than the watchdog hard deadline"
            )
        self.token = token or uuid4().hex
        if not self.token or len(self.token) > 128 or not all(
            character.isalnum() or character in {"-", "_"}
            for character in self.token
        ):
            raise ValueError("guard token contains unsupported characters")
        self.root = root.resolve()
        self.guard_root = self.root / f"process-guard-{self.token}"
        self.guard_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(str(self.guard_root), 0o700)
        guard_root_stat = self.guard_root.stat()
        self._guard_root_identity = (
            guard_root_stat.st_dev,
            guard_root_stat.st_ino,
        )
        self._hard_deadline_seconds = hard_deadline
        self._arm_ack_timeout_seconds = ready_timeout
        self._spawn_ack_timeout_seconds = ready_timeout
        self._state_lock = threading.RLock()
        self.leader_manifest = self.guard_root / "leader.json"
        self.grandchild_manifest = self.guard_root / "grandchild.json"
        self.launch_armed_manifest = self.guard_root / "launch-armed.json"
        self.watchdog_arm_ack_path = self.guard_root / "watchdog-arm-ack.json"
        self.spawn_observed_path = self.guard_root / "spawn-observed.json"
        self.watchdog_spawn_ack_path = self.guard_root / "watchdog-spawn-ack.json"
        self.marker_path = self.guard_root / "marker.log"
        self.control_path = self.guard_root / "cleanup.json"
        self.watchdog_ready_path = self.guard_root / "watchdog-ready.json"
        self.watchdog_result_path = self.guard_root / "watchdog-result.json"
        self._closed = False
        self._close_in_progress = False
        self._cleanup_result: Optional[GuardCleanupResult] = None
        self._deferred_close_error: Optional[BaseException] = None
        self._spawned_process: Optional[object] = None
        self._expected_workload_command: Optional[tuple[str, ...]] = None
        self._owner_pid = os.getpid()
        self._owner_pgid = os.getpgrp()
        executable = (python_executable or Path(sys.executable)).resolve()
        if not executable.is_file() or not FIXTURE_SCRIPT.is_file():
            raise ValueError("Python executable or POSIX fixture script is missing")
        command = (
            str(executable),
            str(FIXTURE_SCRIPT),
            "watchdog",
            "--token",
            self.token,
            "--leader-manifest",
            str(self.leader_manifest),
            "--grandchild-manifest",
            str(self.grandchild_manifest),
            "--launch-armed-manifest",
            str(self.launch_armed_manifest),
            "--arm-ack",
            str(self.watchdog_arm_ack_path),
            "--spawn-observed",
            str(self.spawn_observed_path),
            "--spawn-ack",
            str(self.watchdog_spawn_ack_path),
            "--control",
            str(self.control_path),
            "--ready",
            str(self.watchdog_ready_path),
            "--result",
            str(self.watchdog_result_path),
            "--owner-pid",
            str(self._owner_pid),
            "--owner-pgid",
            str(self._owner_pgid),
            "--hard-deadline-seconds",
            str(hard_deadline),
            "--registration-wait-seconds",
            str(registration_wait),
        )
        self._watchdog = subprocess.Popen(
            command,
            cwd=str(self.guard_root),
            env=_minimal_environment(self.guard_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            start_new_session=True,
            umask=0o077,
        )
        ready = wait_until(
            self._watchdog_is_ready,
            timeout_seconds=ready_timeout,
            poll_interval_seconds=0.02,
        )
        if not ready:
            self._stop_watchdog_only()
            raise RuntimeError("external process watchdog did not become ready")

    def __enter__(self) -> "ExternalProcessGuard":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    @property
    def watchdog_pid(self) -> int:
        return self._watchdog.pid

    @property
    def cleanup_result(self) -> Optional[GuardCleanupResult]:
        return self._cleanup_result

    def workload_command(
        self,
        mode: str,
        *,
        port: int = 0,
        fake_secret: str = "SEC_EXEC_FAKE_NOT_A_SECRET",
        output_chars: int = 25_000,
        tick_seconds: float = 0.05,
    ) -> tuple[str, ...]:
        """Return one launch only after its watchdog has acknowledged the arm.

        Execute the returned command only through ``spawn_observing_popen`` so
        the direct child cannot become caller-visible before watchdog ownership.
        If admission fails before the underlying spawn, call ``disarm_no_spawn``.
        """

        with self._state_lock:
            return self._workload_command_locked(
                mode,
                port=port,
                fake_secret=fake_secret,
                output_chars=output_chars,
                tick_seconds=tick_seconds,
            )

    def _workload_command_locked(
        self,
        mode: str,
        *,
        port: int,
        fake_secret: str,
        output_chars: int,
        tick_seconds: float,
    ) -> tuple[str, ...]:
        if mode not in WORKLOAD_MODES:
            raise ValueError(f"unsupported workload mode: {mode}")
        if self._closed or self._close_in_progress:
            raise RuntimeError("cannot arm a closed process guard")
        if self._watchdog.poll() is not None:
            raise RuntimeError("cannot arm after watchdog idle deadline or exit")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port < 65536:
            raise ValueError("port must be between 0 and 65535")
        if (
            isinstance(output_chars, bool)
            or not isinstance(output_chars, int)
            or output_chars < 0
        ):
            raise ValueError("output_chars cannot be negative")
        tick = _positive_number(tick_seconds, "tick_seconds")
        existing = read_json_if_ready(self.launch_armed_manifest)
        if existing is not None:
            raise RuntimeError("this process guard already consumed its one launch")
        if self.leader_payload() is not None or self.grandchild_payload() is not None:
            raise RuntimeError("this process guard already observed a process launch")
        atomic_write_json(
            self.launch_armed_manifest,
            {
                "token": self.token,
                "state": "armed",
                "owner_pid": os.getpid(),
                "armed_monotonic": time.monotonic(),
            },
        )
        acknowledged = wait_until(
            self._watchdog_arm_is_acknowledged,
            timeout_seconds=self._arm_ack_timeout_seconds,
            poll_interval_seconds=0.02,
        )
        if not acknowledged or not self._watchdog_arm_is_acknowledged():
            atomic_write_json(
                self.launch_armed_manifest,
                {
                    "token": self.token,
                    "state": "disarmed_no_spawn",
                    "owner_pid": os.getpid(),
                    "reason": "watchdog_arm_ack_timeout",
                    "disarmed_monotonic": time.monotonic(),
                },
            )
            raise RuntimeError("watchdog did not acknowledge armed launch")
        command = (
            str(Path(sys.executable).resolve()),
            str(FIXTURE_SCRIPT),
            "workload",
            "--mode",
            mode,
            "--token",
            self.token,
            "--leader-manifest",
            str(self.leader_manifest),
            "--grandchild-manifest",
            str(self.grandchild_manifest),
            "--launch-armed-manifest",
            str(self.launch_armed_manifest),
            "--arm-ack-manifest",
            str(self.watchdog_arm_ack_path),
            "--spawn-ack-manifest",
            str(self.watchdog_spawn_ack_path),
            "--marker",
            str(self.marker_path),
            "--port",
            str(port),
            "--fake-secret",
            fake_secret,
            "--output-chars",
            str(output_chars),
            "--tick-seconds",
            str(tick),
        )
        self._expected_workload_command = command
        return command

    def spawn_observing_popen(self, popen_factory):
        """Wrap one trusted fixture spawn and return only after watchdog ACK.

        The returned callable is intended to be injected at the Runtime's
        already-frozen ``Popen`` seam.  It retains the direct-child handle
        immediately after the underlying factory returns, atomically publishes
        the observed PID/PGID/SID, and withholds the handle from the caller
        until the external watchdog acknowledges that exact identity.  This
        closes the caller-visible gap, not the interpreter-death window between
        the factory return and the following strong-reference assignment.
        """

        if not callable(popen_factory):
            raise TypeError("popen_factory must be callable")

        def guarded_popen(*args, **kwargs):
            with self._state_lock:
                return self._spawn_observing_popen_locked(
                    popen_factory, args, kwargs
                )

        return guarded_popen

    def _spawn_observing_popen_locked(self, popen_factory, args, kwargs):
        if self._closed or self._close_in_progress:
            raise RuntimeError("cannot spawn through a closed process guard")
        if self._watchdog.poll() is not None:
            raise RuntimeError("cannot spawn after watchdog idle deadline or exit")
        if self._spawned_process is not None:
            raise RuntimeError("this process guard already observed one spawn")
        if self._expected_workload_command is None:
            raise RuntimeError("workload command must be armed before spawn")
        if not args or tuple(args[0]) != self._expected_workload_command:
            raise RuntimeError("spawn command does not match the armed fixture command")
        armed = read_json_if_ready(self.launch_armed_manifest)
        if (
            armed is None
            or armed.get("token") != self.token
            or armed.get("state") != "armed"
        ):
            raise RuntimeError("spawn requires one matching armed launch")
        if not self._watchdog_arm_is_acknowledged():
            raise RuntimeError(
                "spawn requires a fresh watchdog arm acknowledgment"
            )

        process = popen_factory(*args, **kwargs)
        self._spawned_process = process
        try:
            pid = _positive_pid(getattr(process, "pid", None), "spawned pid")
            owner_pid = getattr(self, "_owner_pid", os.getpid())
            owner_pgid = getattr(self, "_owner_pgid", os.getpgrp())
            if owner_pid != os.getpid():
                raise RuntimeError("spawn wrapper owner PID changed")
            if owner_pgid != os.getpgrp():
                raise RuntimeError("spawn wrapper owner process group changed")
            if pid in {owner_pid, self._watchdog.pid}:
                raise RuntimeError("spawned PID overlaps a protected process")
            pgid = os.getpgid(pid)
            sid = os.getsid(pid)
            if pid != pgid or pgid != sid:
                raise RuntimeError(
                    "spawned fixture is not its own process-group/session leader"
                )
            if pgid in {owner_pgid, self._watchdog.pid}:
                raise RuntimeError("spawned PGID overlaps a protected process group")
            identity = {
                "token": self.token,
                "owner_pid": owner_pid,
                "pid": pid,
                "pgid": pgid,
                "sid": sid,
            }
            atomic_write_json(
                self.spawn_observed_path,
                {
                    **identity,
                    "state": "spawn_observed",
                    "observed_monotonic": time.monotonic(),
                },
            )
            acknowledged = wait_until(
                lambda: self._watchdog_spawn_is_acknowledged(identity),
                timeout_seconds=self._spawn_ack_timeout_seconds,
                poll_interval_seconds=0.02,
            )
            if (
                not acknowledged
                or not self._watchdog_spawn_is_acknowledged(identity)
            ):
                raise RuntimeError(
                    "watchdog did not provide an exact spawn acknowledgment"
                )
            return process
        except BaseException:
            self._terminate_and_join_spawned_before_raise(process)
            raise

    @staticmethod
    def _terminate_and_join_spawned_before_raise(process) -> None:
        """Never expose a failed registration while its direct child is live."""

        terminal_wait = threading.Event()
        term_attempted = False
        kill_attempted = False
        while True:
            try:
                if process.poll() is not None:
                    try:
                        process.wait()
                    except BaseException:
                        pass
                    return
            except BaseException:
                if getattr(process, "returncode", None) is not None:
                    return
            try:
                if not term_attempted:
                    term_attempted = True
                    process.terminate()
                elif not kill_attempted:
                    kill_attempted = True
                    process.kill()
            except BaseException:
                pass
            try:
                process.wait(timeout=0.05)
            except BaseException:
                pass
            try:
                terminal_wait.wait(0.02)
            except BaseException:
                pass

    def disarm_no_spawn(self) -> None:
        """Explicitly close an admission path that never reached process spawn."""

        with self._state_lock:
            self._disarm_no_spawn_locked()

    def _disarm_no_spawn_locked(self) -> None:
        if self._closed or self._close_in_progress:
            raise RuntimeError("cannot disarm a closed process guard")
        armed = read_json_if_ready(self.launch_armed_manifest)
        if (
            armed is None
            or armed.get("token") != self.token
            or armed.get("state") != "armed"
        ):
            raise RuntimeError("no matching armed launch is available to disarm")
        if self.leader_payload() is not None or self.grandchild_payload() is not None:
            raise RuntimeError("cannot disarm after a process manifest was registered")
        if self._spawned_process is not None:
            raise RuntimeError("cannot disarm after the spawn factory returned")
        observed = read_json_if_ready(self.spawn_observed_path)
        if observed is not None:
            raise RuntimeError("cannot disarm after a spawn observation was published")
        atomic_write_json(
            self.launch_armed_manifest,
            {
                "token": self.token,
                "state": "disarmed_no_spawn",
                "owner_pid": os.getpid(),
                "disarmed_monotonic": time.monotonic(),
            },
        )

    def leader_payload(self) -> Optional[dict[str, object]]:
        return read_json_if_ready(self.leader_manifest)

    def grandchild_payload(self) -> Optional[dict[str, object]]:
        return read_json_if_ready(self.grandchild_manifest)

    def wait_for_leader(self, *, timeout_seconds: float = 2.0) -> bool:
        return wait_until(
            lambda: self.leader_payload() is not None,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=0.02,
        )

    def wait_for_grandchild(self, *, timeout_seconds: float = 2.0) -> bool:
        return wait_until(
            lambda: self.grandchild_payload() is not None,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=0.02,
        )

    def owned_group(self) -> Optional[OwnedProcessGroup]:
        group, errors = _validated_owned_group(
            self.leader_payload(),
            self.grandchild_payload(),
            token=self.token,
            forbidden_pgids=(os.getpgrp(), self._watchdog.pid),
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        return group

    def close(self) -> GuardCleanupResult:
        with self._state_lock:
            return self._close_locked()

    def _close_locked(self) -> GuardCleanupResult:
        if self._cleanup_result is not None:
            if self._deferred_close_error is not None:
                raise self._deferred_close_error
            return self._return_or_raise(self._cleanup_result)
        if self._close_in_progress:
            raise RuntimeError("external process guard close is already in progress")
        self._close_in_progress = True
        errors: list[str] = []
        observations: list[str] = []
        group: Optional[OwnedProcessGroup] = None
        group_gone = False
        pids_gone = False
        deferred_error: Optional[BaseException] = None
        try:
            try:
                ordering_error = self._guard_root_ordering_error()
            except Exception as exc:
                ordering_error = (
                    "guard-root ordering check raised before watchdog join: "
                    f"{type(exc).__name__}: {exc}"
                )
            except BaseException as exc:
                deferred_error = exc
                ordering_error = (
                    "guard-root ordering check was interrupted; deferring "
                    f"{type(exc).__name__} until watchdog terminal"
                )
            if ordering_error is not None:
                errors.append(ordering_error)
            try:
                atomic_write_json(
                    self.control_path,
                    {"token": self.token, "action": "cleanup", "pid": os.getpid()},
                )
            except Exception as exc:
                errors.append(
                    "cleanup control write failed; waiting for watchdog hard deadline: "
                    f"{type(exc).__name__}: {exc}"
                )
            except BaseException as exc:
                if deferred_error is None:
                    deferred_error = exc
                errors.append(
                    "cleanup control write was interrupted; deferring "
                    f"{type(exc).__name__} until watchdog terminal"
                )

            deferred_error = self._join_watchdog_before_return(
                errors, observations, deferred_error
            )

            ordering_error = self._guard_root_ordering_error()
            if ordering_error is not None:
                errors.append(ordering_error)

            try:
                launch_armed = read_json_if_ready(self.launch_armed_manifest)
                leader = self.leader_payload()
                grandchild = self.grandchild_payload()
                registration_unresolved, registration_errors = (
                    _target_registration_unresolved(
                        launch_armed,
                        leader,
                        grandchild,
                        token=self.token,
                    )
                )
                errors.extend(registration_errors)
                group, validation_errors = _validated_owned_group(
                    leader,
                    grandchild,
                    token=self.token,
                    forbidden_pgids=(os.getpgrp(), self._watchdog.pid),
                )
                errors.extend(validation_errors)
                if registration_unresolved:
                    observations.append(
                        "target registration remained unresolved through watchdog "
                        "completion"
                    )
                elif validation_errors:
                    observations.append(
                        "target identity validation failed after watchdog completion"
                    )
                else:
                    (
                        group_gone,
                        pids_gone,
                        cleanup_errors,
                        cleanup_observations,
                    ) = _cleanup_owned_group(group)
                    errors.extend(cleanup_errors)
                    observations.extend(cleanup_observations)
            except Exception as exc:
                errors.append(
                    "parent cleanup raised after watchdog join: "
                    f"{type(exc).__name__}: {exc}"
                )

            watchdog_exit = self._watchdog.poll()
            payload = read_json_if_ready(self.watchdog_result_path) or {}
            payload_observations = payload.get("observations", ())
            if isinstance(payload_observations, list):
                observations.extend(str(item) for item in payload_observations)
            if watchdog_exit != 0:
                errors.append(f"watchdog exited with code {watchdog_exit}")
            if payload.get("clean") is False:
                errors.append("watchdog reported incomplete target cleanup")
            result = GuardCleanupResult(
                watchdog_exit_code=watchdog_exit,
                target_group=group,
                target_group_gone=group_gone,
                target_pids_gone=pids_gone,
                watchdog_payload=payload,
                observations=tuple(dict.fromkeys(observations)),
                errors=tuple(dict.fromkeys(errors)),
            )
            self._cleanup_result = result
            self._deferred_close_error = deferred_error
            self._closed = True
        finally:
            self._close_in_progress = False
        if deferred_error is not None:
            raise deferred_error
        return self._return_or_raise(result)

    cleanup_now = close

    @staticmethod
    def _return_or_raise(result: GuardCleanupResult) -> GuardCleanupResult:
        if not result.clean:
            raise AssertionError(
                "external process guard could not prove cleanup: "
                + "; ".join(result.errors)
            )
        return result

    def _watchdog_is_ready(self) -> bool:
        if self._watchdog.poll() is not None:
            return False
        payload = read_json_if_ready(self.watchdog_ready_path)
        return bool(
            payload
            and payload.get("token") == self.token
            and payload.get("pid") == self._watchdog.pid
            and payload.get("pgid") == self._watchdog.pid
        )

    def _watchdog_arm_is_acknowledged(self) -> bool:
        if self._watchdog.poll() is not None:
            return False
        payload = read_json_if_ready(self.watchdog_arm_ack_path)
        deadline = payload.get("deadline_monotonic") if payload else None
        return bool(
            payload
            and payload.get("token") == self.token
            and payload.get("state") == "armed_acknowledged"
            and payload.get("watchdog_pid") == self._watchdog.pid
            and isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and deadline > 0
            and time.monotonic() < deadline
        )

    def _watchdog_spawn_is_acknowledged(
        self, identity: Mapping[str, object]
    ) -> bool:
        if self._watchdog.poll() is not None:
            return False
        payload = read_json_if_ready(self.watchdog_spawn_ack_path)
        arm_payload = read_json_if_ready(self.watchdog_arm_ack_path)
        deadline = payload.get("deadline_monotonic") if payload else None
        arm_deadline = (
            arm_payload.get("deadline_monotonic") if arm_payload else None
        )
        return bool(
            payload
            and arm_payload
            and payload.get("token") == identity.get("token") == self.token
            and arm_payload.get("token") == self.token
            and payload.get("state") == "spawn_acknowledged"
            and payload.get("owner_pid") == identity.get("owner_pid")
            and payload.get("pid") == identity.get("pid")
            and payload.get("pgid") == identity.get("pgid")
            and payload.get("sid") == identity.get("sid")
            and payload.get("watchdog_pid") == self._watchdog.pid
            and arm_payload.get("watchdog_pid") == self._watchdog.pid
            and isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and deadline > 0
            and deadline == arm_deadline
            and time.monotonic() < deadline
        )

    def _guard_root_ordering_error(self) -> Optional[str]:
        try:
            current = self.guard_root.stat()
        except (FileNotFoundError, OSError) as exc:
            return (
                "cleanup ordering violation: guard root is unavailable before "
                "guard.close(); never let TemporaryDirectory exit first "
                f"({type(exc).__name__}: {exc})"
            )
        identity = (current.st_dev, current.st_ino)
        if identity != self._guard_root_identity:
            return (
                "cleanup ordering violation: guard root identity changed before "
                "guard.close(); never replace or remove its TemporaryDirectory"
            )
        return None

    def _join_watchdog_before_return(
        self,
        errors: list[str],
        observations: list[str],
        deferred_error: Optional[BaseException] = None,
    ) -> Optional[BaseException]:
        """Join normally, then enter an unbounded terminal-no-escape barrier.

        The unbounded disaster path is intentional: Python cannot promise that
        a blocked OS wait/signal/filesystem operation is preemptible, so a live
        watchdog is never traded for a bounded return from ``close``.
        """

        timeout = (
            self._hard_deadline_seconds
            + WATCHDOG_NORMAL_COMPLETION_BUFFER_SECONDS
        )

        def watchdog_is_terminal() -> bool:
            nonlocal deferred_error
            try:
                return self._watchdog.poll() is not None
            except Exception as exc:
                errors.append(
                    "watchdog poll raised: "
                    f"{type(exc).__name__}: {exc}"
                )
            except BaseException as exc:
                if deferred_error is None:
                    deferred_error = exc
                errors.append(
                    "watchdog poll was interrupted; deferring "
                    f"{type(exc).__name__} until terminal"
                )
            return getattr(self._watchdog, "returncode", None) is not None

        try:
            self._watchdog.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.append(
                "watchdog exceeded hard deadline plus normal completion buffer"
            )
        except Exception as exc:
            errors.append(
                f"watchdog join raised: {type(exc).__name__}: {exc}"
            )
        except BaseException as exc:
            if deferred_error is None:
                deferred_error = exc
            errors.append(
                "watchdog join was interrupted; deferring "
                f"{type(exc).__name__} until terminal"
            )

        if not watchdog_is_terminal():
            observations.append(
                "watchdog required emergency stop after bounded completion wait"
            )
            try:
                self._stop_watchdog_only()
            except Exception as exc:
                errors.append(
                    "could not safely stop and join watchdog: "
                    f"{type(exc).__name__}: {exc}"
                )
            except BaseException as exc:
                if deferred_error is None:
                    deferred_error = exc
                errors.append(
                    "watchdog emergency stop was interrupted; deferring "
                    f"{type(exc).__name__} until terminal"
                )
        if not watchdog_is_terminal():
            deferred_error = self._wait_watchdog_terminal_no_escape(
                errors, observations, deferred_error
            )
        observations.append("watchdog joined before guard.close returned")
        return deferred_error

    def _wait_watchdog_terminal_no_escape(
        self,
        errors: list[str],
        observations: list[str],
        deferred_error: Optional[BaseException],
    ) -> Optional[BaseException]:
        """Block indefinitely rather than return or raise with a live watchdog."""

        backoff = threading.Event()
        poll_error_types: list[str] = []
        while True:
            try:
                returncode = self._watchdog.poll()
                if returncode is not None:
                    break
            except Exception as exc:
                poll_error_types.append(type(exc).__name__)
                if getattr(self._watchdog, "returncode", None) is not None:
                    break
            except BaseException as exc:
                if deferred_error is None:
                    deferred_error = exc
                poll_error_types.append(type(exc).__name__)
                if getattr(self._watchdog, "returncode", None) is not None:
                    break
            try:
                backoff.wait(0.02)
            except Exception as exc:
                poll_error_types.append(type(exc).__name__)
            except BaseException as exc:
                if deferred_error is None:
                    deferred_error = exc
                poll_error_types.append(type(exc).__name__)
        if poll_error_types:
            errors.append(
                "watchdog terminal barrier observed exceptions: "
                + ", ".join(dict.fromkeys(poll_error_types))
            )
        observations.append(
            "watchdog reached terminal state inside no-escape barrier"
        )
        return deferred_error

    def _stop_watchdog_only(self) -> None:
        if self._watchdog.poll() is not None:
            self._watchdog.wait()
            return
        pid = self._watchdog.pid
        try:
            pgid = os.getpgid(pid)
            sid = os.getsid(pid)
        except ProcessLookupError:
            self._watchdog.wait(timeout=1)
            return
        if pgid != pid or sid != pid or pgid == os.getpgrp():
            raise RuntimeError(
                "refused to stop watchdog without an isolated session/group"
            )
        self._signal_watchdog_if_still_owned(pgid, signal.SIGTERM)
        try:
            self._watchdog.wait(timeout=0.5)
            return
        except subprocess.TimeoutExpired:
            pass
        self._signal_watchdog_if_still_owned(pgid, signal.SIGKILL)
        self._watchdog.wait(timeout=1.5)

    def _signal_watchdog_if_still_owned(self, pgid: int, signum: int) -> None:
        pid = self._watchdog.pid
        try:
            actual_pgid = os.getpgid(pid)
            actual_sid = os.getsid(pid)
        except ProcessLookupError:
            return
        if (
            actual_pgid != pgid
            or actual_sid != pgid
            or pid != pgid
            or pgid == os.getpgrp()
        ):
            raise RuntimeError(
                "refused watchdog group signal after PID/PGID/SID changed"
            )
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass


__all__ = [
    "ExternalProcessGuard",
    "FIXTURE_SCRIPT",
    "GuardCleanupResult",
    "LOOPBACK_HOST",
    "MarkerSnapshot",
    "OwnedProcessGroup",
    "atomic_write_json",
    "marker_snapshot",
    "pgid_exists",
    "pid_exists",
    "port_is_open",
    "read_json_if_ready",
    "wait_marker_stable",
    "wait_pgid_gone",
    "wait_pid_gone",
    "wait_port_closed",
    "wait_port_open",
    "wait_until",
]
