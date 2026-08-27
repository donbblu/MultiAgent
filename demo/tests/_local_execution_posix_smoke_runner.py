"""Private, fail-closed runner for one reviewed POSIX smoke test.

This module is inert when imported.  Its real ``main`` is reserved for a
separately authorized invocation after artifact review; the checked-in safety
tests exercise only injected, non-boundary seams.
"""

from __future__ import annotations

import _imp
import hashlib
import json
import os
import platform
import re
import signal
import stat
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Callable,
    ContextManager,
    FrozenSet,
    Iterator,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
)


FROZEN_EXECUTABLE = (
    "/Library/Developer/CommandLineTools/Library/Frameworks/"
    "Python3.framework/Versions/3.9/bin/python3.9"
)
FROZEN_IMPLEMENTATION = "CPython"
FROZEN_VERSION = (3, 9, 6)
FROZEN_PATH = (
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
)
FROZEN_LOCALE = "C.UTF-8"
BOOTSTRAP_DIRECTORY = "/private/tmp"
HARD_TIMEOUT_SECONDS = 25.0

CASE_ENV = "SEC_EXEC_POSIX_SMOKE_CASE"
RUN_ID_ENV = "SEC_EXEC_POSIX_SMOKE_RUN_ID"
RUNNER_HASH_ENV = "SEC_EXEC_POSIX_SMOKE_RUNNER_SHA256"
CF_USER_TEXT_ENCODING_ENV = "__CF_USER_TEXT_ENCODING"
FROZEN_CF_USER_TEXT_ENCODING = "0x1F5:0x19:0x34"
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

RUNNER_PATH = Path(__file__).resolve()
DEMO_ROOT = RUNNER_PATH.parent.parent
SMOKE_PATH = RUNNER_PATH.with_name("test_local_execution_posix_smoke.py")
HELPER_PATH = RUNNER_PATH.with_name("_local_execution_posix.py")
FIXTURE_PATH = RUNNER_PATH.parent / "fixtures" / "local_execution_process.py"
SAFETY_PATH = RUNNER_PATH.with_name("test_local_execution_posix_safety.py")

EXPECTED_DEPENDENCY_HASHES = {
    "smoke": "bca89a4f92d329477927972a58f1f3ac7139940e53fb79edfbceb5322812d44f",
    "helper": "a87ed9f82e93877cb473f7c47120a2e73cc18fc75c82e3437c8878f75b002999",
    "fixture": "80ecd65de830f5d61c3e2e9a1dd6948e8207cada79001a418910b57330d206d8",
    "safety": "266b8a328d79af523465355905618ad969ae6ae39c3bf92910e0055f9d149bdd",
}
ARTIFACT_PATHS = {
    "runner": RUNNER_PATH,
    "smoke": SMOKE_PATH,
    "helper": HELPER_PATH,
    "fixture": FIXTURE_PATH,
    "safety": SAFETY_PATH,
}
EXPECTED_ENV_KEYS = frozenset({
    "PATH",
    "LANG",
    "LC_ALL",
    "HOME",
    "TMPDIR",
    CASE_ENV,
    RUN_ID_ENV,
    RUNNER_HASH_ENV,
    CF_USER_TEXT_ENCODING_ENV,
})
_LOWER_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_SCOPE_PREFIX = "sec-exec-posix-smoke-"
PASS_RECEIPT_NAME = "pass-receipt.json"
RUN_MODE = "run"
VERIFY_CLEAN_MODE = "verify-clean"
_SCOPE_IDENTITY_KEYS = frozenset({
    "root",
    "home",
    "tmp",
    "logs",
    "stdout",
    "stderr",
})
_SCOPE_DIRECTORY_KEYS = frozenset({"root", "home", "tmp", "logs"})
_SCOPE_LOG_KEYS = frozenset({"stdout", "stderr"})


class RunnerRejected(RuntimeError):
    """The invocation cannot safely produce an execution receipt."""


@dataclass(frozen=True)
class RuntimeSnapshot:
    argv: Tuple[str, ...]
    environ: Mapping[str, str]
    executable: str
    implementation: str
    version: Tuple[int, int, int]
    isolated: int
    dont_write_bytecode: int
    check_hash_based_pycs: str
    debug: int
    inspect: int
    interactive: int
    optimize: int
    no_user_site: int
    no_site: int
    ignore_environment: int
    verbose: int
    bytes_warning: int
    quiet: int
    hash_randomization: int
    dev_mode: bool
    utf8_mode: int
    warnoptions: Tuple[str, ...]
    xoptions: Mapping[str, object]
    loaded_tests_modules: Tuple[str, ...]
    stdout_write_through: bool
    stderr_write_through: bool
    cwd: Path
    self_path: Path
    alarm_handler: object
    alarm_timer: Tuple[float, float]
    blocked_signals: FrozenSet[int]
    tempfile_tempdir: Optional[str]


@dataclass(frozen=True)
class RunRequest:
    case_name: str
    test_id: str
    run_id: str
    runner_hash: str
    mode: str = RUN_MODE
    scope_root: Optional[Path] = None


@dataclass(frozen=True)
class RecordingOutcome:
    tests_run: int
    started_ids: Tuple[str, ...]
    success_ids: Tuple[str, ...]
    skipped: int
    failures: int
    errors: int
    expected_failures: int
    unexpected_successes: int


@dataclass(frozen=True)
class NodeIdentity:
    kind: str
    device: int
    inode: int
    uid: int
    mode: int


@dataclass(frozen=True)
class ScopeLayout:
    root: Path
    home: Path
    tmp: Path
    logs: Path
    stdout_log: Path
    stderr_log: Path
    pass_receipt: Path
    identities: Mapping[str, NodeIdentity]


@dataclass(frozen=True)
class ScopeSnapshot:
    identities: Mapping[str, NodeIdentity]
    entries: Mapping[str, Tuple[str, ...]]
    log_sizes: Mapping[str, int]


@dataclass(frozen=True)
class RetainedScopeSnapshot:
    scope: ScopeLayout
    receipt_identity: NodeIdentity
    entries: Mapping[str, Tuple[str, ...]]
    log_sizes: Mapping[str, int]
    receipt_bytes: bytes


@dataclass(frozen=True)
class VerifiedRetainedScope:
    root_identity: NodeIdentity
    receipt_sha256: str


@dataclass(frozen=True)
class ExecutionSeams:
    create_scope: Callable[[RunRequest], ScopeLayout]
    announce_scope: Callable[[ScopeLayout, RunRequest], None]
    arm_alarm: Callable[[], None]
    isolate_output: Callable[[ScopeLayout], ContextManager[None]]
    run_test: Callable[[str], RecordingOutcome]
    read_hashes: Callable[[], Mapping[str, str]]
    inspect_scope: Callable[[ScopeLayout], ScopeSnapshot]
    persist_receipt: Callable[[ScopeLayout, bytes], None]
    pipe_buf: Callable[[], int]
    emit_receipt: Callable[[bytes], None]


