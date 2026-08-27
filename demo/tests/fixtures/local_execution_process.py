#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence


LOOPBACK_HOST = "127.0.0.1"
WORKLOAD_MODES = (
    "success_orphan",
    "nonzero_orphan",
    "hang_ignore_term",
    "hang_leader_exits_on_term",
    "server_ready",
    "server_never_ready",
    "exit_before_ready",
    "stdout_short",
    "stdout_long",
)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
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


def _read_json(path: Path) -> Optional[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _valid_token(value: str) -> str:
    if not value or len(value) > 128 or not all(
        character.isalnum() or character in {"-", "_"}
        for character in value
    ):
        raise ValueError("token contains unsupported characters")
    return value


def _positive_pid(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 1:
        raise ValueError(f"{name} must be an integer greater than one")
    return value


def _pid_exists(pid: int) -> bool:
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


def _wait_until(predicate, timeout_seconds: float, interval_seconds: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval_seconds, remaining))


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


def _grandchild(args: argparse.Namespace) -> int:
    token = _valid_token(args.token)
    leader_pid = _positive_pid(args.leader_pid, "leader_pid")
    expected_pgid = _positive_pid(args.expected_pgid, "expected_pgid")
    if args.port < 0 or args.port >= 65536:
        raise ValueError("port must be between 0 and 65535")
    if args.tick_seconds <= 0:
        raise ValueError("tick_seconds must be positive")
    if args.startup_delay_seconds < 0:
        raise ValueError("startup_delay_seconds cannot be negative")

    with os.fdopen(args.start_gate_fd, "rb", closefd=True) as start_gate:
        if start_gate.read(1) != b"R":
            return 73

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if args.startup_delay_seconds:
        time.sleep(args.startup_delay_seconds)
    actual_pgid = os.getpgrp()
    actual_sid = os.getsid(0)
    if actual_pgid != expected_pgid or actual_sid != expected_pgid:
        _atomic_write_json(
            args.grandchild_manifest,
            {
                "token": token,
                "role": "grandchild",
                "pid": os.getpid(),
                "pgid": actual_pgid,
                "sid": actual_sid,
                "leader_pid": leader_pid,
                "port": 0,
                "listening": False,
                "error": "grandchild escaped expected process group",
            },
        )
        return 65

    server: Optional[socket.socket] = None
    actual_port = args.port
    if args.listen:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((LOOPBACK_HOST, args.port))
        server.listen(8)
        server.settimeout(args.tick_seconds)
        actual_port = int(server.getsockname()[1])

    args.marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _atomic_write_json(
        args.grandchild_manifest,
        {
            "token": token,
            "role": "grandchild",
            "pid": os.getpid(),
            "pgid": actual_pgid,
            "sid": actual_sid,
            "leader_pid": leader_pid,
            "port": actual_port if args.listen else 0,
            "requested_port": args.port,
            "listening": bool(server),
            "state": "ready",
            "ready_monotonic": time.monotonic(),
        },
    )

    counter = 0
    try:
        with args.marker.open("a", encoding="utf-8") as marker:
            while True:
                counter += 1
                marker.write(f"{counter}\n")
                marker.flush()
                if server is None:
                    time.sleep(args.tick_seconds)
                    continue
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                except InterruptedError:
                    continue
                with connection:
                    connection.settimeout(args.tick_seconds)
                    try:
                        connection.recv(4096)
                    except (socket.timeout, OSError):
                        pass
                    try:
                        connection.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Length: 2\r\n"
                            b"Connection: close\r\n\r\nOK"
                        )
                    except OSError:
                        pass
    finally:
        if server is not None:
            server.close()