def _validate_runtime(snapshot: RuntimeSnapshot) -> RunRequest:
    environment = dict(snapshot.environ)
    if frozenset(environment) != EXPECTED_ENV_KEYS:
        raise RunnerRejected("environment keys are not the exact frozen set")
    expected_values = {
        "PATH": FROZEN_PATH,
        "LANG": FROZEN_LOCALE,
        "LC_ALL": FROZEN_LOCALE,
        "HOME": BOOTSTRAP_DIRECTORY,
        "TMPDIR": BOOTSTRAP_DIRECTORY,
        CF_USER_TEXT_ENCODING_ENV: FROZEN_CF_USER_TEXT_ENCODING,
    }
    for name, expected in expected_values.items():
        if environment.get(name) != expected:
            raise RunnerRejected(f"environment value rejected: {name}")

    case_name = environment.get(CASE_ENV)
    if case_name not in TEST_IDS:
        raise RunnerRejected("unknown smoke case")
    run_id = environment.get(RUN_ID_ENV)
    if not isinstance(run_id, str) or _LOWER_HEX_32.fullmatch(run_id) is None:
        raise RunnerRejected("run ID must be 32 lowercase hexadecimal characters")
    runner_hash = environment.get(RUNNER_HASH_ENV)
    if (
        not isinstance(runner_hash, str)
        or _LOWER_HEX_64.fullmatch(runner_hash) is None
    ):
        raise RunnerRejected("runner hash must be 64 lowercase hexadecimal characters")

    if snapshot.self_path != RUNNER_PATH:
        raise RunnerRejected("runner self path is not the checked-in path")
    if not snapshot.argv or snapshot.argv[0] != str(RUNNER_PATH):
        raise RunnerRejected("raw argv0 is not the checked-in runner path")
    run_argv = (str(RUNNER_PATH), TEST_IDS[case_name])
    mode = RUN_MODE
    scope_root: Optional[Path] = None
    if snapshot.argv != run_argv:
        if len(snapshot.argv) != 3 or snapshot.argv[1] != "--verify-clean":
            raise RunnerRejected("raw argv is not an exact runner mode")
        candidate = Path(snapshot.argv[2])
        expected_prefix = f"{_SCOPE_PREFIX}{run_id}-"
        if (
            not candidate.is_absolute()
            or candidate.parent != Path(BOOTSTRAP_DIRECTORY)
            or not candidate.name.startswith(expected_prefix)
            or str(candidate) != snapshot.argv[2]
        ):
            raise RunnerRejected("verify-clean root is not an exact owned scope")
        suffix = candidate.name[len(expected_prefix):]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", suffix) is None:
            raise RunnerRejected("verify-clean root suffix is not canonical")
        mode = VERIFY_CLEAN_MODE
        scope_root = candidate
    if snapshot.cwd != DEMO_ROOT:
        raise RunnerRejected("working directory is not the demo root")
    if snapshot.executable != FROZEN_EXECUTABLE:
        raise RunnerRejected("interpreter path is not frozen")
    if snapshot.implementation != FROZEN_IMPLEMENTATION:
        raise RunnerRejected("interpreter implementation is not frozen")
    if snapshot.version != FROZEN_VERSION:
        raise RunnerRejected("interpreter version is not frozen")
    if snapshot.isolated != 1:
        raise RunnerRejected("isolated mode (-I) is required")
    if snapshot.dont_write_bytecode != 1:
        raise RunnerRejected("bytecode suppression (-B) is required")
    if (
        type(snapshot.check_hash_based_pycs) is not str
        or snapshot.check_hash_based_pycs != "default"
    ):
        raise RunnerRejected("hash-based pyc policy must be exact default")
    expected_material_flags = {
        "debug": 0,
        "inspect": 0,
        "interactive": 0,
        "optimize": 0,
        "no_user_site": 1,
        "no_site": 0,
        "ignore_environment": 1,
        "verbose": 0,
        "bytes_warning": 0,
        "quiet": 0,
        "hash_randomization": 1,
        "utf8_mode": 0,
    }
    for name, expected in expected_material_flags.items():
        value = getattr(snapshot, name)
        if type(value) is not int or value != expected:
            raise RunnerRejected(f"material interpreter flag rejected: {name}")
    if snapshot.dev_mode is not False:
        raise RunnerRejected("material interpreter flag rejected: dev_mode")
    if snapshot.warnoptions != ():
        raise RunnerRejected("interpreter warning options must be empty")
    if dict(snapshot.xoptions) != {}:
        raise RunnerRejected("interpreter -X options must be empty")
    if snapshot.loaded_tests_modules != ():
        raise RunnerRejected("tests modules must not be loaded before preflight")
    if snapshot.stdout_write_through is not True:
        raise RunnerRejected("unbuffered stdout (-u) is required")
    if snapshot.stderr_write_through is not True:
        raise RunnerRejected("unbuffered stderr (-u) is required")
    if snapshot.alarm_handler != signal.SIG_DFL:
        raise RunnerRejected("SIGALRM must start with SIG_DFL")
    if snapshot.alarm_timer != (0.0, 0.0):
        raise RunnerRejected("ITIMER_REAL must be inactive")
    if signal.SIGALRM in snapshot.blocked_signals:
        raise RunnerRejected("SIGALRM must not be blocked")
    if snapshot.tempfile_tempdir is not None:
        raise RunnerRejected("tempfile default directory was already initialized")

    return RunRequest(
        case_name=case_name,
        test_id=TEST_IDS[case_name],
        run_id=run_id,
        runner_hash=runner_hash,
        mode=mode,
        scope_root=scope_root,
    )


def _validate_artifact_hashes(
    hashes: Mapping[str, str],
    request: RunRequest,
) -> dict[str, str]:
    actual = dict(hashes)
    if frozenset(actual) != frozenset(ARTIFACT_PATHS):
        raise RunnerRejected("artifact hash keys are not the exact frozen set")
    for name, digest in actual.items():
        if not isinstance(digest, str) or _LOWER_HEX_64.fullmatch(digest) is None:
            raise RunnerRejected(f"malformed artifact hash: {name}")
    if actual["runner"] != request.runner_hash:
        raise RunnerRejected("runner self hash does not match the environment")
    for name, expected in EXPECTED_DEPENDENCY_HASHES.items():
        if actual[name] != expected:
            raise RunnerRejected(f"frozen dependency hash mismatch: {name}")
    return actual


def _validate_post_hashes(
    before: Mapping[str, str],
    after: Mapping[str, str],
) -> None:
    if dict(after) != dict(before):
        raise RunnerRejected("artifact hashes changed during the run")


def _dispatch(
    snapshot: RuntimeSnapshot,
    read_hashes: Callable[[], Mapping[str, str]],
    execute: Callable[[RunRequest, dict[str, str]], int],
) -> int:
    request = _validate_runtime(snapshot)
    hashes = _validate_artifact_hashes(read_hashes(), request)
    return execute(request, hashes)