def _workload(args: argparse.Namespace) -> int:
    token = _valid_token(args.token)
    if args.mode not in WORKLOAD_MODES:
        raise ValueError(f"unsupported workload mode: {args.mode}")
    if args.port < 0 or args.port >= 65536:
        raise ValueError("port must be between 0 and 65535")
    if args.output_chars < 0:
        raise ValueError("output_chars cannot be negative")
    if args.tick_seconds <= 0:
        raise ValueError("tick_seconds must be positive")

    leader_pid = os.getpid()
    pgid = os.getpgrp()
    sid = os.getsid(0)
    if leader_pid != pgid or sid != pgid:
        _atomic_write_json(
            args.leader_manifest,
            {
                "token": token,
                "role": "leader",
                "pid": leader_pid,
                "pgid": pgid,
                "sid": sid,
                "port": 0,
                "error": "workload did not start as a fresh session leader",
            },
        )
        return 64

    # This pre-ACK leader manifest is control-plane identity only.  Marker,
    # pipe, descendant, listener, output, and every mode-specific effect remain
    # below the fresh spawn-ACK gate.
    _atomic_write_json(
        args.leader_manifest,
        {
            "token": token,
            "role": "leader",
            "pid": leader_pid,
            "pgid": pgid,
            "sid": sid,
            "port": 0,
            "mode": args.mode,
            "spawned_monotonic": time.monotonic(),
        },
    )
    armed = _read_json(args.launch_armed_manifest)
    if (
        armed is None
        or armed.get("token") != token
        or armed.get("state") != "armed"
    ):
        _atomic_write_json(
            args.leader_manifest,
            {
                "token": token,
                "role": "leader",
                "pid": leader_pid,
                "pgid": pgid,
                "sid": sid,
                "port": 0,
                "mode": args.mode,
                "error": "workload launch was not armed before process registration",
            },
        )
        return 63
    arm_ack = _read_json(args.arm_ack_manifest)
    arm_deadline = arm_ack.get("deadline_monotonic") if arm_ack else None
    arm_watchdog_pid = arm_ack.get("watchdog_pid") if arm_ack else None
    if (
        arm_ack is None
        or arm_ack.get("token") != token
        or arm_ack.get("state") != "armed_acknowledged"
        or isinstance(arm_deadline, bool)
        or not isinstance(arm_deadline, (int, float))
        or arm_deadline <= 0
        or isinstance(arm_watchdog_pid, bool)
        or not isinstance(arm_watchdog_pid, int)
        or arm_watchdog_pid <= 1
        or time.monotonic() >= arm_deadline
    ):
        _atomic_write_json(
            args.leader_manifest,
            {
                "token": token,
                "role": "leader",
                "pid": leader_pid,
                "pgid": pgid,
                "sid": sid,
                "port": 0,
                "mode": args.mode,
                "error": "watchdog did not acknowledge launch before workload",
            },
        )
        return 62
    owner_pid = armed.get("owner_pid")
    if (
        isinstance(owner_pid, bool)
        or not isinstance(owner_pid, int)
        or owner_pid <= 1
    ):
        _atomic_write_json(
            args.leader_manifest,
            {
                "token": token,
                "role": "leader",
                "pid": leader_pid,
                "pgid": pgid,
                "sid": sid,
                "port": 0,
                "mode": args.mode,
                "error": "armed launch has no valid owner PID",
            },
        )
        return 60

    def spawn_acknowledged() -> bool:
        payload = _read_json(args.spawn_ack_manifest)
        ack_deadline = payload.get("deadline_monotonic") if payload else None
        return bool(
            payload
            and payload.get("token") == token
            and payload.get("state") == "spawn_acknowledged"
            and payload.get("owner_pid") == owner_pid
            and payload.get("pid") == leader_pid
            and payload.get("pgid") == pgid
            and payload.get("sid") == sid
            and payload.get("watchdog_pid") == arm_watchdog_pid
            and isinstance(ack_deadline, (int, float))
            and not isinstance(ack_deadline, bool)
            and ack_deadline == arm_deadline
            and time.monotonic() < ack_deadline
        )

    remaining_ack_time = max(0.0, float(arm_deadline) - time.monotonic())
    if (
        not _wait_until(spawn_acknowledged, remaining_ack_time)
        or not spawn_acknowledged()
    ):
        _atomic_write_json(
            args.leader_manifest,
            {
                "token": token,
                "role": "leader",
                "pid": leader_pid,
                "pgid": pgid,
                "sid": sid,
                "port": 0,
                "mode": args.mode,
                "error": "watchdog did not acknowledge exact spawn identity",
            },
        )
        return 61
    _atomic_write_json(
        args.launch_armed_manifest,
        {
            "token": token,
            "state": "registered",
            "owner_pid": owner_pid,
            "leader_pid": leader_pid,
            "pgid": pgid,
            "sid": sid,
            "watchdog_deadline_monotonic": arm_deadline,
            "registered_monotonic": time.monotonic(),
        },
    )
    args.marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    listening = args.mode == "server_ready"
    startup_delay = 0.25 if args.mode == "exit_before_ready" else 0.0
    gate_read_fd, gate_write_fd = os.pipe()
    try:
        grandchild_command = (
            str(Path(sys.executable).resolve()),
            str(Path(__file__).resolve()),
            "grandchild",
            "--token",
            token,
            "--leader-pid",
            str(leader_pid),
            "--expected-pgid",
            str(pgid),
            "--grandchild-manifest",
            str(args.grandchild_manifest),
            "--marker",
            str(args.marker),
            "--port",
            str(args.port),
            "--tick-seconds",
            str(args.tick_seconds),
            "--startup-delay-seconds",
            str(startup_delay),
            "--start-gate-fd",
            str(gate_read_fd),
        )
        if listening:
            grandchild_command = (*grandchild_command, "--listen")
        grandchild = subprocess.Popen(
            grandchild_command,
            cwd=str(args.marker.parent.resolve()),
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            pass_fds=(gate_read_fd,),
            start_new_session=False,
            umask=0o077,
        )
    except BaseException:
        os.close(gate_write_fd)
        raise
    finally:
        os.close(gate_read_fd)

    try:
        child_pgid = os.getpgid(grandchild.pid)
        child_sid = os.getsid(grandchild.pid)
        if child_pgid != pgid or child_sid != sid:
            raise RuntimeError(
                "spawned grandchild escaped the leader process group"
            )
        _atomic_write_json(
            args.grandchild_manifest,
            {
                "token": token,
                "role": "grandchild",
                "pid": grandchild.pid,
                "pgid": child_pgid,
                "sid": child_sid,
                "leader_pid": leader_pid,
                "port": 0,
                "listening": False,
                "state": "spawn_registered",
                "registered_by_leader_monotonic": time.monotonic(),
            },
        )
        os.write(gate_write_fd, b"R")
    finally:
        os.close(gate_write_fd)

    if args.mode == "exit_before_ready":
        os._exit(31)

    def grandchild_ready() -> bool:
        payload = _read_json(args.grandchild_manifest) or {}
        return payload.get("token") == token and payload.get("state") == "ready"

    ready = _wait_until(grandchild_ready, 2.0)
    if not ready:
        os._exit(72)
    grandchild_payload = _read_json(args.grandchild_manifest) or {}
    actual_port = grandchild_payload.get("port", 0)
    if not isinstance(actual_port, int):
        actual_port = 0
    _atomic_write_json(
        args.leader_manifest,
        {
            "token": token,
            "role": "leader",
            "pid": leader_pid,
            "pgid": pgid,
            "sid": sid,
            "port": actual_port,
            "mode": args.mode,
            "grandchild_pid": grandchild.pid,
            "ready_monotonic": time.monotonic(),
        },
    )

    if args.mode == "stdout_short":
        print("fixture-short-stdout", flush=True)
        print("fixture-short-stderr", file=sys.stderr, flush=True)
        os._exit(0)
    if args.mode == "stdout_long":
        marker = f" api_key={args.fake_secret} "
        remaining = max(0, args.output_chars - len(marker))
        head = remaining // 2
        payload = "A" * head + marker + "Z" * (remaining - head)
        print(payload, flush=True)
        print(payload, file=sys.stderr, flush=True)
        os._exit(0)
    if args.mode == "success_orphan":
        os._exit(0)
    if args.mode == "nonzero_orphan":
        os._exit(23)
    if args.mode == "hang_ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    while True:
        time.sleep(1.0)