def _arm_hard_alarm(signal_api: object = signal) -> None:
    signal_api.signal(signal_api.SIGALRM, signal_api.SIG_DFL)
    signal_api.pthread_sigmask(
        signal_api.SIG_UNBLOCK,
        {signal_api.SIGALRM},
    )
    signal_api.setitimer(
        signal_api.ITIMER_REAL,
        HARD_TIMEOUT_SECONDS,
        0.0,
    )


def _validate_recording_outcome(
    outcome: RecordingOutcome,
    expected_test_id: str,
) -> None:
    expected_identity = (expected_test_id,)
    if type(outcome.tests_run) is not int or outcome.tests_run != 1:
        raise RunnerRejected("exactly one test must run")
    if outcome.started_ids != expected_identity:
        raise RunnerRejected("started test IDs are not exact")
    if outcome.success_ids != expected_identity:
        raise RunnerRejected("successful test IDs are not exact")
    counts = (
        outcome.skipped,
        outcome.failures,
        outcome.errors,
        outcome.expected_failures,
        outcome.unexpected_successes,
    )
    if any(type(count) is not int for count in counts):
        raise RunnerRejected("outcome counts must be integers")
    if counts != (0, 0, 0, 0, 0):
        raise RunnerRejected("skip/failure/error/non-success outcome rejected")


def _validate_owned_scope_path(request: RunRequest, root: Path) -> None:
    bootstrap = Path(BOOTSTRAP_DIRECTORY)
    expected_prefix = f"{_SCOPE_PREFIX}{request.run_id}-"
    if not root.is_absolute() or root.parent != bootstrap:
        raise RunnerRejected("scope root escaped the frozen bootstrap directory")
    if not root.name.startswith(expected_prefix):
        raise RunnerRejected("scope root name is not owned by this runner")
    suffix = root.name[len(expected_prefix):]
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", suffix) is None:
        raise RunnerRejected("scope root suffix is not canonical")


def _validate_node_identity(identity: NodeIdentity, label: str) -> None:
    if identity.kind not in {"directory", "file"}:
        raise RunnerRejected(f"scope identity kind rejected: {label}")
    for name, value, minimum in (
        ("device", identity.device, 0),
        ("inode", identity.inode, 1),
        ("uid", identity.uid, 0),
        ("mode", identity.mode, 0),
    ):
        if type(value) is not int or value < minimum:
            raise RunnerRejected(f"scope identity {name} rejected: {label}")
    if identity.mode > 0o7777:
        raise RunnerRejected(f"scope identity mode rejected: {label}")


def _validate_empty_scope_for_receipt(
    request: RunRequest,
    scope: ScopeLayout,
    snapshot: ScopeSnapshot,
) -> None:
    if request.mode != RUN_MODE or request.scope_root is not None:
        raise RunnerRejected("retained receipt requires run mode")
    _validate_owned_scope_path(request, scope.root)
    expected_paths = {
        "home": scope.root / "home",
        "tmp": scope.root / "tmp",
        "logs": scope.root / "logs",
        "stdout": scope.root / "logs" / "test.stdout.log",
        "stderr": scope.root / "logs" / "test.stderr.log",
        "pass_receipt": scope.root / PASS_RECEIPT_NAME,
    }
    for name, expected_path in expected_paths.items():
        if name in {"stdout", "stderr"}:
            attribute = f"{name}_log"
        else:
            attribute = name
        if getattr(scope, attribute) != expected_path:
            raise RunnerRejected(f"scope path topology mismatch: {name}")

    if frozenset(scope.identities) != _SCOPE_IDENTITY_KEYS:
        raise RunnerRejected("captured scope identities are incomplete")
    if frozenset(snapshot.identities) != _SCOPE_IDENTITY_KEYS:
        raise RunnerRejected("observed scope identities are incomplete")
    if dict(snapshot.identities) != dict(scope.identities):
        raise RunnerRejected("scope identity drift detected")
    if frozenset(snapshot.entries) != _SCOPE_DIRECTORY_KEYS:
        raise RunnerRejected("scope directory observations are incomplete")
    if frozenset(snapshot.log_sizes) != _SCOPE_LOG_KEYS:
        raise RunnerRejected("scope log observations are incomplete")

    for name, identity in snapshot.identities.items():
        _validate_node_identity(identity, name)

    for name in _SCOPE_DIRECTORY_KEYS:
        identity = snapshot.identities[name]
        if identity.kind != "directory" or identity.mode != 0o700:
            raise RunnerRejected(f"unsafe scope directory identity: {name}")
    for name in _SCOPE_LOG_KEYS:
        identity = snapshot.identities[name]
        if identity.kind != "file" or identity.mode != 0o600:
            raise RunnerRejected(f"unsafe scope log identity: {name}")
    if any(identity.uid != os.getuid() for identity in snapshot.identities.values()):
        raise RunnerRejected("scope ownership drift detected")
    root_identity = snapshot.identities["root"]
    if any(
        identity.device != root_identity.device
        for identity in snapshot.identities.values()
    ):
        raise RunnerRejected("scope crossed a filesystem boundary")
    inode_keys = {
        (identity.device, identity.inode)
        for identity in snapshot.identities.values()
    }
    if len(inode_keys) != len(snapshot.identities):
        raise RunnerRejected("scope node identities are not unique")

    expected_entries = {
        "root": ("home", "logs", "tmp"),
        "home": (),
        "tmp": (),
        "logs": ("test.stderr.log", "test.stdout.log"),
    }
    if dict(snapshot.entries) != expected_entries:
        raise RunnerRejected("scope contains unknown or target artifacts")
    if any(type(size) is not int for size in snapshot.log_sizes.values()):
        raise RunnerRejected("scope log sizes must be integers")
    if dict(snapshot.log_sizes) != {"stdout": 0, "stderr": 0}:
        raise RunnerRejected("test output logs are not empty")


def _successful_outcome_for(request: RunRequest) -> RecordingOutcome:
    return RecordingOutcome(
        tests_run=1,
        started_ids=(request.test_id,),
        success_ids=(request.test_id,),
        skipped=0,
        failures=0,
        errors=0,
        expected_failures=0,
        unexpected_successes=0,
    )


def _validate_retained_scope(
    request: RunRequest,
    retained: RetainedScopeSnapshot,
) -> VerifiedRetainedScope:
    if request.mode != VERIFY_CLEAN_MODE or request.scope_root is None:
        raise RunnerRejected("scope cleanup requires verify-clean mode")
    scope = retained.scope
    if scope.root != request.scope_root:
        raise RunnerRejected("verify-clean scope does not match raw argv")
    _validate_owned_scope_path(request, scope.root)
    expected_paths = {
        "home": scope.root / "home",
        "tmp": scope.root / "tmp",
        "logs": scope.root / "logs",
        "stdout_log": scope.root / "logs" / "test.stdout.log",
        "stderr_log": scope.root / "logs" / "test.stderr.log",
        "pass_receipt": scope.root / PASS_RECEIPT_NAME,
    }
    for attribute, expected_path in expected_paths.items():
        if getattr(scope, attribute) != expected_path:
            raise RunnerRejected(f"retained scope topology mismatch: {attribute}")
    if frozenset(scope.identities) != _SCOPE_IDENTITY_KEYS:
        raise RunnerRejected("retained scope identities are incomplete")
    if frozenset(retained.entries) != _SCOPE_DIRECTORY_KEYS:
        raise RunnerRejected("retained directory observations are incomplete")
    if frozenset(retained.log_sizes) != _SCOPE_LOG_KEYS:
        raise RunnerRejected("retained log observations are incomplete")

    identities = dict(scope.identities)
    for name, identity in identities.items():
        _validate_node_identity(identity, name)
    for name in _SCOPE_DIRECTORY_KEYS:
        identity = identities[name]
        if identity.kind != "directory" or identity.mode != 0o700:
            raise RunnerRejected(f"unsafe retained directory identity: {name}")
    for name in _SCOPE_LOG_KEYS:
        identity = identities[name]
        if identity.kind != "file" or identity.mode != 0o600:
            raise RunnerRejected(f"unsafe retained log identity: {name}")
    receipt_identity = retained.receipt_identity
    _validate_node_identity(receipt_identity, "receipt")
    if receipt_identity.kind != "file" or receipt_identity.mode != 0o600:
        raise RunnerRejected("unsafe retained receipt identity")
    all_identities = (*identities.values(), receipt_identity)
    if any(identity.uid != os.getuid() for identity in all_identities):
        raise RunnerRejected("retained scope ownership drift detected")
    root_identity = identities["root"]
    if any(identity.device != root_identity.device for identity in all_identities):
        raise RunnerRejected("retained scope crossed a filesystem boundary")
    inode_keys = {(identity.device, identity.inode) for identity in all_identities}
    if len(inode_keys) != len(all_identities):
        raise RunnerRejected("retained scope node identities are not unique")

    expected_entries = {
        "root": ("home", "logs", PASS_RECEIPT_NAME, "tmp"),
        "home": (),
        "tmp": (),
        "logs": ("test.stderr.log", "test.stdout.log"),
    }
    if dict(retained.entries) != expected_entries:
        raise RunnerRejected("retained scope contains unknown or missing entries")
    if any(type(size) is not int for size in retained.log_sizes.values()):
        raise RunnerRejected("retained log sizes must be integers")
    if dict(retained.log_sizes) != {"stdout": 0, "stderr": 0}:
        raise RunnerRejected("retained output logs are not empty")

    if type(retained.receipt_bytes) is not bytes:
        raise RunnerRejected("retained PASS receipt is not exact bytes")
    expected_receipt = _canonical_bytes(
        _receipt_payload(
            request,
            _successful_outcome_for(request),
            root_identity,
        )
    )
    if len(expected_receipt) >= 512:
        raise RunnerRejected("retained PASS receipt exceeds the frozen bound")
    if retained.receipt_bytes != expected_receipt:
        raise RunnerRejected("retained PASS receipt is not exact and canonical")

    return VerifiedRetainedScope(
        root_identity=root_identity,
        receipt_sha256=hashlib.sha256(retained.receipt_bytes).hexdigest(),
    )


def _receipt_payload(
    request: RunRequest,
    outcome: RecordingOutcome,
    root_identity: Optional[NodeIdentity] = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "case": request.case_name,
        "errors": outcome.errors,
        "expected_failures": outcome.expected_failures,
        "failures": outcome.failures,
        "post_hash": True,
        "runner_sha256": request.runner_hash,
        "run_id": request.run_id,
        "schema": 2,
        "skipped": outcome.skipped,
        "status": "PASS_NO_TARGET_SCOPE_RETAINED",
        "test_id": request.test_id,
        "tests_run": outcome.tests_run,
        "unexpected_successes": outcome.unexpected_successes,
    }
    if root_identity is not None:
        payload.update({
            "root_device": root_identity.device,
            "root_inode": root_identity.inode,
            "root_uid": root_identity.uid,
        })
    return payload


def _cleanup_receipt_payload(
    request: RunRequest,
    verified: VerifiedRetainedScope,
) -> dict[str, object]:
    identity = verified.root_identity
    return {
        "case": request.case_name,
        "post_hash": True,
        "retained_receipt_sha256": verified.receipt_sha256,
        "root_device": identity.device,
        "root_inode": identity.inode,
        "root_uid": identity.uid,
        "runner_sha256": request.runner_hash,
        "run_id": request.run_id,
        "schema": 2,
        "status": "CLEANUP_COMPLETE",
        "test_id": request.test_id,
    }


def _encode_receipt(
    payload: Mapping[str, object],
    *,
    pipe_buf: int,
) -> bytes:
    if isinstance(pipe_buf, bool) or not isinstance(pipe_buf, int) or pipe_buf <= 1:
        raise RunnerRejected("PIPE_BUF is invalid")
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    if len(encoded) >= pipe_buf:
        raise RunnerRejected("canonical receipt is not smaller than PIPE_BUF")
    return encoded


def _execute_validated(
    request: RunRequest,
    initial_hashes: Mapping[str, str],
    seams: ExecutionSeams,
) -> int:
    baseline_hashes = dict(initial_hashes)
    scope = seams.create_scope(request)
    seams.announce_scope(scope, request)
    seams.arm_alarm()
    with seams.isolate_output(scope):
        outcome = seams.run_test(request.test_id)
    _validate_recording_outcome(outcome, request.test_id)
    final_hashes = dict(seams.read_hashes())
    _validate_post_hashes(baseline_hashes, final_hashes)
    snapshot = seams.inspect_scope(scope)
    _validate_empty_scope_for_receipt(request, scope, snapshot)
    receipt = _encode_receipt(
        _receipt_payload(request, outcome, scope.identities["root"]),
        pipe_buf=seams.pipe_buf(),
    )
    seams.persist_receipt(scope, receipt)
    # Stdout is deliberately the final operation.  If it fails or the timer
    # fires, the complete atomic PASS receipt and scope remain available.
    seams.emit_receipt(receipt)
    return 0