def _validated_target(
    leader: Optional[Mapping[str, object]],
    grandchild: Optional[Mapping[str, object]],
    *,
    token: str,
    forbidden_pgids: Sequence[int],
) -> tuple[Optional[int], tuple[int, ...], list[str]]:
    if leader is None:
        if grandchild is None:
            return None, (), []
        return None, (), ["grandchild manifest exists without leader manifest"]
    try:
        if leader.get("token") != token or leader.get("role") != "leader":
            raise ValueError("leader manifest identity mismatch")
        leader_pid = _manifest_int(leader, "pid")
        pgid = _manifest_int(leader, "pgid")
        sid = _manifest_int(leader, "sid")
        if leader_pid != pgid:
            raise ValueError("leader is not its process-group leader")
        if sid != pgid:
            raise ValueError("leader session ID does not match its process group")
        if pgid in set(forbidden_pgids):
            raise ValueError("target PGID overlaps a protected process group")
    except ValueError as exc:
        return None, (), [str(exc)]

    pids = [leader_pid]
    errors: list[str] = []
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
                raise ValueError("grandchild escaped its recorded session")
            if child_pid == leader_pid:
                raise ValueError("grandchild PID collides with leader PID")
            pids.append(child_pid)
        except ValueError as exc:
            errors.append(str(exc))
    return pgid, tuple(pids), errors


def _validated_spawn_candidate(
    observation: Optional[Mapping[str, object]],
    *,
    token: str,
    owner_pid: int,
    forbidden_pgids: Sequence[int],
) -> tuple[Optional[dict[str, object]], list[str]]:
    """Validate one parent-observed direct child against its live identity."""

    if observation is None:
        return None, ["spawn observation is missing"]
    try:
        if observation.get("token") != token:
            raise ValueError("spawn observation token identity mismatch")
        state = observation.get("state")
        if state != "spawn_observed":
            raise ValueError("spawn observation has invalid state")
        observed_owner = _manifest_int(observation, "owner_pid")
        if observed_owner != owner_pid:
            raise ValueError("spawn observation owner identity mismatch")
        pid = _manifest_int(observation, "pid")
        pgid = _manifest_int(observation, "pgid")
        sid = _manifest_int(observation, "sid")
        if pid != pgid or pgid != sid:
            raise ValueError("spawn observation identity is not a session leader")
        if pid == owner_pid or pgid in set(forbidden_pgids):
            raise ValueError("spawn observation overlaps a protected identity")
        try:
            actual_pgid = os.getpgid(pid)
            actual_sid = os.getsid(pid)
        except (ProcessLookupError, OSError) as exc:
            raise ValueError(
                "spawn observation identity is no longer observable"
            ) from exc
        if actual_pgid != pgid or actual_sid != sid:
            raise ValueError("spawn observation current identity mismatch")
    except ValueError as exc:
        return None, [str(exc)]
    return {
        "token": token,
        "owner_pid": owner_pid,
        "pid": pid,
        "pgid": pgid,
        "sid": sid,
    }, []


def _validated_candidate_target(
    candidate: Optional[Mapping[str, object]],
    leader: Optional[Mapping[str, object]],
    grandchild: Optional[Mapping[str, object]],
    *,
    token: str,
    forbidden_pgids: Sequence[int],
) -> tuple[Optional[int], tuple[int, ...], list[str]]:
    """Bind later manifests to the exact watchdog-acknowledged candidate."""

    if candidate is None:
        return None, (), ["no verified spawn candidate is available"]
    try:
        if candidate.get("token") != token:
            raise ValueError("spawn candidate token identity mismatch")
        candidate_pid = _manifest_int(candidate, "pid")
        candidate_pgid = _manifest_int(candidate, "pgid")
        candidate_sid = _manifest_int(candidate, "sid")
        if candidate_pid != candidate_pgid or candidate_pgid != candidate_sid:
            raise ValueError("spawn candidate identity is inconsistent")
        if candidate_pgid in set(forbidden_pgids):
            raise ValueError("spawn candidate overlaps a protected process group")
    except ValueError as exc:
        return None, (), [str(exc)]

    if leader is None:
        if grandchild is not None:
            return None, (), [
                "grandchild manifest exists before exact candidate leader"
            ]
        return candidate_pgid, (candidate_pid,), []

    pgid, pids, validation_errors = _validated_target(
        leader,
        grandchild,
        token=token,
        forbidden_pgids=forbidden_pgids,
    )
    if validation_errors or pgid is None:
        return None, (), validation_errors or [
            "candidate target validation did not produce a process group"
        ]
    try:
        leader_identity = (
            _manifest_int(leader, "pid"),
            _manifest_int(leader, "pgid"),
            _manifest_int(leader, "sid"),
        )
    except ValueError as exc:
        return None, (), [str(exc)]
    if leader_identity != (candidate_pid, candidate_pgid, candidate_sid):
        return None, (), [
            "leader manifest does not exactly match the acknowledged candidate"
        ]
    return pgid, pids, []