def _runtime_snapshot() -> RuntimeSnapshot:
    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    return RuntimeSnapshot(
        argv=tuple(sys.argv),
        environ=dict(os.environ),
        executable=sys.executable,
        implementation=platform.python_implementation(),
        version=(
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ),
        isolated=sys.flags.isolated,
        dont_write_bytecode=sys.flags.dont_write_bytecode,
        check_hash_based_pycs=_imp.check_hash_based_pycs,
        debug=sys.flags.debug,
        inspect=sys.flags.inspect,
        interactive=sys.flags.interactive,
        optimize=sys.flags.optimize,
        no_user_site=sys.flags.no_user_site,
        no_site=sys.flags.no_site,
        ignore_environment=sys.flags.ignore_environment,
        verbose=sys.flags.verbose,
        bytes_warning=sys.flags.bytes_warning,
        quiet=sys.flags.quiet,
        hash_randomization=sys.flags.hash_randomization,
        dev_mode=sys.flags.dev_mode,
        utf8_mode=sys.flags.utf8_mode,
        warnoptions=tuple(sys.warnoptions),
        xoptions=dict(sys._xoptions),
        loaded_tests_modules=tuple(sorted(
            name
            for name in sys.modules
            if name == "tests" or name.startswith("tests.")
        )),
        stdout_write_through=getattr(sys.stdout, "write_through", False),
        stderr_write_through=getattr(sys.stderr, "write_through", False),
        cwd=Path.cwd().resolve(),
        self_path=RUNNER_PATH,
        alarm_handler=signal.getsignal(signal.SIGALRM),
        alarm_timer=signal.getitimer(signal.ITIMER_REAL),
        blocked_signals=frozenset(blocked),
        tempfile_tempdir=tempfile.tempdir,
    )