def _kill_target_group(
    pgid: Optional[int], pids: Sequence[int]
) -> tuple[bool, bool, list[str], list[str]]:
    if pgid is None:
        return True, True, [], []
    errors: list[str] = []
    observations: list[str] = []

    def membership() -> tuple[
        str, tuple[int, ...], tuple[tuple[int, int, int], ...]
    ]:
        matching: list[int] = []
        mismatched: list[tuple[int, int, int]] = []
        missing = 0
        for pid in pids:
            try:
                actual_pgid = os.getpgid(pid)
            except ProcessLookupError:
                missing += 1
                continue
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    missing += 1
                    continue
                return "membership_unknown", (), ()
            try:
                actual_sid = os.getsid(pid)
            except ProcessLookupError:
                missing += 1
                continue
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    missing += 1
                    continue
                return "membership_unknown", (), ()
            if actual_pgid == pgid and actual_sid == pgid:
                matching.append(pid)
            else:
                mismatched.append((pid, actual_pgid, actual_sid))
        if matching:
            return "owned_member_present", tuple(matching), tuple(mismatched)
        if mismatched:
            status = (
                "sid_drift_or_reuse"
                if all(
                    actual_pgid == pgid and actual_sid != pgid
                    for _pid, actual_pgid, actual_sid in mismatched
                )
                else "pgid_may_be_reused"
            )
            return status, (), tuple(mismatched)
        assert missing == len(pids)
        return "target_members_disappeared", (), ()

    def signal_if_owned(signum: int) -> bool:
        status, _matching, mismatched = membership()
        if status == "target_members_disappeared":
            observations.append(
                "target_members_disappeared: refused unnecessary group signal "
                f"for PGID {pgid}"
            )
            return False
        if status == "pgid_may_be_reused":
            errors.append(
                "pgid_may_be_reused: refused group signal for PGID "
                f"{pgid}; mismatched={mismatched}"
            )
            return False
        if status == "sid_drift_or_reuse":
            errors.append(
                "sid_drift_or_reuse: refused group signal for PGID "
                f"{pgid}; mismatched={mismatched}"
            )
            return False
        if status != "owned_member_present":
            errors.append(
                f"membership_unknown: refused group signal for PGID {pgid}"
            )
            return False
        try:
            os.killpg(pgid, signum)
            return True
        except ProcessLookupError:
            observations.append(
                f"target_members_disappeared during killpg({pgid}, {signum})"
            )
            return False
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                errors.append(f"killpg({pgid}, {signum}) failed: {exc}")
            return False

    status, _matching, _mismatched = membership()
    if status == "owned_member_present":
        signal_if_owned(signal.SIGTERM)
        _wait_until(lambda: membership()[0] != "owned_member_present", 0.25)
    status, _matching, _mismatched = membership()
    if status == "owned_member_present":
        signal_if_owned(signal.SIGKILL)
        _wait_until(lambda: membership()[0] != "owned_member_present", 1.5)

    final_status, _matching, mismatched = membership()
    group_gone = final_status == "target_members_disappeared"
    pids_gone = group_gone
    if final_status == "pgid_may_be_reused":
        errors.append(
            "pgid_may_be_reused: watchdog refused further signals; "
            f"mismatched={mismatched}"
        )
    elif final_status == "sid_drift_or_reuse":
        errors.append(
            "sid_drift_or_reuse: watchdog refused further group signals; "
            f"mismatched={mismatched}"
        )
    elif final_status == "membership_unknown":
        errors.append("membership_unknown: watchdog cannot prove cleanup")
    elif final_status == "target_members_disappeared":
        observations.append(
            f"target_members_disappeared: PGID {pgid} was not signalled again"
        )
    if not group_gone:
        errors.append(f"target PGID {pgid} ownership remains unresolved")
    if not pids_gone:
        errors.append(f"target PIDs remain unresolved: {tuple(pids)}")
    return group_gone, pids_gone, errors, observations


def _await_target_registration(
    launch_armed_manifest: Path,
    leader_manifest: Path,
    grandchild_manifest: Path,
    *,
    token: str,
    registration_wait_seconds: float = 0.0,
    registration_deadline_monotonic: Optional[float] = None,
    reader=None,
    clock=None,
    sleeper=None,
) -> tuple[
    Optional[dict[str, object]],
    Optional[dict[str, object]],
    list[str],
]:
    """Close the arm-to-registration crash window after watchdog trigger."""

    read = reader or _read_json
    monotonic = clock or time.monotonic
    sleep = sleeper or time.sleep
    armed = read(launch_armed_manifest)
    leader = read(leader_manifest)
    grandchild = read(grandchild_manifest)
    errors: list[str] = []

    def state_of(payload: Optional[Mapping[str, object]]) -> object:
        return payload.get("state") if payload is not None else None

    def registration_pending() -> bool:
        state = state_of(armed)
        if state == "disarmed_no_spawn":
            return False
        if state in {"armed", "registered"}:
            return leader is None or grandchild is None
        if leader is not None or grandchild is not None:
            return leader is None or grandchild is None
        return False

    if registration_deadline_monotonic is None:
        deadline = monotonic() + max(0.0, registration_wait_seconds)
    else:
        deadline = float(registration_deadline_monotonic)
    while registration_pending() and monotonic() < deadline:
        remaining = max(0.0, deadline - monotonic())
        sleep(min(0.02, remaining))
        latest_armed = read(launch_armed_manifest)
        latest_leader = read(leader_manifest)
        latest_grandchild = read(grandchild_manifest)
        if latest_armed is not None:
            armed = latest_armed
        if latest_leader is not None:
            leader = latest_leader
        if latest_grandchild is not None:
            grandchild = latest_grandchild

    state = state_of(armed)
    if armed is not None:
        if armed.get("token") != token:
            errors.append("launch-armed manifest identity mismatch")
        if state not in {"armed", "registered", "disarmed_no_spawn"}:
            errors.append(f"launch-armed manifest has invalid state: {state}")

    if leader is None:
        if state in {"armed", "registered"}:
            errors.append(
                "launch_armed_without_leader: registration deadline expired"
            )
        if grandchild is not None:
            errors.append(
                "grandchild_before_leader: leader registration never became visible"
            )
    else:
        if armed is None:
            errors.append("leader_registered_without_launch_armed_manifest")
        elif state == "disarmed_no_spawn":
            errors.append("leader_registered_after_explicit_disarm_no_spawn")

    if grandchild is None and state in {"armed", "registered"}:
        errors.append(
            "launch_armed_without_grandchild: registration deadline expired"
        )
    elif grandchild is None and leader is not None:
        errors.append(
            "leader_registered_without_grandchild: registration deadline expired"
        )

    if state == "disarmed_no_spawn" and (
        leader is not None or grandchild is not None
    ):
        errors.append("process manifest exists after explicit disarm_no_spawn")
    return leader, grandchild, errors