def _read_regular_owned_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise RunnerRejected(f"cannot open artifact for hashing: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RunnerRejected(f"artifact is not a regular file: {path.name}")
        if before.st_uid != os.getuid():
            raise RunnerRejected(f"artifact is not owned by this user: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 131072)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_after != identity_before:
            raise RunnerRejected(f"artifact changed while hashing: {path.name}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _hash_regular_owned_file(path: Path) -> str:
    return hashlib.sha256(_read_regular_owned_bytes(path)).hexdigest()


def _read_artifact_hashes() -> dict[str, str]:
    return {
        name: _hash_regular_owned_file(path)
        for name, path in ARTIFACT_PATHS.items()
    }


class _RecordingResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.started_ids: list[str] = []
        self.success_ids: list[str] = []

    def startTest(self, test: unittest.case.TestCase) -> None:
        self.started_ids.append(test.id())
        super().startTest(test)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        self.success_ids.append(test.id())
        super().addSuccess(test)


def _recording_outcome(result: _RecordingResult) -> RecordingOutcome:
    return RecordingOutcome(
        tests_run=result.testsRun,
        started_ids=tuple(result.started_ids),
        success_ids=tuple(result.success_ids),
        skipped=len(result.skipped),
        failures=len(result.failures),
        errors=len(result.errors),
        expected_failures=len(result.expectedFailures),
        unexpected_successes=len(result.unexpectedSuccesses),
    )


def _loaded_test_ids(test: unittest.TestSuite) -> Tuple[str, ...]:
    collected: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, unittest.TestSuite):
            for child in node:
                visit(child)
            return
        identifier = getattr(node, "id", None)
        if not callable(identifier):
            raise RunnerRejected("loader returned an object without a test ID")
        value = identifier()
        if not isinstance(value, str):
            raise RunnerRejected("loader returned a non-string test ID")
        collected.append(value)

    visit(test)
    return tuple(collected)


def _validate_loaded_suite(
    suite: unittest.TestSuite,
    expected_test_id: str,
) -> None:
    if suite.countTestCases() != 1:
        raise RunnerRejected("loader did not resolve exactly one test")
    if _loaded_test_ids(suite) != (expected_test_id,):
        raise RunnerRejected("loader resolved an unexpected test ID")


def _execute_frozen_source_modules(
    source_bytes: Mapping[str, bytes],
    expected_hashes: Mapping[str, str],
    modules: MutableMapping[str, object],
    *,
    compile_source: Callable[..., object] = compile,
) -> types.ModuleType:
    if any(name == "tests" or name.startswith("tests.") for name in modules):
        raise RunnerRejected("tests namespace was loaded before controlled exec")
    expected_keys = frozenset({"helper", "smoke"})
    if frozenset(source_bytes) != expected_keys:
        raise RunnerRejected("controlled source bytes are incomplete")
    if frozenset(expected_hashes) != expected_keys:
        raise RunnerRejected("controlled source hashes are incomplete")
    for name in expected_keys:
        source = source_bytes[name]
        digest = expected_hashes[name]
        if type(source) is not bytes:
            raise RunnerRejected(f"controlled source is not exact bytes: {name}")
        if hashlib.sha256(source).hexdigest() != digest:
            raise RunnerRejected(f"controlled source hash mismatch: {name}")

    tests_package = types.ModuleType("tests")
    tests_package.__package__ = "tests"
    # The two reviewed children are installed directly in ``modules``.  An
    # empty path prevents fallback to namespace or system-site package code.
    tests_package.__path__ = ()
    tests_package.__loader__ = None
    tests_package.__spec__ = None
    modules["tests"] = tests_package
    created = ["tests"]
    smoke_module: Optional[types.ModuleType] = None
    module_specs = (
        (
            "helper",
            "tests._local_execution_posix",
            HELPER_PATH,
        ),
        (
            "smoke",
            "tests.test_local_execution_posix_smoke",
            SMOKE_PATH,
        ),
    )
    try:
        for artifact_name, module_name, path in module_specs:
            source = source_bytes[artifact_name]
            code = compile_source(
                source,
                str(path),
                "exec",
                dont_inherit=True,
                optimize=0,
            )
            if not isinstance(code, types.CodeType):
                raise RunnerRejected("controlled compiler returned a non-code object")
            if code.co_filename != str(path):
                raise RunnerRejected("controlled compiler changed the source identity")
            module = types.ModuleType(module_name)
            module.__file__ = str(path)
            module.__package__ = "tests"
            module.__loader__ = None
            module.__cached__ = None
            module.__spec__ = None
            modules[module_name] = module
            created.append(module_name)
            exec(code, module.__dict__)
            if (
                module.__name__ != module_name
                or module.__file__ != str(path)
                or module.__package__ != "tests"
                or module.__loader__ is not None
                or module.__cached__ is not None
                or module.__spec__ is not None
            ):
                raise RunnerRejected("controlled module metadata drifted")
            if artifact_name == "smoke":
                smoke_module = module
        if (
            modules.get("tests") is not tests_package
            or tests_package.__name__ != "tests"
            or tests_package.__package__ != "tests"
            or tests_package.__path__ != ()
            or tests_package.__loader__ is not None
            or tests_package.__spec__ is not None
        ):
            raise RunnerRejected("controlled tests package metadata drifted")
    except BaseException:
        for name in reversed(created):
            modules.pop(name, None)
        raise
    if smoke_module is None:
        raise RunnerRejected("controlled smoke module was not executed")
    return smoke_module


def _run_exact_test(test_id: str) -> RecordingOutcome:
    sources = {
        "helper": _read_regular_owned_bytes(HELPER_PATH),
        "smoke": _read_regular_owned_bytes(SMOKE_PATH),
    }
    expected = {
        "helper": EXPECTED_DEPENDENCY_HASHES["helper"],
        "smoke": EXPECTED_DEPENDENCY_HASHES["smoke"],
    }
    module = _execute_frozen_source_modules(sources, expected, sys.modules)
    prefix = f"{module.__name__}."
    if not test_id.startswith(prefix):
        raise RunnerRejected("test ID does not belong to the controlled smoke module")
    suite = unittest.defaultTestLoader.loadTestsFromName(
        test_id[len(prefix):],
        module,
    )
    _validate_loaded_suite(suite, test_id)
    result = _RecordingResult()
    suite.run(result)
    return _recording_outcome(result)


def _identity_from_stat(info: os.stat_result) -> NodeIdentity:
    if stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    else:
        kind = "other"
    return NodeIdentity(
        kind=kind,
        device=info.st_dev,
        inode=info.st_ino,
        uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
    )


def _identity_at(path: Path) -> NodeIdentity:
    try:
        return _identity_from_stat(os.lstat(str(path)))
    except OSError as exc:
        raise RunnerRejected(f"cannot inspect scope node: {path.name}") from exc


def _create_exclusive_log(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise RunnerRejected(f"cannot create scope log: {path.name}") from exc
    else:
        os.close(descriptor)
    os.chmod(str(path), 0o600)


def _create_scope(request: RunRequest) -> ScopeLayout:
    bootstrap = Path(BOOTSTRAP_DIRECTORY)
    bootstrap_identity = _identity_at(bootstrap)
    if bootstrap_identity.kind != "directory":
        raise RunnerRejected("bootstrap directory is not a directory")
    root = Path(
        tempfile.mkdtemp(
            prefix=f"{_SCOPE_PREFIX}{request.run_id}-",
            dir=BOOTSTRAP_DIRECTORY,
        )
    )
    os.chmod(str(root), 0o700)
    if not root.is_absolute() or root.parent != bootstrap:
        raise RunnerRejected("created scope escaped the bootstrap directory")
    home = root / "home"
    tmp = root / "tmp"
    logs = root / "logs"
    stdout_log = logs / "test.stdout.log"
    stderr_log = logs / "test.stderr.log"
    pass_receipt = root / PASS_RECEIPT_NAME
    for directory in (home, tmp, logs):
        directory.mkdir(mode=0o700)
        os.chmod(str(directory), 0o700)
    _create_exclusive_log(stdout_log)
    _create_exclusive_log(stderr_log)
    paths = {
        "root": root,
        "home": home,
        "tmp": tmp,
        "logs": logs,
        "stdout": stdout_log,
        "stderr": stderr_log,
    }
    identities = {name: _identity_at(path) for name, path in paths.items()}
    expected_kinds = {
        "root": ("directory", 0o700),
        "home": ("directory", 0o700),
        "tmp": ("directory", 0o700),
        "logs": ("directory", 0o700),
        "stdout": ("file", 0o600),
        "stderr": ("file", 0o600),
    }
    for name, identity in identities.items():
        kind, mode = expected_kinds[name]
        if (
            identity.kind != kind
            or identity.mode != mode
            or identity.uid != os.getuid()
        ):
            raise RunnerRejected(f"unsafe created scope identity: {name}")
    os.environ["HOME"] = str(home)
    os.environ["TMPDIR"] = str(tmp)
    tempfile.tempdir = str(tmp)
    return ScopeLayout(
        root=root,
        home=home,
        tmp=tmp,
        logs=logs,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        pass_receipt=pass_receipt,
        identities=identities,
    )


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _write_one(descriptor: int, payload: bytes) -> None:
    try:
        written = os.write(descriptor, payload)
    except OSError as exc:
        raise RunnerRejected("fd-level write failed") from exc
    if written != len(payload):
        raise RunnerRejected("fd-level write was partial")


def _announce_scope(scope: ScopeLayout, request: RunRequest) -> None:
    root_identity = scope.identities["root"]
    announcement = _canonical_bytes({
        "device": root_identity.device,
        "event": "scope-created",
        "inode": root_identity.inode,
        "kind": root_identity.kind,
        "mode": format(root_identity.mode, "04o"),
        "root": str(scope.root),
        "run_id": request.run_id,
        "uid": root_identity.uid,
    })
    if len(announcement) >= 512:
        raise RunnerRejected("scope announcement is unexpectedly large")
    _write_one(2, announcement)


def _open_verified_log(path: Path, expected: NodeIdentity) -> int:
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise RunnerRejected(f"cannot open isolated output log: {path.name}") from exc
    observed_stat = os.fstat(descriptor)
    observed = _identity_from_stat(observed_stat)
    if observed != expected or observed_stat.st_size != 0:
        os.close(descriptor)
        raise RunnerRejected(f"isolated output log identity drift: {path.name}")
    return descriptor


@contextmanager
def _isolated_test_output(scope: ScopeLayout) -> Iterator[None]:
    stdout_log = _open_verified_log(
        scope.stdout_log,
        scope.identities["stdout"],
    )
    stderr_log = _open_verified_log(
        scope.stderr_log,
        scope.identities["stderr"],
    )
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(stdout_log, 1)
        os.dup2(stderr_log, 2)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
    finally:
        os.close(saved_stderr)
        os.close(saved_stdout)
        os.close(stderr_log)
        os.close(stdout_log)


def _capture_scope(scope: ScopeLayout) -> ScopeSnapshot:
    paths = {
        "root": scope.root,
        "home": scope.home,
        "tmp": scope.tmp,
        "logs": scope.logs,
        "stdout": scope.stdout_log,
        "stderr": scope.stderr_log,
    }
    identities = {name: _identity_at(path) for name, path in paths.items()}
    entries = {
        name: tuple(sorted(os.listdir(str(paths[name]))))
        for name in _SCOPE_DIRECTORY_KEYS
    }
    try:
        log_sizes = {
            "stdout": os.lstat(str(scope.stdout_log)).st_size,
            "stderr": os.lstat(str(scope.stderr_log)).st_size,
        }
    except OSError as exc:
        raise RunnerRejected("cannot inspect isolated output log size") from exc
    return ScopeSnapshot(
        identities=identities,
        entries=entries,
        log_sizes=log_sizes,
    )


def _require_dirfd_receipt_support() -> None:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RunnerRejected("required receipt publication flags are unavailable")
    if any(
        operation not in os.supports_dir_fd
        for operation in (os.open, os.link, os.stat, os.unlink)
    ):
        raise RunnerRejected("required dirfd receipt operation is unavailable")
    if any(
        operation not in os.supports_follow_symlinks
        for operation in (os.link, os.stat)
    ):
        raise RunnerRejected("no-follow receipt publication is unavailable")


def _persist_atomic_pass_receipt(scope: ScopeLayout, payload: bytes) -> None:
    if scope.pass_receipt != scope.root / PASS_RECEIPT_NAME:
        raise RunnerRejected("pass receipt path is not exact")
    if type(payload) is not bytes or not payload or len(payload) >= 512:
        raise RunnerRejected("pass receipt bytes exceed the frozen atomic bound")
    _require_dirfd_receipt_support()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(str(scope.root), directory_flags)
    except OSError as exc:
        raise RunnerRejected("cannot open scope for receipt persistence") from exc
    try:
        if (
            _identity_from_stat(os.fstat(directory_descriptor))
            != scope.identities["root"]
        ):
            raise RunnerRejected("scope root drifted before receipt persistence")
        temporary_name = f".{PASS_RECEIPT_NAME}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except OSError as exc:
            raise RunnerRejected("cannot create atomic pass receipt") from exc
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise RunnerRejected("atomic pass receipt write was incomplete")
                offset += written
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            temporary_stat = os.fstat(descriptor)
            temporary_identity = _identity_from_stat(temporary_stat)
            _validate_node_identity(temporary_identity, "temporary receipt")
            if (
                temporary_identity.kind != "file"
                or temporary_identity.mode != 0o600
                or temporary_identity.uid != os.getuid()
                or temporary_identity.device
                != scope.identities["root"].device
                or temporary_stat.st_size != len(payload)
            ):
                raise RunnerRejected("temporary pass receipt identity rejected")
            if (
                _identity_at_fd_entry(
                    directory_descriptor,
                    temporary_name,
                )
                != temporary_identity
            ):
                raise RunnerRejected("temporary pass receipt name drifted")
            try:
                os.link(
                    temporary_name,
                    PASS_RECEIPT_NAME,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise RunnerRejected("cannot publish atomic pass receipt") from exc

            try:
                receipt_descriptor = os.open(
                    PASS_RECEIPT_NAME,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise RunnerRejected("cannot bind published pass receipt") from exc
            try:
                receipt_stat = os.fstat(receipt_descriptor)
                if (
                    _identity_from_stat(receipt_stat) != temporary_identity
                    or receipt_stat.st_size != len(payload)
                    or _stable_file_bytes(
                        receipt_descriptor,
                        PASS_RECEIPT_NAME,
                        maximum_size=511,
                    )
                    != payload
                ):
                    raise RunnerRejected("published pass receipt diverged")
                if (
                    _identity_from_stat(os.fstat(directory_descriptor))
                    != scope.identities["root"]
                ):
                    raise RunnerRejected(
                        "scope root drifted during receipt persistence"
                    )
                os.fsync(directory_descriptor)
                os.unlink(temporary_name, dir_fd=directory_descriptor)
                os.fsync(directory_descriptor)
                if (
                    _identity_at_fd_entry(
                        directory_descriptor,
                        PASS_RECEIPT_NAME,
                    )
                    != temporary_identity
                    or _stable_file_bytes(
                        receipt_descriptor,
                        PASS_RECEIPT_NAME,
                        maximum_size=511,
                    )
                    != payload
                ):
                    raise RunnerRejected("durable pass receipt drifted")
            finally:
                os.close(receipt_descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def _require_dirfd_cleanup_support() -> None:
    for operation in (os.open, os.stat, os.unlink, os.rmdir):
        if operation not in os.supports_dir_fd:
            raise RunnerRejected("required dirfd cleanup operation is unavailable")
    if os.stat not in os.supports_follow_symlinks:
        raise RunnerRejected("no-follow dirfd stat is unavailable")
    if os.listdir not in os.supports_fd:
        raise RunnerRejected("directory-fd listing is unavailable")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RunnerRejected("required no-follow directory flags are unavailable")


def _open_directory_fd(
    path: str,
    *,
    parent_fd: Optional[int] = None,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        if parent_fd is None:
            descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise RunnerRejected(f"cannot open retained directory: {path}") from exc
    if _identity_from_stat(os.fstat(descriptor)).kind != "directory":
        os.close(descriptor)
        raise RunnerRejected(f"retained node is not a directory: {path}")
    return descriptor


def _open_regular_fd(name: str, *, parent_fd: int) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise RunnerRejected(f"cannot open retained file: {name}") from exc
    if _identity_from_stat(os.fstat(descriptor)).kind != "file":
        os.close(descriptor)
        raise RunnerRejected(f"retained node is not a regular file: {name}")
    return descriptor


def _stable_directory_entries(descriptor: int, label: str) -> Tuple[str, ...]:
    before = os.fstat(descriptor)
    try:
        entries = tuple(sorted(os.listdir(descriptor)))
    except OSError as exc:
        raise RunnerRejected(f"cannot list retained directory: {label}") from exc
    after = os.fstat(descriptor)
    before_key = (before.st_dev, before.st_ino, before.st_mtime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_mtime_ns)
    if before_key != after_key:
        raise RunnerRejected(f"retained directory changed while listing: {label}")
    return entries


def _stable_file_bytes(
    descriptor: int,
    label: str,
    *,
    maximum_size: int,
) -> bytes:
    before = os.fstat(descriptor)
    if before.st_size < 0 or before.st_size > maximum_size:
        raise RunnerRejected(f"retained file size rejected: {label}")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = before.st_size + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(remaining, 131072))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    after = os.fstat(descriptor)
    before_key = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_key = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    payload = b"".join(chunks)
    if before_key != after_key or len(payload) != before.st_size:
        raise RunnerRejected(f"retained file changed while reading: {label}")
    return payload


def _identity_at_fd_entry(parent_fd: int, name: str) -> NodeIdentity:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RunnerRejected(f"cannot inspect retained entry: {name}") from exc
    return _identity_from_stat(info)


def _require_bound_entry(
    parent_fd: int,
    name: str,
    descriptor: int,
    expected: NodeIdentity,
) -> None:
    if _identity_from_stat(os.fstat(descriptor)) != expected:
        raise RunnerRejected(f"open retained entry drifted: {name}")
    if _identity_at_fd_entry(parent_fd, name) != expected:
        raise RunnerRejected(f"named retained entry drifted: {name}")


def _unlink_known_entry(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError as exc:
        raise RunnerRejected(f"cannot unlink retained entry: {name}") from exc


def _rmdir_known_entry(parent_fd: int, name: str) -> None:
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError as exc:
        raise RunnerRejected(f"cannot remove retained directory: {name}") from exc


def _verify_and_delete_scope(request: RunRequest) -> VerifiedRetainedScope:
    """Validate an exact retained PASS scope, then remove only known entries.

    This is the sole real verify-clean filesystem boundary.  Safety tests call
    it only with every low-level filesystem operation replaced by pure fakes.
    """

    if request.mode != VERIFY_CLEAN_MODE or request.scope_root is None:
        raise RunnerRejected("verify-clean request required")
    _validate_owned_scope_path(request, request.scope_root)
    _require_dirfd_cleanup_support()

    descriptors: dict[str, int] = {}

    def remember(name: str, descriptor: int) -> int:
        descriptors[name] = descriptor
        return descriptor

    def close_one(name: str) -> None:
        descriptor = descriptors.pop(name, None)
        if descriptor is not None:
            os.close(descriptor)

    try:
        bootstrap_fd = remember(
            "bootstrap",
            _open_directory_fd(BOOTSTRAP_DIRECTORY),
        )
        root_fd = remember(
            "root",
            _open_directory_fd(request.scope_root.name, parent_fd=bootstrap_fd),
        )
        home_fd = remember("home", _open_directory_fd("home", parent_fd=root_fd))
        tmp_fd = remember("tmp", _open_directory_fd("tmp", parent_fd=root_fd))
        logs_fd = remember("logs", _open_directory_fd("logs", parent_fd=root_fd))
        stdout_fd = remember(
            "stdout",
            _open_regular_fd("test.stdout.log", parent_fd=logs_fd),
        )
        stderr_fd = remember(
            "stderr",
            _open_regular_fd("test.stderr.log", parent_fd=logs_fd),
        )
        receipt_fd = remember(
            "receipt",
            _open_regular_fd(PASS_RECEIPT_NAME, parent_fd=root_fd),
        )

        identities = {
            name: _identity_from_stat(os.fstat(descriptors[name]))
            for name in _SCOPE_IDENTITY_KEYS
        }
        scope = ScopeLayout(
            root=request.scope_root,
            home=request.scope_root / "home",
            tmp=request.scope_root / "tmp",
            logs=request.scope_root / "logs",
            stdout_log=request.scope_root / "logs" / "test.stdout.log",
            stderr_log=request.scope_root / "logs" / "test.stderr.log",
            pass_receipt=request.scope_root / PASS_RECEIPT_NAME,
            identities=identities,
        )
        retained = RetainedScopeSnapshot(
            scope=scope,
            receipt_identity=_identity_from_stat(os.fstat(receipt_fd)),
            entries={
                "root": _stable_directory_entries(root_fd, "root"),
                "home": _stable_directory_entries(home_fd, "home"),
                "tmp": _stable_directory_entries(tmp_fd, "tmp"),
                "logs": _stable_directory_entries(logs_fd, "logs"),
            },
            log_sizes={
                "stdout": os.fstat(stdout_fd).st_size,
                "stderr": os.fstat(stderr_fd).st_size,
            },
            receipt_bytes=_stable_file_bytes(
                receipt_fd,
                PASS_RECEIPT_NAME,
                maximum_size=4096,
            ),
        )
        verified = _validate_retained_scope(request, retained)

        if _stable_directory_entries(logs_fd, "logs") != (
            "test.stderr.log",
            "test.stdout.log",
        ):
            raise RunnerRejected("retained logs changed before cleanup")
        for key, name in (
            ("stdout", "test.stdout.log"),
            ("stderr", "test.stderr.log"),
        ):
            descriptor = descriptors[key]
            _require_bound_entry(logs_fd, name, descriptor, identities[key])
            if _stable_file_bytes(
                descriptor,
                name,
                maximum_size=0,
            ) != b"":
                raise RunnerRejected(f"retained log changed before unlink: {name}")
            _require_bound_entry(logs_fd, name, descriptor, identities[key])
            _unlink_known_entry(logs_fd, name)
            close_one(key)
        if _stable_directory_entries(logs_fd, "logs") != ():
            raise RunnerRejected("retained logs are not empty after known unlinks")
        _require_bound_entry(root_fd, "logs", logs_fd, identities["logs"])
        _rmdir_known_entry(root_fd, "logs")
        close_one("logs")

        for key in ("home", "tmp"):
            descriptor = descriptors[key]
            if _stable_directory_entries(descriptor, key) != ():
                raise RunnerRejected(f"retained directory is not empty: {key}")
            _require_bound_entry(root_fd, key, descriptor, identities[key])
            _rmdir_known_entry(root_fd, key)
            close_one(key)

        expected_before_receipt = (PASS_RECEIPT_NAME,)
        if _stable_directory_entries(root_fd, "root") != expected_before_receipt:
            raise RunnerRejected("retained root changed before receipt unlink")
        if _stable_file_bytes(
            receipt_fd,
            PASS_RECEIPT_NAME,
            maximum_size=4096,
        ) != retained.receipt_bytes:
            raise RunnerRejected("retained PASS receipt changed before cleanup")
        _require_bound_entry(
            root_fd,
            PASS_RECEIPT_NAME,
            receipt_fd,
            retained.receipt_identity,
        )
        _unlink_known_entry(root_fd, PASS_RECEIPT_NAME)
        close_one("receipt")

        if _stable_directory_entries(root_fd, "root") != ():
            raise RunnerRejected("retained root is not empty after known unlinks")
        if _identity_from_stat(os.fstat(root_fd)) != verified.root_identity:
            raise RunnerRejected("open retained root drifted before removal")
        if (
            _identity_at_fd_entry(bootstrap_fd, request.scope_root.name)
            != verified.root_identity
        ):
            raise RunnerRejected("named retained root drifted before removal")
        _rmdir_known_entry(bootstrap_fd, request.scope_root.name)
        close_one("root")
        return verified
    finally:
        for descriptor in reversed(tuple(descriptors.values())):
            os.close(descriptor)


def _stdout_pipe_buf() -> int:
    try:
        value = os.fpathconf(1, "PC_PIPE_BUF")
    except (OSError, ValueError) as exc:
        raise RunnerRejected("cannot obtain stdout PIPE_BUF") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value <= 1:
        raise RunnerRejected("stdout PIPE_BUF is invalid")
    return value


def _emit_receipt(payload: bytes) -> None:
    _write_one(1, payload)


def _real_execution_seams() -> ExecutionSeams:
    return ExecutionSeams(
        create_scope=_create_scope,
        announce_scope=_announce_scope,
        arm_alarm=_arm_hard_alarm,
        isolate_output=_isolated_test_output,
        run_test=_run_exact_test,
        read_hashes=_read_artifact_hashes,
        inspect_scope=_capture_scope,
        persist_receipt=_persist_atomic_pass_receipt,
        pipe_buf=_stdout_pipe_buf,
        emit_receipt=_emit_receipt,
    )


def _execute_request(
    request: RunRequest,
    initial_hashes: Mapping[str, str],
) -> int:
    if request.mode == RUN_MODE:
        return _execute_validated(
            request,
            initial_hashes,
            _real_execution_seams(),
        )
    if request.mode != VERIFY_CLEAN_MODE:
        raise RunnerRejected("unknown runner mode")
    _arm_hard_alarm()
    _validate_post_hashes(initial_hashes, _read_artifact_hashes())
    verified = _verify_and_delete_scope(request)
    receipt = _encode_receipt(
        _cleanup_receipt_payload(request, verified),
        pipe_buf=_stdout_pipe_buf(),
    )
    # Cleanup is intentionally complete before this one-write audit receipt.
    # A final write failure cannot restore the already removed retained scope.
    _emit_receipt(receipt)
    return 0


def _write_failure(exc: BaseException) -> None:
    message = str(exc).replace("\n", " ")[:240]
    payload = _canonical_bytes({
        "error": type(exc).__name__,
        "event": "runner-failed",
        "message": message,
    })
    try:
        _write_one(2, payload)
    except RunnerRejected:
        pass


def main() -> int:
    try:
        return _dispatch(
            _runtime_snapshot(),
            _read_artifact_hashes,
            _execute_request,
        )
    except Exception as exc:
        _write_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