def _watchdog(args: argparse.Namespace) -> int:
    token = _valid_token(args.token)
    owner_pid = _positive_pid(args.owner_pid, "owner_pid")
    owner_pgid = _positive_pid(args.owner_pgid, "owner_pgid")
    if args.hard_deadline_seconds <= 0 or args.hard_deadline_seconds > 60:
        raise ValueError("hard deadline must be in (0, 60] seconds")
    if (
        args.registration_wait_seconds < 0
        or args.registration_wait_seconds >= args.hard_deadline_seconds
    ):
        raise ValueError(
            "registration wait must be non-negative and shorter than hard deadline"
        )
    watchdog_pid = os.getpid()
    watchdog_pgid = os.getpgrp()
    if watchdog_pid != watchdog_pgid:
        _atomic_write_json(
            args.result,
            {
                "token": token,
                "clean": False,
                "errors": ["watchdog did not start as a fresh session leader"],
            },
        )
        return 66
    if watchdog_pgid == owner_pgid:
        _atomic_write_json(
            args.result,
            {
                "token": token,
                "clean": False,
                "errors": ["watchdog shares the owner process group"],
            },
        )
        return 67

    ready_monotonic = time.monotonic()
    _atomic_write_json(
        args.ready,
        {
            "token": token,
            "role": "watchdog",
            "pid": watchdog_pid,
            "pgid": watchdog_pgid,
            "ready_monotonic": ready_monotonic,
        },
    )
    idle_deadline = ready_monotonic + args.hard_deadline_seconds
    deadline: Optional[float] = None
    candidate: Optional[dict[str, object]] = None
    reason = "untriggered"
    control_errors: list[str] = []
    while True:
        launch_armed = _read_json(args.launch_armed_manifest)
        if launch_armed is not None:
            launch_state = launch_armed.get("state")
            if launch_armed.get("token") != token:
                control_errors.append("launch-armed manifest identity mismatch")
                reason = "launch_protocol_error"
                break
            if launch_state in {"armed", "registered"} and deadline is None:
                acknowledged = time.monotonic()
                deadline = acknowledged + args.hard_deadline_seconds
                _atomic_write_json(
                    args.arm_ack,
                    {
                        "token": token,
                        "state": "armed_acknowledged",
                        "watchdog_pid": watchdog_pid,
                        "watchdog_pgid": watchdog_pgid,
                        "acknowledged_monotonic": acknowledged,
                        "deadline_monotonic": deadline,
                    },
                )
            elif launch_state == "disarmed_no_spawn" and deadline is None:
                existing_ack = _read_json(args.arm_ack)
                existing_deadline = (
                    existing_ack.get("deadline_monotonic")
                    if existing_ack is not None
                    else None
                )
                if (
                    existing_ack is not None
                    and existing_ack.get("token") == token
                    and not isinstance(existing_deadline, bool)
                    and isinstance(existing_deadline, (int, float))
                    and existing_deadline > 0
                ):
                    deadline = float(existing_deadline)
            elif launch_state not in {
                "armed",
                "registered",
                "disarmed_no_spawn",
            }:
                control_errors.append(
                    f"launch-armed manifest has invalid state: {launch_state}"
                )
                reason = "launch_protocol_error"
                break

        spawn_observation = _read_json(args.spawn_observed)
        if spawn_observation is not None:
            if launch_armed is None:
                control_errors.append(
                    "spawn observation exists without an armed launch"
                )
                reason = "spawn_protocol_error"
                break
            if launch_armed.get("state") == "disarmed_no_spawn":
                control_errors.append(
                    "spawn observation coexists with disarmed_no_spawn"
                )
                reason = "spawn_protocol_error"
                break
            if deadline is None:
                control_errors.append(
                    "spawn observation arrived before watchdog arm acknowledgment"
                )
                reason = "spawn_protocol_error"
                break
            if candidate is None:
                candidate, candidate_errors = _validated_spawn_candidate(
                    spawn_observation,
                    token=token,
                    owner_pid=owner_pid,
                    forbidden_pgids=(owner_pgid, watchdog_pgid),
                )
                if candidate_errors or candidate is None:
                    control_errors.extend(candidate_errors)
                    reason = "spawn_protocol_error"
                    break
                acknowledged = time.monotonic()
                if acknowledged >= deadline:
                    # Retain the verified candidate for cleanup, but never
                    # mint authority after (or exactly at) the hard deadline.
                    reason = "hard_deadline"
                    break
                _atomic_write_json(
                    args.spawn_ack,
                    {
                        **candidate,
                        "state": "spawn_acknowledged",
                        "watchdog_pid": watchdog_pid,
                        "watchdog_pgid": watchdog_pgid,
                        "acknowledged_monotonic": acknowledged,
                        "deadline_monotonic": deadline,
                    },
                )
            else:
                observed_identity = {
                    key: spawn_observation.get(key)
                    for key in ("token", "owner_pid", "pid", "pgid", "sid")
                }
                if observed_identity != candidate:
                    control_errors.append(
                        "spawn observation identity changed after acknowledgment"
                    )
                    reason = "spawn_protocol_error"
                    break

        control = _read_json(args.control)
        if control is not None:
            if control.get("token") != token or control.get("action") != "cleanup":
                control_errors.append("cleanup control identity mismatch")
            reason = "cleanup_control"
            break
        if not _pid_exists(owner_pid):
            reason = "owner_exit"
            break
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "hard_deadline"
                break
            time.sleep(min(0.02, remaining))
        else:
            remaining = idle_deadline - time.monotonic()
            if remaining <= 0:
                reason = "idle_deadline"
                break
            time.sleep(min(0.02, remaining))

    registration_deadline = (
        deadline if deadline is not None else time.monotonic()
    )

    leader, grandchild, registration_errors = _await_target_registration(
        args.launch_armed_manifest,
        args.leader_manifest,
        args.grandchild_manifest,
        token=token,
        registration_deadline_monotonic=registration_deadline,
    )
    final_launch = _read_json(args.launch_armed_manifest)
    final_state = final_launch.get("state") if final_launch else None
    final_protocol_errors: list[str] = []
    if final_launch is not None and final_launch.get("token") != token:
        final_protocol_errors.append("final launch identity mismatch")
    if final_state == "disarmed_no_spawn" and candidate is not None:
        final_protocol_errors.append(
            "verified spawn candidate coexists with disarmed_no_spawn"
        )
    final_observation = _read_json(args.spawn_observed)
    if candidate is not None:
        final_identity = {
            key: final_observation.get(key) if final_observation else None
            for key in ("token", "owner_pid", "pid", "pgid", "sid")
        }
        if final_identity != candidate:
            final_protocol_errors.append(
                "final spawn observation does not match acknowledged candidate"
            )
    if final_state == "disarmed_no_spawn" and candidate is None:
        pgid, pids, validation_errors = None, (), []
    elif candidate is None:
        pgid, pids = None, ()
        validation_errors = [
            "armed launch has no verified spawn observation/candidate"
        ] if final_state in {"armed", "registered"} else []
    else:
        pgid, pids, validation_errors = _validated_candidate_target(
            candidate,
            leader,
            grandchild,
            token=token,
            forbidden_pgids=(owner_pgid, watchdog_pgid),
        )
    (
        group_gone,
        pids_gone,
        cleanup_errors,
        cleanup_observations,
    ) = _kill_target_group(pgid, pids)
    errors = [
        *control_errors,
        *final_protocol_errors,
        *registration_errors,
        *validation_errors,
        *cleanup_errors,
    ]
    clean = group_gone and pids_gone and not errors
    _atomic_write_json(
        args.result,
        {
            "token": token,
            "role": "watchdog",
            "pid": watchdog_pid,
            "pgid": watchdog_pgid,
            "reason": reason,
            "idle_deadline_monotonic": idle_deadline,
            "arm_deadline_monotonic": deadline or 0.0,
            "target_pgid": pgid or 0,
            "target_pids": list(pids),
            "target_group_gone": group_gone,
            "target_pids_gone": pids_gone,
            "clean": clean,
            "errors": errors,
            "observations": cleanup_observations,
            "finished_monotonic": time.monotonic(),
        },
    )
    return 0 if clean else 70


def _path(value: str) -> Path:
    return Path(value).resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trusted POSIX process fixture for local execution tests"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    workload = commands.add_parser("workload")
    workload.add_argument("--mode", choices=WORKLOAD_MODES, required=True)
    workload.add_argument("--token", required=True)
    workload.add_argument("--leader-manifest", type=_path, required=True)
    workload.add_argument("--grandchild-manifest", type=_path, required=True)
    workload.add_argument("--launch-armed-manifest", type=_path, required=True)
    workload.add_argument("--arm-ack-manifest", type=_path, required=True)
    workload.add_argument("--spawn-ack-manifest", type=_path, required=True)
    workload.add_argument("--marker", type=_path, required=True)
    workload.add_argument("--port", type=int, default=0)
    workload.add_argument("--fake-secret", default="SEC_EXEC_FAKE_NOT_A_SECRET")
    workload.add_argument("--output-chars", type=int, default=25_000)
    workload.add_argument("--tick-seconds", type=float, default=0.05)

    grandchild = commands.add_parser("grandchild")
    grandchild.add_argument("--token", required=True)
    grandchild.add_argument("--leader-pid", type=int, required=True)
    grandchild.add_argument("--expected-pgid", type=int, required=True)
    grandchild.add_argument("--grandchild-manifest", type=_path, required=True)
    grandchild.add_argument("--marker", type=_path, required=True)
    grandchild.add_argument("--port", type=int, default=0)
    grandchild.add_argument("--tick-seconds", type=float, default=0.05)
    grandchild.add_argument("--startup-delay-seconds", type=float, default=0.0)
    grandchild.add_argument("--start-gate-fd", type=int, required=True)
    grandchild.add_argument("--listen", action="store_true")

    watchdog = commands.add_parser("watchdog")
    watchdog.add_argument("--token", required=True)
    watchdog.add_argument("--leader-manifest", type=_path, required=True)
    watchdog.add_argument("--grandchild-manifest", type=_path, required=True)
    watchdog.add_argument("--launch-armed-manifest", type=_path, required=True)
    watchdog.add_argument("--arm-ack", type=_path, required=True)
    watchdog.add_argument("--spawn-observed", type=_path, required=True)
    watchdog.add_argument("--spawn-ack", type=_path, required=True)
    watchdog.add_argument("--control", type=_path, required=True)
    watchdog.add_argument("--ready", type=_path, required=True)
    watchdog.add_argument("--result", type=_path, required=True)
    watchdog.add_argument("--owner-pid", type=int, required=True)
    watchdog.add_argument("--owner-pgid", type=int, required=True)
    watchdog.add_argument("--hard-deadline-seconds", type=float, required=True)
    watchdog.add_argument("--registration-wait-seconds", type=float, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "workload":
        return _workload(args)
    if args.command == "grandchild":
        return _grandchild(args)
    if args.command == "watchdog":
        return _watchdog(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"fixture error: {exc}\n")
        raise SystemExit(74) from exc
