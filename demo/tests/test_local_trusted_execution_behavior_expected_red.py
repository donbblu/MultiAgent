from __future__ import annotations

import ast
import asyncio
import _asyncio
import _posixsubprocess
import _socket
import _thread
import builtins
import contextlib
import functools
import hashlib
import io
import inspect
import json
import math
import os
import posix
import selectors
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from unittest import mock


_PRELOADED_PRODUCTION_MODULES = tuple(sorted(
    name for name in sys.modules
    if name == "coding_workflow" or name.startswith("coding_workflow.")
))
if _PRELOADED_PRODUCTION_MODULES:
    raise RuntimeError(
        "SEC-EXEC behavior red card requires a fresh dedicated interpreter; "
        "production modules were already loaded: "
        f"{_PRELOADED_PRODUCTION_MODULES}"
    )


_ACTIVE_AUDIT_HITS: list[str] | None = None
_AUDIT_ALLOWANCE = threading.local()
_AUDITED_BOUNDARY_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.spawn",
    "os.exec",
    "os.kill",
    "os.killpg",
    "pty.spawn",
    "signal.pthread_kill",
    "socket.__new__",
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "socket.getnameinfo",
    "socket.sendto",
    "socket.sendmsg",
}


def _fail_closed_audit_hook(event: str, args: tuple[object, ...]) -> None:
    """Process-lifetime backstop for C-level and pre-bound aliases.

    The red card never needs a real process, signal, or network operation.  An
    audit hook cannot be removed by production code, so a cached alias or a
    freshly imported low-level module cannot silently escape the Python mocks.
    """
    if (
        event == "socket.__new__"
        and getattr(_AUDIT_ALLOWANCE, "asyncio_socketpair", False)
    ):
        family = args[1] if len(args) > 1 else None
        if family == socket.AF_UNIX:
            return
    if event not in _AUDITED_BOUNDARY_EVENTS:
        return
    if _ACTIVE_AUDIT_HITS is not None:
        _ACTIVE_AUDIT_HITS.append(f"audit:{event}")
    raise AssertionError(f"blocked audited process/signal/network boundary: {event}")


sys.addaudithook(_fail_closed_audit_hook)


def _reject_import_time_boundary(label: str):
    def reject(*_args, **_kwargs):
        raise AssertionError(
            f"blocked production import-time process/network boundary: {label}"
        )

    return reject


_IMPORT_REAL_POPEN = subprocess.Popen
_IMPORT_REAL_RUN = subprocess.run
_IMPORT_REAL_SOCKET = socket.socket
_IMPORT_REAL_CREATE_CONNECTION = socket.create_connection
_IMPORT_REAL_GETADDRINFO = socket.getaddrinfo
_IMPORT_REAL_SOCKETPAIR = socket.socketpair
_IMPORT_REAL_FROMFD = socket.fromfd
_IMPORT_REAL_CREATE_SERVER = getattr(socket, "create_server", None)
_IMPORT_REAL_URLOPEN = urllib.request.urlopen
_IMPORT_REAL_OPENER_OPEN = urllib.request.OpenerDirector.open
_IMPORT_TRAP_POPEN = _reject_import_time_boundary("Popen")
_IMPORT_TRAP_RUN = _reject_import_time_boundary("run")
_IMPORT_TRAP_SOCKET_CALL = _reject_import_time_boundary("socket")
_IMPORT_TRAP_CONNECT = _reject_import_time_boundary("socket.connect")
_IMPORT_TRAP_CONNECT_EX = _reject_import_time_boundary("socket.connect_ex")
_IMPORT_TRAP_BIND = _reject_import_time_boundary("socket.bind")
_IMPORT_TRAP_LISTEN = _reject_import_time_boundary("socket.listen")


class _ImportSocketTripwire:
    def __call__(self, *args, **kwargs):
        return _IMPORT_TRAP_SOCKET_CALL(*args, **kwargs)

    def connect(self, *args, **kwargs):
        return _IMPORT_TRAP_CONNECT(*args, **kwargs)

    def connect_ex(self, *args, **kwargs):
        return _IMPORT_TRAP_CONNECT_EX(*args, **kwargs)

    def bind(self, *args, **kwargs):
        return _IMPORT_TRAP_BIND(*args, **kwargs)

    def listen(self, *args, **kwargs):
        return _IMPORT_TRAP_LISTEN(*args, **kwargs)


_IMPORT_TRAP_SOCKET = _ImportSocketTripwire()
_IMPORT_TRAP_CREATE_CONNECTION = _reject_import_time_boundary(
    "create_connection"
)
_IMPORT_TRAP_GETADDRINFO = _reject_import_time_boundary("getaddrinfo")
_IMPORT_TRAP_SOCKETPAIR = _reject_import_time_boundary("socketpair")
_IMPORT_TRAP_FROMFD = _reject_import_time_boundary("fromfd")
_IMPORT_TRAP_CREATE_SERVER = _reject_import_time_boundary("create_server")
_IMPORT_TRAP_URLOPEN = _reject_import_time_boundary("urlopen")
_IMPORT_TRAP_OPENER_OPEN = _reject_import_time_boundary("urllib-opener")
_IMPORT_REAL_THREAD_START = threading.Thread.start
_IMPORT_REAL_LOW_THREAD_START = _thread.start_new_thread
_IMPORT_REAL_LOW_THREAD_START_ALIAS = _thread.start_new
_IMPORT_REAL_THREADING_LOW_START = threading._start_new_thread
_IMPORT_TRAP_THREAD_START = _reject_import_time_boundary("Thread.start")
_IMPORT_TRAP_LOW_THREAD_START = _reject_import_time_boundary(
    "_thread.start_new_thread"
)
_IMPORT_REAL_ASYNCIO_CREATE_TASK = asyncio.create_task
_IMPORT_REAL_TASKS_CREATE_TASK = asyncio.tasks.create_task
_IMPORT_REAL_ASYNCIO_ENSURE_FUTURE = asyncio.ensure_future
_IMPORT_REAL_TASKS_ENSURE_FUTURE = asyncio.tasks.ensure_future
_IMPORT_REAL_LOOP_CREATE_TASK = asyncio.BaseEventLoop.create_task
_IMPORT_REAL_ASYNCIO_TASK = asyncio.Task
_IMPORT_REAL_TASKS_TASK = asyncio.tasks.Task
_IMPORT_TRAP_ASYNCIO_CREATE_TASK = _reject_import_time_boundary(
    "asyncio.create_task"
)
_IMPORT_TRAP_TASKS_CREATE_TASK = _reject_import_time_boundary(
    "asyncio.tasks.create_task"
)
_IMPORT_TRAP_ASYNCIO_ENSURE_FUTURE = _reject_import_time_boundary(
    "asyncio.ensure_future"
)
_IMPORT_TRAP_TASKS_ENSURE_FUTURE = _reject_import_time_boundary(
    "asyncio.tasks.ensure_future"
)
_IMPORT_TRAP_LOOP_CREATE_TASK = _reject_import_time_boundary(
    "BaseEventLoop.create_task"
)
_IMPORT_TRAP_TASK_CONSTRUCTOR = _reject_import_time_boundary(
    "asyncio.Task"
)
_LOOP_SCHEDULING_NAMES = (
    "call_soon",
    "call_later",
    "call_at",
    "call_soon_threadsafe",
    "run_in_executor",
)
_IMPORT_REAL_LOOP_SCHEDULING = {
    name: getattr(asyncio.BaseEventLoop, name)
    for name in _LOOP_SCHEDULING_NAMES
}
_IMPORT_TRAP_LOOP_SCHEDULING = {
    name: _reject_import_time_boundary(f"BaseEventLoop.{name}")
    for name in _LOOP_SCHEDULING_NAMES
}
_THREAD_START_PERMIT = threading.local()


def _suite_thread_start_router(function, args, kwargs=None):
    permitted_thread = getattr(_THREAD_START_PERMIT, "thread", None)
    if (
        permitted_thread is None
        or getattr(function, "__self__", None) is not permitted_thread
        or getattr(function, "__name__", "") != "_bootstrap"
    ):
        return _IMPORT_TRAP_LOW_THREAD_START(function, args, kwargs or {})
    if kwargs is None:
        return _IMPORT_REAL_THREADING_LOW_START(function, args)
    return _IMPORT_REAL_THREADING_LOW_START(function, args, kwargs)

_EXTRA_BOUNDARY_SPECS = tuple(
    (module, name, label)
    for module, names in (
        (subprocess, (
            "call", "check_call", "check_output", "getoutput",
            "getstatusoutput",
        )),
        (os, (
            "kill", "killpg", "posix_spawn", "posix_spawnp", "system",
            "popen", "fork", "forkpty", "spawnl", "spawnle", "spawnlp",
            "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
            "execl", "execle", "execlp", "execlpe", "execv", "execve",
            "execvp", "execvpe",
        )),
        (posix, (
            "kill", "killpg", "posix_spawn", "posix_spawnp", "system",
            "fork", "forkpty", "execv", "execve",
        )),
        (_posixsubprocess, ("fork_exec",)),
        (_socket, ("socket", "getaddrinfo", "socketpair")),
        (asyncio, ("create_subprocess_exec", "create_subprocess_shell")),
        (asyncio.BaseEventLoop, (
            "subprocess_exec", "subprocess_shell", "_make_subprocess_transport",
        )),
        (asyncio.SelectorEventLoop, ("_make_subprocess_transport",)),
        (asyncio.AbstractEventLoop, (
            "subprocess_exec", "subprocess_shell",
        )),
    )
    for name in names
    if hasattr(module, name)
    for label in (
        ("signal:" if module is os and name in {"kill", "killpg"} else "process:")
        + f"{module.__name__}.{name}",
    )
)
_IMPORT_EXTRA_REAL = {
    (module, name): getattr(module, name)
    for module, name, _ in _EXTRA_BOUNDARY_SPECS
}
_IMPORT_EXTRA_TRAPS = {
    (module, name): _reject_import_time_boundary(label)
    for module, name, label in _EXTRA_BOUNDARY_SPECS
}
_FAIL_CLOSED_TRIPWIRE_STACKS: list[contextlib.ExitStack] = []


def _install_suite_fail_closed_boundaries() -> None:
    """Leave no real boundary installed between methods in this interpreter."""
    subprocess.Popen = _IMPORT_TRAP_POPEN
    subprocess.run = _IMPORT_TRAP_RUN
    socket.socket = _IMPORT_TRAP_SOCKET
    socket.create_connection = _IMPORT_TRAP_CREATE_CONNECTION
    socket.getaddrinfo = _IMPORT_TRAP_GETADDRINFO
    socket.socketpair = _IMPORT_TRAP_SOCKETPAIR
    socket.fromfd = _IMPORT_TRAP_FROMFD
    if _IMPORT_REAL_CREATE_SERVER is not None:
        socket.create_server = _IMPORT_TRAP_CREATE_SERVER
    urllib.request.urlopen = _IMPORT_TRAP_URLOPEN
    urllib.request.OpenerDirector.open = _IMPORT_TRAP_OPENER_OPEN
    for (module, name), trap in _IMPORT_EXTRA_TRAPS.items():
        setattr(module, name, trap)
    threading.Thread.start = _IMPORT_TRAP_THREAD_START
    _thread.start_new_thread = _IMPORT_TRAP_LOW_THREAD_START
    _thread.start_new = _IMPORT_TRAP_LOW_THREAD_START
    threading._start_new_thread = _suite_thread_start_router
    asyncio.create_task = _IMPORT_TRAP_ASYNCIO_CREATE_TASK
    asyncio.tasks.create_task = _IMPORT_TRAP_TASKS_CREATE_TASK
    asyncio.ensure_future = _IMPORT_TRAP_ASYNCIO_ENSURE_FUTURE
    asyncio.tasks.ensure_future = _IMPORT_TRAP_TASKS_ENSURE_FUTURE
    asyncio.BaseEventLoop.create_task = _IMPORT_TRAP_LOOP_CREATE_TASK
    asyncio.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
    asyncio.tasks.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
    _asyncio.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
    for name, trap in _IMPORT_TRAP_LOOP_SCHEDULING.items():
        setattr(asyncio.BaseEventLoop, name, trap)

# Install before importing production modules so import-time aliases and lazy
# imports cannot bypass the same fail-closed boundary used by each test.
subprocess.Popen = _IMPORT_TRAP_POPEN
subprocess.run = _IMPORT_TRAP_RUN
socket.socket = _IMPORT_TRAP_SOCKET
socket.create_connection = _IMPORT_TRAP_CREATE_CONNECTION
socket.getaddrinfo = _IMPORT_TRAP_GETADDRINFO
socket.socketpair = _IMPORT_TRAP_SOCKETPAIR
socket.fromfd = _IMPORT_TRAP_FROMFD
if _IMPORT_REAL_CREATE_SERVER is not None:
    socket.create_server = _IMPORT_TRAP_CREATE_SERVER
urllib.request.urlopen = _IMPORT_TRAP_URLOPEN
urllib.request.OpenerDirector.open = _IMPORT_TRAP_OPENER_OPEN
for (_module, _name), _trap in _IMPORT_EXTRA_TRAPS.items():
    setattr(_module, _name, _trap)
threading.Thread.start = _IMPORT_TRAP_THREAD_START
_thread.start_new_thread = _IMPORT_TRAP_LOW_THREAD_START
_thread.start_new = _IMPORT_TRAP_LOW_THREAD_START
threading._start_new_thread = _IMPORT_TRAP_LOW_THREAD_START
asyncio.create_task = _IMPORT_TRAP_ASYNCIO_CREATE_TASK
asyncio.tasks.create_task = _IMPORT_TRAP_TASKS_CREATE_TASK
asyncio.ensure_future = _IMPORT_TRAP_ASYNCIO_ENSURE_FUTURE
asyncio.tasks.ensure_future = _IMPORT_TRAP_TASKS_ENSURE_FUTURE
asyncio.BaseEventLoop.create_task = _IMPORT_TRAP_LOOP_CREATE_TASK
asyncio.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
asyncio.tasks.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
_asyncio.Task = _IMPORT_TRAP_TASK_CONSTRUCTOR
for _name, _trap in _IMPORT_TRAP_LOOP_SCHEDULING.items():
    setattr(asyncio.BaseEventLoop, _name, _trap)

import coding_workflow
import coding_workflow.command_validators as command_module
import coding_workflow.visionforge.browser as browser_module
from coding_workflow import (
    Artifact,
    ArtifactStore,
    CommandPolicy,
    CommandVerificationAgent,
    ControlledCommandResult,
    ControlledCommandRunner,
    FileChange,
    ProjectWorkspace,
    TaskContext,
)
from coding_workflow.harness.lifecycle import TaskCancelledError
from coding_workflow.models import CommandResult
from coding_workflow.visionforge import (
    BrowserProcessRunner,
    BrowserProjectConfig,
    BrowserProjectRuntime,
    BrowserRuntimeError,
    ImageAssetStore,
    PlaywrightBrowserTester,
    ProcessExecution,
    UISpec,
)
from coding_workflow.workspace import WorkspaceError

# Production import completed without crossing a forbidden boundary.  Keep the
# suite-level traps installed until interpreter exit; each setUp only overlays
# tracked per-test fakes and therefore tears down to these traps, never to real
# process/network/scheduling primitives.
_install_suite_fail_closed_boundaries()


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "visionforge_vue_template"
FROZEN_VERSION = "local_trusted_execution/v1"
FROZEN_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
FAKE_SECRET = "SEC_EXEC_FAKE_NOT_A_SECRET_7f24"
PROFILE_IDS = {
    "core": "core_validator",
    "legacy": "legacy_workspace_verify",
    "visionforge_build": "visionforge_build",
    "visionforge_browser": "visionforge_browser",
    "visionforge_dev": "visionforge_dev",
}
_UNSET = object()
_AUTO_TRUSTED = object()
CORE_COMMAND = (
    "python3", "-m", "unittest", "discover", "-s", "tests", "-v",
)
LEGACY_COMMAND = ("python3", "-V")
BUILD_COMMAND = ("pnpm", "run", "build")
DEV_COMMAND = ("pnpm", "run", "dev", "--port", "4173")
TEST_ONLY_PROCESS_BOUNDARY_MANIFEST = {
    "tests/test_local_trusted_execution_behavior_expected_red.py": (),
    "tests/_local_execution_posix.py": (
        (
            "tests/_local_execution_posix.py",
            "subprocess.Popen",
            697,
            "__init__",
        ),
    ),
    "tests/fixtures/local_execution_process.py": (
        (
            "tests/fixtures/local_execution_process.py",
            "subprocess.Popen",
            345,
            "_workload",
        ),
    ),
    "tests/test_runtime_thread_event_store.py": (
        (
            "tests/test_runtime_thread_event_store.py",
            "subprocess.run",
            965,
            "_run_process_mutation",
        ),
    ),
    "tests/test_runtime_outbox_adversarial.py": (
        (
            "tests/test_runtime_outbox_adversarial.py",
            "subprocess.run",
            757,
            "run_process_mutation",
        ),
    ),
    "tests/test_runtime_sqlite_uow.py": (
        (
            "tests/test_runtime_sqlite_uow.py",
            "subprocess.run",
            1015,
            "run_crash_process",
        ),
    ),
}


class FakeProcess:
    """Small Popen stand-in; it never creates an operating-system process."""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        *,
        running: bool = False,
        communicate_effects: list[object] | None = None,
        wait_effects: list[object] | None = None,
        trace: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.pid = 424_242
        self._final_returncode = returncode
        self.returncode = None if running else returncode
        self.stdout = stdout
        self.stderr = stderr
        self.communicate_effects = list(communicate_effects or [])
        self.wait_effects = list(wait_effects or [])
        self.communicate_count = 0
        self.wait_count = 0
        self.terminate_count = 0
        self.kill_count = 0
        self.trace = trace

    def communicate(self, timeout: float | None = None):
        self.communicate_count += 1
        if self.trace is not None:
            self.trace.append(("communicate", self.pid, timeout))
        if self.communicate_effects:
            effect = self.communicate_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if isinstance(effect, tuple):
                self.returncode = self._final_returncode
                return effect
        self.returncode = self._final_returncode
        return self.stdout, self.stderr

    def poll(self):
        if self.trace is not None:
            self.trace.append(("poll", self.pid, self.returncode))
        return self.returncode

    def wait(self, timeout: float | None = None):
        self.wait_count += 1
        if self.trace is not None:
            self.trace.append(("wait", self.pid, timeout))
        if self.wait_effects:
            effect = self.wait_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if isinstance(effect, int):
                self.returncode = effect
                return effect
        self.returncode = self._final_returncode
        return self.returncode

    def terminate(self) -> None:
        self.terminate_count += 1
        if self.trace is not None:
            self.trace.append(("terminate", self.pid))

    def kill(self) -> None:
        self.kill_count += 1
        if self.trace is not None:
            self.trace.append(("kill", self.pid))
        self.returncode = -9


class FakePopenFactory:
    def __init__(self, process_factory=None, on_spawn=None) -> None:
        self.process_factory = process_factory or (lambda: FakeProcess())
        self.on_spawn = on_spawn
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, dict(kwargs)))
        if self.on_spawn is not None:
            self.on_spawn(args, kwargs)
        process = self.process_factory()
        self.processes.append(process)
        return process


class FakeRunFactory:
    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        *,
        on_spawn=None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.on_spawn = on_spawn
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, dict(kwargs)))
        if self.on_spawn is not None:
            self.on_spawn(args, kwargs)
        command = args[0] if args else kwargs.get("args", ())
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class FakeSignalFactory:
    """Records signals without touching an operating-system PID or PGID."""

    def __init__(
        self,
        *,
        probe_alive: bool = False,
        trace: list[tuple[object, ...]] | None = None,
        label: str = "signal",
    ) -> None:
        self.probe_alive = probe_alive
        self.trace = trace
        self.label = label
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, dict(kwargs)))
        target = args[0] if args else kwargs.get("pid", kwargs.get("pgid"))
        signal_value = args[1] if len(args) > 1 else kwargs.get("sig")
        if self.trace is not None:
            self.trace.append((self.label, target, signal_value))
        if signal_value == 0 and not self.probe_alive:
            raise ProcessLookupError("fake process/group is gone")
        return None


class FakeManaged:
    def __init__(
        self,
        *,
        running: bool = True,
        log_text: str = "",
        process: FakeProcess | None = None,
        killpg: FakeSignalFactory | None = None,
    ) -> None:
        self._running = running
        self._log_text = log_text
        self.process = process
        self.killpg = killpg
        self.stop_count = 0

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self.stop_count += 1
        if self.process is not None and self.killpg is not None:
            self.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=1)
            self.process.poll()
            try:
                self.killpg(self.process.pid, 0)
            except ProcessLookupError:
                pass
        self._running = False

    def log_tail(self, limit: int = 4000) -> str:
        return self._log_text[-limit:]


class ScriptedBrowserRunner:
    """Duck-typed Browser runner used only for path/output tests."""

    def __init__(self, results: list[ProcessExecution]) -> None:
        self.results = list(results)
        self.run_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.background_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.managed = FakeManaged()

    def run(self, *args, **kwargs):
        self.run_calls.append((args, dict(kwargs)))
        if not self.results:
            raise AssertionError("unexpected ScriptedBrowserRunner.run")
        return self.results.pop(0)

    def start_background(self, *args, **kwargs):
        self.background_calls.append((args, dict(kwargs)))
        return self.managed


class CancelImmediately:
    def checkpoint(self) -> None:
        raise TaskCancelledError("SEC-EXEC fake cancellation")


class LocalTrustedExecutionBehaviorExpectedRedTests(unittest.TestCase):
    """Full mock-only A-H behavioral card for SEC-EXEC-01.

    No test in this module runs a real subprocess, opens a network connection,
    loads .env, or uses a real secret.  Every entrypoint invocation is backed
    by FakeProcess/FakeRunFactory or a duck-typed browser runner.
    """

    def setUp(self) -> None:
        """Fail closed if any test escapes its explicit fake backends."""
        global _ACTIVE_AUDIT_HITS
        self._tripwire_hits: list[str] = []
        self._worker_threads: list[threading.Thread] = []
        self._explicit_test_threads: set[threading.Thread] = set()
        self._thread_errors: list[str] = []
        self._async_errors: list[str] = []
        self._closing_work_gate = False
        self._low_level_workers: list[tuple[str, threading.Event]] = []
        self._async_tasks: list[asyncio.Future] = []
        self._async_callbacks: list[
            tuple[str, object, threading.Event]
        ] = []
        self._async_socketpairs: list[object] = []
        self._pipe_records: list[dict[str, object]] = []
        self._baseline_threads = set(threading.enumerate())
        self._baseline_thread_frames = set(sys._current_frames())
        self._baseline_async_tasks = set(
            getattr(asyncio.tasks, "_all_tasks", ())
        )
        _ACTIVE_AUDIT_HITS = self._tripwire_hits
        self._real_popen = _IMPORT_REAL_POPEN
        self._real_run = _IMPORT_REAL_RUN
        self._real_socket = _IMPORT_REAL_SOCKET
        self._real_create_connection = _IMPORT_REAL_CREATE_CONNECTION
        self._real_getaddrinfo = _IMPORT_REAL_GETADDRINFO
        self._real_socketpair = _IMPORT_REAL_SOCKETPAIR
        self._real_fromfd = _IMPORT_REAL_FROMFD
        self._real_create_server = _IMPORT_REAL_CREATE_SERVER
        self._real_urlopen = _IMPORT_REAL_URLOPEN
        self._real_opener_open = _IMPORT_REAL_OPENER_OPEN
        self._real_thread_start = _IMPORT_REAL_THREAD_START
        self._real_low_thread_start = _IMPORT_REAL_LOW_THREAD_START
        self._real_asyncio_create_task = _IMPORT_REAL_ASYNCIO_CREATE_TASK
        self._real_tasks_create_task = _IMPORT_REAL_TASKS_CREATE_TASK
        self._real_asyncio_ensure_future = _IMPORT_REAL_ASYNCIO_ENSURE_FUTURE
        self._real_tasks_ensure_future = _IMPORT_REAL_TASKS_ENSURE_FUTURE
        self._real_loop_create_task = _IMPORT_REAL_LOOP_CREATE_TASK
        self._real_task_constructor = _IMPORT_REAL_ASYNCIO_TASK
        self._real_loop_scheduling = dict(_IMPORT_REAL_LOOP_SCHEDULING)
        self._real_thread_excepthook = threading.excepthook

        def tracked_thread_start(thread, *args, **kwargs):
            if self._closing_work_gate:
                self._tripwire_hits.append(
                    f"thread:start-after-closing:{thread.name}"
                )
                raise AssertionError("blocked thread start during tearDown")
            if thread not in self._worker_threads:
                self._worker_threads.append(thread)
            _THREAD_START_PERMIT.thread = thread
            try:
                return self._real_thread_start(thread, *args, **kwargs)
            finally:
                _THREAD_START_PERMIT.thread = None

        def tracked_thread_excepthook(args):
            self._thread_errors.append(
                f"{getattr(args.thread, 'name', '<unknown>')}:"
                f"{getattr(args.exc_type, '__name__', args.exc_type)}:"
                f"{args.exc_value}"
            )

        def tracked_low_thread_start(function, args, kwargs=None):
            finished = threading.Event()
            name = getattr(function, "__qualname__", repr(function))

            def tracked_target(*target_args, **target_kwargs):
                try:
                    return function(*target_args, **target_kwargs)
                except BaseException as exc:
                    self._thread_errors.append(
                        f"_thread:{name}:{type(exc).__name__}:{exc}"
                    )
                    return None
                finally:
                    finished.set()

            self._low_level_workers.append((name, finished))
            if kwargs is None:
                return self._real_low_thread_start(tracked_target, args)
            return self._real_low_thread_start(tracked_target, args, kwargs)

        def remember_task(task):
            if isinstance(task, asyncio.Future) and task not in self._async_tasks:
                self._async_tasks.append(task)
            return task

        def tracked_asyncio_create_task(*args, **kwargs):
            return remember_task(self._real_asyncio_create_task(*args, **kwargs))

        def tracked_tasks_create_task(*args, **kwargs):
            return remember_task(self._real_tasks_create_task(*args, **kwargs))

        def tracked_asyncio_ensure_future(*args, **kwargs):
            return remember_task(self._real_asyncio_ensure_future(*args, **kwargs))

        def tracked_tasks_ensure_future(*args, **kwargs):
            return remember_task(self._real_tasks_ensure_future(*args, **kwargs))

        def tracked_loop_create_task(loop, *args, **kwargs):
            return remember_task(
                self._real_loop_create_task(loop, *args, **kwargs)
            )

        def tracked_task_constructor(*args, **kwargs):
            return remember_task(self._real_task_constructor(*args, **kwargs))

        def tracked_schedule(name, original):
            def schedule(loop, callback, *args, **kwargs):
                completed = threading.Event()

                def tracked_callback(*callback_args):
                    try:
                        return callback(*callback_args)
                    except BaseException as exc:
                        self._async_errors.append(
                            f"{name}:{type(exc).__name__}:{exc}"
                        )
                        raise
                    finally:
                        completed.set()

                handle = original(
                    loop,
                    tracked_callback,
                    *args,
                    **kwargs,
                )
                self._async_callbacks.append((name, handle, completed))
                return handle

            return schedule

        def tracked_run_in_executor(loop, executor, function, *args):
            completed = threading.Event()

            def tracked_function(*function_args):
                try:
                    return function(*function_args)
                except BaseException as exc:
                    self._async_errors.append(
                        "run_in_executor:"
                        f"{type(exc).__name__}:{exc}"
                    )
                    raise
                finally:
                    completed.set()

            future = self._real_loop_scheduling["run_in_executor"](
                loop,
                executor,
                tracked_function,
                *args,
            )
            self._async_callbacks.append(
                ("run_in_executor", future, completed)
            )
            return remember_task(future)

        self._tracked_thread_start = tracked_thread_start
        self._tracked_thread_excepthook = tracked_thread_excepthook
        self._tracked_low_thread_start = tracked_low_thread_start
        self._tracked_asyncio_create_task = tracked_asyncio_create_task
        self._tracked_tasks_create_task = tracked_tasks_create_task
        self._tracked_asyncio_ensure_future = tracked_asyncio_ensure_future
        self._tracked_tasks_ensure_future = tracked_tasks_ensure_future
        self._tracked_loop_create_task = tracked_loop_create_task
        self._tracked_task_constructor = tracked_task_constructor
        self._tracked_loop_scheduling = {
            name: (
                tracked_run_in_executor
                if name == "run_in_executor"
                else tracked_schedule(name, original)
            )
            for name, original in self._real_loop_scheduling.items()
        }
        self._trap_popen = self._tripwire("process:Popen")
        self._trap_run = self._tripwire("process:run")
        self._trap_low_thread_start = self._tripwire(
            "thread:_thread.start_new_thread"
        )
        self._trap_async_task = self._tripwire("async:Task/create_task")
        self._trap_async_schedule = self._tripwire(
            "async:loop-scheduling"
        )
        trap_socket_call = self._tripwire("network:socket")
        trap_connect = self._tripwire("network:socket.connect")
        trap_connect_ex = self._tripwire("network:socket.connect_ex")
        trap_bind = self._tripwire("network:socket.bind")
        trap_listen = self._tripwire("network:socket.listen")

        class SocketTripwire:
            def __call__(self, *args, **kwargs):
                return trap_socket_call(*args, **kwargs)

            def connect(self, *args, **kwargs):
                return trap_connect(*args, **kwargs)

            def connect_ex(self, *args, **kwargs):
                return trap_connect_ex(*args, **kwargs)

            def bind(self, *args, **kwargs):
                return trap_bind(*args, **kwargs)

            def listen(self, *args, **kwargs):
                return trap_listen(*args, **kwargs)

        self._trap_socket = SocketTripwire()
        self._trap_create_connection = self._tripwire(
            "network:create_connection"
        )
        self._trap_getaddrinfo = self._tripwire("network:getaddrinfo")
        self._trap_socketpair = self._tripwire("network:socketpair")
        self._trap_fromfd = self._tripwire("network:fromfd")
        self._trap_create_server = self._tripwire("network:create_server")
        self._trap_urlopen = self._tripwire("network:urlopen")
        self._trap_opener_open = self._tripwire("network:urllib-opener")

        def tracked_async_socketpair(*args, **kwargs):
            caller = sys._getframe(1)
            caller_module = str(caller.f_globals.get("__name__", ""))
            if (
                not caller_module.startswith("asyncio.")
                or caller.f_code.co_name != "_make_self_pipe"
            ):
                return self._trap_socketpair(*args, **kwargs)
            family = (
                args[0]
                if args
                else kwargs.get("family", socket.AF_UNIX)
            )
            if family != socket.AF_UNIX:
                return self._trap_socketpair(*args, **kwargs)
            _AUDIT_ALLOWANCE.asyncio_socketpair = True
            try:
                pair = self._real_extra_boundaries[
                    (_socket, "socketpair")
                ](*args, **kwargs)
            finally:
                _AUDIT_ALLOWANCE.asyncio_socketpair = False
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or any(
                    getattr(endpoint, "family", None) != socket.AF_UNIX
                    for endpoint in pair
                )
            ):
                for endpoint in pair if isinstance(pair, tuple) else ():
                    close = getattr(endpoint, "close", None)
                    if callable(close):
                        close()
                raise AssertionError("asyncio socketpair was not AF_UNIX pair")
            self._async_socketpairs.extend(pair)
            return pair

        self._tracked_async_socketpair = tracked_async_socketpair
        self._real_extra_boundaries = {
            key: value for key, value in _IMPORT_EXTRA_REAL.items()
        }
        self._trap_extra_boundaries = {
            (module, name): self._tripwire(label)
            for module, name, label in _EXTRA_BOUNDARY_SPECS
        }
        self._tripwire_stack = contextlib.ExitStack()

        alias_replacements = {
            self._real_popen: self._trap_popen,
            self._real_run: self._trap_run,
            _IMPORT_TRAP_POPEN: self._trap_popen,
            _IMPORT_TRAP_RUN: self._trap_run,
        }
        alias_replacements.update({
            self._real_socket: self._trap_socket,
            self._real_create_connection: self._trap_create_connection,
            self._real_getaddrinfo: self._trap_getaddrinfo,
            self._real_socketpair: self._tracked_async_socketpair,
            self._real_fromfd: self._trap_fromfd,
            self._real_urlopen: self._trap_urlopen,
            self._real_opener_open: self._trap_opener_open,
            _IMPORT_TRAP_SOCKET: self._trap_socket,
            _IMPORT_TRAP_CREATE_CONNECTION: self._trap_create_connection,
            _IMPORT_TRAP_GETADDRINFO: self._trap_getaddrinfo,
            _IMPORT_TRAP_SOCKETPAIR: self._tracked_async_socketpair,
            _IMPORT_TRAP_FROMFD: self._trap_fromfd,
            _IMPORT_TRAP_URLOPEN: self._trap_urlopen,
            _IMPORT_TRAP_OPENER_OPEN: self._trap_opener_open,
        })
        if self._real_create_server is not None:
            alias_replacements.update({
                self._real_create_server: self._trap_create_server,
                _IMPORT_TRAP_CREATE_SERVER: self._trap_create_server,
            })
        alias_replacements.update({
            original: self._trap_extra_boundaries[key]
            for key, original in self._real_extra_boundaries.items()
        })
        alias_replacements.update({
            _IMPORT_EXTRA_TRAPS[key]: self._trap_extra_boundaries[key]
            for key in _IMPORT_EXTRA_TRAPS
        })
        self._tripwire_stack.enter_context(
            mock.patch.object(subprocess, "Popen", new=self._trap_popen)
        )
        self._tripwire_stack.enter_context(
            mock.patch.object(subprocess, "run", new=self._trap_run)
        )
        self._tripwire_stack.enter_context(
            mock.patch.object(socket, "socket", new=self._trap_socket)
        )
        self._tripwire_stack.enter_context(mock.patch.object(
            socket,
            "create_connection",
            new=self._trap_create_connection,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            socket,
            "getaddrinfo",
            new=self._trap_getaddrinfo,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            socket,
            "socketpair",
            new=self._tracked_async_socketpair,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            socket,
            "fromfd",
            new=self._trap_fromfd,
        ))
        if self._real_create_server is not None:
            self._tripwire_stack.enter_context(mock.patch.object(
                socket,
                "create_server",
                new=self._trap_create_server,
            ))
        self._tripwire_stack.enter_context(
            mock.patch.object(
                urllib.request,
                "urlopen",
                new=self._trap_urlopen,
            )
        )
        self._tripwire_stack.enter_context(mock.patch.object(
            urllib.request.OpenerDirector,
            "open",
            new=self._trap_opener_open,
        ))
        for (module, name), trap in self._trap_extra_boundaries.items():
            self._tripwire_stack.enter_context(
                mock.patch.object(module, name, new=trap)
            )
        alias_replacements.update({
            self._real_thread_start: self._tracked_thread_start,
            _IMPORT_TRAP_THREAD_START: self._tracked_thread_start,
            self._real_low_thread_start: self._tracked_low_thread_start,
            _IMPORT_TRAP_LOW_THREAD_START: self._tracked_low_thread_start,
        })
        alias_replacements.update({
            self._real_asyncio_create_task: self._tracked_asyncio_create_task,
            self._real_tasks_create_task: self._tracked_tasks_create_task,
            self._real_asyncio_ensure_future: self._tracked_asyncio_ensure_future,
            self._real_tasks_ensure_future: self._tracked_tasks_ensure_future,
            self._real_loop_create_task: self._tracked_loop_create_task,
            _IMPORT_TRAP_ASYNCIO_CREATE_TASK: self._tracked_asyncio_create_task,
            _IMPORT_TRAP_TASKS_CREATE_TASK: self._tracked_tasks_create_task,
            _IMPORT_TRAP_ASYNCIO_ENSURE_FUTURE: self._tracked_asyncio_ensure_future,
            _IMPORT_TRAP_TASKS_ENSURE_FUTURE: self._tracked_tasks_ensure_future,
            _IMPORT_TRAP_LOOP_CREATE_TASK: self._tracked_loop_create_task,
            _IMPORT_TRAP_TASK_CONSTRUCTOR: self._tracked_task_constructor,
        })
        alias_replacements.update({
            self._real_loop_scheduling[name]: self._tracked_loop_scheduling[name]
            for name in self._real_loop_scheduling
        })
        alias_replacements.update({
            _IMPORT_TRAP_LOOP_SCHEDULING[name]: self._tracked_loop_scheduling[name]
            for name in self._real_loop_scheduling
        })
        aliases = self._loaded_production_aliases(alias_replacements)
        for module, name, replacement in aliases:
            self._tripwire_stack.enter_context(
                self._patch_cached_alias(module, name, replacement)
            )
        self._tripwire_stack.enter_context(mock.patch.object(
            threading.Thread,
            "start",
            new=self._tracked_thread_start,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            threading,
            "excepthook",
            new=self._tracked_thread_excepthook,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            _thread,
            "start_new_thread",
            new=self._tracked_low_thread_start,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            _thread,
            "start_new",
            new=self._tracked_low_thread_start,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            asyncio,
            "create_task",
            new=self._tracked_asyncio_create_task,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            asyncio.tasks,
            "create_task",
            new=self._tracked_tasks_create_task,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            asyncio,
            "ensure_future",
            new=self._tracked_asyncio_ensure_future,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            asyncio.tasks,
            "ensure_future",
            new=self._tracked_tasks_ensure_future,
        ))
        self._tripwire_stack.enter_context(mock.patch.object(
            asyncio.BaseEventLoop,
            "create_task",
            new=self._tracked_loop_create_task,
        ))
        for owner in (asyncio, asyncio.tasks, _asyncio):
            self._tripwire_stack.enter_context(mock.patch.object(
                owner,
                "Task",
                new=self._tracked_task_constructor,
            ))
        for name, replacement in self._tracked_loop_scheduling.items():
            self._tripwire_stack.enter_context(mock.patch.object(
                asyncio.BaseEventLoop,
                name,
                new=replacement,
            ))

    def tearDown(self) -> None:
        global _ACTIVE_AUDIT_HITS
        self._closing_work_gate = True
        delta_threads = [
            thread for thread in threading.enumerate()
            if thread not in self._baseline_threads
            and thread is not threading.current_thread()
        ]
        for thread in delta_threads:
            if thread not in self._worker_threads:
                self._worker_threads.append(thread)
        for thread in dict.fromkeys(self._worker_threads):
            if thread.is_alive():
                thread.join(timeout=1.0)
        for _name, finished in self._low_level_workers:
            if not finished.is_set():
                finished.wait(timeout=1.0)
        alive = [
            thread.name for thread in dict.fromkeys(self._worker_threads)
            if thread.is_alive()
        ]
        alive.extend(
            f"_thread:{name}"
            for name, finished in self._low_level_workers
            if not finished.is_set()
        )
        extra_frame_ids = set(sys._current_frames()) - self._baseline_thread_frames
        known_idents = {thread.ident for thread in self._worker_threads}
        alive.extend(
            f"unregistered-thread-ident:{ident}"
            for ident in sorted(extra_frame_ids - known_idents)
        )
        all_new_tasks = set(self._async_tasks)
        all_new_tasks.update(
            set(getattr(asyncio.tasks, "_all_tasks", ()))
            - self._baseline_async_tasks
        )
        pending_tasks = [task for task in all_new_tasks if not task.done()]
        for task in all_new_tasks:
            if not task.done() or task.cancelled():
                continue
            try:
                task_error = task.exception()
            except BaseException as exc:
                self._async_errors.append(
                    f"task-inspection:{type(exc).__name__}:{exc}"
                )
            else:
                if task_error is not None:
                    self._async_errors.append(
                        f"task:{type(task_error).__name__}:{task_error}"
                    )
        pending_callbacks = [
            name for name, handle, completed in self._async_callbacks
            if not completed.is_set()
            and not (
                callable(getattr(handle, "cancelled", None))
                and handle.cancelled()
            )
            and not (
                callable(getattr(handle, "done", None))
                and handle.done()
            )
        ]
        for index, endpoint in enumerate(self._async_socketpairs):
            fileno = getattr(endpoint, "fileno", None)
            try:
                descriptor = fileno() if callable(fileno) else _UNSET
            except BaseException as exc:
                self._async_errors.append(
                    f"asyncio-socketpair-{index}:fileno:"
                    f"{type(exc).__name__}:{exc}"
                )
                descriptor = _UNSET
            if descriptor != -1:
                self._async_errors.append(
                    f"asyncio-socketpair-{index}:not-closed:{descriptor!r}"
                )
                close = getattr(endpoint, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        pass
        for index, record in enumerate(self._pipe_records):
            stream = record.get("read_stream")
            if getattr(stream, "closed", False) is not True:
                self._async_errors.append(
                    f"background-pipe-{index}:read-end-not-closed"
                )
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        pass
            else:
                try:
                    descriptor = stream.fileno()
                except ValueError:
                    pass
                except BaseException as exc:
                    self._async_errors.append(
                        f"background-pipe-{index}:fileno-check:"
                        f"{type(exc).__name__}:{exc}"
                    )
                else:
                    self._async_errors.append(
                        f"background-pipe-{index}:closed-reader-fd:{descriptor}"
                    )
            if record.get("write_closed_verified") is not True:
                self._async_errors.append(
                    f"background-pipe-{index}:write-end-not-closed"
                )
                descriptor = record.get("write_fd")
                if isinstance(descriptor, int):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
        hits = list(self._tripwire_hits)
        thread_errors = list(self._thread_errors)
        async_errors = list(self._async_errors)
        _ACTIVE_AUDIT_HITS = None
        self._restore_late_boundary_aliases()
        if (
            alive
            or pending_tasks
            or pending_callbacks
            or thread_errors
            or async_errors
        ):
            _FAIL_CLOSED_TRIPWIRE_STACKS.append(
                self._tripwire_stack.pop_all()
            )
        else:
            self._tripwire_stack.close()
        _install_suite_fail_closed_boundaries()
        if (
            hits
            or alive
            or pending_tasks
            or pending_callbacks
            or thread_errors
            or async_errors
        ):
            self.fail(
                "fail-closed boundary violation: "
                f"tripwire_hits={hits}, live_workers={alive}, "
                f"pending_tasks={len(pending_tasks)}, "
                f"pending_callbacks={pending_callbacks}, "
                f"thread_errors={thread_errors}, async_errors={async_errors}"
            )

    def test_a_entrypoint_signatures_accept_optional_trusted_local(self) -> None:
        targets = {
            "core.run": ControlledCommandRunner.run,
            "legacy.run": ProjectWorkspace.run,
            "browser.run": BrowserProcessRunner.run,
            "browser.start_background": BrowserProcessRunner.start_background,
        }
        violations: list[str] = []
        for name, target in targets.items():
            signature, signature_error = self._capture(
                lambda target=target: inspect.signature(target)
            )
            if signature_error is not None or not isinstance(
                signature, inspect.Signature
            ):
                violations.append(f"{name}: signature unavailable")
                continue
            parameter = signature.parameters.get("trusted_local")
            if parameter is None:
                violations.append(f"{name}: trusted_local parameter absent")
            elif parameter.default is inspect.Parameter.empty:
                violations.append(f"{name}: trusted_local is not optional")
        self.assertEqual(
            violations,
            [],
            "SEC-A: all existing execution methods need one optional "
            f"trusted_local seam; violations={violations}",
        )

    def test_a_valid_confirmation_is_opaque_and_consumed_once(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for mode in ("sequential", "concurrent"):
                root = base / mode
                root.mkdir()
                confirmation = self._confirmation_for(
                    root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=1,
                    python_profile=True,
                )
                if confirmation is _UNSET:
                    violations.append(
                        f"{mode}: production-owned confirmation request unavailable"
                    )
                    continue
                if isinstance(
                    confirmation,
                    (bool, str, bytes, bytearray, Mapping),
                ):
                    violations.append(
                        f"{mode}: confirmation is plain reconstructible data"
                    )
                try:
                    json.dumps(confirmation)
                except (TypeError, ValueError):
                    pass
                else:
                    violations.append(
                        f"{mode}: confirmation is JSON reconstructible"
                    )

                runner = ControlledCommandRunner(
                    root,
                    CommandPolicy(
                        allowed_executables={"python3"},
                        allowed_commands=[list(CORE_COMMAND)],
                    ),
                    output_limit_chars=10_000,
                )
                popen = FakePopenFactory()
                outcomes: list[tuple[object, BaseException | None]] = []
                with self._patched_processes(
                    popen,
                    FakeRunFactory(),
                ), mock.patch.object(
                    command_module.shutil,
                    "which",
                    return_value="/usr/bin/python3",
                ):
                    if mode == "sequential":
                        for _ in range(2):
                            outcomes.append(self._capture(lambda: runner.run(
                                CORE_COMMAND,
                                timeout_seconds=1,
                                trusted_local=confirmation,
                            )))
                    else:
                        barrier = threading.Barrier(3)
                        slots: list[tuple[object, BaseException | None] | None] = [
                            None,
                            None,
                        ]

                        def compete(index: int) -> None:
                            barrier.wait()
                            slots[index] = self._capture(lambda: runner.run(
                                CORE_COMMAND,
                                timeout_seconds=1,
                                trusted_local=confirmation,
                            ))

                        threads = [
                            self._registered_thread(
                                target=compete,
                                args=(index,),
                                name=f"sec-a-confirmation-race-{index}",
                            )
                            for index in range(2)
                        ]
                        for thread in threads:
                            thread.start()
                        barrier.wait()
                        for thread in threads:
                            thread.join(timeout=2)
                        if any(thread.is_alive() for thread in threads):
                            violations.append("concurrent: barrier race did not finish")
                        outcomes.extend(item for item in slots if item is not None)

                if len(popen.calls) != 1:
                    violations.append(
                        f"{mode}: matching Core request spawn_count={len(popen.calls)}"
                    )
                rejected = [
                    item for item in outcomes
                    if self._has_structured_code(*item, "SANDBOX_REQUIRED")
                ]
                if len(rejected) != 1:
                    violations.append(
                        f"{mode}: structured reuse rejection count={len(rejected)}"
                    )

            for mode in ("sequential", "concurrent"):
                global_root = base / f"global-four-methods-{mode}"
                confirmation = self._confirmation_for(
                    global_root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=30,
                    python_profile=True,
                )
                if confirmation is _UNSET:
                    violations.append(
                        f"global-one-shot/{mode}: Core token unavailable"
                    )
                    continue
                outcomes, spawn_total = (
                    self._invoke_four_public_methods_with_one_token(
                        global_root,
                        confirmation,
                        concurrent=mode == "concurrent",
                    )
                )
                rejected = sum(
                    self._has_structured_code(
                        value,
                        error,
                        "SANDBOX_REQUIRED",
                    )
                    for _, value, error in outcomes
                )
                if spawn_total != 1 or rejected != 3 or len(outcomes) != 4:
                    violations.append(
                        f"global-one-shot/{mode}: methods={len(outcomes)}, "
                        f"spawns={spawn_total}, rejections={rejected}"
                    )

        self.assertEqual(
            violations,
            [],
            "SEC-A: every Profile token must be opaque, globally one-shot, "
            "and atomic under reuse races; "
            f"violations={violations}",
        )

    def test_a_missing_plain_expired_and_drifted_confirmation_fail_before_spawn(self) -> None:
        violations: list[str] = []

        class ArbitraryCode:
            code = "SANDBOX_REQUIRED"

        arbitrary_type_error = TypeError("not a controlled public rejection")
        arbitrary_type_error.code = "SANDBOX_REQUIRED"
        false_positive_controls = (
            (None, None),
            (ArbitraryCode(), None),
            ({"code": "SANDBOX_REQUIRED"}, None),
            (None, arbitrary_type_error),
        )
        if any(
            self._has_structured_code(value, error, "SANDBOX_REQUIRED")
            for value, error in false_positive_controls
        ):
            violations.append(
                "SANDBOX_REQUIRED helper accepted None, bare code, or TypeError"
            )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            broad_cases: list[tuple[str, object]] = [
                ("missing", _UNSET),
                ("plain-bool", True),
                ("plain-dict", {"trusted_local": True}),
            ]
            issuer = getattr(
                coding_workflow,
                "issue_trusted_local_confirmation",
                None,
            )
            if not callable(issuer):
                violations.append("issuer capability absent")
            else:
                signature, signature_error = self._capture(
                    lambda: inspect.signature(issuer)
                )
                if signature_error is not None or not isinstance(
                    signature, inspect.Signature
                ):
                    signature = None
                expected = {
                    "workspace_digest",
                    "input_digest",
                    "profile_digest",
                    "expires_at_monotonic",
                }
                actual = (
                    set(signature.parameters) if signature is not None else set()
                )
                if signature is None or actual != expected or any(
                    item.kind is not inspect.Parameter.KEYWORD_ONLY
                    for item in signature.parameters.values()
                ):
                    violations.append(
                        f"issuer signature invalid: parameters={sorted(actual)}"
                    )
                else:
                    request = self._confirmation_request_for(
                        root,
                        PROFILE_IDS["core"],
                        "/usr/bin/python3",
                        CORE_COMMAND,
                        wall_deadline_seconds=1,
                        output_limit_chars=10_000,
                    )
                    if request is _UNSET:
                        violations.append(
                            "production-owned confirmation_request unavailable; "
                            "three single-digest drift cases blocked"
                        )
                    else:
                        exact_cases: list[tuple[str, object, float | None]] = []
                        expiry_base = time.monotonic()
                        try:
                            expired = issuer(
                                workspace_digest=request["workspace_digest"],
                                input_digest=request["input_digest"],
                                profile_digest=request["profile_digest"],
                                expires_at_monotonic=expiry_base + 0.01,
                            )
                        except Exception as exc:
                            violations.append(
                                "expired: short-lived opaque token unavailable: "
                                f"{type(exc).__name__}"
                            )
                        else:
                            exact_cases.append((
                                "expired",
                                expired,
                                expiry_base + 1.0,
                            ))
                        for field in (
                            "workspace_digest",
                            "input_digest",
                            "profile_digest",
                        ):
                            changed = dict(request)
                            changed[field] = self._different_digest(request[field])
                            try:
                                token = issuer(
                                    workspace_digest=changed["workspace_digest"],
                                    input_digest=changed["input_digest"],
                                    profile_digest=changed["profile_digest"],
                                    expires_at_monotonic=time.monotonic() + 60,
                                )
                            except Exception as exc:
                                violations.append(
                                    f"{field}-drift: issuer refused opaque token: "
                                    f"{type(exc).__name__}"
                                )
                            else:
                                exact_cases.append((
                                    f"{field}-drift",
                                    token,
                                    None,
                                ))

                        core = ControlledCommandRunner(
                            root,
                            CommandPolicy(
                                allowed_executables={"python3"},
                                allowed_commands=[list(CORE_COMMAND)],
                            ),
                            output_limit_chars=10_000,
                        )
                        for case_name, confirmation, advanced_clock in exact_cases:
                            popen = FakePopenFactory()
                            clock = (
                                self._patched_monotonic(advanced_clock)
                                if advanced_clock is not None
                                else contextlib.nullcontext()
                            )
                            with clock, self._patched_processes(
                                popen, FakeRunFactory()
                            ), mock.patch.object(
                                command_module.shutil, "which",
                                return_value="/usr/bin/python3"
                            ):
                                result, error = self._capture(lambda: core.run(
                                    CORE_COMMAND,
                                    timeout_seconds=1,
                                    trusted_local=confirmation,
                                ))
                            if not self._has_structured_code(
                                result,
                                error,
                                "SANDBOX_REQUIRED",
                            ):
                                violations.append(
                                    f"{case_name}: structured SANDBOX_REQUIRED absent"
                                )
                            if popen.calls:
                                violations.append(
                                    f"{case_name}: spawn_count={len(popen.calls)}"
                                )

                        try:
                            mutation_token = issuer(
                                workspace_digest=request["workspace_digest"],
                                input_digest=request["input_digest"],
                                profile_digest=request["profile_digest"],
                                expires_at_monotonic=time.monotonic() + 60,
                            )
                        except Exception as exc:
                            violations.append(
                                "workspace-mutation: opaque token unavailable: "
                                f"{type(exc).__name__}"
                            )
                        else:
                            (root / "after-token-mutation.txt").write_text(
                                "mutation after challenge\n",
                                encoding="utf-8",
                            )
                            popen = FakePopenFactory()
                            with self._patched_processes(
                                popen, FakeRunFactory()
                            ), mock.patch.object(
                                command_module.shutil, "which",
                                return_value="/usr/bin/python3"
                            ):
                                mutation_result, mutation_error = self._capture(
                                    lambda: core.run(
                                        CORE_COMMAND,
                                        timeout_seconds=1,
                                        trusted_local=mutation_token,
                                    )
                                )
                            if not self._has_structured_code(
                                mutation_result,
                                mutation_error,
                                "SANDBOX_REQUIRED",
                            ):
                                violations.append(
                                    "workspace-mutation: token was not rejected"
                                )
                            if popen.calls:
                                violations.append(
                                    "workspace-mutation: spawn reached"
                                )

                    matrix_root = root / "all-profile-expiry-drift"
                    matrix_requests = self._all_confirmation_requests(
                        matrix_root
                    )
                    matrix_cases = [
                        ("expired", None, time.monotonic() + 0.01),
                        ("workspace-drift", "workspace_digest", None),
                        ("input-drift", "input_digest", None),
                        ("profile-drift", "profile_digest", None),
                    ]
                    for case_name, drift_field, expiry in matrix_cases:
                        clock_value = None
                        if expiry is None:
                            expiry = time.monotonic() + 60
                        else:
                            clock_value = expiry + 1.0
                        tokens = self._issue_confirmation_map(
                            issuer,
                            matrix_requests,
                            expires_at_monotonic=expiry,
                            drift_field=drift_field,
                        )
                        if not isinstance(tokens, Mapping):
                            violations.append(
                                f"{case_name}/all-profiles: tokens unavailable"
                            )
                            continue
                        clock = (
                            self._patched_monotonic(clock_value)
                            if clock_value is not None
                            else contextlib.nullcontext()
                        )
                        with clock:
                            observations = self._invoke_all_entrypoints(
                                matrix_root,
                                trusted_local_by_entrypoint=tokens,
                            )
                        for (
                            entrypoint,
                            result,
                            error,
                            spawn_count,
                            _,
                        ) in observations:
                            if spawn_count != 0 or not self._has_structured_code(
                                result,
                                error,
                                "SANDBOX_REQUIRED",
                            ):
                                violations.append(
                                    f"{case_name}/{entrypoint}: "
                                    f"spawn={spawn_count}, rejection absent"
                                )

            for case_name, confirmation in broad_cases:
                observations = self._invoke_all_entrypoints(
                    root / case_name,
                    trusted_local=confirmation,
                )
                for entrypoint, result, error, spawn_count, _ in observations:
                    if not self._has_structured_code(
                        result,
                        error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            f"{case_name}/{entrypoint}: structured "
                            "SANDBOX_REQUIRED absent"
                        )
                    if spawn_count != 0:
                        violations.append(
                            f"{case_name}/{entrypoint}: spawn_count={spawn_count}"
                        )
                    if case_name != "missing" and self._first_mapping_value(
                        result,
                        error,
                        "confirmation_request",
                    ) is not _UNSET:
                        violations.append(
                            f"{case_name}/{entrypoint}: invalid confirmation "
                            "exposed a fresh challenge"
                        )

        self.assertEqual(
            violations,
            [],
            "SEC-A: invalid admission must fail before every spawn; "
            f"violations={violations}",
        )

    def test_b_spawn_kwargs_use_only_the_frozen_environment_and_fd_boundary(self) -> None:
        violations: list[str] = []
        injected = {
            "SEC_EXEC_PARENT_SENTINEL": FAKE_SECRET,
            "OPENAI_API_KEY": FAKE_SECRET,
            "HTTPS_PROXY": f"http://{FAKE_SECRET}.invalid",
            "SSH_AUTH_SOCK": f"/tmp/{FAKE_SECRET}.sock",
            "PYTHONPATH": f"/tmp/{FAKE_SECRET}",
            "NODE_OPTIONS": f"--require=/tmp/{FAKE_SECRET}.js",
            "DYLD_INSERT_LIBRARIES": f"/tmp/{FAKE_SECRET}.dylib",
            "LANG": f"{FAKE_SECRET}_PARENT_LANG",
            "LC_ALL": f"{FAKE_SECRET}_PARENT_LC_ALL",
            "PYTHONDONTWRITEBYTECODE": (
                f"{FAKE_SECRET}_PARENT_DONT_WRITE_BYTECODE"
            ),
            "PYTHONUNBUFFERED": f"{FAKE_SECRET}_PARENT_UNBUFFERED",
        }
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            injected,
            clear=False,
        ):
            observations = self._invoke_all_entrypoints(Path(temp))
            for entrypoint, _, _, spawn_count, spawn_kwargs in observations:
                if spawn_count != 1 or spawn_kwargs is None:
                    violations.append(
                        f"{entrypoint}: expected one normal-control spawn, got {spawn_count}"
                    )
                    continue
                environment = spawn_kwargs.get("env")
                if not isinstance(environment, Mapping):
                    violations.append(f"{entrypoint}: explicit env absent")
                    environment = {}
                if FAKE_SECRET in repr(environment):
                    violations.append(f"{entrypoint}: parent sentinel inherited")
                expected_names = {
                    "PATH", "LANG", "LC_ALL", "HOME", "TMPDIR",
                }
                if entrypoint in {"core", "legacy"}:
                    expected_names.update({
                        "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
                    })
                if set(environment) != expected_names:
                    violations.append(
                        f"{entrypoint}: env_names={sorted(environment)}"
                    )
                if environment.get("PATH") != FROZEN_PATH:
                    violations.append(
                        f"{entrypoint}: PATH={environment.get('PATH')!r}"
                    )
                frozen_values = {
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                }
                if entrypoint in {"core", "legacy"}:
                    frozen_values.update({
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONUNBUFFERED": "1",
                    })
                for name, expected_value in frozen_values.items():
                    if environment.get(name) != expected_value:
                        violations.append(
                            f"{entrypoint}: {name} was not fixed to Profile value"
                        )
                if spawn_kwargs.get("stdin") is not subprocess.DEVNULL:
                    violations.append(f"{entrypoint}: stdin is not DEVNULL")
                if spawn_kwargs.get("close_fds") is not True:
                    violations.append(f"{entrypoint}: close_fds is not True")
                if spawn_kwargs.get("shell") is not False:
                    violations.append(f"{entrypoint}: shell is not False")
                if spawn_kwargs.get("start_new_session") is not True:
                    violations.append(
                        f"{entrypoint}: start_new_session is not True"
                    )
                umask = spawn_kwargs.get("umask")
                preexec_fn = spawn_kwargs.get("preexec_fn")
                if umask != 0o077:
                    violations.append(f"{entrypoint}: umask 077 absent")
                if preexec_fn is not None:
                    violations.append(f"{entrypoint}: preexec_fn must be absent")
                pass_fds = spawn_kwargs.get("pass_fds", ())
                if (
                    not isinstance(pass_fds, (tuple, list))
                    or tuple(pass_fds) != ()
                ):
                    violations.append(
                        f"{entrypoint}: unexpected pass_fds={spawn_kwargs.get('pass_fds')}"
                    )

        self.assertEqual(
            violations,
            [],
            "SEC-B: child process kwargs must start from the frozen profile; "
            f"violations={violations}",
        )

    def test_b_private_home_and_tmp_are_unique_0700_and_cleaned_after_return_or_stop(self) -> None:
        violations: list[str] = []
        captured: dict[str, list[Path]] = {}
        parent_values = {
            name: os.environ.get(name) for name in ("HOME", "TMPDIR")
        }
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for index in range(2):
                spawn_states: dict[str, dict[str, tuple[Path, bool, int | None]]] = {}

                def observe_spawn(entrypoint, _args, kwargs) -> None:
                    environment = kwargs.get("env")
                    states: dict[str, tuple[Path, bool, int | None]] = {}
                    if isinstance(environment, Mapping):
                        for name in ("HOME", "TMPDIR"):
                            value = environment.get(name)
                            if isinstance(value, str):
                                path = Path(value)
                                exists = path.exists()
                                mode = path.stat().st_mode & 0o777 if exists else None
                                states[name] = (path, exists, mode)
                    spawn_states[entrypoint] = states

                observations = self._invoke_all_entrypoints(
                    base / f"run-{index}",
                    on_spawn=observe_spawn,
                )
                for entrypoint, result, _, spawn_count, spawn_kwargs in observations:
                    if spawn_count != 1 or spawn_kwargs is None:
                        violations.append(
                            f"{entrypoint}/{index}: no normal-control spawn"
                        )
                        continue
                    environment = spawn_kwargs.get("env")
                    if not isinstance(environment, Mapping):
                        violations.append(f"{entrypoint}/{index}: env absent")
                        continue
                    paths: list[Path] = []
                    for name in ("HOME", "TMPDIR"):
                        value = environment.get(name)
                        if not isinstance(value, str):
                            violations.append(
                                f"{entrypoint}/{index}: {name} absent"
                            )
                            continue
                        path = Path(value)
                        paths.append(path)
                        if value == parent_values[name]:
                            violations.append(
                                f"{entrypoint}/{index}: {name} reused parent value"
                            )
                        state = spawn_states.get(entrypoint, {}).get(name)
                        if state is None or not state[1]:
                            violations.append(
                                f"{entrypoint}/{index}: {name} absent at spawn"
                            )
                        elif state[2] != 0o700:
                            violations.append(
                                f"{entrypoint}/{index}: {name} mode is not 0700"
                            )
                    captured.setdefault(entrypoint, []).extend(paths)
                    terminal = (
                        "stop" if entrypoint == "visionforge_dev" else "return"
                    )
                    for path in paths:
                        if path.exists():
                            violations.append(
                                f"{entrypoint}/{index}: private dir survives {terminal}"
                            )

            for entrypoint, paths in captured.items():
                values = [str(path) for path in paths]
                if len(values) != len(set(values)):
                    violations.append(
                        f"{entrypoint}: HOME/TMPDIR paths are reused"
                    )

        self.assertEqual(
            violations,
            [],
            "SEC-B: HOME/TMPDIR must be private per execution and cleaned; "
            f"violations={violations}",
        )

    def test_c_argv_and_limit_mutation_matrix_never_reaches_spawn(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core_policy = CommandPolicy(
                allowed_executables={"python3"},
                allowed_commands=[list(CORE_COMMAND)],
            )
            core = ControlledCommandRunner(
                root,
                core_policy,
                max_timeout_seconds=30,
                output_limit_chars=10_000,
            )
            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"pnpm"}),
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
            )
            legacy = ProjectWorkspace(root, command_timeout=60)
            mutations = (
                (
                    "argument-changed",
                    CORE_COMMAND[:-1] + ("-q",),
                    ("python3", "--changed"),
                    ("pnpm", "run", "test"),
                ),
                (
                    "argument-added",
                    CORE_COMMAND + ("extra",),
                    LEGACY_COMMAND + ("extra",),
                    BUILD_COMMAND + ("extra",),
                ),
                (
                    "shell-metachar",
                    ("python3", "$(touch canary)"),
                    ("python3", "$(touch canary)"),
                    ("pnpm", "$(touch canary)"),
                ),
            )
            for case_name, core_command, legacy_command, build_command in mutations:
                preflight_calls = (
                    (
                        "core",
                        lambda command=core_command: core.run(
                            command,
                            timeout_seconds=1,
                        ),
                    ),
                    (
                        "legacy",
                        lambda command=legacy_command: legacy.run(list(command)),
                    ),
                    (
                        "browser",
                        lambda command=build_command: browser.run(
                            command,
                            cwd=root,
                            timeout_seconds=1,
                        ),
                    ),
                )
                for entrypoint, preflight_call in preflight_calls:
                    self._check_invalid_request_no_challenge(
                        f"{entrypoint}/{case_name}",
                        preflight_call,
                        violations,
                    )
                core_kwargs = {"timeout_seconds": 1}
                legacy_kwargs: dict[str, object] = {}
                browser_kwargs = {"cwd": root, "timeout_seconds": 1}
                self._add_trusted_local(core_kwargs, self._confirmation_for(
                    root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=1,
                    python_profile=True,
                ))
                self._add_trusted_local(legacy_kwargs, self._confirmation_for(
                    root,
                    PROFILE_IDS["legacy"],
                    "/usr/bin/python3",
                    LEGACY_COMMAND,
                    wall_deadline_seconds=60,
                    python_profile=True,
                ))
                self._add_trusted_local(browser_kwargs, self._confirmation_for(
                    root,
                    PROFILE_IDS["visionforge_build"],
                    "/usr/bin/pnpm",
                    BUILD_COMMAND,
                    wall_deadline_seconds=1,
                ))
                calls = (
                    (
                        "core",
                        lambda command=core_command, kwargs=core_kwargs: core.run(
                            command,
                            **kwargs,
                        ),
                    ),
                    (
                        "legacy",
                        lambda command=legacy_command, kwargs=legacy_kwargs: legacy.run(
                            list(command),
                            **kwargs,
                        ),
                    ),
                    (
                        "browser",
                        lambda command=build_command, kwargs=browser_kwargs: browser.run(
                            command,
                            **kwargs,
                        ),
                    ),
                )
                for entrypoint, call in calls:
                    popen = FakePopenFactory()
                    run = FakeRunFactory()
                    with self._patched_processes(popen, run), mock.patch.object(
                        command_module.shutil,
                        "which",
                        return_value="/usr/bin/python3",
                    ):
                        invalid_result, invalid_error = self._capture(call)
                    count = len(popen.calls) + len(run.calls)
                    if count != 0:
                        violations.append(
                            f"{entrypoint}/{case_name}: spawn_count={count}"
                        )
                    if not self._has_structured_code(
                        invalid_result,
                        invalid_error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            f"{entrypoint}/{case_name}: token-bearing "
                            "SANDBOX_REQUIRED absent"
                        )
                    if self._first_mapping_value(
                        invalid_result,
                        invalid_error,
                        "confirmation_request",
                    ) is not _UNSET:
                        violations.append(
                            f"{entrypoint}/{case_name}: token-bearing invalid "
                            "request exposed confirmation_request"
                        )

            popen = FakePopenFactory()
            self._check_invalid_request_no_challenge(
                "browser/deadline-relaxed",
                lambda: browser.run(
                    BUILD_COMMAND,
                    cwd=root,
                    timeout_seconds=61,
                ),
                violations,
            )
            relaxed_kwargs = {"cwd": root, "timeout_seconds": 61}
            self._add_trusted_local(relaxed_kwargs, self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=60,
            ))
            with self._patched_processes(popen, FakeRunFactory()):
                relaxed_result, relaxed_error = self._capture(lambda: browser.run(
                    BUILD_COMMAND,
                    **relaxed_kwargs,
                ))
            if len(popen.calls) != 0:
                violations.append("browser/deadline-relaxed: spawn reached")
            if not self._has_structured_code(
                relaxed_result,
                relaxed_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "browser/deadline-relaxed: token-bearing "
                    "SANDBOX_REQUIRED absent"
                )
            if self._first_mapping_value(
                relaxed_result,
                relaxed_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append(
                    "browser/deadline-relaxed: token-bearing invalid request "
                    "exposed confirmation_request"
                )

            output_limit_token = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
                output_limit_chars=10_000,
            )
            tighter_output_runner = ControlledCommandRunner(
                root,
                core_policy,
                max_timeout_seconds=30,
                output_limit_chars=9_999,
            )
            tighter_output_popen = FakePopenFactory()
            with self._patched_processes(
                tighter_output_popen,
                FakeRunFactory(),
            ), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                tighter_result, tighter_error = self._capture(
                    lambda: tighter_output_runner.run(
                        CORE_COMMAND,
                        timeout_seconds=1,
                        trusted_local=output_limit_token,
                    )
                )
            if tighter_output_popen.calls or not self._has_structured_code(
                tighter_result,
                tighter_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "core/output-limit-10000-token-on-9999-runner was not "
                    "rejected before spawn"
                )

            unfamiliar = BrowserProcessRunner(
                allowed_executables=frozenset({"npx"}),
                executable_overrides={"npx": "/usr/bin/npx"},
            )
            self._check_invalid_request_no_challenge(
                "browser/unfamiliar-dependency",
                lambda: unfamiliar.run(
                    ("npx", "definitely-unreviewed-package"),
                    cwd=root,
                    timeout_seconds=1,
                ),
                violations,
            )

            empty_core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables=set(),
                    allowed_commands=[],
                ),
                output_limit_chars=10_000,
            )
            empty_browser = BrowserProcessRunner(
                allowed_executables=frozenset(),
            )
            for case_name, call in (
                (
                    "core/empty-registry",
                    lambda: empty_core.run(CORE_COMMAND, timeout_seconds=1),
                ),
                (
                    "browser/empty-registry",
                    lambda: empty_browser.run(
                        BUILD_COMMAND,
                        cwd=root,
                        timeout_seconds=1,
                    ),
                ),
                (
                    "core/missing-argv",
                    lambda: core.run((), timeout_seconds=1),
                ),
                (
                    "legacy/missing-argv",
                    lambda: legacy.run([]),
                ),
                (
                    "browser/missing-argv",
                    lambda: browser.run((), cwd=root, timeout_seconds=1),
                ),
            ):
                self._check_invalid_request_no_challenge(
                    case_name,
                    call,
                    violations,
                )

            invalid_commands = {
                "core": CORE_COMMAND + ("unexpected",),
                "legacy": LEGACY_COMMAND + ("unexpected",),
                "visionforge_build": ("pnpm", "install"),
                "visionforge_browser": (
                    "node", "--eval", "console.log('unreviewed')",
                ),
                "visionforge_dev": DEV_COMMAND + (
                    "--host", "0.0.0.0",
                ),
            }
            issuer = getattr(
                coding_workflow,
                "issue_trusted_local_confirmation",
                None,
            )
            for auth_state in ("expired", "replayed"):
                auth_root = root / f"invalid-{auth_state}"
                requests = self._all_confirmation_requests(auth_root)
                if auth_state == "expired":
                    expiry = time.monotonic() + 0.01
                else:
                    expiry = time.monotonic() + 60
                tokens = (
                    self._issue_confirmation_map(
                        issuer,
                        requests,
                        expires_at_monotonic=expiry,
                    )
                    if callable(issuer)
                    else _UNSET
                )
                if not isinstance(tokens, Mapping):
                    violations.append(
                        f"invalid/{auth_state}: token matrix unavailable"
                    )
                    continue
                if auth_state == "replayed":
                    controls = self._invoke_all_entrypoints(
                        auth_root,
                        trusted_local_by_entrypoint=tokens,
                    )
                    if any(item[3] != 1 for item in controls):
                        violations.append(
                            "invalid/replayed: original grants were not consumed"
                        )
                    clock = contextlib.nullcontext()
                else:
                    clock = self._patched_monotonic(expiry + 1.0)
                with clock:
                    observations = self._invoke_all_entrypoints(
                        auth_root,
                        trusted_local_by_entrypoint=tokens,
                        command_overrides=invalid_commands,
                    )
                for entrypoint, result, error, spawn_count, _ in observations:
                    if spawn_count:
                        violations.append(
                            f"invalid/{auth_state}/{entrypoint}: spawn reached"
                        )
                    if self._first_mapping_value(
                        result,
                        error,
                        "confirmation_request",
                    ) is not _UNSET:
                        violations.append(
                            f"invalid/{auth_state}/{entrypoint}: "
                            "fresh challenge exposed"
                        )
                    if not self._has_structured_code(
                        result,
                        error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            f"invalid/{auth_state}/{entrypoint}: structured "
                            "SANDBOX_REQUIRED absent"
                        )

        self.assertEqual(
            violations,
            [],
            "SEC-C: argv/profile/limit changes must fail before spawn; "
            f"violations={violations}",
        )

    def test_c_workspace_shadow_executable_is_never_selected(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "shadow-ran"
            for name in ("python3", "pnpm"):
                executable = root / name
                executable.write_text(
                    f"#!/bin/sh\ntouch '{marker}'\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)

            core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            legacy = ProjectWorkspace(root, command_timeout=60)
            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"pnpm"}),
            )
            core_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
            )
            legacy_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            browser_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=1,
            )
            core_kwargs = {"timeout_seconds": 1}
            legacy_kwargs: dict[str, object] = {}
            browser_kwargs = {"cwd": root, "timeout_seconds": 1}
            self._add_trusted_local(core_kwargs, core_confirmation)
            self._add_trusted_local(legacy_kwargs, legacy_confirmation)
            self._add_trusted_local(browser_kwargs, browser_confirmation)
            with mock.patch.dict(
                os.environ,
                {"PATH": f"{root}:{FROZEN_PATH}"},
                clear=False,
            ):
                for entrypoint, call in (
                    ("core", lambda: core.run(CORE_COMMAND, **core_kwargs)),
                    ("legacy", lambda: legacy.run(list(LEGACY_COMMAND), **legacy_kwargs)),
                    (
                        "browser",
                        lambda: browser.run(
                            BUILD_COMMAND,
                            **browser_kwargs,
                        ),
                    ),
                ):
                    popen = FakePopenFactory()
                    run = FakeRunFactory()
                    with self._patched_processes(popen, run):
                        self._capture(call)
                    calls = popen.calls or run.calls
                    if len(calls) != 1:
                        violations.append(
                            f"{entrypoint}: expected one normal-control spawn"
                        )
                        continue
                    command = calls[0][0][0]
                    executable = command[0] if isinstance(command, (tuple, list)) else ""
                    if not Path(str(executable)).is_absolute():
                        violations.append(
                            f"{entrypoint}: basename executable forwarded"
                        )
                    elif Path(str(executable)).resolve().is_relative_to(root.resolve()):
                        violations.append(
                            f"{entrypoint}: Workspace shadow selected"
                        )
            if marker.exists():
                violations.append("Workspace shadow executable actually ran")

        self.assertEqual(
            violations,
            [],
            "SEC-C: only a trusted absolute executable may reach spawn; "
            f"violations={violations}",
        )

    def test_d_public_workspace_apis_reject_escape_and_reserved_paths(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            outside = base / "outside"
            outside.mkdir()
            canary = outside / "canary.txt"
            canary.write_text("canary", encoding="utf-8")
            canary_hash = hashlib.sha256(canary.read_bytes()).hexdigest()
            workspace = ProjectWorkspace(root)
            workspace.apply_changes([
                FileChange("src/normal.txt", "normal", "normal control")
            ])
            if workspace.read_text("src/normal.txt") != "normal":
                violations.append("normal relative path did not round-trip")

            unsafe = (
                str((outside / "absolute.txt").resolve()),
                "../outside/traversal.txt",
                ".env",
                ".env.local",
                ".git/config",
                ".runtime/state.sqlite3",
                ".runs/result.json",
                ".verification/report.json",
                ".harness-hidden-tests/private.py",
                "solution/reference.py",
            )
            for value in unsafe:
                try:
                    workspace.apply_changes([
                        FileChange(value, "changed", "negative path fixture")
                    ])
                except WorkspaceError:
                    continue
                violations.append(f"accepted write path: {value}")

            read_unsafe = (
                str(canary.resolve()),
                "../outside/canary.txt",
                ".env",
                ".env.local",
                ".git/config",
                ".runtime/state.sqlite3",
                ".runs/result.json",
                ".verification/report.json",
                ".harness-hidden-tests/private.py",
                "solution/reference.py",
            )
            for value in read_unsafe:
                try:
                    workspace.read_text(value)
                except WorkspaceError:
                    continue
                except OSError as exc:
                    violations.append(
                        f"read path did not fail with WorkspaceError: {value}: "
                        f"{type(exc).__name__}"
                    )
                else:
                    violations.append(f"accepted read path: {value}")

            link = root / "escape"
            link.symlink_to(outside, target_is_directory=True)
            try:
                workspace.apply_changes([
                    FileChange("escape/canary.txt", "changed", "symlink escape")
                ])
            except WorkspaceError:
                pass
            else:
                violations.append("accepted symlink escape")
            try:
                workspace.read_text("escape/canary.txt")
            except WorkspaceError:
                pass
            else:
                violations.append("accepted symlink read escape")

            if hashlib.sha256(canary.read_bytes()).hexdigest() != canary_hash:
                violations.append("outside canary changed")

        self.assertEqual(
            violations,
            [],
            "SEC-D: public Workspace file APIs must reject every escape; "
            f"violations={violations}",
        )

    def test_d_browser_paths_reject_external_and_symlink_targets_before_write_or_spawn(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            runtime_dir = base / "runtime"
            outside = base / "outside"
            project.mkdir()
            runtime_dir.mkdir()
            outside.mkdir()
            external_runner = outside / "browser-runner.mjs"
            external_runner.write_text("", encoding="utf-8")
            runner_link = project / "runner-link.mjs"
            runner_link.symlink_to(external_runner)
            config = self._browser_config("runner-link.mjs")
            config_path = project / "visionforge.template.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            try:
                BrowserProjectConfig.load(project)
            except BrowserRuntimeError:
                pass
            else:
                violations.append("symlink browser_runner accepted")
            runner_link.unlink()

            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"pnpm"}),
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
            )
            self._check_invalid_request_no_challenge(
                "browser/external-cwd",
                lambda: browser.run(
                    BUILD_COMMAND,
                    cwd=outside,
                    timeout_seconds=1,
                ),
                violations,
            )
            build_confirmation = self._confirmation_for(
                project,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=1,
            )
            build_kwargs = {"cwd": outside, "timeout_seconds": 1}
            self._add_trusted_local(build_kwargs, build_confirmation)
            popen = FakePopenFactory(lambda: FakeProcess(running=True))
            with self._patched_processes(popen, FakeRunFactory()):
                cwd_result, cwd_error = self._capture(lambda: browser.run(
                    BUILD_COMMAND,
                    **build_kwargs,
                ))
            if popen.calls:
                violations.append("external browser cwd reached spawn")
            if not self._has_structured_code(
                cwd_result,
                cwd_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "external browser cwd lacks SANDBOX_REQUIRED"
                )
            if self._first_mapping_value(
                cwd_result,
                cwd_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append(
                    "external browser cwd exposed confirmation_request"
                )

            external_log = outside / "server.log"
            self._check_invalid_request_no_challenge(
                "browser/external-log",
                lambda: browser.start_background(
                    DEV_COMMAND,
                    cwd=project,
                    log_path=external_log,
                ),
                violations,
            )
            dev_confirmation = self._confirmation_for(
                project,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            dev_kwargs = {"cwd": project, "log_path": external_log}
            self._add_trusted_local(dev_kwargs, dev_confirmation)
            popen = FakePopenFactory(lambda: FakeProcess(running=True))
            with self._patched_processes(popen, FakeRunFactory()):
                managed, managed_error = self._capture(lambda: browser.start_background(
                    DEV_COMMAND,
                    **dev_kwargs,
                ))
                stop = getattr(managed, "stop", None)
                if callable(stop):
                    self._capture(stop)
            if popen.calls or external_log.exists():
                violations.append("external browser log was opened or spawned")
            if not self._has_structured_code(
                managed,
                managed_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "external browser log lacks SANDBOX_REQUIRED"
                )
            if self._first_mapping_value(
                managed,
                managed_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append(
                    "external browser log exposed confirmation_request"
                )
            stream = getattr(managed, "stream", None)
            if stream is not None:
                stream.close()

            cwd_link = base / "cwd-link"
            cwd_link.symlink_to(outside, target_is_directory=True)
            self._check_invalid_request_no_challenge(
                "browser/symlink-cwd",
                lambda: browser.run(
                    BUILD_COMMAND,
                    cwd=cwd_link,
                    timeout_seconds=1,
                ),
                violations,
            )
            symlink_cwd_kwargs = {"cwd": cwd_link, "timeout_seconds": 1}
            self._add_trusted_local(symlink_cwd_kwargs, build_confirmation)
            symlink_cwd_popen = FakePopenFactory()
            with self._patched_processes(
                symlink_cwd_popen,
                FakeRunFactory(),
            ):
                symlink_cwd_result, symlink_cwd_error = self._capture(
                    lambda: browser.run(
                        BUILD_COMMAND,
                        **symlink_cwd_kwargs,
                    )
                )
            if symlink_cwd_popen.calls or not self._has_structured_code(
                symlink_cwd_result,
                symlink_cwd_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "symlink browser cwd was not rejected before spawn"
                )
            if self._first_mapping_value(
                symlink_cwd_result,
                symlink_cwd_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append("symlink browser cwd exposed challenge")

            symlink_log_target = outside / "symlink-server.log"
            symlink_log_target.write_text("outside-canary\n", encoding="utf-8")
            symlink_log_digest = hashlib.sha256(
                symlink_log_target.read_bytes()
            ).hexdigest()
            symlink_log = project / "server-link.log"
            symlink_log.symlink_to(symlink_log_target)
            self._check_invalid_request_no_challenge(
                "browser/symlink-log",
                lambda: browser.start_background(
                    DEV_COMMAND,
                    cwd=project,
                    log_path=symlink_log,
                ),
                violations,
            )
            symlink_log_kwargs = {
                "cwd": project,
                "log_path": symlink_log,
            }
            self._add_trusted_local(symlink_log_kwargs, dev_confirmation)
            symlink_log_popen = FakePopenFactory(
                lambda: FakeProcess(running=True)
            )
            with self._patched_processes(
                symlink_log_popen,
                FakeRunFactory(),
            ):
                symlink_managed, symlink_log_error = self._capture(
                    lambda: browser.start_background(
                        DEV_COMMAND,
                        **symlink_log_kwargs,
                    )
                )
                symlink_stop = getattr(symlink_managed, "stop", None)
                if callable(symlink_stop):
                    self._capture(symlink_stop)
            if symlink_log_popen.calls or not self._has_structured_code(
                symlink_managed,
                symlink_log_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "symlink browser log was not rejected before open/spawn"
                )
            if self._first_mapping_value(
                symlink_managed,
                symlink_log_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append("symlink browser log exposed challenge")
            if hashlib.sha256(symlink_log_target.read_bytes()).hexdigest() != (
                symlink_log_digest
            ):
                violations.append("symlink browser log outside canary changed")

            runner = project / "runner.mjs"
            runner.write_text("", encoding="utf-8")
            path_runtime = project / "runtime"
            path_runtime.mkdir()
            valid_spec = path_runtime / "ui-spec.json"
            valid_spec.write_text("{}", encoding="utf-8")
            valid_result = path_runtime / "result.json"
            valid_screenshot = path_runtime / "actual.png"
            base_browser_command = (
                "node",
                str(runner),
                "--url",
                "http://127.0.0.1:4173/",
                "--spec",
                str(valid_spec),
                "--screenshot",
                str(valid_screenshot),
                "--result",
                str(valid_result),
            )
            browser_paths = BrowserProcessRunner(
                allowed_executables=frozenset({"node"}),
                executable_overrides={"node": "/usr/bin/node"},
            )
            external_targets = {
                "spec": outside / "external-spec.json",
                "screenshot": outside / "external-screenshot.png",
                "result": outside / "external-result.json",
            }
            external_targets["spec"].write_text("{}", encoding="utf-8")
            for target in external_targets.values():
                if not target.exists():
                    target.write_bytes(b"outside-canary")
            target_hashes = {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in external_targets.items()
            }
            switches = {
                "spec": "--spec",
                "screenshot": "--screenshot",
                "result": "--result",
            }
            for field, switch in switches.items():
                for path_kind in ("external", "symlink"):
                    if path_kind == "external":
                        unsafe_path = external_targets[field]
                    else:
                        unsafe_path = path_runtime / f"{field}-link"
                        unsafe_path.symlink_to(external_targets[field])
                    command = list(base_browser_command)
                    command[command.index(switch) + 1] = str(unsafe_path)
                    self._check_invalid_request_no_challenge(
                        f"browser/{field}/{path_kind}",
                        lambda command=tuple(command): browser_paths.run(
                            command,
                            cwd=project,
                            timeout_seconds=45,
                        ),
                        violations,
                    )
                    confirmation = self._confirmation_for(
                        project,
                        PROFILE_IDS["visionforge_browser"],
                        "/usr/bin/node",
                        base_browser_command,
                        wall_deadline_seconds=45,
                    )
                    kwargs = {"cwd": project, "timeout_seconds": 45}
                    self._add_trusted_local(kwargs, confirmation)
                    popen = FakePopenFactory()
                    with self._patched_processes(popen, FakeRunFactory()):
                        path_result, path_error = self._capture(
                            lambda command=tuple(command): browser_paths.run(
                                command,
                                **kwargs,
                            )
                        )
                    if popen.calls:
                        violations.append(
                            f"browser {field}/{path_kind} reached spawn"
                        )
                    if not self._has_structured_code(
                        path_result,
                        path_error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            f"browser {field}/{path_kind} lacks "
                            "SANDBOX_REQUIRED"
                        )
                    if self._first_mapping_value(
                        path_result,
                        path_error,
                        "confirmation_request",
                    ) is not _UNSET:
                        violations.append(
                            f"browser {field}/{path_kind} exposed "
                            "confirmation_request"
                        )

            non_loopback_command = list(base_browser_command)
            url_index = non_loopback_command.index("--url") + 1
            non_loopback_command[url_index] = "http://192.0.2.1:4173/"
            self._check_invalid_request_no_challenge(
                "browser/non-loopback",
                lambda: browser_paths.run(
                    tuple(non_loopback_command),
                    cwd=project,
                    timeout_seconds=45,
                ),
                violations,
            )
            non_loopback_confirmation = self._confirmation_for(
                project,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                base_browser_command,
                wall_deadline_seconds=45,
            )
            non_loopback_kwargs = {"cwd": project, "timeout_seconds": 45}
            self._add_trusted_local(
                non_loopback_kwargs,
                non_loopback_confirmation,
            )
            non_loopback_spawn = FakePopenFactory()
            with self._patched_processes(
                non_loopback_spawn,
                FakeRunFactory(),
            ):
                non_loopback_result, non_loopback_error = self._capture(lambda: browser_paths.run(
                    tuple(non_loopback_command),
                    **non_loopback_kwargs,
                ))
            if non_loopback_spawn.calls:
                violations.append("browser non-loopback URL reached spawn")
            if not self._has_structured_code(
                non_loopback_result,
                non_loopback_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "browser non-loopback URL lacks SANDBOX_REQUIRED"
                )
            if self._first_mapping_value(
                non_loopback_result,
                non_loopback_error,
                "confirmation_request",
            ) is not _UNSET:
                violations.append(
                    "browser non-loopback URL exposed confirmation_request"
                )

            for name, target in external_targets.items():
                if hashlib.sha256(target.read_bytes()).hexdigest() != target_hashes[name]:
                    violations.append(f"browser {name} outside canary changed")

            config_path.write_text(
                json.dumps(self._browser_config("runner.mjs")),
                encoding="utf-8",
            )
            scripted = ScriptedBrowserRunner([
                ProcessExecution(("pnpm", "run", "build"), 0, "", "", 1),
                ProcessExecution(("node", "runner.mjs"), 1, "", "fake fail", 1),
            ])
            tester = PlaywrightBrowserTester(
                project,
                scripted,
                ArtifactStore(),
                ImageAssetStore(base / "assets"),
                runtime_dir,
            )
            ui_spec = UISpec.from_dict(json.loads(
                (TEMPLATE / "visionforge.ui-spec.json").read_text(encoding="utf-8")
            ))
            escaped_spec = base / "escape-ui-spec.json"
            with mock.patch.object(
                BrowserProjectRuntime,
                "_wait_until_ready",
                return_value=None,
            ):
                self._capture(lambda: tester.run(
                    task_id="sec-d",
                    ui_spec=ui_spec,
                    artifact_prefix="../escape",
                ))
            if escaped_spec.exists():
                violations.append("artifact_prefix traversal wrote outside runtime")

        self.assertEqual(
            violations,
            [],
            "SEC-D: every Browser path must be validated before write/spawn; "
            f"violations={violations}",
        )

    def test_e_all_terminal_paths_return_cleanup_barrier_evidence(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            cases: list[tuple[str, object, BaseException | None]] = []

            ordering_process = FakeProcess()
            for label, bad_trace in (
                (
                    "probe-before-signals",
                    [
                        ("killpg", ordering_process.pid, 0),
                        ("killpg", ordering_process.pid, signal.SIGTERM),
                        ("killpg", ordering_process.pid, signal.SIGKILL),
                        ("wait", ordering_process.pid, 1),
                    ],
                ),
                (
                    "probe-before-final-reap",
                    [
                        ("killpg", ordering_process.pid, signal.SIGTERM),
                        ("killpg", ordering_process.pid, signal.SIGKILL),
                        ("killpg", ordering_process.pid, 0),
                        ("wait", ordering_process.pid, 1),
                    ],
                ),
            ):
                ordering_violations: list[str] = []
                self._check_fake_cleanup_trace(
                    label,
                    ordering_process,
                    bad_trace,
                    ordering_violations,
                    expect_term=True,
                    expect_kill=True,
                )
                if not any(
                    "disappearance probe preceded final wait/reap" in item
                    for item in ordering_violations
                ):
                    violations.append(
                        f"cleanup trace checker accepted {label} ordering"
                    )

            for name, process in (
                ("success", FakeProcess(returncode=0)),
                ("nonzero", FakeProcess(returncode=7)),
                (
                    "timeout",
                    FakeProcess(
                        running=True,
                        communicate_effects=[
                            subprocess.TimeoutExpired(CORE_COMMAND, 0.01),
                            ("", ""),
                        ],
                        wait_effects=[
                            subprocess.TimeoutExpired(CORE_COMMAND, 1),
                            -9,
                        ],
                    ),
                ),
            ):
                core_trace: list[tuple[object, ...]] = []
                process.trace = core_trace
                timeout = 0.01 if name == "timeout" else 1
                confirmation = self._confirmation_for(
                    root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=timeout,
                    python_profile=True,
                )
                kwargs = {"timeout_seconds": timeout}
                self._add_trusted_local(kwargs, confirmation)
                popen = FakePopenFactory(lambda process=process: process)
                kill = FakeSignalFactory(
                    trace=core_trace,
                    label="kill",
                )
                killpg = FakeSignalFactory(
                    trace=core_trace,
                    label="killpg",
                )
                with self._patched_processes(
                    popen,
                    FakeRunFactory(),
                    kill=kill,
                    killpg=killpg,
                ), mock.patch.object(
                    command_module.shutil,
                    "which",
                    return_value="/usr/bin/python3",
                ):
                    result, error = self._capture(lambda: core.run(
                        CORE_COMMAND,
                        **kwargs,
                    ))
                cases.append((f"core-{name}", result, error))
                self._check_fake_cleanup_trace(
                    f"core-{name}",
                    process,
                    core_trace,
                    violations,
                    expect_term=name == "timeout",
                    expect_kill=name == "timeout",
                )

            legacy = ProjectWorkspace(root, command_timeout=60)
            legacy_cases = (
                (
                    "success",
                    FakeProcess(returncode=0),
                    FakeRunFactory(returncode=0),
                ),
                (
                    "nonzero",
                    FakeProcess(returncode=7),
                    FakeRunFactory(returncode=7),
                ),
                (
                    "timeout",
                    FakeProcess(
                        running=True,
                        communicate_effects=[
                            subprocess.TimeoutExpired(LEGACY_COMMAND, 60),
                            ("", ""),
                        ],
                    ),
                    mock.Mock(side_effect=subprocess.TimeoutExpired(
                        LEGACY_COMMAND,
                        60,
                        output="",
                        stderr="",
                    )),
                ),
            )
            for name, process, run in legacy_cases:
                legacy_trace: list[tuple[object, ...]] = []
                process.trace = legacy_trace
                if name == "timeout":
                    process.wait_effects = [
                        subprocess.TimeoutExpired(LEGACY_COMMAND, 1),
                        -9,
                    ]
                popen = FakePopenFactory(
                    lambda process=process: process
                )
                legacy_signals = FakeSignalFactory(
                    trace=legacy_trace,
                    label="killpg",
                )
                legacy_kill = FakeSignalFactory(
                    trace=legacy_trace,
                    label="kill",
                )
                timeout = 60
                legacy_kwargs: dict[str, object] = {}
                self._add_trusted_local(
                    legacy_kwargs,
                    self._confirmation_for(
                        root,
                        PROFILE_IDS["legacy"],
                        "/usr/bin/python3",
                        LEGACY_COMMAND,
                        wall_deadline_seconds=timeout,
                        python_profile=True,
                    ),
                )
                with self._patched_processes(
                    popen,
                    run,
                    kill=legacy_kill,
                    killpg=legacy_signals,
                ):
                    result, error = self._capture(
                        lambda kwargs=legacy_kwargs: legacy.run(
                            list(LEGACY_COMMAND),
                            **kwargs,
                        )
                    )
                if len(popen.calls) + self._factory_call_count(run) != 1:
                    violations.append(
                        f"legacy-{name}: expected exactly one spawn backend"
                    )
                cases.append((f"legacy-{name}", result, error))
                self._check_fake_cleanup_trace(
                    f"legacy-{name}",
                    process,
                    legacy_trace,
                    violations,
                    expect_term=name == "timeout",
                    expect_kill=name == "timeout",
                )

            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"pnpm"}),
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
                poll_interval=0.001,
            )
            browser_cases = (
                ("success", FakeProcess(returncode=0), 1, None),
                ("nonzero", FakeProcess(returncode=4), 1, None),
                ("timeout", FakeProcess(running=True), 0.000001, None),
                ("cancel", FakeProcess(running=True), 1, CancelImmediately()),
                (
                    "exception",
                    FakeProcess(
                        running=True,
                        communicate_effects=[RuntimeError("fake runner exception")],
                    ),
                    1,
                    None,
                ),
            )
            for name, process, timeout, lifecycle in browser_cases:
                browser_trace: list[tuple[object, ...]] = []
                process.trace = browser_trace
                if name in {"timeout", "cancel"}:
                    process.wait_effects = [
                        subprocess.TimeoutExpired(BUILD_COMMAND, 1),
                        -9,
                    ]
                elif name == "exception":
                    process.returncode = 0
                confirmation = self._confirmation_for(
                    root,
                    PROFILE_IDS["visionforge_build"],
                    "/usr/bin/pnpm",
                    BUILD_COMMAND,
                    wall_deadline_seconds=timeout,
                )
                kwargs = {
                    "cwd": root,
                    "timeout_seconds": timeout,
                    "lifecycle": lifecycle,
                }
                self._add_trusted_local(kwargs, confirmation)
                popen = FakePopenFactory(lambda process=process: process)
                browser_signals = FakeSignalFactory(
                    trace=browser_trace,
                    label="killpg",
                )
                browser_kill = FakeSignalFactory(
                    trace=browser_trace,
                    label="kill",
                )
                with self._patched_processes(
                    popen,
                    FakeRunFactory(),
                    kill=browser_kill,
                    killpg=browser_signals,
                ):
                    result, error = self._capture(lambda: browser.run(
                        BUILD_COMMAND,
                        **kwargs,
                    ))
                cases.append((f"browser-{name}", result, error))
                self._check_fake_cleanup_trace(
                    f"browser-{name}",
                    process,
                    browser_trace,
                    violations,
                    expect_term=name in {"timeout", "cancel"},
                    expect_kill=name in {"timeout", "cancel"},
                )

            dev_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            dev_kwargs = {"cwd": root, "log_path": root / "server.log"}
            self._add_trusted_local(dev_kwargs, dev_confirmation)
            background_trace: list[tuple[object, ...]] = []
            background_process = FakeProcess(
                running=True,
                wait_effects=[
                    subprocess.TimeoutExpired(DEV_COMMAND, 1),
                    -9,
                ],
                trace=background_trace,
            )
            popen = FakePopenFactory(lambda: background_process)
            background_signals = FakeSignalFactory(
                trace=background_trace,
                label="killpg",
            )
            background_kill = FakeSignalFactory(
                trace=background_trace,
                label="kill",
            )
            with self._patched_processes(
                popen,
                FakeRunFactory(),
                kill=background_kill,
                killpg=background_signals,
            ):
                managed, error = self._capture(lambda: browser.start_background(
                    DEV_COMMAND,
                    **dev_kwargs,
                ))
                if managed is not None:
                    _, stop_error = self._capture(managed.stop)
                    error = error or stop_error
            cases.append(("browser-background-stop", managed, error))
            self._check_fake_cleanup_trace(
                "browser-background-stop",
                background_process,
                background_trace,
                violations,
                expect_term=True,
                expect_kill=True,
            )

            readiness_project = root / "readiness-project"
            readiness_project.mkdir()
            readiness_runner = readiness_project / "runner.mjs"
            readiness_runner.write_text("", encoding="utf-8")
            (readiness_project / "visionforge.template.json").write_text(
                json.dumps(self._browser_config("runner.mjs")),
                encoding="utf-8",
            )
            readiness_processes = ScriptedBrowserRunner([])
            readiness_trace: list[tuple[object, ...]] = []
            readiness_process = FakeProcess(
                running=True,
                wait_effects=[
                    subprocess.TimeoutExpired(DEV_COMMAND, 1),
                    -9,
                ],
                trace=readiness_trace,
            )
            readiness_signals = FakeSignalFactory(
                trace=readiness_trace,
                label="killpg",
            )
            readiness_processes.managed = FakeManaged(
                running=False,
                process=readiness_process,
                killpg=readiness_signals,
            )
            runtime = BrowserProjectRuntime(
                readiness_project,
                readiness_processes,
            )
            result, error = self._capture(lambda: self._consume_context(
                runtime.running_server(log_path=root / "readiness.log")
            ))
            cases.append(("browser-readiness-failure", result, error))
            self._check_fake_cleanup_trace(
                "browser-readiness-failure",
                readiness_process,
                readiness_trace,
                violations,
                expect_term=True,
                expect_kill=True,
            )

            for name, result, error in cases:
                self._check_terminal_contract(
                    name,
                    result,
                    error,
                    violations,
                )
                self._check_cleanup_evidence(
                    name,
                    result,
                    error,
                    violations,
                )

        self.assertEqual(
            violations,
            [],
            "SEC-E: every terminal path must cross one verified Finalizer; "
            f"violations={violations}",
        )

    def test_e_cleanup_failure_is_typed_and_quarantines_the_workspace(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "browser-runner.mjs").write_text(
                "// SEC-EXEC fake runner\n",
                encoding="utf-8",
            )
            (root / "ui-spec.json").write_text("{}\n", encoding="utf-8")
            runner = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            cleanup_failure_trace: list[tuple[object, ...]] = []
            timeout_process = FakeProcess(
                running=True,
                communicate_effects=[
                    subprocess.TimeoutExpired(CORE_COMMAND, 0.01),
                ],
                trace=cleanup_failure_trace,
            )
            confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=0.01,
                python_profile=True,
            )
            blocked_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=0.01,
                python_profile=True,
            )
            cross_domain_admission_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=0.01,
                python_profile=True,
            )
            quarantine_probe_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=0.01,
                python_profile=True,
            )
            preliminary_issuer = getattr(
                coding_workflow,
                "issue_trusted_local_confirmation",
                None,
            )
            quarantine_control_requests = self._all_confirmation_requests(root)
            quarantine_control_tokens = (
                self._issue_confirmation_map(
                    preliminary_issuer,
                    quarantine_control_requests,
                    expires_at_monotonic=time.monotonic() + 60,
                )
                if callable(preliminary_issuer)
                else _UNSET
            )
            first_kwargs = {"timeout_seconds": 0.01}
            self._add_trusted_local(first_kwargs, confirmation)
            first = FakePopenFactory(lambda: timeout_process)
            failing_killpg = mock.Mock(
                side_effect=OSError("fake cleanup audit failure")
            )
            with self._patched_processes(
                first,
                FakeRunFactory(),
                killpg=failing_killpg,
            ), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                result, error = self._capture(lambda: runner.run(
                    CORE_COMMAND,
                    **first_kwargs,
                ))
            if len(first.calls) != 1:
                violations.append(
                    f"cleanup failure initial spawn_count={len(first.calls)}"
                )
            failing_signal_calls = [
                call.args for call in failing_killpg.call_args_list
            ]
            if not any(
                len(args) >= 2
                and args[0] == timeout_process.pid
                and args[1] in {signal.SIGTERM, signal.SIGKILL}
                for args in failing_signal_calls
            ):
                violations.append(
                    "cleanup failure did not exercise failing owned killpg"
                )
            if not self._has_structured_code(
                result,
                error,
                "CLEANUP_FAILED",
            ):
                violations.append("cleanup failure is not typed CLEANUP_FAILED")
            quarantine_id = self._first_mapping_value(
                result,
                error,
                "quarantine_id",
            )
            quarantine_generation = self._first_mapping_value(
                result,
                error,
                "quarantine_generation",
            )
            cleanup_evidence = self._first_mapping_value(
                result,
                error,
                "cleanup_evidence",
            )
            cleanup_evidence_digest = self._first_mapping_value(
                result,
                error,
                "cleanup_evidence_digest",
            )
            if not isinstance(quarantine_id, str) or not quarantine_id:
                violations.append("cleanup failure lacks structured quarantine_id")
            if (
                not isinstance(quarantine_generation, int)
                or isinstance(quarantine_generation, bool)
                or quarantine_generation <= 0
            ):
                violations.append(
                    "cleanup failure lacks positive quarantine_generation"
                )
            if not isinstance(cleanup_evidence, Mapping):
                violations.append("cleanup failure lacks structured cleanup_evidence")
            if not self._is_digest(cleanup_evidence_digest):
                violations.append(
                    "cleanup failure lacks Runtime cleanup_evidence_digest"
                )
            self._check_cleanup_evidence(
                "cleanup-failure",
                result,
                error,
                violations,
                expect_verified=False,
            )

            second = FakePopenFactory()
            second_kwargs = {"timeout_seconds": 0.01}
            self._add_trusted_local(second_kwargs, blocked_confirmation)
            with self._patched_processes(second, FakeRunFactory()), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                blocked_result, blocked_error = self._capture(lambda: runner.run(
                    CORE_COMMAND,
                    **second_kwargs,
                ))
            if second.calls:
                violations.append("quarantined Workspace spawned again")
            if not self._has_structured_code(
                blocked_result,
                blocked_error,
                "SANDBOX_REQUIRED",
            ):
                violations.append(
                    "quarantined Workspace lacks structured SANDBOX_REQUIRED"
                )
            if not isinstance(quarantine_control_tokens, Mapping):
                violations.append(
                    "cross-instance quarantine control tokens unavailable"
                )
            else:
                quarantine_observations = self._invoke_all_entrypoints(
                    root,
                    use_default_limits=True,
                    trusted_local_by_entrypoint=quarantine_control_tokens,
                )
                for (
                    entrypoint,
                    quarantine_result,
                    quarantine_error,
                    quarantine_spawn_count,
                    _,
                ) in quarantine_observations:
                    if (
                        quarantine_spawn_count != 0
                        or not self._has_structured_code(
                            quarantine_result,
                            quarantine_error,
                            "SANDBOX_REQUIRED",
                        )
                    ):
                        violations.append(
                            "quarantine did not fence new instance "
                            f"{entrypoint}: spawn={quarantine_spawn_count}"
                        )

            request_recovery = getattr(
                coding_workflow,
                "request_local_execution_recovery",
                None,
            )
            recover = getattr(
                coding_workflow,
                "recover_local_execution_quarantine",
                None,
            )
            issuer = getattr(
                coding_workflow,
                "issue_trusted_local_confirmation",
                None,
            )
            if not all(callable(item) for item in (
                request_recovery,
                recover,
                issuer,
            )):
                violations.append(
                    "operator recovery request/recover/issuer seam unavailable"
                )
            elif not isinstance(quarantine_id, str) or not quarantine_id:
                violations.append("operator recovery blocked by absent quarantine_id")
            else:
                request_signature, request_signature_error = self._capture(
                    lambda: inspect.signature(request_recovery)
                )
                recover_signature, recover_signature_error = self._capture(
                    lambda: inspect.signature(recover)
                )
                if request_signature_error is not None or not isinstance(
                    request_signature, inspect.Signature
                ):
                    violations.append("recovery request signature unavailable")
                    request_parameters: Mapping = {}
                else:
                    request_parameters = request_signature.parameters
                if recover_signature_error is not None or not isinstance(
                    recover_signature, inspect.Signature
                ):
                    violations.append("recover signature unavailable")
                    recovery_parameters: Mapping = {}
                else:
                    recovery_parameters = recover_signature.parameters
                if (
                    set(request_parameters) != {"quarantine_id"}
                    or any(
                        parameter.kind is not inspect.Parameter.KEYWORD_ONLY
                        for parameter in request_parameters.values()
                    )
                ):
                    violations.append("recovery request signature is not operator-only")
                if (
                    set(recovery_parameters)
                    != {"quarantine_id", "recovery_confirmation"}
                    or any(
                        parameter.kind is not inspect.Parameter.KEYWORD_ONLY
                        for parameter in recovery_parameters.values()
                    )
                    or recovery_parameters["recovery_confirmation"].default
                    is not inspect.Parameter.empty
                ):
                    violations.append(
                        "recover seam permits naked or non-keyword clear"
                    )

                alive_popen = FakePopenFactory()
                alive_run = FakeRunFactory()
                alive_kill = FakeSignalFactory(probe_alive=True)
                alive_killpg = FakeSignalFactory(probe_alive=True)
                with self._patched_processes(
                    alive_popen,
                    alive_run,
                    kill=alive_kill,
                    killpg=alive_killpg,
                ):
                    alive_result, alive_error = self._capture(
                        lambda: request_recovery(quarantine_id=quarantine_id)
                    )
                if not self._has_structured_code(
                    alive_result,
                    alive_error,
                    "SANDBOX_REQUIRED",
                ):
                    violations.append(
                        "recovery request did not reject a live owned resource"
                    )
                if self._first_mapping_value(
                    alive_result,
                    alive_error,
                    "recovery_request",
                ) is not _UNSET or self._first_mapping_value(
                    alive_result,
                    alive_error,
                    "confirmation_request",
                ) is not _UNSET:
                    violations.append(
                        "live owned resource exposed a signable recovery challenge"
                    )
                if alive_popen.calls or self._factory_call_count(alive_run):
                    violations.append("recovery audit unexpectedly spawned")
                owned_signal_probes = [
                    args
                    for fake in (alive_kill, alive_killpg)
                    for args, _ in fake.calls
                    if len(args) >= 2 and args[1] == 0
                ]
                owned_poll_probes = [
                    item for item in cleanup_failure_trace
                    if item[:2] == ("poll", timeout_process.pid)
                ]
                if (
                    not any(
                        args[0] == timeout_process.pid
                        for args in owned_signal_probes
                    )
                    and not owned_poll_probes
                ):
                    violations.append(
                        "recovery live-resource verification was not bound to "
                        f"owned identity={timeout_process.pid}"
                    )

                timeout_process.returncode = -9
                with self._patched_processes(
                    FakePopenFactory(),
                    FakeRunFactory(),
                    kill=FakeSignalFactory(),
                    killpg=FakeSignalFactory(),
                ):
                    recovery_result, recovery_error = self._capture(
                        lambda: request_recovery(quarantine_id=quarantine_id)
                    )
                recovery_request = self._first_mapping_value(
                    recovery_result,
                    recovery_error,
                    "recovery_request",
                )
                required = {
                    "quarantine_id",
                    "quarantine_generation",
                    "workspace_digest",
                    "input_digest",
                    "profile_digest",
                    "cleanup_evidence_digest",
                    "recovery_evidence_digest",
                }
                if (
                    not isinstance(recovery_request, Mapping)
                    or set(recovery_request) != required
                ):
                    violations.append(
                        "independent recheck lacks structured recovery_request"
                    )
                else:
                    if recovery_request["quarantine_id"] != quarantine_id:
                        violations.append("recovery_request quarantine_id mismatch")
                    if (
                        isinstance(quarantine_generation, int)
                        and recovery_request["quarantine_generation"]
                        != quarantine_generation
                    ):
                        violations.append(
                            "recovery_request quarantine_generation mismatch"
                        )
                    if (
                        self._is_digest(cleanup_evidence_digest)
                        and recovery_request["cleanup_evidence_digest"]
                        != cleanup_evidence_digest
                    ):
                        violations.append(
                            "recovery_request changed cleanup evidence binding"
                        )
                    for name in (
                        "workspace_digest",
                        "input_digest",
                        "profile_digest",
                        "cleanup_evidence_digest",
                        "recovery_evidence_digest",
                    ):
                        if not self._is_digest(recovery_request[name]):
                            violations.append(
                                f"recovery_request {name} is not Runtime digest"
                            )
                    negative_tokens: list[
                        tuple[str, str, object, float | None]
                    ] = []
                    for field in (
                        "workspace_digest",
                        "input_digest",
                        "profile_digest",
                    ):
                        changed = dict(recovery_request)
                        changed[field] = self._different_digest(changed[field])
                        try:
                            token = issuer(
                                workspace_digest=changed["workspace_digest"],
                                input_digest=changed["input_digest"],
                                profile_digest=changed["profile_digest"],
                                expires_at_monotonic=time.monotonic() + 60,
                            )
                        except Exception as exc:
                            violations.append(
                                f"recovery {field} drift token unavailable: "
                                f"{type(exc).__name__}"
                            )
                        else:
                            negative_tokens.append((
                                f"{field}-drift",
                                quarantine_id,
                                token,
                                None,
                            ))
                    recovery_expiry_base = time.monotonic()
                    try:
                        expired_recovery = issuer(
                            workspace_digest=recovery_request["workspace_digest"],
                            input_digest=recovery_request["input_digest"],
                            profile_digest=recovery_request["profile_digest"],
                            expires_at_monotonic=recovery_expiry_base + 0.01,
                        )
                    except Exception as exc:
                        violations.append(
                            "expired recovery token unavailable: "
                            f"{type(exc).__name__}"
                        )
                    else:
                        negative_tokens.append((
                            "expired",
                            quarantine_id,
                            expired_recovery,
                            recovery_expiry_base + 1.0,
                        ))
                    try:
                        wrong_id_token = issuer(
                            workspace_digest=recovery_request["workspace_digest"],
                            input_digest=recovery_request["input_digest"],
                            profile_digest=recovery_request["profile_digest"],
                            expires_at_monotonic=time.monotonic() + 60,
                        )
                    except Exception as exc:
                        violations.append(
                            "recovery wrong-id token unavailable: "
                            f"{type(exc).__name__}"
                        )
                    else:
                        negative_tokens.append((
                            "wrong-quarantine-id",
                            quarantine_id + "-wrong",
                            wrong_id_token,
                            None,
                        ))
                    for case_name, case_id, token, advanced_clock in negative_tokens:
                        clock = (
                            self._patched_monotonic(advanced_clock)
                            if advanced_clock is not None
                            else contextlib.nullcontext()
                        )
                        with clock:
                            denied_result, denied_error = self._capture(
                                lambda: recover(
                                    quarantine_id=case_id,
                                    recovery_confirmation=token,
                                )
                            )
                        if not self._has_structured_code(
                            denied_result,
                            denied_error,
                            "SANDBOX_REQUIRED",
                        ):
                            violations.append(
                                f"recovery {case_name} was not rejected"
                            )
                    for case_name, plain_confirmation in (
                        ("plain-bool", True),
                        ("plain-dict", {"recovery_confirmation": True}),
                    ):
                        denied_result, denied_error = self._capture(lambda: recover(
                            quarantine_id=quarantine_id,
                            recovery_confirmation=plain_confirmation,
                        ))
                        if not self._has_structured_code(
                            denied_result,
                            denied_error,
                            "SANDBOX_REQUIRED",
                        ):
                            violations.append(
                                f"recovery {case_name} was not rejected"
                            )
                    if cross_domain_admission_confirmation is _UNSET:
                        violations.append(
                            "ordinary admission token unavailable for domain test"
                        )
                    else:
                        cross_result, cross_error = self._capture(lambda: recover(
                            quarantine_id=quarantine_id,
                            recovery_confirmation=(
                                cross_domain_admission_confirmation
                            ),
                        ))
                        if not self._has_structured_code(
                            cross_result,
                            cross_error,
                            "SANDBOX_REQUIRED",
                        ):
                            violations.append(
                                "ordinary admission token crossed into recovery"
                            )

                    try:
                        execution_cross_token = issuer(
                            workspace_digest=recovery_request["workspace_digest"],
                            input_digest=recovery_request["input_digest"],
                            profile_digest=recovery_request["profile_digest"],
                            expires_at_monotonic=time.monotonic() + 60,
                        )
                    except Exception as exc:
                        execution_cross_token = _UNSET
                        violations.append(
                            "recovery-domain execution token unavailable: "
                            f"{type(exc).__name__}"
                        )
                    try:
                        reappeared_token = issuer(
                            workspace_digest=recovery_request["workspace_digest"],
                            input_digest=recovery_request["input_digest"],
                            profile_digest=recovery_request["profile_digest"],
                            expires_at_monotonic=time.monotonic() + 60,
                        )
                    except Exception as exc:
                        violations.append(
                            "resource-reappearance token unavailable: "
                            f"{type(exc).__name__}"
                        )
                    else:
                        with self._patched_processes(
                            FakePopenFactory(),
                            FakeRunFactory(),
                            kill=FakeSignalFactory(probe_alive=True),
                            killpg=FakeSignalFactory(probe_alive=True),
                        ):
                            reappeared_result, reappeared_error = self._capture(
                                lambda: recover(
                                    quarantine_id=quarantine_id,
                                    recovery_confirmation=reappeared_token,
                                )
                            )
                        if not self._has_structured_code(
                            reappeared_result,
                            reappeared_error,
                            "SANDBOX_REQUIRED",
                        ):
                            violations.append(
                                "owned resource reappearance did not block recovery"
                            )
                        with self._patched_processes(
                            FakePopenFactory(),
                            FakeRunFactory(),
                            kill=FakeSignalFactory(),
                            killpg=FakeSignalFactory(),
                        ):
                            replay_after_gone, replay_after_gone_error = (
                                self._capture(lambda: recover(
                                    quarantine_id=quarantine_id,
                                    recovery_confirmation=reappeared_token,
                                ))
                            )
                        if not self._has_structured_code(
                            replay_after_gone,
                            replay_after_gone_error,
                            "SANDBOX_REQUIRED",
                        ):
                            violations.append(
                                "live-resource rejected token was reusable after "
                                "the resource disappeared"
                            )

                    probe_kwargs = {"timeout_seconds": 0.01}
                    self._add_trusted_local(
                        probe_kwargs,
                        quarantine_probe_confirmation,
                    )
                    probe_spawn = FakePopenFactory()
                    with self._patched_processes(
                        probe_spawn,
                        FakeRunFactory(),
                    ), mock.patch.object(
                        command_module.shutil,
                        "which",
                        return_value="/usr/bin/python3",
                    ):
                        probe_result, probe_error = self._capture(lambda: runner.run(
                            CORE_COMMAND,
                            **probe_kwargs,
                        ))
                    if probe_spawn.calls or not self._has_structured_code(
                        probe_result,
                        probe_error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            "resource-reappearance failure did not retain quarantine"
                        )

                    with self._patched_processes(
                        FakePopenFactory(),
                        FakeRunFactory(),
                        kill=FakeSignalFactory(),
                        killpg=FakeSignalFactory(),
                    ):
                        refreshed_result, refreshed_error = self._capture(
                            lambda: request_recovery(quarantine_id=quarantine_id)
                        )
                    refreshed_request = self._first_mapping_value(
                        refreshed_result,
                        refreshed_error,
                        "recovery_request",
                    )
                    if (
                        not isinstance(refreshed_request, Mapping)
                        or set(refreshed_request) != required
                    ):
                        violations.append(
                            "post-reappearance independent recovery request absent"
                        )
                    else:
                        old_generation_token = _UNSET
                        try:
                            old_generation_token = issuer(
                                workspace_digest=refreshed_request["workspace_digest"],
                                input_digest=refreshed_request["input_digest"],
                                profile_digest=refreshed_request["profile_digest"],
                                expires_at_monotonic=time.monotonic() + 60,
                            )
                        except Exception as exc:
                            violations.append(
                                "old-generation control token unavailable: "
                                f"{type(exc).__name__}"
                            )
                        race_tokens = []
                        for index in range(2):
                            try:
                                race_tokens.append(issuer(
                                    workspace_digest=refreshed_request["workspace_digest"],
                                    input_digest=refreshed_request["input_digest"],
                                    profile_digest=refreshed_request["profile_digest"],
                                    expires_at_monotonic=time.monotonic() + 60,
                                ))
                            except Exception as exc:
                                violations.append(
                                    f"recovery race token {index} unavailable: "
                                    f"{type(exc).__name__}"
                                )
                        recovery_cleared = False
                        if len(race_tokens) == 2:
                            barrier = threading.Barrier(3)
                            race_outcomes: list[
                                tuple[object, BaseException | None] | None
                            ] = [None, None]

                            def recover_competitor(index: int) -> None:
                                barrier.wait()
                                race_outcomes[index] = self._capture(lambda: recover(
                                    quarantine_id=quarantine_id,
                                    recovery_confirmation=race_tokens[index],
                                ))

                            threads = [
                                self._registered_thread(
                                    target=recover_competitor,
                                    args=(index,),
                                    name=f"sec-e-recovery-race-{index}",
                                )
                                for index in range(2)
                            ]
                            with self._patched_processes(
                                FakePopenFactory(),
                                FakeRunFactory(),
                                kill=FakeSignalFactory(),
                                killpg=FakeSignalFactory(),
                            ):
                                for thread in threads:
                                    thread.start()
                                barrier.wait()
                                for thread in threads:
                                    thread.join(timeout=2)
                            if any(thread.is_alive() for thread in threads):
                                violations.append("recovery race did not finish")
                            completed = [
                                item for item in race_outcomes if item is not None
                            ]
                            successes = [
                                item for item in completed
                                if self._has_recovery_success(
                                    item[0], item[1], quarantine_id
                                )
                            ]
                            rejected = [
                                item for item in completed
                                if self._has_structured_code(
                                    item[0], item[1], "SANDBOX_REQUIRED"
                                )
                            ]
                            if len(successes) != 1 or len(rejected) != 1:
                                violations.append(
                                    "same-generation recovery race was not linearized"
                                )
                            recovery_cleared = len(successes) == 1

                        if recovery_cleared and execution_cross_token is not _UNSET:
                            cross_runner = ControlledCommandRunner(
                                root,
                                CommandPolicy(
                                    allowed_executables={"python3"},
                                    allowed_commands=[list(CORE_COMMAND)],
                                ),
                                output_limit_chars=10_000,
                            )
                            cross_kwargs = {"timeout_seconds": 0.01}
                            self._add_trusted_local(
                                cross_kwargs,
                                execution_cross_token,
                            )
                            cross_spawn = FakePopenFactory()
                            with self._patched_processes(
                                cross_spawn,
                                FakeRunFactory(),
                            ), mock.patch.object(
                                command_module.shutil,
                                "which",
                                return_value="/usr/bin/python3",
                            ):
                                cross_result, cross_error = self._capture(
                                    lambda: cross_runner.run(
                                        CORE_COMMAND,
                                        **cross_kwargs,
                                    )
                                )
                            if cross_spawn.calls or not self._has_structured_code(
                                cross_result,
                                cross_error,
                                "SANDBOX_REQUIRED",
                            ):
                                violations.append(
                                    "recovery-domain token crossed into an "
                                    "unquarantined same-profile execution"
                                )
                        elif recovery_cleared:
                            violations.append(
                                "recovery-domain execution controls unavailable"
                            )

                        next_generation_recovered = False
                        if recovery_cleared:
                            next_failure_confirmation = self._confirmation_for(
                                root,
                                PROFILE_IDS["core"],
                                "/usr/bin/python3",
                                CORE_COMMAND,
                                wall_deadline_seconds=0.01,
                                python_profile=True,
                            )
                            next_failure_kwargs = {"timeout_seconds": 0.01}
                            self._add_trusted_local(
                                next_failure_kwargs,
                                next_failure_confirmation,
                            )
                            next_timeout_process = FakeProcess(
                                running=True,
                                communicate_effects=[
                                    subprocess.TimeoutExpired(
                                        CORE_COMMAND,
                                        0.01,
                                    ),
                                ],
                            )
                            next_spawn = FakePopenFactory(
                                lambda: next_timeout_process
                            )
                            with self._patched_processes(
                                next_spawn,
                                FakeRunFactory(),
                                killpg=mock.Mock(side_effect=OSError(
                                    "fake next-generation cleanup failure"
                                )),
                            ), mock.patch.object(
                                command_module.shutil,
                                "which",
                                return_value="/usr/bin/python3",
                            ):
                                next_result, next_error = self._capture(
                                    lambda: runner.run(
                                        CORE_COMMAND,
                                        **next_failure_kwargs,
                                    )
                                )
                            if not self._has_structured_code(
                                next_result,
                                next_error,
                                "CLEANUP_FAILED",
                            ):
                                violations.append(
                                    "second cleanup failure is not CLEANUP_FAILED"
                                )
                            next_id = self._first_mapping_value(
                                next_result,
                                next_error,
                                "quarantine_id",
                            )
                            next_generation = self._first_mapping_value(
                                next_result,
                                next_error,
                                "quarantine_generation",
                            )
                            old_generation = refreshed_request.get(
                                "quarantine_generation"
                            )
                            if (
                                not isinstance(next_id, str)
                                or not next_id
                                or not isinstance(next_generation, int)
                                or isinstance(next_generation, bool)
                                or next_generation <= 0
                            ):
                                violations.append(
                                    "next quarantine fence lacks a current identity/generation"
                                )

                            next_timeout_process.returncode = -9
                            if isinstance(next_id, str) and next_id:
                                with self._patched_processes(
                                    FakePopenFactory(),
                                    FakeRunFactory(),
                                    kill=FakeSignalFactory(),
                                    killpg=FakeSignalFactory(),
                                ):
                                    next_request_result, next_request_error = (
                                        self._capture(lambda: request_recovery(
                                            quarantine_id=next_id
                                        ))
                                    )
                                next_request = self._first_mapping_value(
                                    next_request_result,
                                    next_request_error,
                                    "recovery_request",
                                )
                            else:
                                next_request = _UNSET
                            if (
                                not isinstance(next_request, Mapping)
                                or set(next_request) != required
                            ):
                                violations.append(
                                    "next-fence recovery request unavailable"
                                )
                            else:
                                if next_request.get(
                                    "quarantine_generation"
                                ) != next_generation:
                                    violations.append(
                                        "next-fence recovery request generation mismatch"
                                    )
                                old_recovery_evidence = refreshed_request.get(
                                    "recovery_evidence_digest"
                                )
                                new_recovery_evidence = next_request.get(
                                    "recovery_evidence_digest"
                                )
                                old_fence = (
                                    refreshed_request.get("quarantine_id"),
                                    old_generation,
                                    old_recovery_evidence,
                                )
                                new_fence = (
                                    next_request.get("quarantine_id"),
                                    next_generation,
                                    new_recovery_evidence,
                                )
                                if (
                                    not self._is_digest(old_recovery_evidence)
                                    or not self._is_digest(new_recovery_evidence)
                                    or new_fence == old_fence
                                ):
                                    violations.append(
                                        "next independent quarantine fence did not change"
                                    )
                                if old_generation_token is _UNSET:
                                    violations.append(
                                        "stale token unavailable for next-fence control"
                                    )
                                else:
                                    with self._patched_processes(
                                        FakePopenFactory(),
                                        FakeRunFactory(),
                                        kill=FakeSignalFactory(),
                                        killpg=FakeSignalFactory(),
                                    ):
                                        stale_result, stale_error = self._capture(
                                            lambda: recover(
                                                quarantine_id=next_id,
                                                recovery_confirmation=(
                                                    old_generation_token
                                                ),
                                            )
                                        )
                                    if not self._has_structured_code(
                                        stale_result,
                                        stale_error,
                                        "SANDBOX_REQUIRED",
                                    ):
                                        violations.append(
                                            "old-fence recovery token accepted for next fence"
                                        )
                                try:
                                    next_token = issuer(
                                        workspace_digest=next_request[
                                            "workspace_digest"
                                        ],
                                        input_digest=next_request["input_digest"],
                                        profile_digest=next_request[
                                            "profile_digest"
                                        ],
                                        expires_at_monotonic=(
                                            time.monotonic() + 60
                                        ),
                                    )
                                except Exception as exc:
                                    violations.append(
                                        "next-fence recovery token unavailable: "
                                        f"{type(exc).__name__}"
                                    )
                                else:
                                    with self._patched_processes(
                                        FakePopenFactory(),
                                        FakeRunFactory(),
                                        kill=FakeSignalFactory(),
                                        killpg=FakeSignalFactory(),
                                    ):
                                        next_recovered, next_recovery_error = (
                                            self._capture(lambda: recover(
                                                quarantine_id=next_id,
                                                recovery_confirmation=next_token,
                                            ))
                                        )
                                    next_generation_recovered = (
                                        self._has_recovery_success(
                                            next_recovered,
                                            next_recovery_error,
                                            next_id,
                                        )
                                    )
                                    if not next_generation_recovered:
                                        violations.append(
                                            "valid next-fence recovery token did not clear"
                                        )

                        if next_generation_recovered:
                            normal_confirmation = self._confirmation_for(
                                root,
                                PROFILE_IDS["core"],
                                "/usr/bin/python3",
                                CORE_COMMAND,
                                wall_deadline_seconds=1,
                                python_profile=True,
                            )
                            normal_kwargs = {"timeout_seconds": 1}
                            self._add_trusted_local(
                                normal_kwargs,
                                normal_confirmation,
                            )
                            normal_spawn = FakePopenFactory()
                            with self._patched_processes(
                                normal_spawn,
                                FakeRunFactory(),
                            ), mock.patch.object(
                                command_module.shutil,
                                "which",
                                return_value="/usr/bin/python3",
                            ):
                                self._capture(lambda: runner.run(
                                    CORE_COMMAND,
                                    **normal_kwargs,
                                ))
                            if len(normal_spawn.calls) != 1:
                                violations.append(
                                    "fresh-token normal control did not recover"
                                )

        self.assertEqual(
            violations,
            [],
            "SEC-E: failed cleanup must return CLEANUP_FAILED, quarantine, and "
            "only recover through a generation/evidence-bound opaque operator "
            "action before a fresh-token normal control; "
            f"violations={violations}",
        )

    def test_f_result_and_log_representations_never_retain_fake_secret(self) -> None:
        violations: list[str] = []

        class SlottedResult:
            __slots__ = ("_raw_stdout",)

            def __init__(self) -> None:
                self._raw_stdout = FAKE_SECRET

        class PropertyResult:
            __slots__ = ()

            @property
            def raw_payload(self) -> str:
                return FAKE_SECRET

        if not self._direct_secret_locations(
            SlottedResult(),
            None,
            FAKE_SECRET,
        ) or not self._direct_secret_locations(
            PropertyResult(),
            None,
            FAKE_SECRET,
        ):
            violations.append(
                "direct-field scanner missed slots/property-only sentinel"
            )
        pipe_probe_factory = self._fake_background_popen("pipe-fixture-ready")
        pipe_probe, pipe_probe_error = self._capture(
            lambda: pipe_probe_factory(
                ["fake-background-child"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        )
        pipe_probe_text = None
        if pipe_probe_error is None and pipe_probe is not None:
            pipe_probe_text, pipe_read_error = self._capture(
                pipe_probe.stdout.read
            )
            _, pipe_close_error = self._capture(pipe_probe.stdout.close)
            if pipe_read_error is not None or pipe_close_error is not None:
                violations.append(
                    "tracked os.pipe fixture could not be read/closed"
                )
        if pipe_probe_error is not None or pipe_probe_text != "pipe-fixture-ready":
            violations.append(
                "tracked os.pipe fixture did not deliver bounded payload"
            )
        self._check_background_pipe_closed(
            pipe_probe_factory,
            "direct os.pipe fixture",
            violations,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner_path = root / "browser-runner.mjs"
            spec_path = root / "ui-spec.json"
            runner_path.write_text("// fake runner\n", encoding="utf-8")
            spec_path.write_text("{}\n", encoding="utf-8")
            browser_command = (
                "node", str(runner_path),
                "--url", "http://127.0.0.1:4173/",
                "--spec", str(spec_path),
                "--screenshot", str(root / "actual.png"),
                "--result", str(root / "result.json"),
            )
            secret_stdout = f"api_key={FAKE_SECRET}"
            secret_stderr = f"Bearer {FAKE_SECRET}"
            results: list[tuple[str, object, BaseException | None, int]] = []

            core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            core_token = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
            )
            core_popen = FakePopenFactory(
                lambda: FakeProcess(secret_stdout, secret_stderr)
            )
            with self._patched_processes(
                core_popen,
                FakeRunFactory(),
            ), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                core_result, core_error = self._capture(lambda: core.run(
                    CORE_COMMAND,
                    timeout_seconds=1,
                    trusted_local=core_token,
                ))
            results.append(("core", core_result, core_error, len(core_popen.calls)))

            legacy = ProjectWorkspace(root, command_timeout=60)
            legacy_token = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            legacy_popen = FakePopenFactory(
                lambda: FakeProcess(secret_stdout, secret_stderr)
            )
            legacy_run = FakeRunFactory(secret_stdout, secret_stderr)
            with self._patched_processes(legacy_popen, legacy_run):
                legacy_result, legacy_error = self._capture(lambda: legacy.run(
                    list(LEGACY_COMMAND),
                    trusted_local=legacy_token,
                ))
            results.append((
                "legacy",
                legacy_result,
                legacy_error,
                len(legacy_popen.calls) + len(legacy_run.calls),
            ))

            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"node", "pnpm"}),
                executable_overrides={
                    "node": "/usr/bin/node",
                    "pnpm": "/usr/bin/pnpm",
                },
            )
            browser_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=45,
            )
            browser_popen = FakePopenFactory(
                lambda: FakeProcess(secret_stdout, secret_stderr)
            )
            with self._patched_processes(browser_popen, FakeRunFactory()):
                browser_result, browser_error = self._capture(lambda: browser.run(
                    browser_command,
                    cwd=root,
                    timeout_seconds=45,
                    trusted_local=browser_token,
                ))
            results.append((
                "browser",
                browser_result,
                browser_error,
                len(browser_popen.calls),
            ))

            for name, value, error, spawn_count in results:
                direct_hits = self._direct_secret_locations(
                    value,
                    error,
                    FAKE_SECRET,
                )
                if direct_hits:
                    violations.append(
                        f"{name}: direct Result/error fields={direct_hits}"
                    )
                if error is not None or value is None or spawn_count != 1:
                    violations.append(
                        f"{name}: secret fixture did not cross one supervised spawn"
                    )
                    continue
                serialized = [repr(value)] + [
                    repr(mapping)
                    for mapping in self._structured_mappings(value, error)
                ]
                if any(FAKE_SECRET in item for item in serialized):
                    violations.append(f"{name}: returned representation")

            error_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=45,
            )
            exploding_popen = mock.Mock(side_effect=OSError(
                f"spawn error api_key={FAKE_SECRET}"
            ))
            with self._patched_processes(
                exploding_popen,
                FakeRunFactory(),
            ):
                error_result, error_error = self._capture(lambda: browser.run(
                    browser_command,
                    cwd=root,
                    timeout_seconds=45,
                    trusted_local=error_token,
                ))
            if exploding_popen.call_count != 1:
                violations.append(
                    "browser error-field fixture did not cross one supervised spawn"
                )
            if error_error is None or error_result is not None:
                violations.append(
                    "browser error-field fixture did not produce a public error"
                )
            error_hits = self._direct_secret_locations(
                error_result,
                error_error,
                FAKE_SECRET,
            )
            if error_hits:
                violations.append(
                    f"browser: direct sanitized error fields={error_hits}"
                )

            log_path = root / "server.log"
            log_payload = f"server api_key={FAKE_SECRET}\n"

            dev_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            dev_popen = self._fake_background_popen(log_payload)
            log_view = ""
            running_durable_log = ""
            self._check_cached_os_write_aliases(root, violations)
            with self._record_temp_writes(root) as write_recorder:
                recorder_probe = root / "write-recorder-probe.txt"
                recorder_probe.write_text(FAKE_SECRET, encoding="utf-8")
                recorder_probe.write_text("overwritten-safe", encoding="utf-8")
                if not self._write_events_containing(
                    write_recorder.events,
                    FAKE_SECRET,
                ):
                    violations.append(
                        "persistent-write recorder missed overwritten sentinel"
                    )
                write_recorder.events.clear()
                probe_fd = os.open(
                    recorder_probe,
                    os.O_WRONLY | os.O_TRUNC,
                )
                secret_bytes = FAKE_SECRET.encode("utf-8")
                midpoint = len(secret_bytes) // 2
                os.write(probe_fd, secret_bytes[:midpoint])
                os.write(probe_fd, secret_bytes[midpoint:])
                os.close(probe_fd)
                recorder_probe.write_text("low-level-overwritten-safe")
                if not self._write_events_containing(
                    write_recorder.events,
                    FAKE_SECRET,
                ):
                    violations.append(
                        "low-level write recorder missed split/overwritten sentinel"
                    )
                write_recorder.events.clear()
                with self._patched_processes(dev_popen, FakeRunFactory()):
                    managed, managed_error = self._capture(
                        lambda: browser.start_background(
                            DEV_COMMAND,
                            cwd=root,
                            log_path=log_path,
                            trusted_local=dev_token,
                        )
                    )
                    if (
                        len(dev_popen.calls) != 1
                        or dev_popen.calls[0][1].get("stdout")
                        is not subprocess.PIPE
                    ):
                        violations.append(
                            "browser.server_log did not ingest through stdout=PIPE"
                        )
                    if managed_error is not None or managed is None:
                        violations.append("browser.server_log: managed result absent")
                    else:
                        if getattr(managed, "running", None) is not True:
                            violations.append(
                                "browser.server_log: fixture was not running at ingest"
                            )
                        log_view = self._poll_log_tail(
                            managed,
                            10_000,
                            "browser.server_log",
                            violations,
                        )
                        running_durable_log = (
                            log_path.read_text(encoding="utf-8", errors="replace")
                            if log_path.exists()
                            else ""
                        )
                        if FAKE_SECRET in running_durable_log:
                            violations.append(
                                "browser.server_log_file_while_running"
                            )
                        if len(running_durable_log) > 10_000:
                            violations.append(
                                "browser.server_log_file_while_running: "
                                "persisted output unbounded"
                            )
                        if self._write_events_containing(
                            write_recorder.events,
                            FAKE_SECRET,
                        ):
                            violations.append(
                                "browser.server_log raw sentinel reached a "
                                "persistent write before running scan"
                            )
                        self._scan_workspace_secret(
                            root,
                            FAKE_SECRET,
                            "browser.server_log_while_running",
                            violations,
                        )
                        _, stop_error = self._capture(managed.stop)
                        if stop_error is not None:
                            violations.append("browser.server_log: stop failed")
                        self._check_background_pipe_closed(
                            dev_popen,
                            "browser.server_log",
                            violations,
                        )
                if self._write_events_containing(
                    write_recorder.events,
                    FAKE_SECRET,
                ):
                    violations.append(
                        "browser.server_log raw sentinel reached persistent "
                        "write history before/through stop"
                    )
            if write_recorder.unclosed_fds:
                violations.append(
                    "persistent-write recorder observed unclosed low-level fds"
                )
            self._close_fake_process_streams(dev_popen)
            self._scan_workspace_secret(
                root,
                FAKE_SECRET,
                "browser.server_log_after_stop",
                violations,
            )
            durable_log = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.exists()
                else ""
            )
            for name, value in (
                ("browser.server_log_view", log_view),
                ("browser.server_log_file_while_running", running_durable_log),
                ("browser.server_log_file", durable_log),
            ):
                if FAKE_SECRET in value:
                    violations.append(name)
                if len(value) > 10_000:
                    violations.append(f"{name}: persisted output unbounded")

            database_path = root / "server-log-sink.sqlite3"
            with sqlite3.connect(str(database_path)) as connection:
                connection.execute("CREATE TABLE sink(value TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO sink(value) VALUES (?)",
                    ((log_view,), (running_durable_log,), (durable_log,)),
                )
                persisted = repr(connection.execute(
                    "SELECT value FROM sink ORDER BY rowid"
                ).fetchall())
            if FAKE_SECRET in persisted:
                violations.append("browser.server_log_sqlite_sink")
            self._scan_workspace_secret(
                root,
                FAKE_SECRET,
                "browser.server_log_after_sqlite",
                violations,
            )

        self.assertEqual(
            violations,
            [],
            "SEC-F: fake secret must not survive in any returned/log form; "
            f"leaked={violations}",
        )

    def test_f_overlimit_output_has_exact_head_tail_length_and_digest_metadata(self) -> None:
        violations: list[str] = []
        raw = "H" * 120 + "M" * 80 + "T" * 120
        short = "short-output\n"
        limit = 200
        expected = (
            raw[:100]
            + f"\n... [TRUNCATED {len(raw) - limit} CHARS] ...\n"
            + raw[-100:]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=limit,
            )
            core_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
                output_limit_chars=limit,
            )
            core_kwargs = {"timeout_seconds": 1}
            self._add_trusted_local(core_kwargs, core_confirmation)
            popen = FakePopenFactory(lambda: FakeProcess(raw, raw))
            with self._patched_processes(popen, FakeRunFactory()), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                core_result, core_error = self._capture(lambda: core.run(
                    CORE_COMMAND,
                    **core_kwargs,
                ))
            self._check_bounded_output(
                "core",
                core_result,
                core_error,
                expected,
                len(raw),
                digest,
                violations,
            )
            short_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
                output_limit_chars=limit,
            )
            short_kwargs = {"timeout_seconds": 1}
            self._add_trusted_local(short_kwargs, short_confirmation)
            short_popen = FakePopenFactory(lambda: FakeProcess(short, short))
            with self._patched_processes(
                short_popen, FakeRunFactory()
            ), mock.patch.object(
                command_module.shutil,
                "which",
                return_value="/usr/bin/python3",
            ):
                short_core, short_core_error = self._capture(
                    lambda: core.run(CORE_COMMAND, **short_kwargs)
                )
            self._check_untruncated_output(
                "core-short",
                short_core,
                short_core_error,
                short,
                violations,
            )

            legacy_limit = 10_000
            legacy_raw = "H" * 6000 + "M" * 2000 + "T" * 6000
            legacy_expected = (
                legacy_raw[:5000]
                + f"\n... [TRUNCATED {len(legacy_raw) - legacy_limit} CHARS] ...\n"
                + legacy_raw[-5000:]
            )
            legacy_digest = hashlib.sha256(
                legacy_raw.encode("utf-8")
            ).hexdigest()
            legacy_run = FakeRunFactory(legacy_raw, legacy_raw)
            legacy_popen = FakePopenFactory(
                lambda: FakeProcess(legacy_raw, legacy_raw)
            )
            legacy = ProjectWorkspace(root, command_timeout=60)
            legacy_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            legacy_kwargs: dict[str, object] = {}
            self._add_trusted_local(legacy_kwargs, legacy_confirmation)
            with self._patched_processes(legacy_popen, legacy_run):
                legacy_result, legacy_error = self._capture(
                    lambda: legacy.run(list(LEGACY_COMMAND), **legacy_kwargs)
                )
            if len(legacy_popen.calls) + len(legacy_run.calls) != 1:
                violations.append("legacy: expected exactly one spawn backend")
            self._check_bounded_output(
                "legacy",
                legacy_result,
                legacy_error,
                legacy_expected,
                len(legacy_raw),
                legacy_digest,
                violations,
            )
            short_legacy_run = FakeRunFactory(short, short)
            short_legacy_popen = FakePopenFactory(
                lambda: FakeProcess(short, short)
            )
            short_legacy_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            short_legacy_kwargs: dict[str, object] = {}
            self._add_trusted_local(
                short_legacy_kwargs, short_legacy_confirmation
            )
            with self._patched_processes(
                short_legacy_popen, short_legacy_run
            ):
                short_legacy, short_legacy_error = self._capture(
                    lambda: legacy.run(
                        list(LEGACY_COMMAND), **short_legacy_kwargs
                    )
                )
            if (
                len(short_legacy_popen.calls)
                + len(short_legacy_run.calls)
                != 1
            ):
                violations.append(
                    "legacy-short: expected exactly one spawn backend"
                )
            self._check_untruncated_output(
                "legacy-short",
                short_legacy,
                short_legacy_error,
                short,
                violations,
            )

            runner_path = root / "browser-runner.mjs"
            spec_path = root / "ui-spec.json"
            runner_path.write_text("// fake runner\n", encoding="utf-8")
            spec_path.write_text("{}\n", encoding="utf-8")
            browser_command = (
                "node", str(runner_path),
                "--url", "http://127.0.0.1:4173/",
                "--spec", str(spec_path),
                "--screenshot", str(root / "actual.png"),
                "--result", str(root / "result.json"),
            )
            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"node", "pnpm"}),
                executable_overrides={
                    "node": "/usr/bin/node",
                    "pnpm": "/usr/bin/pnpm",
                },
            )
            browser_raw = "H" * 6000 + "M" * 2000 + "T" * 6000
            browser_limit = 10_000
            browser_expected = (
                browser_raw[:5000]
                + f"\n... [TRUNCATED {len(browser_raw) - browser_limit} CHARS] ...\n"
                + browser_raw[-5000:]
            )
            browser_digest = hashlib.sha256(
                browser_raw.encode("utf-8")
            ).hexdigest()
            browser_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=45,
            )
            browser_popen = FakePopenFactory(
                lambda: FakeProcess(browser_raw, browser_raw)
            )
            with self._patched_processes(browser_popen, FakeRunFactory()):
                browser_result, browser_error = self._capture(lambda: browser.run(
                    browser_command,
                    cwd=root,
                    timeout_seconds=45,
                    trusted_local=browser_token,
                ))
            self._check_bounded_output(
                "browser",
                browser_result,
                browser_error,
                browser_expected,
                len(browser_raw),
                browser_digest,
                violations,
            )
            short_browser_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=45,
            )
            short_browser_popen = FakePopenFactory(
                lambda: FakeProcess(short, short)
            )
            with self._patched_processes(
                short_browser_popen,
                FakeRunFactory(),
            ):
                short_browser_result, short_browser_error = self._capture(
                    lambda: browser.run(
                        browser_command,
                        cwd=root,
                        timeout_seconds=45,
                        trusted_local=short_browser_token,
                    )
                )
            self._check_untruncated_output(
                "browser-short",
                short_browser_result,
                short_browser_error,
                short,
                violations,
            )

            log_path = root / "server.log"

            dev_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            dev_popen = self._fake_background_popen(browser_raw)
            log_value = ""
            running_log_value = ""
            managed = None
            with self._record_temp_writes(root) as long_write_recorder:
                with self._patched_processes(dev_popen, FakeRunFactory()):
                    managed, managed_error = self._capture(
                        lambda: browser.start_background(
                            DEV_COMMAND,
                            cwd=root,
                            log_path=log_path,
                            trusted_local=dev_token,
                        )
                    )
                    if (
                        len(dev_popen.calls) != 1
                        or dev_popen.calls[0][1].get("stdout")
                        is not subprocess.PIPE
                    ):
                        violations.append(
                            "server_log-long did not ingest through stdout=PIPE"
                        )
                    if managed is not None:
                        if getattr(managed, "running", None) is not True:
                            violations.append(
                                "server_log: long fixture was not running at ingest"
                            )
                        log_value = self._poll_log_tail(
                            managed,
                            browser_limit,
                            "server_log-long",
                            violations,
                            expected=browser_expected,
                        )
                        running_log_value = (
                            log_path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            )
                            if log_path.exists()
                            else ""
                        )
                        self._capture(managed.stop)
                        self._check_background_pipe_closed(
                            dev_popen,
                            "server_log-long",
                            violations,
                        )
                    elif managed_error is not None:
                        violations.append("server_log: managed process absent")
                if self._write_events_containing(
                    long_write_recorder.events,
                    browser_raw,
                ):
                    violations.append(
                        "server_log-long raw output reached persistent write "
                        "history before truncation"
                    )
            if long_write_recorder.unclosed_fds:
                violations.append(
                    "server_log-long recorder observed unclosed low-level fds"
                )
            self._close_fake_process_streams(dev_popen)
            if log_value != browser_expected:
                violations.append("server_log: head/marker/tail mismatch")
            if running_log_value != browser_expected:
                violations.append(
                    "server_log: running durable file is not bounded canonical text"
                )
            self._check_log_metadata(
                managed,
                len(browser_raw),
                browser_digest,
                True,
                violations,
            )
            if log_path.exists():
                durable = log_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                if durable != browser_expected:
                    violations.append(
                        "server_log: durable file is not bounded canonical text"
                    )

            short_log_path = root / "server-short.log"

            short_dev_token = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            short_dev_popen = self._fake_background_popen(short)
            short_log_value = ""
            short_running_log_value = ""
            short_managed = None
            with self._patched_processes(short_dev_popen, FakeRunFactory()):
                short_managed, _ = self._capture(
                    lambda: browser.start_background(
                        DEV_COMMAND,
                        cwd=root,
                        log_path=short_log_path,
                        trusted_local=short_dev_token,
                    )
                )
                if (
                    len(short_dev_popen.calls) != 1
                    or short_dev_popen.calls[0][1].get("stdout")
                    is not subprocess.PIPE
                ):
                    violations.append(
                        "server_log-short did not ingest through stdout=PIPE"
                    )
                if short_managed is not None:
                    if getattr(short_managed, "running", None) is not True:
                        violations.append(
                            "server_log: short fixture was not running at ingest"
                        )
                    short_log_value = self._poll_log_tail(
                        short_managed,
                        browser_limit,
                        "server_log-short",
                        violations,
                        expected=short,
                    )
                    short_running_log_value = (
                        short_log_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                        if short_log_path.exists()
                        else ""
                    )
                    self._capture(short_managed.stop)
                    self._check_background_pipe_closed(
                        short_dev_popen,
                        "server_log-short",
                        violations,
                    )
            self._close_fake_process_streams(short_dev_popen)
            if short_log_value != short:
                violations.append("server_log: short output changed")
            if short_running_log_value != short:
                violations.append(
                    "server_log: short running durable file changed"
                )
            self._check_log_metadata(
                short_managed,
                len(short),
                hashlib.sha256(short.encode("utf-8")).hexdigest(),
                False,
                violations,
            )
            if short_log_path.exists() and short_log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ) != short:
                violations.append("server_log: short durable file changed")

        self.assertEqual(
            violations,
            [],
            "SEC-F: every stream must share exact bounded metadata; "
            f"violations={violations}",
        )

    def test_f_fake_secret_never_reaches_artifact_sqlite_or_next_model_input(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = ProjectWorkspace(root)
            command = ["python3", "-V"]
            run = FakeRunFactory(stderr=f"api_key={FAKE_SECRET}", returncode=1)
            legacy_popen = FakePopenFactory(lambda: FakeProcess(
                stderr=f"api_key={FAKE_SECRET}",
                returncode=1,
            ))
            confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            legacy_kwargs: dict[str, object] = {}
            self._add_trusted_local(legacy_kwargs, confirmation)
            with self._patched_processes(legacy_popen, run):
                secret_result, secret_error = self._capture(lambda: workspace.run(
                    command,
                    **legacy_kwargs,
                ))
            if secret_error is not None or secret_result is None:
                violations.append("legacy valid-confirmation result unavailable")
            spawn_count = len(legacy_popen.calls) + len(run.calls)
            if spawn_count != 1:
                violations.append(
                    f"legacy valid-confirmation spawn_count={spawn_count}"
                )

            with mock.patch.object(
                workspace,
                "run",
                return_value=secret_result,
            ):
                verification = CommandVerificationAgent(
                    workspace,
                    CommandPolicy(
                        allowed_executables={"python3"},
                        allowed_commands=[command],
                    ),
                ).run(TaskContext(
                    "SEC-F",
                    "fake output propagation",
                    ["must fail safely"],
                    [command],
                ))
            task = TaskContext(
                "SEC-F-NEXT",
                "next input",
                ["sanitized"],
            )
            task.feedback = list(verification.feedback)
            next_input = task.model_input()
            if self._direct_secret_locations(
                next_input,
                None,
                FAKE_SECRET,
            ):
                violations.append("next_model_input")
            if len(repr(next_input)) > 10_000:
                violations.append("next_model_input: view is unbounded")

            config = TEMPLATE / "visionforge.template.json"
            if not config.is_file():
                violations.append("VisionForge normal-control config missing")
            ui_spec = UISpec.from_dict(json.loads(
                (TEMPLATE / "visionforge.ui-spec.json").read_text(encoding="utf-8")
            ))
            artifacts = ArtifactStore()
            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"pnpm"}),
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
            )
            browser_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=1,
            )
            browser_kwargs = {"cwd": root, "timeout_seconds": 1}
            self._add_trusted_local(
                browser_kwargs,
                browser_confirmation,
            )
            browser_popen = FakePopenFactory(lambda: FakeProcess(
                stderr=f"api_key={FAKE_SECRET}",
                returncode=1,
            ))
            with self._patched_processes(browser_popen, FakeRunFactory()):
                supervised_browser_result, supervised_browser_error = self._capture(
                    lambda: browser.run(BUILD_COMMAND, **browser_kwargs)
                )
            if (
                supervised_browser_error is not None
                or supervised_browser_result is None
                or len(browser_popen.calls) != 1
            ):
                violations.append(
                    "browser valid-confirmation secret result unavailable"
                )
                supervised_browser_result = ProcessExecution(
                    BUILD_COMMAND, 1, "", "", 0
                )
            scripted = ScriptedBrowserRunner([supervised_browser_result])
            PlaywrightBrowserTester(
                TEMPLATE,
                scripted,
                artifacts,
                ImageAssetStore(root / "assets"),
                root / "runtime",
            ).run(task_id="sec-f-browser", ui_spec=ui_spec)
            snapshot, snapshot_error = self._capture(artifacts.snapshot)
            artifact_items: list[object] = []
            if snapshot_error is not None or not isinstance(
                snapshot, (tuple, list)
            ):
                violations.append("browser artifact snapshot unavailable")
            else:
                for index, item in enumerate(snapshot):
                    if not isinstance(item, (tuple, list)) or len(item) != 2:
                        violations.append(
                            f"browser artifact snapshot[{index}] malformed"
                        )
                        continue
                    artifact = item[0]
                    artifact_items.append(artifact)
                    direct_hits = self._direct_secret_locations(
                        artifact,
                        None,
                        FAKE_SECRET,
                    )
                    if direct_hits:
                        violations.append(
                            "browser_artifact direct content/metadata="
                            f"{direct_hits}"
                        )
            artifact_payload = repr([
                getattr(artifact, "content", _UNSET)
                for artifact in artifact_items
            ])
            artifact_repr = repr(artifact_items)
            if FAKE_SECRET in artifact_payload:
                violations.append("browser_artifact")
            if FAKE_SECRET in artifact_repr:
                violations.append("browser_artifact_repr")
            if len(artifact_payload) > 10_000:
                violations.append("browser_artifact: view is unbounded")
            self._scan_workspace_secret(
                root,
                FAKE_SECRET,
                "actual_artifact_and_agent_views",
                violations,
            )

            database_path = root / "sink.sqlite3"
            with sqlite3.connect(str(database_path)) as connection:
                connection.execute("CREATE TABLE sink(value TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO sink(value) VALUES (?)",
                    (
                        (repr(next_input),),
                        (artifact_payload,),
                        (artifact_repr,),
                    ),
                )
                persisted = repr(connection.execute(
                    "SELECT value FROM sink ORDER BY rowid"
                ).fetchall())
            if FAKE_SECRET in persisted:
                violations.append("sqlite_sink")
            self._scan_workspace_secret(
                root,
                FAKE_SECRET,
                "actual_artifact_sqlite_and_runtime",
                violations,
            )

        self.assertEqual(
            violations,
            [],
            "SEC-F: the unified redaction boundary must precede downstream sinks; "
            f"leaked={violations}",
        )

    def test_g_frozen_manifest_is_observable_without_breaking_old_result_contracts(self) -> None:
        violations: list[str] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "main"
            root.mkdir()
            confirmation_requests: dict[str, Mapping] = {}
            parent_allowed_sentinels = {
                "LANG": f"{FAKE_SECRET}_G_PARENT_LANG",
                "LC_ALL": f"{FAKE_SECRET}_G_PARENT_LC_ALL",
                "PYTHONDONTWRITEBYTECODE": (
                    f"{FAKE_SECRET}_G_PARENT_DONT_WRITE_BYTECODE"
                ),
                "PYTHONUNBUFFERED": f"{FAKE_SECRET}_G_PARENT_UNBUFFERED",
            }
            with mock.patch.dict(
                os.environ,
                parent_allowed_sentinels,
                clear=False,
            ):
                observations = self._invoke_all_entrypoints(
                    root,
                    use_default_limits=True,
                    confirmation_requests=confirmation_requests,
                )
            runner_path = root / "browser-runner.mjs"
            spec_path = root / "ui-spec.json"
            browser_command = (
                "node",
                str(runner_path),
                "--url",
                "http://127.0.0.1:4173/",
                "--spec",
                str(spec_path),
                "--screenshot",
                str(root / "actual.png"),
                "--result",
                str(root / "result.json"),
            )
            expected_profiles = {
                "core": {
                    "profile_id": PROFILE_IDS["core"],
                    "executable": "/usr/bin/python3",
                    "argv": CORE_COMMAND,
                    "deadline": 30,
                    "output": "stdout_stderr",
                    "python": True,
                },
                "legacy": {
                    "profile_id": PROFILE_IDS["legacy"],
                    "executable": "/usr/bin/python3",
                    "argv": LEGACY_COMMAND,
                    "deadline": 60,
                    "output": "stdout_stderr",
                    "python": True,
                },
                "visionforge_build": {
                    "profile_id": PROFILE_IDS["visionforge_build"],
                    "executable": "/usr/bin/pnpm",
                    "argv": BUILD_COMMAND,
                    "deadline": 60,
                    "output": "stdout_stderr",
                    "python": False,
                },
                "visionforge_browser": {
                    "profile_id": PROFILE_IDS["visionforge_browser"],
                    "executable": "/usr/bin/node",
                    "argv": browser_command,
                    "deadline": 45,
                    "output": "stdout_stderr",
                    "python": False,
                },
                "visionforge_dev": {
                    "profile_id": PROFILE_IDS["visionforge_dev"],
                    "executable": "/usr/bin/pnpm",
                    "argv": DEV_COMMAND,
                    "deadline": 60,
                    "output": "server_log",
                    "python": False,
                },
            }
            for entrypoint, result, error, spawn_count, spawn in observations:
                if error is not None:
                    violations.append(
                        f"{entrypoint}: normal control error={type(error).__name__}"
                    )
                if result is None:
                    violations.append(f"{entrypoint}: normal control result absent")
                if spawn_count != 1:
                    violations.append(
                        f"{entrypoint}: normal control spawn_count={spawn_count}"
                    )
                manifest = self._find_profile_manifest(result, error)
                if manifest is _UNSET:
                    violations.append(
                        f"{entrypoint}: nested structured profile manifest absent"
                    )
                else:
                    self._check_profile_manifest(
                        entrypoint,
                        manifest,
                        expected_profiles[entrypoint],
                        root,
                        confirmation_requests.get(
                            expected_profiles[entrypoint]["profile_id"],
                            _UNSET,
                        ),
                        spawn,
                        violations,
                    )
                if entrypoint == "visionforge_dev":
                    if result is not None and getattr(result, "running", None) is not False:
                        violations.append(
                            "visionforge_dev: stopped normal control is still running"
                        )
                else:
                    self._check_old_result_contract(
                        entrypoint,
                        result,
                        expected_profiles[entrypoint]["argv"],
                        violations,
                    )

            baseline_requests = self._all_confirmation_requests(root)
            repeated_requests = self._all_confirmation_requests(root)
            for entrypoint in PROFILE_IDS:
                baseline = baseline_requests.get(entrypoint)
                repeated = repeated_requests.get(entrypoint)
                if not isinstance(baseline, Mapping) or not isinstance(
                    repeated, Mapping
                ):
                    violations.append(
                        f"{entrypoint}: repeated canonical request unavailable"
                    )
                    continue
                for digest_name in (
                    "workspace_digest",
                    "input_digest",
                    "profile_digest",
                ):
                    digest = baseline.get(digest_name)
                    if (
                        not self._is_digest(digest)
                        or repeated.get(digest_name) != digest
                    ):
                        violations.append(
                            f"{entrypoint}: canonical {digest_name} is unstable"
                        )
            dev_variant_root = Path(temp) / "dev-cwd-variant"
            changed_requests = {
                "core": self._confirmation_request_for(
                    root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=29,
                    output_limit_chars=10_000,
                ),
                "legacy": self._confirmation_request_for(
                    root,
                    PROFILE_IDS["legacy"],
                    "/usr/bin/python3",
                    LEGACY_COMMAND,
                    wall_deadline_seconds=59,
                    output_limit_chars=10_000,
                ),
                "visionforge_build": self._confirmation_request_for(
                    root,
                    PROFILE_IDS["visionforge_build"],
                    "/usr/bin/pnpm",
                    BUILD_COMMAND,
                    wall_deadline_seconds=59,
                    output_limit_chars=10_000,
                ),
                "visionforge_browser": self._confirmation_request_for(
                    root,
                    PROFILE_IDS["visionforge_browser"],
                    "/usr/bin/node",
                    browser_command,
                    wall_deadline_seconds=44,
                    output_limit_chars=10_000,
                ),
                "visionforge_dev": self._confirmation_request_for(
                    dev_variant_root,
                    PROFILE_IDS["visionforge_dev"],
                    "/usr/bin/pnpm",
                    DEV_COMMAND,
                    wall_deadline_seconds=60,
                    output_limit_chars=10_000,
                ),
            }
            output_request = self._confirmation_request_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=30,
                output_limit_chars=9_999,
            )
            if (
                not all(isinstance(item, Mapping) for item in (
                    *baseline_requests.values(),
                    *changed_requests.values(),
                    output_request,
                ))
                or set(baseline_requests) != set(PROFILE_IDS)
            ):
                violations.append(
                    "all Profiles: legal single-field digest controls unavailable"
                )
            else:
                for name, changed in changed_requests.items():
                    baseline_digest = baseline_requests[name].get(
                        "profile_digest"
                    )
                    changed_digest = changed.get("profile_digest")
                    if (
                        not self._is_digest(changed_digest)
                        or changed_digest == baseline_digest
                    ):
                        violations.append(
                            f"{name}: legal Profile field change did not change "
                            "profile_digest"
                        )
                core_baseline_digest = baseline_requests["core"].get(
                    "profile_digest"
                )
                if (
                    not self._is_digest(output_request.get("profile_digest"))
                    or output_request.get("profile_digest")
                    == core_baseline_digest
                ):
                    violations.append(
                        "core: legal output-limit change did not change "
                        "profile_digest"
                    )

            issuer = getattr(
                coding_workflow,
                "issue_trusted_local_confirmation",
                None,
            )
            original_tokens = (
                self._issue_confirmation_map(
                    issuer,
                    baseline_requests,
                    expires_at_monotonic=time.monotonic() + 60,
                )
                if callable(issuer)
                else _UNSET
            )
            if not isinstance(original_tokens, Mapping):
                violations.append(
                    "all Profiles: original tokens unavailable for legal drift"
                )
            else:
                drift_observations = self._invoke_all_entrypoints(
                    root,
                    trusted_local_by_entrypoint=original_tokens,
                    deadline_overrides={
                        "core": 29,
                        "legacy": 59,
                        "visionforge_build": 59,
                        "visionforge_browser": 44,
                    },
                    dev_cwd=dev_variant_root,
                )
                for entrypoint, result, error, spawn_count, _ in (
                    drift_observations
                ):
                    if spawn_count != 0 or not self._has_structured_code(
                        result,
                        error,
                        "SANDBOX_REQUIRED",
                    ):
                        violations.append(
                            f"{entrypoint}: original token survived legal "
                            "Profile drift"
                        )

            terminal_runner = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            for (
                name,
                process,
                deadline,
                expected_exit,
                expected_stdout,
                expected_stderr,
                expected_timed_out,
            ) in (
                (
                    "failure",
                    FakeProcess("failure-out", "failure-err", 7),
                    30,
                    7,
                    "failure-out",
                    "failure-err",
                    False,
                ),
                (
                    "timeout",
                    FakeProcess(
                        running=True,
                        communicate_effects=[
                            subprocess.TimeoutExpired(CORE_COMMAND, 0.01),
                            ("timeout-out", "timeout-err"),
                        ],
                    ),
                    0.01,
                    None,
                    "timeout-out",
                    "timeout-err",
                    True,
                ),
            ):
                confirmation = self._confirmation_for(
                    root,
                    PROFILE_IDS["core"],
                    "/usr/bin/python3",
                    CORE_COMMAND,
                    wall_deadline_seconds=deadline,
                    python_profile=True,
                )
                kwargs = {"timeout_seconds": deadline}
                self._add_trusted_local(kwargs, confirmation)
                popen = FakePopenFactory(lambda process=process: process)
                with self._patched_processes(
                    popen,
                    FakeRunFactory(),
                    killpg=FakeSignalFactory(),
                ), mock.patch.object(
                    command_module.shutil,
                    "which",
                    return_value="/usr/bin/python3",
                ):
                    result, error = self._capture(lambda: terminal_runner.run(
                        CORE_COMMAND,
                        **kwargs,
                    ))
                if error is not None or len(popen.calls) != 1:
                    violations.append(
                        f"core-{name}: supervised terminal path unavailable"
                    )
                self._check_old_result_contract(
                    "core",
                    result,
                    CORE_COMMAND,
                    violations,
                    expected_exit_code=expected_exit,
                    expected_stdout=expected_stdout,
                    expected_stderr=expected_stderr,
                    expected_timed_out=expected_timed_out,
                )
                if self._find_profile_manifest(result, error) is _UNSET:
                    violations.append(
                        f"core-{name}: nested Profile manifest absent"
                    )

            legacy_runner = ProjectWorkspace(root, command_timeout=60)
            for name, run_backend, expected_exit, expected_out, expected_err, timed_out in (
                (
                    "failure",
                    FakeRunFactory("legacy-failure-out", "legacy-failure-err", 7),
                    7,
                    "legacy-failure-out",
                    "legacy-failure-err",
                    False,
                ),
                (
                    "timeout",
                    mock.Mock(side_effect=subprocess.TimeoutExpired(
                        LEGACY_COMMAND,
                        60,
                        output="legacy-timeout-out",
                        stderr="legacy-timeout-err",
                    )),
                    124,
                    "legacy-timeout-out",
                    "legacy-timeout-err",
                    True,
                ),
            ):
                legacy_token = self._confirmation_for(
                    root,
                    PROFILE_IDS["legacy"],
                    "/usr/bin/python3",
                    LEGACY_COMMAND,
                    wall_deadline_seconds=60,
                    python_profile=True,
                )
                legacy_kwargs: dict[str, object] = {}
                self._add_trusted_local(legacy_kwargs, legacy_token)
                legacy_process = FakePopenFactory(lambda: FakeProcess(
                    expected_out,
                    expected_err,
                    expected_exit,
                ))
                with self._patched_processes(legacy_process, run_backend):
                    legacy_result, legacy_error = self._capture(
                        lambda: legacy_runner.run(
                            list(LEGACY_COMMAND),
                            **legacy_kwargs,
                        )
                    )
                if (
                    legacy_error is not None
                    or len(legacy_process.calls)
                    + self._factory_call_count(run_backend) != 1
                ):
                    violations.append(
                        f"legacy-{name}: supervised terminal path unavailable"
                    )
                self._check_old_result_contract(
                    "legacy",
                    legacy_result,
                    LEGACY_COMMAND,
                    violations,
                    expected_exit_code=expected_exit,
                    expected_stdout=expected_out,
                    expected_stderr=expected_err,
                    expected_timed_out=timed_out,
                )
                if self._find_profile_manifest(
                    legacy_result,
                    legacy_error,
                ) is _UNSET:
                    violations.append(
                        f"legacy-{name}: nested Profile manifest absent"
                    )

            terminal_browser = BrowserProcessRunner(
                allowed_executables=frozenset({"node"}),
                executable_overrides={"node": "/usr/bin/node"},
                poll_interval=0.001,
            )
            for name, process, deadline, expected_exit, expected_out, expected_err, timed_out in (
                (
                    "failure",
                    FakeProcess("browser-failure-out", "browser-failure-err", 7),
                    45,
                    7,
                    "browser-failure-out",
                    "browser-failure-err",
                    False,
                ),
                (
                    "timeout",
                    FakeProcess(
                        "browser-timeout-out",
                        "browser-timeout-err",
                        running=True,
                    ),
                    0.000000001,
                    124,
                    "browser-timeout-out",
                    "browser-timeout-err",
                    True,
                ),
            ):
                browser_token = self._confirmation_for(
                    root,
                    PROFILE_IDS["visionforge_browser"],
                    "/usr/bin/node",
                    browser_command,
                    wall_deadline_seconds=deadline,
                )
                browser_kwargs = {
                    "cwd": root,
                    "timeout_seconds": deadline,
                }
                self._add_trusted_local(browser_kwargs, browser_token)
                browser_popen = FakePopenFactory(
                    lambda process=process: process
                )
                with self._patched_processes(
                    browser_popen,
                    FakeRunFactory(),
                    killpg=FakeSignalFactory(),
                ):
                    terminal_result, terminal_error = self._capture(
                        lambda: terminal_browser.run(
                            browser_command,
                            **browser_kwargs,
                        )
                    )
                if terminal_error is not None or len(browser_popen.calls) != 1:
                    violations.append(
                        f"browser-{name}: supervised terminal path unavailable"
                    )
                self._check_old_result_contract(
                    "visionforge_browser",
                    terminal_result,
                    browser_command,
                    violations,
                    expected_exit_code=expected_exit,
                    expected_stdout=expected_out,
                    expected_stderr=expected_err,
                    expected_timed_out=timed_out,
                )
                if self._find_profile_manifest(
                    terminal_result,
                    terminal_error,
                ) is _UNSET:
                    violations.append(
                        f"browser-{name}: nested Profile manifest absent"
                    )

            old_artifact, old_artifact_error = self._capture(lambda: Artifact.create(
                "sec-g-old-artifact",
                "sec-g",
                {"status": "ok"},
                kind="result",
                metadata={"source": "legacy-consumer"},
            ))
            old_store, old_store_error = self._capture(ArtifactStore)
            if old_artifact_error is not None or old_store_error is not None:
                violations.append("old Artifact/ArtifactStore constructor changed")
            else:
                artifact_hash, artifact_hash_error = self._capture(
                    lambda: old_artifact.content_hash
                )
                expected_artifact_fields = {
                    "name": "sec-g-old-artifact",
                    "task_id": "sec-g",
                    "kind": "result",
                    "content": {"status": "ok"},
                    "metadata": {"source": "legacy-consumer"},
                }
                for field, expected_value in expected_artifact_fields.items():
                    observed = getattr(old_artifact, field, _UNSET)
                    if (
                        dict(observed) != expected_value
                        if field == "metadata" and isinstance(observed, Mapping)
                        else observed != expected_value
                    ):
                        violations.append(
                            f"old Artifact field {field} changed"
                        )
                artifact_id = getattr(old_artifact, "artifact_id", _UNSET)
                created_at = getattr(old_artifact, "created_at", _UNSET)
                if not isinstance(artifact_id, str) or not artifact_id:
                    violations.append("old Artifact artifact_id changed")
                if not isinstance(created_at, str) or not created_at:
                    violations.append("old Artifact created_at changed")
                if artifact_hash_error is not None or not self._is_digest(
                    artifact_hash
                ):
                    violations.append("old Artifact content_hash changed")
                old_reference, put_error = self._capture(
                    lambda: old_store.put(old_artifact)
                )
                restored_artifact, restored_error = self._capture(
                    lambda: old_store.get(old_reference)
                ) if put_error is None else (None, put_error)
                if (
                    restored_error is not None
                    or restored_artifact is not old_artifact
                    or old_reference != f"artifact://{artifact_id}"
                ):
                    violations.append(
                        "old ArtifactStore reference contract changed"
                    )
                snapshot, snapshot_error = self._capture(old_store.snapshot)
                if (
                    snapshot_error is not None
                    or not isinstance(snapshot, tuple)
                    or len(snapshot) != 1
                    or not isinstance(snapshot[0], tuple)
                    or len(snapshot[0]) != 2
                    or snapshot[0][0] is not old_artifact
                ):
                    violations.append("old ArtifactStore snapshot contract changed")
                restored_hash, restored_hash_error = self._capture(
                    lambda: restored_artifact.content_hash
                ) if restored_error is None else (None, restored_error)
                if (
                    restored_hash_error is not None
                    or restored_hash != artifact_hash
                ):
                    violations.append("old Artifact content_hash was not stable")

        self.assertEqual(
            violations,
            [],
            "SEC-G: all five nested frozen manifests and old Result contracts "
            "must coexist; "
            f"violations={violations}",
        )

    def test_h_static_scan_allows_one_supervised_popen_owner_and_no_run(self) -> None:
        paths = self._production_python_paths()
        calls = [
            call
            for path in dict.fromkeys(paths)
            for call in self._process_boundary_calls(path)
        ]
        popen = [call for call in calls if call[1] == "subprocess.Popen"]
        bypass = [call for call in calls if call[1] != "subprocess.Popen"]
        reference_findings = [
            finding
            for path in paths
            for finding in self._restricted_boundary_imports(path)
        ]

        def is_exempt_binding(finding, binding) -> bool:
            return (
                binding is not None
                and len(finding) >= 7
                and finding[1]
                == "subprocess.Popen.<process-reference>"
                and (
                    finding[0],
                    finding[2],
                    finding[4],
                    finding[5],
                    finding[6],
                ) == binding
            )

        if len(popen) == 1:
            exempt_binding = self._unique_popen_callee_binding(popen[0])
            reference_findings = [
                finding for finding in reference_findings
                if not is_exempt_binding(finding, exempt_binding)
            ]
        bypass.extend(reference_findings)
        violations: list[str] = []
        required_audit_events = {
            "subprocess.Popen",
            "os.system",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.exec",
            "os.spawn",
            "os.kill",
            "os.killpg",
            "socket.__new__",
            "socket.bind",
            "socket.connect",
            "socket.getaddrinfo",
            "socket.sendto",
            "socket.sendmsg",
        }
        if not required_audit_events.issubset(_AUDITED_BOUNDARY_EVENTS):
            violations.append("process-lifetime audit event coverage incomplete")
        for (owner, name), real in _IMPORT_EXTRA_REAL.items():
            if owner in {posix, _posixsubprocess, _socket} and getattr(
                owner, name
            ) is real:
                violations.append(
                    f"suite low-level boundary restored real {owner.__name__}.{name}"
                )
        if (
            _thread.start_new_thread is _IMPORT_REAL_LOW_THREAD_START
            or _thread.start_new is _IMPORT_REAL_LOW_THREAD_START_ALIAS
            or threading.Thread.start is _IMPORT_REAL_THREAD_START
            or asyncio.BaseEventLoop.create_task
            is _IMPORT_REAL_LOOP_CREATE_TASK
        ):
            violations.append(
                "suite thread/task boundary restored a real primitive"
            )
        if len(popen) != 1:
            violations.append(f"Popen owners={popen}")
        if bypass:
            violations.append(f"alternative process owners={bypass}")

        async_pair_start = len(self._async_socketpairs)

        async def benign_asyncio_work() -> str:
            await asyncio.sleep(0)
            return "closed"

        async_result, async_error = self._capture(
            lambda: asyncio.run(benign_asyncio_work())
        )
        async_pair_delta = self._async_socketpairs[async_pair_start:]
        if (
            async_error is not None
            or async_result != "closed"
            or len(async_pair_delta) != 2
            or any(endpoint.fileno() != -1 for endpoint in async_pair_delta)
        ):
            violations.append(
                "bounded asyncio AF_UNIX socketpair was not tracked/closed: "
                f"error={type(async_error).__name__ if async_error else None}, "
                f"endpoints={len(async_pair_delta)}"
            )

        alias_module_name = "coding_workflow._sec_alias_fixture_v5"
        alias_module = types.ModuleType(alias_module_name)
        original_alias = _IMPORT_TRAP_POPEN
        original_run_alias = _IMPORT_TRAP_RUN

        class CachedBoundary:
            alias = staticmethod(original_alias)

        CachedBoundary.__module__ = alias_module_name

        def default_alias_call(
            factory=original_alias,
            *,
            keyword_factory=original_alias,
        ):
            factory([])
            return keyword_factory([])

        default_alias_call.__module__ = alias_module_name

        def mixed_default_alias_call(
            popen_factory=original_alias,
            run_factory=original_run_alias,
        ):
            popen_factory([])
            return run_factory([])

        mixed_default_alias_call.__module__ = alias_module_name

        def make_closure_call():
            cached = original_alias

            def closure_call():
                return cached([])

            return closure_call

        closure_alias_call = make_closure_call()
        closure_alias_call.__module__ = alias_module_name
        alias_module.direct = original_alias
        alias_module.CachedBoundary = CachedBoundary
        alias_module.default_alias_call = default_alias_call
        alias_module.mixed_default_alias_call = mixed_default_alias_call
        alias_module.closure_alias_call = closure_alias_call
        alias_module.partial_alias = functools.partial(original_alias, [])
        alias_module.containers = {
            "list": [original_alias],
            "tuple": (original_alias,),
        }
        original_static = vars(CachedBoundary)["alias"]
        original_defaults = default_alias_call.__defaults__
        original_kwdefaults = default_alias_call.__kwdefaults__
        original_mixed_defaults = mixed_default_alias_call.__defaults__
        original_partial = alias_module.partial_alias
        original_containers = alias_module.containers
        original_list = original_containers["list"]
        original_tuple = original_containers["tuple"]
        sys.modules[alias_module_name] = alias_module
        alias_popen = FakePopenFactory()
        alias_run = FakeRunFactory()
        alias_errors: list[str] = []
        try:
            with self._patched_processes(alias_popen, alias_run):
                alias_calls = (
                    lambda: alias_module.direct([]),
                    lambda: alias_module.CachedBoundary.alias([]),
                    alias_module.default_alias_call,
                    alias_module.mixed_default_alias_call,
                    alias_module.closure_alias_call,
                    alias_module.partial_alias,
                    lambda: alias_module.containers["list"][0]([]),
                    lambda: alias_module.containers["tuple"][0]([]),
                )
                for index, call in enumerate(alias_calls):
                    _, error = self._capture(call)
                    if error is not None:
                        alias_errors.append(
                            f"{index}:{type(error).__name__}:{error}"
                        )
                alias_module.late_container = {
                    "popen": alias_popen,
                    "run": alias_run,
                }
            restored_static = vars(CachedBoundary).get("alias")
            closure_cells = closure_alias_call.__closure__ or ()
            restored = (
                alias_module.direct is original_alias
                and restored_static is original_static
                and default_alias_call.__defaults__ is original_defaults
                and default_alias_call.__kwdefaults__ is original_kwdefaults
                and mixed_default_alias_call.__defaults__
                is original_mixed_defaults
                and len(closure_cells) == 1
                and closure_cells[0].cell_contents is original_alias
                and alias_module.partial_alias is original_partial
                and alias_module.containers is original_containers
                and alias_module.containers["list"] is original_list
                and alias_module.containers["tuple"] is original_tuple
                and alias_module.late_container["popen"] is self._trap_popen
                and alias_module.late_container["run"] is self._trap_run
            )
            if (
                alias_errors
                or len(alias_popen.calls) != 9
                or len(alias_run.calls) != 1
                or not restored
            ):
                violations.append(
                    "cached production alias overlay/restore mismatch: "
                    f"popen={len(alias_popen.calls)}, "
                    f"run={len(alias_run.calls)}, errors={alias_errors}, "
                    f"restored={restored}"
                )
        finally:
            sys.modules.pop(alias_module_name, None)

        for relative, expected in TEST_ONLY_PROCESS_BOUNDARY_MANIFEST.items():
            actual = tuple(self._process_boundary_calls(ROOT / relative))
            if actual != expected:
                violations.append(
                    f"test-only boundary manifest mismatch {relative}: {actual}"
                )
        manifested_paths = set(TEST_ONLY_PROCESS_BOUNDARY_MANIFEST)
        actual_test_boundaries = {
            str(path.relative_to(ROOT)): tuple(
                self._process_boundary_calls(path)
            )
            for path in sorted((ROOT / "tests").rglob("*.py"))
            if self._process_boundary_calls(path)
        }
        if set(actual_test_boundaries) != {
            path for path in manifested_paths
            if TEST_ONLY_PROCESS_BOUNDARY_MANIFEST[path]
        }:
            violations.append(
                "test-only raw boundary files are not exhaustively manifested: "
                f"actual={sorted(actual_test_boundaries)}, "
                f"manifested={sorted(manifested_paths)}"
            )

        with tempfile.TemporaryDirectory() as temp:
            scanner_fixture = Path(temp) / "process_alias_fixture.py"
            scanner_fixture.write_text(
                "\n".join((
                    "import asyncio as async_runtime",
                    "import _posixsubprocess as native_process",
                    "import _socket as native_socket",
                    "import _thread as low_thread",
                    "import ctypes",
                    "import importlib",
                    "from importlib import import_module as im",
                    "import os as operating_system",
                    "import subprocess as process_module",
                    "from subprocess import *",
                    "popen_alias = process_module.Popen",
                    "run_alias = getattr(process_module, 'run')",
                    "dynamic_import_run = importlib.import_module('subprocess').run",
                    "loader_alias = im",
                    "imported_run = loader_alias('subprocess').run",
                    "module_name = 'subprocess'",
                    "MODULE = 'subprocess'",
                    "API = 'run'",
                    "BENIGN_MODULE = 'myapp'",
                    "unknown_run = im(module_name).run",
                    "attribute_loader = importlib.import_module",
                    "attribute_unknown_run = attribute_loader(module_name).run",
                    "constant_run = getattr(importlib.import_module(MODULE), API)",
                    "spawn_alias = operating_system.posix_spawn",
                    "async_alias = async_runtime.create_subprocess_exec",
                    "dynamic_name = 'system'",
                    "dynamic_alias = getattr(operating_system, dynamic_name)",
                    "boundary_box = {'run': process_module.run}",
                    "class BoundaryHolder:",
                    "    popen = process_module.Popen",
                    "    def execute(self):",
                    "        self.popen([])",
                    "def via_default(factory=process_module.Popen):",
                    "    factory([])",
                    "def via_container():",
                    "    boundary_box['run']([])",
                    "def via_import_defaults(module=MODULE, api=API, loader=__import__):",
                    "    getattr(loader(module), api)([])",
                    "def benign_constant():",
                    "    importlib.import_module(BENIGN_MODULE).run([])",
                    "def benign_default(module=BENIGN_MODULE, api=API, loader=im):",
                    "    getattr(loader(module), api)([])",
                    "def bare_dynamic(name, api):",
                    "    getattr(im(name), api)([])",
                    "def native_bypass(name):",
                    "    __import__(name)",
                    "    importlib.reload(native_socket)",
                    "def exercise():",
                    "    popen_alias([])",
                    "    run_alias([])",
                    "    dynamic_import_run([])",
                    "    imported_run([])",
                    "    unknown_run([])",
                    "    attribute_unknown_run([])",
                    "    __import__(module_name).run([])",
                    "    constant_run([])",
                    "    via_import_defaults()",
                    "    run([])",
                    "    spawn_alias('', [], {})",
                    "    async_alias('')",
                    "    dynamic_alias('')",
                    "    low_thread.start_new_thread(lambda: None, ())",
                )),
                encoding="utf-8",
            )
            scanner_calls = self._process_boundary_calls(scanner_fixture)
            scanner_apis = {item[1] for item in scanner_calls}
            restricted_scanner_apis = {
                item[1]
                for item in self._restricted_boundary_imports(scanner_fixture)
            }
            shadow_fixture = Path(temp) / "benign_shadow_fixture.py"
            shadow_fixture.write_text(
                "import importlib\n"
                "MODULE = 'subprocess'\n"
                "API = 'run'\n"
                "def parameter_shadow(MODULE, API):\n"
                "    getattr(importlib.import_module(MODULE), API)([])\n"
                "def local_shadow():\n"
                "    MODULE = 'json'\n"
                "    importlib.import_module(MODULE).run([])\n",
                encoding="utf-8",
            )
            shadow_calls = self._process_boundary_calls(shadow_fixture)
            shadow_refs = self._restricted_boundary_imports(shadow_fixture)
            reexport_fixture = Path(temp) / "process_reexport.py"
            reexport_fixture.write_text(
                "import subprocess as process_module\n"
                "exported_process_factory = process_module.Popen\n",
                encoding="utf-8",
            )
            consumer_fixture = Path(temp) / "process_consumer.py"
            consumer_fixture.write_text(
                "from process_reexport import exported_process_factory\n",
                encoding="utf-8",
            )
            reexport_findings = [
                finding
                for fixture in (reexport_fixture, consumer_fixture)
                for finding in self._restricted_boundary_imports(fixture)
            ]
            legal_owner_observations = []
            for fixture_name, source in (
                (
                    "legal_imported_owner.py",
                    "from subprocess import Popen as spawn_process\n"
                    "def owner():\n"
                    "    return spawn_process([])\n",
                ),
                (
                    "legal_cached_owner.py",
                    "import subprocess as process_module\n"
                    "_CACHED_POPEN = process_module.Popen\n"
                    "def owner():\n"
                    "    return _CACHED_POPEN([])\n",
                ),
            ):
                owner_fixture = Path(temp) / fixture_name
                owner_fixture.write_text(source, encoding="utf-8")
                owner_calls = self._process_boundary_calls(owner_fixture)
                owner_refs = self._restricted_boundary_imports(owner_fixture)
                binding = (
                    self._unique_popen_callee_binding(owner_calls[0])
                    if len(owner_calls) == 1
                    else None
                )
                remaining = [
                    finding for finding in owner_refs
                    if not is_exempt_binding(finding, binding)
                ]
                legal_owner_observations.append(
                    (fixture_name, owner_calls, owner_refs, binding, remaining)
                )
            leaking_owner_observations = []
            for fixture_name, source in (
                (
                    "leaking_imported_owner.py",
                    "from subprocess import Popen as spawn_process\n"
                    "leaked_export = spawn_process\n"
                    "def owner():\n"
                    "    return spawn_process([])\n",
                ),
                (
                    "leaking_cached_owner.py",
                    "import subprocess as process_module\n"
                    "_CACHED_POPEN = process_module.Popen; leaked_export = _CACHED_POPEN\n"
                    "def owner():\n"
                    "    return _CACHED_POPEN([])\n",
                ),
                (
                    "leaking_second_import_owner.py",
                    "from subprocess import Popen as spawn_process\n"
                    "from subprocess import Popen as exported_process\n"
                    "def owner():\n"
                    "    return spawn_process([])\n",
                ),
            ):
                owner_fixture = Path(temp) / fixture_name
                owner_fixture.write_text(source, encoding="utf-8")
                owner_calls = self._process_boundary_calls(owner_fixture)
                owner_refs = self._restricted_boundary_imports(owner_fixture)
                binding = (
                    self._unique_popen_callee_binding(owner_calls[0])
                    if len(owner_calls) == 1
                    else None
                )
                remaining = [
                    finding for finding in owner_refs
                    if not is_exempt_binding(finding, binding)
                ]
                leaking_owner_observations.append(
                    (fixture_name, owner_calls, owner_refs, binding, remaining)
                )
        expected_scanner_apis = {
            "subprocess.Popen",
            "subprocess.run",
            "os.posix_spawn",
            "asyncio.create_subprocess_exec",
            "os.system",
        }
        if scanner_apis != expected_scanner_apis:
            violations.append(
                f"AST alias/getattr coverage mismatch={sorted(scanner_apis)}"
            )
        scanner_counts = {
            api: sum(item[1] == api for item in scanner_calls)
            for api in expected_scanner_apis
        }
        expected_scanner_counts = {
            "subprocess.Popen": 3,
            "subprocess.run": 10,
            "os.posix_spawn": 1,
            "asyncio.create_subprocess_exec": 1,
            "os.system": 1,
        }
        if scanner_counts != expected_scanner_counts:
            violations.append(
                "AST class/default/container alias coverage mismatch="
                f"{scanner_counts}"
            )
        if shadow_calls or shadow_refs:
            violations.append(
                "function parameter/local constant shadow was falsely treated "
                f"as a process boundary: calls={shadow_calls}, refs={shadow_refs}"
            )
        required_reference_apis = {
            "subprocess.*.<process-reference>",
            "subprocess.Popen.<process-reference>",
            "subprocess.run.<process-reference>",
            "os.posix_spawn.<process-reference>",
            "os.system.<process-reference>",
            "asyncio.create_subprocess_exec.<process-reference>",
        }
        if not required_reference_apis.issubset(restricted_scanner_apis):
            violations.append(
                "AST dormant import/export coverage mismatch="
                f"{sorted(restricted_scanner_apis)}"
            )
        forbidden_benign_findings = {
            "_posixsubprocess.<restricted-import>",
            "_socket.<restricted-import>",
            "ctypes.<restricted-import>",
            "<dynamic>.<dynamic-import>",
            "importlib.reload",
        }
        if restricted_scanner_apis.intersection(forbidden_benign_findings):
            violations.append(
                "benign native/dynamic imports were treated as process calls"
            )
        if any(
            api.startswith("_thread.")
            for api in scanner_apis | restricted_scanner_apis
        ):
            violations.append("bounded _thread work was treated as process bypass")
        if (
            len(reexport_findings) != 1
            or reexport_findings[0][1]
            != "subprocess.Popen.<process-reference>"
        ):
            violations.append(
                f"cross-module dormant re-export escaped={reexport_findings}"
            )
        invalid_legal_owners = [
            observation for observation in legal_owner_observations
            if (
                len(observation[1]) != 1
                or observation[1][0][1] != "subprocess.Popen"
                or observation[3] is None
                or observation[4]
            )
        ]
        if invalid_legal_owners:
            violations.append(
                "unique name-independent Popen owner was falsely rejected: "
                f"observations={invalid_legal_owners}"
            )
        invalid_leaking_owners = [
            observation for observation in leaking_owner_observations
            if (
                len(observation[1]) != 1
                or observation[3] is None
                or not any(
                    finding[1] == "subprocess.Popen.<process-reference>"
                    for finding in observation[4]
                )
            )
        ]
        if invalid_leaking_owners:
            violations.append(
                "same-owner dormant Popen export escaped: "
                f"observations={invalid_leaking_owners}"
            )

        self.assertEqual(
            violations,
            [],
            "SEC-H: all direct/aliased/dynamic process APIs must reduce to one "
            "manifested subprocess.Popen owner; "
            f"violations={violations}",
        )

    def test_h_all_existing_entrypoints_delegate_to_one_raw_spawn_owner(self) -> None:
        violations: list[str] = []
        owners: list[tuple[str, str, str, str]] = []
        active_entrypoint = "<preflight>"

        def owner() -> tuple[str, str]:
            for frame in inspect.stack()[2:]:
                path = Path(frame.filename)
                try:
                    relative = path.resolve().relative_to(ROOT.resolve())
                except (OSError, ValueError):
                    continue
                if "coding_workflow" in relative.parts:
                    return str(relative), frame.function
            return "<unknown>", "<unknown>"

        popen_factory = FakePopenFactory()

        def record_popen(*args, **kwargs):
            path, function = owner()
            owners.append((
                active_entrypoint,
                "subprocess.Popen",
                path,
                function,
            ))
            return popen_factory(*args, **kwargs)

        run_factory = FakeRunFactory()

        def record_run(*args, **kwargs):
            path, function = owner()
            owners.append((
                active_entrypoint,
                "subprocess.run",
                path,
                function,
            ))
            return run_factory(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, self._patched_processes(
            record_popen,
            record_run,
        ), mock.patch.object(
            command_module.shutil,
            "which",
            return_value="/usr/bin/python3",
        ):
            root = Path(temp)
            runner_path = root / "browser-runner.mjs"
            spec_path = root / "ui-spec.json"
            runner_path.write_text("// SEC-EXEC fake runner\n", encoding="utf-8")
            spec_path.write_text("{}\n", encoding="utf-8")
            browser_command = (
                "node", str(runner_path),
                "--url", "http://127.0.0.1:4173/",
                "--spec", str(spec_path),
                "--screenshot", str(root / "actual.png"),
                "--result", str(root / "result.json"),
            )
            core = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={"python3"},
                    allowed_commands=[list(CORE_COMMAND)],
                ),
                output_limit_chars=10_000,
            )
            core_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=1,
                python_profile=True,
            )
            core_kwargs = {"timeout_seconds": 1}
            self._add_trusted_local(core_kwargs, core_confirmation)
            active_entrypoint = "core"
            self._capture(lambda: core.run(CORE_COMMAND, **core_kwargs))

            legacy_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=60,
                python_profile=True,
            )
            legacy_kwargs: dict[str, object] = {}
            self._add_trusted_local(legacy_kwargs, legacy_confirmation)
            active_entrypoint = "legacy"
            self._capture(lambda: ProjectWorkspace(
                root,
                command_timeout=60,
            ).run(list(LEGACY_COMMAND), **legacy_kwargs))

            browser = BrowserProcessRunner(
                allowed_executables=frozenset({"node", "pnpm"}),
                executable_overrides={
                    "node": "/usr/bin/node",
                    "pnpm": "/usr/bin/pnpm",
                },
            )
            build_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=1,
            )
            build_kwargs = {"cwd": root, "timeout_seconds": 1}
            self._add_trusted_local(build_kwargs, build_confirmation)
            active_entrypoint = "visionforge_build"
            self._capture(lambda: browser.run(BUILD_COMMAND, **build_kwargs))

            browser_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=1,
            )
            browser_kwargs = {"cwd": root, "timeout_seconds": 1}
            self._add_trusted_local(browser_kwargs, browser_confirmation)
            active_entrypoint = "visionforge_browser"
            self._capture(lambda: browser.run(
                browser_command,
                **browser_kwargs,
            ))

            dev_confirmation = self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
            )
            dev_kwargs = {"cwd": root, "log_path": root / "server.log"}
            self._add_trusted_local(dev_kwargs, dev_confirmation)
            active_entrypoint = "visionforge_dev"
            managed, _ = self._capture(lambda: browser.start_background(
                DEV_COMMAND,
                **dev_kwargs,
            ))
            stop = getattr(managed, "stop", None)
            if callable(stop):
                self._capture(stop)

        expected_entrypoints = set(PROFILE_IDS)
        counts = {
            entrypoint: sum(item[0] == entrypoint for item in owners)
            for entrypoint in expected_entrypoints
        }
        if counts != {name: 1 for name in expected_entrypoints}:
            violations.append(f"dynamic per-entrypoint spawn counts={counts}")
        if len(popen_factory.calls) != 5:
            violations.append(
                f"dynamic Popen count={len(popen_factory.calls)}"
            )
        if run_factory.calls:
            violations.append(
                f"dynamic subprocess.run count={len(run_factory.calls)}"
            )

        paths = self._production_python_paths()
        static_calls = [
            call
            for path in dict.fromkeys(paths)
            for call in self._process_boundary_calls(path)
        ]
        static_popen = [
            call for call in static_calls if call[1] == "subprocess.Popen"
        ]
        if len(static_popen) == 1:
            static_identity = (static_popen[0][0], static_popen[0][3])
            dynamic_identities = {(item[2], item[3]) for item in owners}
            if dynamic_identities != {static_identity}:
                violations.append(
                    "dynamic owner identity does not match static owner: "
                    f"static={static_identity}, dynamic={sorted(dynamic_identities)}"
                )
        else:
            violations.append(
                f"static Popen owner count unavailable={len(static_popen)}"
            )

        self.assertEqual(
            violations,
            [],
            "SEC-H: all five Profile paths must delegate to one raw owner; "
            f"violations={violations}",
        )

    def _invoke_all_entrypoints(
        self,
        root: Path,
        *,
        trusted_local: object = _AUTO_TRUSTED,
        trusted_local_by_entrypoint: Mapping[str, object] | None = None,
        on_spawn=None,
        use_default_limits: bool = False,
        confirmation_requests: dict[str, Mapping] | None = None,
        deadline_overrides: Mapping[str, float] | None = None,
        dev_cwd: Path | None = None,
        command_overrides: Mapping[str, tuple[str, ...]] | None = None,
    ) -> list[tuple[str, object, BaseException | None, int, dict[str, object] | None]]:
        root.mkdir(parents=True, exist_ok=True)
        runner_path = root / "browser-runner.mjs"
        spec_path = root / "ui-spec.json"
        runner_path.write_text("// SEC-EXEC fake runner\n", encoding="utf-8")
        spec_path.write_text("{}\n", encoding="utf-8")
        browser_command = (
            "node", str(runner_path),
            "--url", "http://127.0.0.1:4173/",
            "--spec", str(spec_path),
            "--screenshot", str(root / "actual.png"),
            "--result", str(root / "result.json"),
        )
        observations = []

        core = ControlledCommandRunner(
            root,
            CommandPolicy(
                allowed_executables={"python3"},
                allowed_commands=[list(CORE_COMMAND)],
            ),
            output_limit_chars=10_000,
        )
        core_command = (
            command_overrides.get("core", CORE_COMMAND)
            if command_overrides else CORE_COMMAND
        )
        popen = FakePopenFactory(
            on_spawn=(
                (lambda args, kwargs: on_spawn("core", args, kwargs))
                if on_spawn is not None else None
            ),
        )
        core_deadline = (
            deadline_overrides.get("core")
            if deadline_overrides and "core" in deadline_overrides
            else 30 if use_default_limits else 1
        )
        kwargs = {"timeout_seconds": core_deadline}
        confirmation = (
            trusted_local_by_entrypoint["core"]
            if trusted_local_by_entrypoint is not None
            else self._confirmation_for(
                root,
                PROFILE_IDS["core"],
                "/usr/bin/python3",
                CORE_COMMAND,
                wall_deadline_seconds=core_deadline,
                python_profile=True,
                request_sink=confirmation_requests,
            ) if trusted_local is _AUTO_TRUSTED else trusted_local
        )
        self._add_trusted_local(kwargs, confirmation)
        with self._patched_processes(popen, FakeRunFactory()), mock.patch.object(
            command_module.shutil,
            "which",
            return_value="/usr/bin/python3",
        ):
            result, error = self._capture(
                lambda: core.run(core_command, **kwargs)
            )
        observations.append((
            "core",
            result,
            error,
            len(popen.calls),
            self._spawn_metadata(popen.calls),
        ))

        legacy_deadline = (
            deadline_overrides.get("legacy")
            if deadline_overrides and "legacy" in deadline_overrides
            else 60
        )
        legacy = ProjectWorkspace(root, command_timeout=int(legacy_deadline))
        legacy_command = (
            command_overrides.get("legacy", LEGACY_COMMAND)
            if command_overrides else LEGACY_COMMAND
        )
        legacy_popen = FakePopenFactory(
            lambda: FakeProcess(stdout="ok"),
            on_spawn=(
                (lambda args, kwargs: on_spawn("legacy", args, kwargs))
                if on_spawn is not None else None
            ),
        )
        run = FakeRunFactory(
            stdout="ok",
            on_spawn=(
                (lambda args, kwargs: on_spawn("legacy", args, kwargs))
                if on_spawn is not None else None
            ),
        )
        kwargs = {}
        confirmation = (
            trusted_local_by_entrypoint["legacy"]
            if trusted_local_by_entrypoint is not None
            else self._confirmation_for(
                root,
                PROFILE_IDS["legacy"],
                "/usr/bin/python3",
                LEGACY_COMMAND,
                wall_deadline_seconds=legacy_deadline,
                python_profile=True,
                request_sink=confirmation_requests,
            ) if trusted_local is _AUTO_TRUSTED else trusted_local
        )
        self._add_trusted_local(kwargs, confirmation)
        with self._patched_processes(legacy_popen, run):
            result, error = self._capture(
                lambda: legacy.run(list(legacy_command), **kwargs)
            )
        legacy_calls = legacy_popen.calls + run.calls
        observations.append((
            "legacy",
            result,
            error,
            len(legacy_calls),
            self._spawn_metadata(legacy_calls),
        ))

        browser = BrowserProcessRunner(
            allowed_executables=frozenset({"node", "pnpm"}),
            executable_overrides={
                "node": "/usr/bin/node",
                "pnpm": "/usr/bin/pnpm",
            },
        )
        popen = FakePopenFactory(
            on_spawn=(
                (lambda args, kwargs: on_spawn(
                    "visionforge_build", args, kwargs
                ))
                if on_spawn is not None else None
            ),
        )
        build_deadline = (
            deadline_overrides.get("visionforge_build")
            if deadline_overrides and "visionforge_build" in deadline_overrides
            else 60 if use_default_limits else 1
        )
        build_command = (
            command_overrides.get("visionforge_build", BUILD_COMMAND)
            if command_overrides else BUILD_COMMAND
        )
        kwargs = {"cwd": root, "timeout_seconds": build_deadline}
        confirmation = (
            trusted_local_by_entrypoint["visionforge_build"]
            if trusted_local_by_entrypoint is not None
            else self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                wall_deadline_seconds=build_deadline,
                request_sink=confirmation_requests,
            ) if trusted_local is _AUTO_TRUSTED else trusted_local
        )
        self._add_trusted_local(kwargs, confirmation)
        with self._patched_processes(popen, FakeRunFactory()):
            result, error = self._capture(
                lambda: browser.run(build_command, **kwargs)
            )
        observations.append((
            "visionforge_build",
            result,
            error,
            len(popen.calls),
            self._spawn_metadata(popen.calls),
        ))

        popen = FakePopenFactory(
            on_spawn=(
                (lambda args, kwargs: on_spawn(
                    "visionforge_browser", args, kwargs
                ))
                if on_spawn is not None else None
            ),
        )
        browser_deadline = (
            deadline_overrides.get("visionforge_browser")
            if deadline_overrides and "visionforge_browser" in deadline_overrides
            else 45 if use_default_limits else 1
        )
        invoked_browser_command = (
            command_overrides.get("visionforge_browser", browser_command)
            if command_overrides else browser_command
        )
        kwargs = {"cwd": root, "timeout_seconds": browser_deadline}
        confirmation = (
            trusted_local_by_entrypoint["visionforge_browser"]
            if trusted_local_by_entrypoint is not None
            else self._confirmation_for(
                root,
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                wall_deadline_seconds=browser_deadline,
                request_sink=confirmation_requests,
            ) if trusted_local is _AUTO_TRUSTED else trusted_local
        )
        self._add_trusted_local(kwargs, confirmation)
        with self._patched_processes(popen, FakeRunFactory()):
            result, error = self._capture(
                lambda: browser.run(invoked_browser_command, **kwargs)
            )
        observations.append((
            "visionforge_browser",
            result,
            error,
            len(popen.calls),
            self._spawn_metadata(popen.calls),
        ))

        popen = FakePopenFactory(
            lambda: FakeProcess(running=True),
            on_spawn=(
                (lambda args, kwargs: on_spawn(
                    "visionforge_dev", args, kwargs
                ))
                if on_spawn is not None else None
            ),
        )
        dev_root = dev_cwd or root
        dev_root.mkdir(parents=True, exist_ok=True)
        kwargs = {"cwd": dev_root, "log_path": dev_root / "server.log"}
        dev_command = (
            command_overrides.get("visionforge_dev", DEV_COMMAND)
            if command_overrides else DEV_COMMAND
        )
        confirmation = (
            trusted_local_by_entrypoint["visionforge_dev"]
            if trusted_local_by_entrypoint is not None
            else self._confirmation_for(
                dev_root,
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                wall_deadline_seconds=60,
                server_log=True,
                request_sink=confirmation_requests,
            ) if trusted_local is _AUTO_TRUSTED else trusted_local
        )
        self._add_trusted_local(kwargs, confirmation)
        with self._patched_processes(popen, FakeRunFactory()):
            result, error = self._capture(
                lambda: browser.start_background(dev_command, **kwargs)
            )
            stop = getattr(result, "stop", None)
            if callable(stop):
                _, stop_error = self._capture(stop)
                error = error or stop_error
        observations.append((
            "visionforge_dev",
            result,
            error,
            len(popen.calls),
            self._spawn_metadata(popen.calls),
        ))
        return observations

    def _invoke_four_public_methods_with_one_token(
        self,
        root: Path,
        confirmation: object,
        *,
        concurrent: bool,
    ) -> tuple[
        list[tuple[str, object, BaseException | None]],
        int,
    ]:
        root.mkdir(parents=True, exist_ok=True)
        core = ControlledCommandRunner(
            root,
            CommandPolicy(
                allowed_executables={"python3"},
                allowed_commands=[list(CORE_COMMAND)],
            ),
            output_limit_chars=10_000,
        )
        legacy = ProjectWorkspace(root, command_timeout=60)
        browser = BrowserProcessRunner(
            allowed_executables=frozenset({"pnpm"}),
            executable_overrides={"pnpm": "/usr/bin/pnpm"},
        )
        popen = FakePopenFactory()
        run = FakeRunFactory()

        def invoke(name: str, call):
            value, error = self._capture(call)
            stop = getattr(value, "stop", None)
            if callable(stop):
                _, stop_error = self._capture(stop)
                error = error or stop_error
            return name, value, error

        calls = (
            (
                "legacy.run",
                lambda: legacy.run(
                    list(LEGACY_COMMAND),
                    trusted_local=confirmation,
                ),
            ),
            (
                "browser.run",
                lambda: browser.run(
                    BUILD_COMMAND,
                    cwd=root,
                    timeout_seconds=60,
                    trusted_local=confirmation,
                ),
            ),
            (
                "browser.start_background",
                lambda: browser.start_background(
                    DEV_COMMAND,
                    cwd=root,
                    log_path=root / "server.log",
                    trusted_local=confirmation,
                ),
            ),
            (
                "core.run",
                lambda: core.run(
                    CORE_COMMAND,
                    timeout_seconds=30,
                    trusted_local=confirmation,
                ),
            ),
        )
        outcomes: list[tuple[str, object, BaseException | None]] = []
        with self._patched_processes(popen, run), mock.patch.object(
            command_module.shutil,
            "which",
            return_value="/usr/bin/python3",
        ):
            if not concurrent:
                outcomes.extend(invoke(name, call) for name, call in calls)
            else:
                barrier = threading.Barrier(len(calls) + 1)
                slots: list[
                    tuple[str, object, BaseException | None] | None
                ] = [None] * len(calls)

                def compete(index: int) -> None:
                    barrier.wait()
                    name, call = calls[index]
                    slots[index] = invoke(name, call)

                threads = [
                    self._registered_thread(
                        target=compete,
                        args=(index,),
                        name=f"sec-a-global-token-race-{index}",
                    )
                    for index in range(len(calls))
                ]
                for thread in threads:
                    thread.start()
                barrier.wait()
                for thread in threads:
                    thread.join(timeout=2)
                outcomes.extend(item for item in slots if item is not None)
        return outcomes, len(popen.calls) + self._factory_call_count(run)

    @staticmethod
    def _add_trusted_local(kwargs: dict[str, object], value: object) -> None:
        if value is not _UNSET:
            kwargs["trusted_local"] = value

    @staticmethod
    def _spawn_metadata(calls) -> dict[str, object] | None:
        if not calls:
            return None
        args, kwargs = calls[0]
        metadata = dict(kwargs)
        metadata["_recorded_command"] = (
            args[0] if args else kwargs.get("args")
        )
        return metadata

    def _check_invalid_request_no_challenge(
        self,
        name: str,
        call,
        violations: list[str],
    ) -> tuple[object, BaseException | None]:
        """Illegal requests are denied, never offered a signable challenge."""
        popen = FakePopenFactory()
        run = FakeRunFactory()
        before_hits = len(self._tripwire_hits)
        with self._patched_processes(popen, run), mock.patch.object(
            command_module.shutil,
            "which",
            return_value="/usr/bin/python3",
        ):
            result, error = self._capture(call)
            stop = getattr(result, "stop", None)
            if callable(stop):
                _, stop_error = self._capture(stop)
                error = error or stop_error
        spawn_count = len(popen.calls) + self._factory_call_count(run)
        if spawn_count:
            violations.append(
                f"{name}: invalid preflight spawn_count={spawn_count}"
            )
        if self._first_mapping_value(
            result,
            error,
            "confirmation_request",
        ) is not _UNSET:
            violations.append(f"{name}: invalid request exposed confirmation_request")
        if not self._has_structured_code(
            result,
            error,
            "SANDBOX_REQUIRED",
        ):
            observed = (
                type(error).__name__ if error is not None
                else type(result).__name__ if result is not None
                else "None"
            )
            violations.append(
                f"{name}: structured SANDBOX_REQUIRED absent "
                f"(observed={observed})"
            )
        if len(self._tripwire_hits) != before_hits:
            violations.append(f"{name}: escaped fail-closed boundary")
        return result, error

    def _confirmation_for(
        self,
        root: Path,
        profile_id: str,
        executable: str,
        command: tuple[str, ...],
        *,
        wall_deadline_seconds: float,
        python_profile: bool = False,
        server_log: bool = False,
        output_limit_chars: int = 10_000,
        request_sink: dict[str, Mapping] | None = None,
    ) -> object:
        issuer = getattr(
            coding_workflow,
            "issue_trusted_local_confirmation",
            None,
        )
        if not callable(issuer):
            return _UNSET
        request = self._confirmation_request_for(
            root,
            profile_id,
            executable,
            command,
            wall_deadline_seconds=wall_deadline_seconds,
            output_limit_chars=output_limit_chars,
        )
        if request is _UNSET:
            return _UNSET
        if request_sink is not None:
            request_sink[profile_id] = dict(request)
        try:
            return issuer(
                workspace_digest=request["workspace_digest"],
                input_digest=request["input_digest"],
                profile_digest=request["profile_digest"],
                expires_at_monotonic=time.monotonic() + 60,
            )
        except Exception:
            return _UNSET

    def _all_confirmation_requests(
        self,
        root: Path,
    ) -> dict[str, object]:
        """Return the five Profile challenges for one unchanged Workspace."""
        root.mkdir(parents=True, exist_ok=True)
        runner_path = root / "browser-runner.mjs"
        spec_path = root / "ui-spec.json"
        runner_path.write_text("// SEC-EXEC fake runner\n", encoding="utf-8")
        spec_path.write_text("{}\n", encoding="utf-8")
        browser_command = (
            "node", str(runner_path),
            "--url", "http://127.0.0.1:4173/",
            "--spec", str(spec_path),
            "--screenshot", str(root / "actual.png"),
            "--result", str(root / "result.json"),
        )
        specifications = {
            "core": (
                PROFILE_IDS["core"], "/usr/bin/python3", CORE_COMMAND, 30,
            ),
            "legacy": (
                PROFILE_IDS["legacy"], "/usr/bin/python3", LEGACY_COMMAND, 60,
            ),
            "visionforge_build": (
                PROFILE_IDS["visionforge_build"],
                "/usr/bin/pnpm",
                BUILD_COMMAND,
                60,
            ),
            "visionforge_browser": (
                PROFILE_IDS["visionforge_browser"],
                "/usr/bin/node",
                browser_command,
                45,
            ),
            "visionforge_dev": (
                PROFILE_IDS["visionforge_dev"],
                "/usr/bin/pnpm",
                DEV_COMMAND,
                60,
            ),
        }
        return {
            entrypoint: self._confirmation_request_for(
                root,
                profile_id,
                executable,
                command,
                wall_deadline_seconds=deadline,
                output_limit_chars=10_000,
            )
            for entrypoint, (
                profile_id,
                executable,
                command,
                deadline,
            ) in specifications.items()
        }

    @staticmethod
    def _issue_confirmation_map(
        issuer,
        requests: Mapping[str, object],
        *,
        expires_at_monotonic: float,
        drift_field: str | None = None,
    ) -> object:
        tokens: dict[str, object] = {}
        for entrypoint, candidate in requests.items():
            if not isinstance(candidate, Mapping):
                return _UNSET
            values = dict(candidate)
            if drift_field is not None:
                values[drift_field] = (
                    LocalTrustedExecutionBehaviorExpectedRedTests
                    ._different_digest(values.get(drift_field))
                )
            try:
                tokens[entrypoint] = issuer(
                    workspace_digest=values["workspace_digest"],
                    input_digest=values["input_digest"],
                    profile_digest=values["profile_digest"],
                    expires_at_monotonic=expires_at_monotonic,
                )
            except Exception:
                return _UNSET
        return tokens

    def _confirmation_request_for(
        self,
        root: Path,
        profile_id: str,
        executable: str,
        command: tuple[str, ...],
        *,
        wall_deadline_seconds: float,
        output_limit_chars: int,
    ) -> object:
        """Obtain Runtime-computed digests without freezing their preimages."""
        root.mkdir(parents=True, exist_ok=True)
        before = self._tree_digest(root)
        popen = FakePopenFactory()
        run = FakeRunFactory()
        if profile_id == PROFILE_IDS["core"]:
            target = ControlledCommandRunner(
                root,
                CommandPolicy(
                    allowed_executables={command[0]},
                    allowed_commands=[list(command)],
                ),
                max_timeout_seconds=max(30, wall_deadline_seconds),
                output_limit_chars=output_limit_chars,
            )
            call = lambda: target.run(
                command,
                timeout_seconds=wall_deadline_seconds,
            )
        elif profile_id == PROFILE_IDS["legacy"]:
            target = ProjectWorkspace(
                root,
                command_timeout=int(wall_deadline_seconds),
            )
            call = lambda: target.run(list(command))
        else:
            target = BrowserProcessRunner(
                allowed_executables=frozenset({command[0]}),
                executable_overrides={command[0]: executable},
            )
            if profile_id == PROFILE_IDS["visionforge_dev"]:
                call = lambda: target.start_background(
                    command,
                    cwd=root,
                    log_path=root / "server.log",
                )
            else:
                call = lambda: target.run(
                    command,
                    cwd=root,
                    timeout_seconds=wall_deadline_seconds,
                )
        with self._patched_processes(popen, run), mock.patch.object(
            command_module.shutil,
            "which",
            return_value=executable,
        ):
            result, error = self._capture(call)
        if (
            popen.calls
            or run.calls
            or self._tree_digest(root) != before
            or not self._has_structured_code(
                result,
                error,
                "SANDBOX_REQUIRED",
            )
        ):
            return _UNSET
        request = self._first_mapping_value(
            result,
            error,
            "confirmation_request",
        )
        expected = {"workspace_digest", "input_digest", "profile_digest"}
        if not isinstance(request, Mapping) or set(request) != expected:
            return _UNSET
        if any(
            not isinstance(request[name], str)
            or len(request[name]) != 64
            or any(character not in "0123456789abcdef" for character in request[name])
            for name in expected
        ):
            return _UNSET
        return dict(request)

    @staticmethod
    def _tree_digest(root: Path) -> tuple[tuple[str, str], ...]:
        entries = []
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(root))
            if path.is_symlink():
                entries.append((relative, f"symlink:{os.readlink(path)}"))
            elif path.is_file():
                entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
            elif path.is_dir():
                entries.append((relative + "/", "directory"))
        return tuple(entries)

    @staticmethod
    def _capture(call):
        try:
            return call(), None
        except Exception as exc:
            return None, exc

    def _fake_background_popen(self, payload: str) -> FakePopenFactory:
        """Feed a fake background child through PIPE or an opened file sink."""
        spawn_state: dict[str, object] = {}

        def on_spawn(_args, kwargs) -> None:
            stream = kwargs.get("stdout")
            spawn_state["pipe"] = stream == subprocess.PIPE
            if stream != subprocess.PIPE:
                write = getattr(stream, "write", None)
                flush = getattr(stream, "flush", None)
                if callable(write):
                    write(payload)
                if callable(flush):
                    flush()

        def process_factory() -> FakeProcess:
            process = FakeProcess(running=True)
            if spawn_state.get("pipe") is True:
                read_fd, write_fd = os.pipe()
                try:
                    os.set_inheritable(read_fd, False)
                    os.set_inheritable(write_fd, False)
                    os.set_blocking(read_fd, False)
                    os.set_blocking(write_fd, False)
                    encoded = payload.encode("utf-8")
                    offset = 0
                    while offset < len(encoded):
                        try:
                            written = os.write(write_fd, encoded[offset:])
                        except BlockingIOError as exc:
                            raise AssertionError(
                                "bounded fake pipe payload exceeded capacity"
                            ) from exc
                        if written <= 0:
                            raise AssertionError(
                                "bounded fake pipe write made no progress"
                            )
                        offset += written
                except BaseException:
                    try:
                        os.close(read_fd)
                    except OSError:
                        pass
                    raise
                finally:
                    try:
                        os.close(write_fd)
                    except OSError:
                        pass
                try:
                    os.fstat(write_fd)
                except OSError:
                    write_closed_verified = True
                else:
                    write_closed_verified = False
                try:
                    read_stream = os.fdopen(
                        read_fd,
                        "r",
                        encoding="utf-8",
                        errors="replace",
                        closefd=True,
                    )
                    with selectors.DefaultSelector() as selector:
                        selector.register(read_stream, selectors.EVENT_READ)
                        ready = selector.select(0)
                    if os.get_blocking(read_fd) is not False or not ready:
                        raise AssertionError(
                            "fake background pipe was not nonblocking/readable"
                        )
                except BaseException:
                    try:
                        os.close(read_fd)
                    except OSError:
                        pass
                    raise
                record: dict[str, object] = {
                    "read_fd": read_fd,
                    "write_fd": write_fd,
                    "read_stream": read_stream,
                    "write_closed_verified": write_closed_verified,
                }
                self._pipe_records.append(record)
                factory.pipe_records.append(record)
                process.stdout = read_stream
                process.stderr = None
            else:
                process.stdout = None
                process.stderr = None
            return process

        factory = FakePopenFactory(process_factory, on_spawn=on_spawn)
        factory.pipe_records = []
        return factory

    @staticmethod
    def _check_background_pipe_closed(
        factory: FakePopenFactory,
        label: str,
        violations: list[str],
    ) -> None:
        records = getattr(factory, "pipe_records", None)
        if not isinstance(records, list) or len(records) != 1:
            violations.append(f"{label}: tracked pipe endpoints unavailable")
            return
        stream = records[0].get("read_stream")
        if getattr(stream, "closed", False) is not True:
            violations.append(f"{label}: tracked pipe read end was not closed")
        else:
            try:
                descriptor = stream.fileno()
            except ValueError:
                pass
            except Exception as exc:
                violations.append(
                    f"{label}: tracked pipe fileno check failed "
                    f"({type(exc).__name__})"
                )
            else:
                violations.append(
                    f"{label}: closed pipe retained descriptor {descriptor}"
                )
        if records[0].get("write_closed_verified") is not True:
            violations.append(f"{label}: tracked pipe write end was not closed")

    @staticmethod
    def _close_fake_process_streams(factory: FakePopenFactory) -> None:
        for process in factory.processes:
            for name in ("stdout", "stderr", "stdin"):
                stream = getattr(process, name, None)
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

    @staticmethod
    def _scan_workspace_secret(
        root: Path,
        secret: str,
        phase: str,
        violations: list[str],
    ) -> None:
        secret_bytes = secret.encode("utf-8")
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                payload = path.read_bytes()
            except (OSError, ValueError) as exc:
                violations.append(
                    f"{phase}: cannot inspect {path.relative_to(root)}: "
                    f"{type(exc).__name__}"
                )
                continue
            if secret_bytes in payload:
                violations.append(
                    f"{phase}: raw secret in {path.relative_to(root)}"
                )

    def _check_cached_os_write_aliases(
        self,
        root: Path,
        violations: list[str],
    ) -> None:
        """Prove the write recorder reaches pre-bound production aliases."""
        real_open = os.open
        real_write = os.write
        real_close = os.close
        module_name = "coding_workflow._sec_write_alias_fixture_v7"
        alias_module = types.ModuleType(module_name)

        def write_once(open_alias, write_alias, close_alias, path, payload):
            descriptor = open_alias(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                return write_alias(descriptor, payload)
            finally:
                close_alias(descriptor)

        class CachedFileDescriptors:
            open_alias = staticmethod(real_open)
            write_alias = staticmethod(real_write)
            close_alias = staticmethod(real_close)

        CachedFileDescriptors.__module__ = module_name

        def default_writer(
            path,
            payload,
            open_alias=real_open,
            *,
            write_alias=real_write,
            close_alias=real_close,
        ):
            return write_once(
                open_alias,
                write_alias,
                close_alias,
                path,
                payload,
            )

        default_writer.__module__ = module_name

        def make_closure_writer():
            open_alias = real_open
            write_alias = real_write
            close_alias = real_close

            def closure_writer(path, payload):
                return write_once(
                    open_alias,
                    write_alias,
                    close_alias,
                    path,
                    payload,
                )

            return closure_writer

        closure_writer = make_closure_writer()
        closure_writer.__module__ = module_name
        alias_module.open_alias = real_open
        alias_module.write_alias = real_write
        alias_module.close_alias = real_close
        alias_module.CachedFileDescriptors = CachedFileDescriptors
        alias_module.default_writer = default_writer
        alias_module.closure_writer = closure_writer
        alias_module.write_partial = functools.partial(real_write)
        alias_module.alias_container = {
            "open": [real_open],
            "write": (real_write,),
            "close": {"call": real_close},
        }
        original_class_slots = tuple(
            vars(CachedFileDescriptors)[name]
            for name in ("open_alias", "write_alias", "close_alias")
        )
        original_defaults = default_writer.__defaults__
        original_kwdefaults = default_writer.__kwdefaults__
        original_closure = tuple(
            cell.cell_contents for cell in closure_writer.__closure__ or ()
        )
        original_partial = alias_module.write_partial
        original_container = alias_module.alias_container
        sys.modules[module_name] = alias_module

        def exercise() -> None:
            with self._record_temp_writes(root) as recorder:
                def module_writer(path, payload):
                    return write_once(
                        alias_module.open_alias,
                        alias_module.write_alias,
                        alias_module.close_alias,
                        path,
                        payload,
                    )

                def class_writer(path, payload):
                    return write_once(
                        alias_module.CachedFileDescriptors.open_alias,
                        alias_module.CachedFileDescriptors.write_alias,
                        alias_module.CachedFileDescriptors.close_alias,
                        path,
                        payload,
                    )

                def partial_writer(path, payload):
                    descriptor = alias_module.open_alias(
                        path,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                        0o600,
                    )
                    try:
                        return alias_module.write_partial(descriptor, payload)
                    finally:
                        alias_module.close_alias(descriptor)

                def container_writer(path, payload):
                    aliases = alias_module.alias_container
                    return write_once(
                        aliases["open"][0],
                        aliases["write"][0],
                        aliases["close"]["call"],
                        path,
                        payload,
                    )

                writers = {
                    "module": module_writer,
                    "class": class_writer,
                    "defaults": alias_module.default_writer,
                    "closure": alias_module.closure_writer,
                    "partial": partial_writer,
                    "container": container_writer,
                }
                expected_paths: set[Path] = set()
                for label, writer in writers.items():
                    path = (root / f"cached-os-alias-{label}.log").resolve()
                    expected_paths.add(path)
                    writer(path, FAKE_SECRET.encode("utf-8"))
                    writer(path, b"overwritten-safe")
                recorded_paths = set(self._write_events_containing(
                    recorder.events,
                    FAKE_SECRET,
                ))
                if recorded_paths != expected_paths:
                    violations.append(
                        "cached os.open/write/close aliases escaped recorder: "
                        f"missing={sorted(expected_paths - recorded_paths)}"
                    )
            if recorder.unclosed_fds:
                violations.append(
                    "cached os alias fixture left low-level fds open"
                )

        try:
            _, exercise_error = self._capture(exercise)
            if exercise_error is not None:
                violations.append(
                    "cached os alias recorder fixture failed: "
                    f"{type(exercise_error).__name__}:{exercise_error}"
                )
            restored = (
                alias_module.open_alias is real_open
                and alias_module.write_alias is real_write
                and alias_module.close_alias is real_close
                and all(
                    vars(CachedFileDescriptors)[name] is original
                    for name, original in zip(
                        ("open_alias", "write_alias", "close_alias"),
                        original_class_slots,
                    )
                )
                and default_writer.__defaults__ is original_defaults
                and default_writer.__kwdefaults__ is original_kwdefaults
                and all(
                    cell.cell_contents is original
                    for cell, original in zip(
                        closure_writer.__closure__ or (),
                        original_closure,
                    )
                )
                and alias_module.write_partial is original_partial
                and alias_module.alias_container is original_container
            )
            if not restored:
                violations.append(
                    "cached os alias recorder did not restore exact identities"
                )
        finally:
            sys.modules.pop(module_name, None)

    @staticmethod
    def _record_temp_writes(root: Path):
        """Record high- and low-level writes under one temporary root."""
        resolved_root = root.resolve()
        real_builtin_open = builtins.open
        real_io_open = io.open
        real_os_open = os.open
        real_os_write = os.write
        real_os_close = os.close

        class RecordingStream:
            def __init__(self, stream, path: Path, events: list) -> None:
                self._stream = stream
                self._path = path
                self._events = events

            def write(self, payload):
                self._events.append((self._path, payload))
                return self._stream.write(payload)

            def writelines(self, lines):
                for line in lines:
                    self.write(line)

            def __enter__(self):
                self._stream.__enter__()
                return self

            def __exit__(self, exc_type, exc, traceback):
                return self._stream.__exit__(exc_type, exc, traceback)

            def __iter__(self):
                return iter(self._stream)

            def __next__(self):
                return next(self._stream)

            def __getattr__(self, name):
                return getattr(self._stream, name)

        class WriteRecorder:
            def __init__(self) -> None:
                self.events: list[tuple[Path, object]] = []
                self._fd_paths: dict[int, Path] = {}
                self.unclosed_fds: list[tuple[int, Path]] = []
                self._recording_os_open = self._os_open
                self._recording_os_write = self._os_write
                self._recording_os_close = self._os_close

            @staticmethod
            def _resolved_path(file, dir_fd=None) -> Path | None:
                try:
                    candidate = Path(file)
                    if not candidate.is_absolute() and dir_fd is not None:
                        candidate = (
                            Path(os.readlink(f"/dev/fd/{dir_fd}")) / candidate
                        )
                    path = candidate.resolve()
                    path.relative_to(resolved_root)
                except (TypeError, OSError, ValueError):
                    return None
                return path

            def _wrap(self, open_function, file, *args, **kwargs):
                stream = open_function(file, *args, **kwargs)
                mode = args[0] if args else kwargs.get("mode", "r")
                if not isinstance(mode, str) or not any(
                    marker in mode for marker in ("w", "a", "x", "+")
                ):
                    return stream
                try:
                    path = self._resolved_path(file)
                except Exception:
                    path = None
                if path is None:
                    return stream
                return RecordingStream(stream, path, self.events)

            def _os_open(
                self,
                file,
                flags,
                mode=0o777,
                *,
                dir_fd=None,
            ):
                kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
                descriptor = real_os_open(file, flags, mode, **kwargs)
                writable = bool(flags & (os.O_WRONLY | os.O_RDWR))
                path = self._resolved_path(file, dir_fd)
                if writable and path is not None:
                    self._fd_paths[descriptor] = path
                return descriptor

            def _os_write(self, descriptor, payload):
                path = self._fd_paths.get(descriptor)
                if path is not None:
                    try:
                        recorded = bytes(payload)
                    except (TypeError, ValueError):
                        recorded = payload
                    self.events.append((path, recorded))
                return real_os_write(descriptor, payload)

            def _os_close(self, descriptor):
                result = real_os_close(descriptor)
                self._fd_paths.pop(descriptor, None)
                return result

            def __enter__(self):
                self._stack = contextlib.ExitStack()
                aliases = (
                    LocalTrustedExecutionBehaviorExpectedRedTests
                    ._loaded_production_aliases({
                        real_os_open: self._recording_os_open,
                        real_os_write: self._recording_os_write,
                        real_os_close: self._recording_os_close,
                    })
                )
                self._stack.enter_context(mock.patch.object(
                    builtins,
                    "open",
                    new=lambda file, *args, **kwargs: self._wrap(
                        real_builtin_open,
                        file,
                        *args,
                        **kwargs,
                    ),
                ))
                self._stack.enter_context(mock.patch.object(
                    io,
                    "open",
                    new=lambda file, *args, **kwargs: self._wrap(
                        real_io_open,
                        file,
                        *args,
                        **kwargs,
                    ),
                ))
                self._stack.enter_context(mock.patch.object(
                    os,
                    "open",
                    new=self._recording_os_open,
                ))
                self._stack.enter_context(mock.patch.object(
                    os,
                    "write",
                    new=self._recording_os_write,
                ))
                self._stack.enter_context(mock.patch.object(
                    os,
                    "close",
                    new=self._recording_os_close,
                ))
                for owner, name, replacement in aliases:
                    self._stack.enter_context(
                        LocalTrustedExecutionBehaviorExpectedRedTests
                        ._patch_cached_alias(owner, name, replacement)
                    )
                return self

            def __exit__(self, exc_type, exc, traceback):
                late_replacements = {
                    self._recording_os_open: real_os_open,
                    self._recording_os_write: real_os_write,
                    self._recording_os_close: real_os_close,
                }
                for owner, name, replacement in (
                    LocalTrustedExecutionBehaviorExpectedRedTests
                    ._loaded_production_aliases(late_replacements)
                ):
                    setattr(owner, name, replacement)
                self._stack.close()
                self.unclosed_fds = list(self._fd_paths.items())
                for descriptor in tuple(self._fd_paths):
                    try:
                        real_os_close(descriptor)
                    except OSError:
                        pass
                    self._fd_paths.pop(descriptor, None)
                return False

        return WriteRecorder()

    @staticmethod
    def _write_events_containing(
        events: list[tuple[Path, object]],
        secret: str,
    ) -> list[Path]:
        secret_bytes = secret.encode("utf-8")
        combined: dict[Path, bytearray] = {}
        matches: set[Path] = set()
        for path, payload in events:
            if isinstance(payload, str):
                chunk = payload.encode("utf-8", errors="replace")
            elif isinstance(payload, (bytes, bytearray, memoryview)):
                chunk = bytes(payload)
            else:
                continue
            combined.setdefault(path, bytearray()).extend(chunk)
            if secret_bytes in chunk:
                matches.add(path)
        matches.update(
            path for path, payload in combined.items()
            if secret_bytes in payload
        )
        return sorted(matches)

    @staticmethod
    def _direct_secret_locations(
        value: object,
        error: BaseException | None,
        secret: str,
    ) -> list[str]:
        """Inspect real fields without trusting repr/to_dict serializers."""
        hits: list[str] = []
        seen: set[int] = set()

        def visit(item: object, path: str) -> None:
            if isinstance(item, str):
                if secret in item:
                    hits.append(path)
                return
            if isinstance(item, (bytes, bytearray)):
                if secret.encode("utf-8") in bytes(item):
                    hits.append(path)
                return
            if item is None or isinstance(item, (bool, int, float)):
                return
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            if isinstance(item, Mapping):
                for key, child in item.items():
                    visit(key, f"{path}.<key>")
                    visit(child, f"{path}[{key!s}]")
                return
            if isinstance(item, (tuple, list, set, frozenset)):
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]")
                return
            if isinstance(item, BaseException):
                visit(item.args, f"{path}.args")
                visit(item.__cause__, f"{path}.__cause__")
                visit(item.__context__, f"{path}.__context__")
            descriptor_names: set[str] = set()
            try:
                mro = inspect.getmro(type(item))
            except BaseException as exc:
                visit(exc, f"{path}.<mro-error>")
                mro = ()
            for owner in mro:
                try:
                    namespace = vars(owner)
                except BaseException as exc:
                    visit(exc, f"{path}.<descriptor-error>")
                    continue
                slots = namespace.get("__slots__", ())
                if isinstance(slots, str):
                    descriptor_names.add(slots)
                else:
                    try:
                        descriptor_names.update(
                            name for name in slots if isinstance(name, str)
                        )
                    except (TypeError, ValueError):
                        pass
                descriptor_names.update(
                    name
                    for name, descriptor in namespace.items()
                    if isinstance(name, str)
                    and (
                        isinstance(descriptor, property)
                        or inspect.isdatadescriptor(descriptor)
                    )
                )
            descriptor_names.difference_update({"__dict__", "__weakref__"})
            descriptor_names = {
                name for name in descriptor_names
                if not (name.startswith("__") and name.endswith("__"))
            }
            for name in sorted(descriptor_names):
                try:
                    child = getattr(item, name)
                except AttributeError:
                    continue
                except BaseException as exc:
                    visit(exc, f"{path}.{name}.<getter-error>")
                    continue
                visit(child, f"{path}.{name}")
            try:
                attributes = vars(item)
            except (TypeError, ValueError):
                return
            for name, child in attributes.items():
                visit(child, f"{path}.{name}")

        visit(value, "result")
        visit(error, "error")
        return hits

    @classmethod
    def _safe_log_tail(
        cls,
        managed: object,
        limit: int,
        label: str,
        violations: list[str],
    ) -> str:
        method = getattr(managed, "log_tail", None)
        if not callable(method):
            violations.append(f"{label}: log_tail unavailable")
            return ""
        value, error = cls._capture(lambda: method(limit))
        if error is not None or not isinstance(value, str):
            violations.append(
                f"{label}: log_tail failed with "
                f"{type(error).__name__ if error is not None else type(value).__name__}"
            )
            return ""
        return value

    @classmethod
    def _poll_log_tail(
        cls,
        managed: object,
        limit: int,
        label: str,
        violations: list[str],
        *,
        expected: str | None = None,
    ) -> str:
        method = getattr(managed, "log_tail", None)
        if not callable(method):
            violations.append(f"{label}: log_tail unavailable")
            return ""
        last_value = ""
        last_error: BaseException | None = None
        for _ in range(50):
            value, error = cls._capture(lambda: method(limit))
            if error is None and isinstance(value, str):
                last_value = value
                if value == expected if expected is not None else bool(value):
                    return value
            else:
                last_error = error
            threading.Event().wait(0.005)
        if last_error is not None:
            violations.append(
                f"{label}: log_tail failed with {type(last_error).__name__}"
            )
        return last_value

    @staticmethod
    def _consume_context(context) -> None:
        with context:
            return None

    @staticmethod
    def _different_digest(value: object) -> str:
        if not LocalTrustedExecutionBehaviorExpectedRedTests._is_digest(value):
            return "0" * 64
        return ("0" if value[:1] != "0" else "1") + value[1:]

    @staticmethod
    def _factory_call_count(factory: object) -> int:
        calls = getattr(factory, "calls", None)
        if isinstance(calls, list):
            return len(calls)
        return int(getattr(factory, "call_count", 0))

    @classmethod
    def _structured_mappings(
        cls,
        value: object,
        error: BaseException | None,
    ) -> list[Mapping]:
        mappings: list[Mapping] = []
        seen: set[int] = set()

        def visit(item: object) -> None:
            if item is None or id(item) in seen:
                return
            seen.add(id(item))
            if isinstance(item, Mapping):
                mappings.append(item)
                for child in item.values():
                    visit(child)
                return
            for method_name in ("to_dict", "evidence"):
                method = getattr(item, method_name, None)
                if callable(method):
                    try:
                        produced = method()
                    except Exception:
                        continue
                    if isinstance(produced, Mapping):
                        visit(produced)
            attributes = {}
            for name in (
                "code",
                "error_code",
                "confirmation_request",
                "cleanup",
                "cleanup_manifest",
                "cleanup_evidence",
                "cleanup_evidence_digest",
                "quarantine_id",
                "quarantine_generation",
                "recovery_request",
                "recovered",
                "profile_manifest",
                "execution_manifest",
            ):
                if hasattr(item, name):
                    attributes[name] = getattr(item, name)
            if attributes:
                visit(attributes)

        visit(error)
        visit(value)
        return mappings

    @classmethod
    def _first_mapping_value(
        cls,
        value: object,
        error: BaseException | None,
        key: str,
    ) -> object:
        for mapping in cls._structured_mappings(value, error):
            if key in mapping:
                return mapping[key]
        return _UNSET

    @classmethod
    def _has_structured_code(
        cls,
        value: object,
        error: BaseException | None,
        expected: str,
    ) -> bool:
        if expected == "SANDBOX_REQUIRED":
            if error is not None:
                error_type = type(error)
                if (
                    isinstance(error, TypeError)
                    or not error_type.__module__.startswith("coding_workflow")
                    or not error_type.__name__.endswith("Error")
                ):
                    return False
            elif value is None:
                return False
            else:
                structured_return = isinstance(value, Mapping) and len(value) > 1
                for method_name in ("to_dict", "evidence"):
                    method = getattr(value, method_name, None)
                    if not callable(method):
                        continue
                    try:
                        produced = method()
                    except Exception:
                        continue
                    if isinstance(produced, Mapping) and len(produced) > 1:
                        structured_return = True
                attributes = getattr(value, "__dict__", None)
                if (
                    isinstance(attributes, Mapping)
                    and type(value).__module__.startswith("coding_workflow")
                    and any(key in attributes for key in ("code", "error_code"))
                    and any(
                        key in attributes
                        for key in (
                            "message",
                            "reason",
                            "status",
                            "error",
                            "confirmation_request",
                        )
                    )
                ):
                    structured_return = True
                if not structured_return:
                    return False
        for mapping in cls._structured_mappings(value, error):
            for key in ("code", "error_code"):
                candidate = mapping.get(key)
                if getattr(candidate, "value", candidate) == expected:
                    return True
        return False

    @staticmethod
    def _is_digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @classmethod
    def _has_recovery_success(
        cls,
        value: object,
        error: BaseException | None,
        quarantine_id: str,
    ) -> bool:
        if error is not None:
            return False
        for mapping in cls._structured_mappings(value, error):
            if mapping.get("quarantine_id") != quarantine_id:
                continue
            if mapping.get("recovered") is True:
                return True
            status = mapping.get("status")
            status = getattr(status, "value", status)
            if status in {"recovered", "cleared"}:
                return True
        return False

    @classmethod
    def _check_cleanup_evidence(
        cls,
        name: str,
        value: object,
        error: BaseException | None,
        violations: list[str],
        *,
        expect_verified: bool = True,
    ) -> None:
        cleanup = _UNSET
        for mapping in cls._structured_mappings(value, error):
            for key in ("cleanup_evidence", "cleanup_manifest", "cleanup"):
                candidate = mapping.get(key)
                if isinstance(candidate, Mapping):
                    cleanup = candidate
                    break
            if cleanup is not _UNSET:
                break
        if not isinstance(cleanup, Mapping):
            violations.append(f"{name}: cleanup barrier evidence absent")
            return

        raw_actions = cleanup.get("actions")
        phases: list[str] = []
        action_names = {
            "term": "term",
            "sigterm": "term",
            "term_process_group": "term",
            "terminate_process_group": "term",
            "kill": "kill",
            "sigkill": "kill",
            "kill_process_group": "kill",
            "wait_reap": "wait_reap",
            "wait/reap": "wait_reap",
            "wait_and_reap_direct_child": "wait_reap",
            "wait_reap_direct_child": "wait_reap",
            "verify": "verify",
            "verify_owned_resources": "verify",
            "audit_owned_resources": "verify",
        }
        if isinstance(raw_actions, (tuple, list)):
            for item in raw_actions:
                if isinstance(item, Mapping):
                    item = next((
                        item.get(key)
                        for key in ("action", "phase", "operation", "name")
                        if item.get(key) is not None
                    ), "")
                text = str(getattr(item, "value", item)).strip().lower()
                normalized = text.replace("-", "_").replace(" ", "_")
                phases.append(
                    action_names.get(normalized, f"unknown:{normalized}")
                )
        if phases != ["term", "kill", "wait_reap", "verify"]:
            violations.append(
                f"{name}: cleanup action order={phases!r}"
            )

        resources = cleanup.get("resources")
        # This mock-only card can observe the owned PID/PGID through its fake
        # signal/reap trace.  Real port, inherited-handle, and marker-stability
        # oracles are deliberately routed to tests/_local_execution_posix.py;
        # merely echoing those keys here is not accepted as proof.
        required_resources = {"pid", "pgid"}
        if (
            not isinstance(resources, Mapping)
            or not required_resources.issubset(resources)
        ):
            violations.append(
                f"{name}: cleanup resources must include owned pid/pgid"
            )
        direct_child_reaped = cleanup.get(
            "direct_child_reaped",
            cleanup.get("reaped"),
        )
        if expect_verified:
            if direct_child_reaped is not True:
                violations.append(f"{name}: direct child was not proved reaped")
        elif type(direct_child_reaped) is not bool:
            violations.append(
                f"{name}: direct-child reap outcome is not structured bool"
            )
        verified = cleanup.get(
            "verified",
            cleanup.get("barrier_verified"),
        )
        if expect_verified:
            if verified is not True:
                violations.append(f"{name}: owned resources were not verified gone")
        elif verified is not False:
            violations.append(
                f"{name}: failed cleanup did not record verified=False"
            )
        duration = cleanup.get(
            "barrier_duration_seconds",
            cleanup.get(
                "duration_seconds",
                cleanup.get("cleanup_duration_seconds"),
            ),
        )
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or not 0 <= duration <= 5
        ):
            violations.append(f"{name}: cleanup duration is not within 0..5s")
        digest = cls._first_mapping_value(
            value,
            error,
            "cleanup_evidence_digest",
        )
        if not cls._is_digest(digest):
            violations.append(f"{name}: cleanup_evidence_digest absent")

    @staticmethod
    def _check_fake_cleanup_trace(
        name: str,
        process: FakeProcess,
        trace: list[tuple[object, ...]],
        violations: list[str],
        *,
        expect_term: bool,
        expect_kill: bool,
    ) -> None:
        signals = [
            item for item in trace
            if item[0] in {"killpg", "kill"}
            and len(item) >= 3
            and item[2] in {signal.SIGTERM, signal.SIGKILL}
        ]
        if any(
            item[1] != process.pid
            if item[0] == "killpg"
            else item[1] != -process.pid
            for item in signals
        ):
            violations.append(
                f"{name}: signal target escaped owned pgid={process.pid}: "
                f"{signals!r}"
            )
        expected = []
        if expect_term:
            expected.append(signal.SIGTERM)
        if expect_kill:
            expected.append(signal.SIGKILL)
        if [item[2] for item in signals] != expected:
            violations.append(
                f"{name}: TERM/KILL order={[item[2] for item in signals]!r}"
            )
        reap_indexes = [
            index for index, item in enumerate(trace)
            if item[0] in {"wait", "communicate"}
        ]
        if not reap_indexes:
            violations.append(f"{name}: FakeProcess was never waited/reaped")
        probes = [
            item for item in trace
            if item[0] in {"killpg", "kill"}
            and len(item) >= 3
            and item[2] == 0
        ]
        if any(
            item[1] != process.pid
            if item[0] == "killpg"
            else item[1] != -process.pid
            for item in probes
        ):
            violations.append(
                f"{name}: verification probe escaped owned pgid={process.pid}: "
                f"{probes!r}"
            )
        if not probes:
            violations.append(
                f"{name}: owned process-group disappearance was not probed"
            )
        elif reap_indexes:
            probe_indexes = [
                index for index, item in enumerate(trace)
                if item[0] in {"killpg", "kill"}
                and len(item) >= 3
                and item[2] == 0
            ]
            if min(probe_indexes) <= max(reap_indexes):
                violations.append(
                    f"{name}: disappearance probe preceded final wait/reap"
                )

    @staticmethod
    def _check_terminal_contract(
        name: str,
        value: object,
        error: BaseException | None,
        violations: list[str],
    ) -> None:
        expected_results = {
            "core-success": (0, False),
            "core-nonzero": (7, False),
            "core-timeout": (None, True),
            "legacy-success": (0, False),
            "legacy-nonzero": (7, False),
            "legacy-timeout": (124, True),
            "browser-success": (0, False),
            "browser-nonzero": (4, False),
            "browser-timeout": (124, True),
        }
        if name in expected_results:
            expected_exit, expected_timeout = expected_results[name]
            if error is not None or value is None:
                violations.append(
                    f"{name}: old terminal Result was replaced by an error"
                )
                return
            if getattr(value, "exit_code", _UNSET) != expected_exit:
                violations.append(f"{name}: exit_code semantics changed")
            timed_out = getattr(value, "timed_out", _UNSET)
            if type(timed_out) is not bool or timed_out is not expected_timeout:
                violations.append(f"{name}: timed_out semantics changed")
            return
        if name == "browser-cancel":
            if value is not None or not isinstance(error, TaskCancelledError):
                violations.append("browser-cancel: cancellation semantics changed")
            return
        if name == "browser-exception":
            if value is not None or type(error) is not RuntimeError:
                violations.append("browser-exception: error semantics changed")
            return
        if name == "browser-background-stop":
            if error is not None or value is None:
                violations.append("browser-background-stop: stop failed")
            elif getattr(value, "running", None) is not False:
                violations.append(
                    "browser-background-stop: process remains running after stop"
                )
            return
        if name == "browser-readiness-failure" and (
            value is not None or not isinstance(error, BrowserRuntimeError)
        ):
            violations.append(
                "browser-readiness-failure: error semantics changed"
            )

    @classmethod
    def _find_profile_manifest(
        cls,
        value: object,
        error: BaseException | None,
    ) -> object:
        for mapping in cls._structured_mappings(value, error):
            if (
                isinstance(mapping.get("profile_id"), str)
                and "argv" in mapping
                and "limits" in mapping
                and "environment" in mapping
            ):
                return mapping
        return _UNSET

    @staticmethod
    def _check_profile_manifest(
        entrypoint: str,
        manifest: Mapping,
        expected: Mapping,
        root: Path,
        confirmation_request: object,
        spawn: Mapping | None,
        violations: list[str],
    ) -> None:
        version = manifest.get(
            "contract_version",
            manifest.get("version", manifest.get("schema")),
        )
        if version != FROZEN_VERSION:
            violations.append(f"{entrypoint}: contract version mismatch")
        if manifest.get("profile_id") != expected["profile_id"]:
            violations.append(f"{entrypoint}: profile_id mismatch")
        executable = manifest.get("executable")
        if executable != expected["executable"] or not Path(
            str(executable)
        ).is_absolute():
            violations.append(f"{entrypoint}: executable mismatch")
        expected_spawn_command = (
            expected["executable"],
            *tuple(expected["argv"])[1:],
        )
        manifest_argv = manifest.get("argv")
        if (
            not isinstance(manifest_argv, (tuple, list))
            or tuple(manifest_argv) != expected_spawn_command
        ):
            violations.append(f"{entrypoint}: executed argv mismatch")
        if manifest.get("cwd") != str(root.resolve()):
            violations.append(f"{entrypoint}: cwd mismatch")
        digest = manifest.get("profile_digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            violations.append(f"{entrypoint}: profile_digest invalid")
        if (
            not isinstance(confirmation_request, Mapping)
            or digest != confirmation_request.get("profile_digest")
        ):
            violations.append(
                f"{entrypoint}: profile_digest is not bound to Runtime challenge"
            )

        if not isinstance(spawn, Mapping):
            violations.append(f"{entrypoint}: spawn metadata absent")
        else:
            recorded_command = spawn.get("_recorded_command")
            if (
                not isinstance(recorded_command, (tuple, list))
                or tuple(recorded_command) != expected_spawn_command
            ):
                violations.append(
                    f"{entrypoint}: manifest is not bound to actual spawn argv"
                )
            recorded_cwd = spawn.get("cwd")
            try:
                resolved_cwd = Path(recorded_cwd).resolve()
            except (TypeError, OSError):
                resolved_cwd = None
            if resolved_cwd != root.resolve():
                violations.append(
                    f"{entrypoint}: manifest is not bound to actual spawn cwd"
                )

        environment = manifest.get("environment")
        sources: dict[str, object] = {}
        if isinstance(environment, Mapping):
            for name, source in environment.items():
                sources[str(name)] = (
                    source.get("value_source", _UNSET)
                    if isinstance(source, Mapping)
                    else source
                )
        elif isinstance(environment, (tuple, list)):
            for item in environment:
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    sources[item["name"]] = item.get("value_source", _UNSET)
        expected_names = {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR"}
        if expected["python"]:
            expected_names.update({
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONUNBUFFERED",
            })
        if set(sources) != expected_names:
            violations.append(
                f"{entrypoint}: environment name manifest mismatch"
            )
        spawn_environment = (
            spawn.get("env") if isinstance(spawn, Mapping) else None
        )
        if not isinstance(spawn_environment, Mapping) or set(
            spawn_environment
        ) != expected_names:
            violations.append(
                f"{entrypoint}: manifest environment is not bound to spawn env"
            )
        for name, source in sources.items():
            if source is _UNSET or source is None:
                violations.append(
                    f"{entrypoint}: {name} value_source is absent"
                )
                continue
            try:
                json.dumps(
                    source,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            except BaseException as exc:
                violations.append(
                    f"{entrypoint}: {name} value_source is not serializable="
                    f"{type(exc).__name__}"
                )
                continue
            actual_value = (
                spawn_environment.get(name)
                if isinstance(spawn_environment, Mapping)
                else _UNSET
            )
            if not isinstance(actual_value, str) or not actual_value:
                violations.append(
                    f"{entrypoint}: {name} spawn value absent"
                )
            elif FAKE_SECRET in actual_value:
                violations.append(
                    f"{entrypoint}: {name} retained a parent allowed-var sentinel"
                )
            elif name == "PATH" and actual_value != FROZEN_PATH:
                violations.append(
                    f"{entrypoint}: PATH source does not match frozen spawn value"
                )
            elif name in {"LANG", "LC_ALL"} and actual_value != "C.UTF-8":
                violations.append(
                    f"{entrypoint}: {name} source does not match fixed Profile value"
                )
            elif name in {
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONUNBUFFERED",
            } and actual_value != "1":
                violations.append(
                    f"{entrypoint}: {name} source does not match fixed Python value"
                )
            elif name in {"HOME", "TMPDIR"} and not Path(
                actual_value
            ).is_absolute():
                violations.append(
                    f"{entrypoint}: {name} runtime-owned path is not absolute"
                )

        limits = manifest.get("limits")
        if not isinstance(limits, Mapping):
            violations.append(f"{entrypoint}: limits mapping absent")
        else:
            exact_limits = {
                "wall_deadline_seconds": expected["deadline"],
                "term_grace_seconds": 1,
                "cleanup_barrier_seconds": 5,
            }
            if expected["output"] == "stdout_stderr":
                exact_limits.update({
                    "stdout_limit_chars": 10_000,
                    "stderr_limit_chars": 10_000,
                })
            else:
                exact_limits["server_log_limit_chars"] = 10_000
            for name, expected_value in exact_limits.items():
                if limits.get(name) != expected_value:
                    violations.append(
                        f"{entrypoint}: limit {name}={limits.get(name)!r}"
                    )
        input_summary = manifest.get("input_summary")
        if not isinstance(input_summary, Mapping) or not input_summary:
            violations.append(f"{entrypoint}: input_summary mapping absent")
        else:
            values: list[object] = []

            def collect(item: object) -> None:
                values.append(item)
                if isinstance(item, Mapping):
                    for child in item.values():
                        collect(child)
                elif isinstance(item, (tuple, list)):
                    for child in item:
                        collect(child)

            collect(input_summary)
            argv_present = any(
                isinstance(item, (tuple, list))
                and tuple(item) == tuple(expected["argv"])
                for item in values
            )
            cwd_present = str(root.resolve()) in values
            if not argv_present:
                violations.append(
                    f"{entrypoint}: input_summary does not bind full argv"
                )
            if not cwd_present:
                violations.append(
                    f"{entrypoint}: input_summary does not bind cwd"
                )

    @classmethod
    def _check_old_result_contract(
        cls,
        entrypoint: str,
        result: object,
        expected_command: tuple[str, ...],
        violations: list[str],
        *,
        expected_exit_code: int | None = 0,
        expected_stdout: str | None = None,
        expected_stderr: str = "",
        expected_timed_out: bool = False,
    ) -> None:
        if expected_stdout is None:
            expected_stdout = "ok" if entrypoint == "legacy" else ""
        command = getattr(result, "command", _UNSET)
        command_type = list if entrypoint == "legacy" else tuple
        if type(command) is not command_type:
            violations.append(
                f"{entrypoint}: command value/type changed"
            )
        elif tuple(command) != tuple(expected_command):
            violations.append(
                f"{entrypoint}: command value/type changed"
            )
        exit_code = getattr(result, "exit_code", _UNSET)
        if expected_exit_code is None:
            valid_exit_code = exit_code is None
        else:
            valid_exit_code = (
                type(exit_code) is int and exit_code == expected_exit_code
            )
        if not valid_exit_code:
            violations.append(f"{entrypoint}: success exit_code value/type changed")
        stdout = getattr(result, "stdout", _UNSET)
        stderr = getattr(result, "stderr", _UNSET)
        timed_out = getattr(result, "timed_out", _UNSET)
        if type(stdout) is not str or stdout != expected_stdout:
            violations.append(f"{entrypoint}: stdout value/type changed")
        if type(stderr) is not str or stderr != expected_stderr:
            violations.append(f"{entrypoint}: stderr value/type changed")
        if (
            type(timed_out) is not bool
            or timed_out is not expected_timed_out
        ):
            violations.append(f"{entrypoint}: timed_out value/type changed")
        if entrypoint != "legacy":
            duration = getattr(result, "duration_ms", _UNSET)
            if type(duration) is not int or duration < 0:
                violations.append(
                    f"{entrypoint}: duration_ms value/type changed"
                )

        method_name = (
            None if entrypoint == "legacy"
            else "evidence" if entrypoint == "core"
            else "to_dict"
        )
        if method_name is None:
            return
        serializer = getattr(result, method_name, None)
        try:
            serialized = serializer() if callable(serializer) else None
        except Exception:
            serialized = None
        if not isinstance(serialized, Mapping):
            violations.append(
                f"{entrypoint}: public {method_name} mapping absent"
            )
            return
        exact_fields = {
            "command": list(expected_command),
            "exit_code": expected_exit_code,
            "stdout": expected_stdout,
            "stderr": expected_stderr,
            "timed_out": expected_timed_out,
        }
        for field, expected_value in exact_fields.items():
            if serialized.get(field, _UNSET) != expected_value:
                violations.append(
                    f"{entrypoint}: {method_name} {field} changed"
                )
        if expected_exit_code is None:
            serialized_exit_type_valid = serialized.get("exit_code") is None
        else:
            serialized_exit_type_valid = type(serialized.get("exit_code")) is int
        if not serialized_exit_type_valid:
            violations.append(
                f"{entrypoint}: {method_name} exit_code type changed"
            )
        if type(serialized.get("stdout")) is not str:
            violations.append(
                f"{entrypoint}: {method_name} stdout type changed"
            )
        if type(serialized.get("stderr")) is not str:
            violations.append(
                f"{entrypoint}: {method_name} stderr type changed"
            )
        if type(serialized.get("timed_out")) is not bool:
            violations.append(
                f"{entrypoint}: {method_name} timed_out type changed"
            )

    @staticmethod
    def _security_text(value: object, error: BaseException | None) -> str:
        parts = []
        if error is not None:
            parts.extend((type(error).__name__, str(error), repr(error)))
            code = getattr(error, "code", None)
            if code is not None:
                parts.append(str(code))
        if value is not None:
            parts.append(repr(value))
            for method_name in ("to_dict", "evidence"):
                method = getattr(value, method_name, None)
                if callable(method):
                    try:
                        parts.append(repr(method()))
                    except Exception as exc:
                        parts.append(f"{method_name}:{type(exc).__name__}:{exc}")
            for name in (
                "code", "error_code", "status", "profile_id",
                "profile_digest", "contract_version", "cleanup",
            ):
                item = getattr(value, name, None)
                if item is not None:
                    parts.append(f"{name}={item!r}")
        return "\n".join(parts)

    @staticmethod
    def _browser_config(runner: str) -> dict[str, object]:
        return {
            "origin": "http://127.0.0.1:4173",
            "entry_route": "/",
            "viewport": {
                "width": 1440,
                "height": 900,
                "device_scale_factor": 1,
            },
            "commands": {
                "build": ["pnpm", "run", "build"],
                "dev": ["pnpm", "run", "dev", "--port", "4173"],
            },
            "browser_runner": runner,
        }

    @staticmethod
    def _check_bounded_output(
        name: str,
        value: object,
        error: BaseException | None,
        expected: str,
        chars: int,
        digest: str,
        violations: list[str],
    ) -> None:
        if error is not None or value is None:
            violations.append(
                f"{name}: no bounded result ({type(error).__name__ if error else 'None'})"
            )
            return
        stdout = getattr(value, "stdout", None)
        stderr = getattr(value, "stderr", None)
        if stdout != expected:
            violations.append(f"{name}: stdout head/marker/tail mismatch")
        if stderr != expected:
            violations.append(f"{name}: stderr head/marker/tail mismatch")
        for stream in ("stdout", "stderr"):
            if getattr(value, f"{stream}_chars", None) != chars:
                violations.append(f"{name}: {stream}_chars missing/mismatch")
            if getattr(value, f"{stream}_sha256", None) != digest:
                violations.append(f"{name}: {stream}_sha256 missing/mismatch")
            if getattr(value, f"{stream}_truncated", None) is not True:
                violations.append(f"{name}: {stream}_truncated missing/mismatch")

    @staticmethod
    def _check_untruncated_output(
        name: str,
        value: object,
        error: BaseException | None,
        expected: str,
        violations: list[str],
    ) -> None:
        if error is not None or value is None:
            violations.append(
                f"{name}: no untruncated result "
                f"({type(error).__name__ if error else 'None'})"
            )
            return
        digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
        for stream in ("stdout", "stderr"):
            if getattr(value, stream, None) != expected:
                violations.append(f"{name}: {stream} short output changed")
            if getattr(value, f"{stream}_chars", None) != len(expected):
                violations.append(f"{name}: {stream}_chars missing/mismatch")
            if getattr(value, f"{stream}_sha256", None) != digest:
                violations.append(f"{name}: {stream}_sha256 missing/mismatch")
            if getattr(value, f"{stream}_truncated", None) is not False:
                violations.append(f"{name}: {stream}_truncated missing/mismatch")

    @classmethod
    def _check_log_metadata(
        cls,
        managed: object,
        chars: int,
        digest: str,
        truncated: bool,
        violations: list[str],
    ) -> None:
        candidates = []
        for mapping in cls._structured_mappings(managed, None):
            nested = mapping.get("server_log")
            if isinstance(nested, Mapping):
                candidates.append(nested)
            if any(
                key in mapping
                for key in (
                    "server_log_chars",
                    "server_log_sha256",
                    "server_log_truncated",
                )
            ):
                candidates.append({
                    "chars": mapping.get("server_log_chars"),
                    "sha256": mapping.get("server_log_sha256"),
                    "truncated": mapping.get("server_log_truncated"),
                })
        matched = any(
            candidate.get("chars") == chars
            and candidate.get("sha256") == digest
            and candidate.get("truncated") is truncated
            for candidate in candidates
        )
        if not matched:
            violations.append("server_log: structured length/hash metadata absent")

    def _tripwire(self, label: str):
        def reject(*_args, **_kwargs):
            self._tripwire_hits.append(label)
            raise AssertionError(f"blocked unexpected real {label}")

        return reject

    def _registered_thread(self, *, target, args, name: str):
        thread = threading.Thread(
            target=target,
            args=args,
            name=name,
            daemon=True,
        )
        self._explicit_test_threads.add(thread)
        self._worker_threads.append(thread)
        return thread

    @staticmethod
    @contextlib.contextmanager
    def _patch_cached_alias(owner: object, name: str, replacement: object):
        # ``getattr`` unwraps class descriptors (notably staticmethod), which
        # would make the restoration ledger lossy.  Preserve the exact slot.
        if isinstance(owner, type) and name in vars(owner):
            original = vars(owner)[name]
        else:
            original = getattr(owner, name)
        setattr(owner, name, replacement)
        try:
            yield
        finally:
            setattr(owner, name, original)

    @staticmethod
    def _loaded_production_aliases(replacements):
        """Return reversible patches for cached production boundary aliases."""
        aliases: list[tuple[object, str, object]] = []
        scanned: set[int] = set()

        def transformed(value: object, active: set[int] | None = None):
            for original, replacement in replacements.items():
                if value is original:
                    return replacement, True
            active = set() if active is None else active
            identity = id(value)
            if identity in active:
                return value, False
            active.add(identity)
            try:
                if isinstance(value, functools.partial):
                    function, function_changed = transformed(value.func, active)
                    args: list[object] = []
                    changed = function_changed
                    for item in value.args:
                        updated, item_changed = transformed(item, active)
                        args.append(updated)
                        changed = changed or item_changed
                    keywords: dict[str, object] = {}
                    for name, item in (value.keywords or {}).items():
                        updated, item_changed = transformed(item, active)
                        keywords[name] = updated
                        changed = changed or item_changed
                    if changed:
                        result = functools.partial(function, *args, **keywords)
                        result.__dict__.update(getattr(value, "__dict__", {}))
                        return result, True
                elif isinstance(value, tuple):
                    items = [transformed(item, active) for item in value]
                    if any(changed for _, changed in items):
                        return tuple(item for item, _ in items), True
                elif isinstance(value, list):
                    items = [transformed(item, active) for item in value]
                    if any(changed for _, changed in items):
                        return [item for item, _ in items], True
                elif isinstance(value, dict):
                    items = [
                        (transformed(key, active), transformed(item, active))
                        for key, item in value.items()
                    ]
                    if any(
                        key_changed or value_changed
                        for (_, key_changed), (_, value_changed) in items
                    ):
                        return {
                            key: item
                            for (key, _), (item, _) in items
                        }, True
                elif isinstance(value, set):
                    items = [transformed(item, active) for item in value]
                    if any(changed for _, changed in items):
                        return {item for item, _ in items}, True
                elif isinstance(value, frozenset):
                    items = [transformed(item, active) for item in value]
                    if any(changed for _, changed in items):
                        return frozenset(item for item, _ in items), True
                elif isinstance(value, staticmethod):
                    function, changed = transformed(value.__func__, active)
                    if changed:
                        return staticmethod(function), True
                elif isinstance(value, classmethod):
                    function, changed = transformed(value.__func__, active)
                    if changed:
                        return classmethod(function), True
            finally:
                active.discard(identity)
            return value, False

        def inspect_cached(value: object) -> None:
            identity = id(value)
            if identity in scanned:
                return
            scanned.add(identity)
            module_name = getattr(value, "__module__", "")
            if isinstance(value, type) and (
                module_name == "coding_workflow"
                or module_name.startswith("coding_workflow.")
            ):
                for name, item in tuple(vars(value).items()):
                    updated, changed = transformed(item)
                    if changed:
                        aliases.append((value, name, updated))
                    inspect_cached(item)
                return
            if isinstance(value, types.FunctionType) and (
                module_name == "coding_workflow"
                or module_name.startswith("coding_workflow.")
            ):
                defaults = value.__defaults__
                if defaults is not None:
                    updated, changed = transformed(defaults)
                    if changed:
                        aliases.append((value, "__defaults__", updated))
                    for item in defaults:
                        inspect_cached(item)
                kwdefaults = value.__kwdefaults__
                if kwdefaults is not None:
                    updated, changed = transformed(kwdefaults)
                    if changed:
                        aliases.append((value, "__kwdefaults__", updated))
                    for item in kwdefaults.values():
                        inspect_cached(item)
                for cell in value.__closure__ or ():
                    try:
                        item = cell.cell_contents
                    except ValueError:
                        continue
                    updated, changed = transformed(item)
                    if changed:
                        aliases.append((cell, "cell_contents", updated))
                    inspect_cached(item)
                for item in vars(value).values():
                    inspect_cached(item)
                return
            if isinstance(value, functools.partial):
                inspect_cached(value.func)
                for item in value.args:
                    inspect_cached(item)
                for item in (value.keywords or {}).values():
                    inspect_cached(item)
                return
            if isinstance(value, Mapping):
                for key, item in value.items():
                    inspect_cached(key)
                    inspect_cached(item)
                return
            if isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    inspect_cached(item)

        for module_name, module in tuple(sys.modules.items()):
            if module is None or not (
                module_name == "coding_workflow"
                or module_name.startswith("coding_workflow.")
            ):
                continue
            for name, value in tuple(vars(module).items()):
                updated, changed = transformed(value)
                if changed:
                    aliases.append((module, name, updated))
                inspect_cached(value)
        return aliases

    def _restore_late_boundary_aliases(self) -> None:
        """Keep aliases imported during a test on suite-level fail-closed traps."""
        replacements = [
            (self._trap_popen, _IMPORT_TRAP_POPEN),
            (self._trap_run, _IMPORT_TRAP_RUN),
            (self._trap_socket, _IMPORT_TRAP_SOCKET),
            (self._trap_create_connection, _IMPORT_TRAP_CREATE_CONNECTION),
            (self._trap_getaddrinfo, _IMPORT_TRAP_GETADDRINFO),
            (self._trap_socketpair, _IMPORT_TRAP_SOCKETPAIR),
            (self._tracked_async_socketpair, _IMPORT_TRAP_SOCKETPAIR),
            (self._trap_fromfd, _IMPORT_TRAP_FROMFD),
            (self._trap_urlopen, _IMPORT_TRAP_URLOPEN),
            (self._trap_opener_open, _IMPORT_TRAP_OPENER_OPEN),
            (self._tracked_thread_start, _IMPORT_TRAP_THREAD_START),
            (self._tracked_low_thread_start, _IMPORT_TRAP_LOW_THREAD_START),
            (self._trap_low_thread_start, _IMPORT_TRAP_LOW_THREAD_START),
            (self._trap_async_task, _IMPORT_TRAP_ASYNCIO_CREATE_TASK),
            (self._trap_async_schedule, _IMPORT_TRAP_LOOP_CREATE_TASK),
            (
                self._tracked_asyncio_create_task,
                _IMPORT_TRAP_ASYNCIO_CREATE_TASK,
            ),
            (
                self._tracked_tasks_create_task,
                _IMPORT_TRAP_TASKS_CREATE_TASK,
            ),
            (
                self._tracked_asyncio_ensure_future,
                _IMPORT_TRAP_ASYNCIO_ENSURE_FUTURE,
            ),
            (
                self._tracked_tasks_ensure_future,
                _IMPORT_TRAP_TASKS_ENSURE_FUTURE,
            ),
            (
                self._tracked_loop_create_task,
                _IMPORT_TRAP_LOOP_CREATE_TASK,
            ),
            (
                self._tracked_task_constructor,
                _IMPORT_TRAP_TASK_CONSTRUCTOR,
            ),
        ]
        replacements.extend(
            (
                self._tracked_loop_scheduling[name],
                _IMPORT_TRAP_LOOP_SCHEDULING[name],
            )
            for name in self._tracked_loop_scheduling
        )
        if self._real_create_server is not None:
            replacements.append((
                self._trap_create_server,
                _IMPORT_TRAP_CREATE_SERVER,
            ))
        replacements.extend(
            (
                self._trap_extra_boundaries[key],
                _IMPORT_EXTRA_TRAPS[key],
            )
            for key in self._trap_extra_boundaries
        )
        for owner, name, suite_trap in self._loaded_production_aliases(
            dict(replacements)
        ):
            setattr(owner, name, suite_trap)

    @staticmethod
    def _patched_monotonic(value: float):
        """Advance Runtime clocks deterministically without sleeping."""
        real_monotonic = time.monotonic

        def frozen_monotonic() -> float:
            return value

        aliases = (
            LocalTrustedExecutionBehaviorExpectedRedTests
            ._loaded_production_aliases({real_monotonic: frozen_monotonic})
        )

        class MonotonicContext:
            def __enter__(self):
                self._stack = contextlib.ExitStack()
                self._stack.enter_context(
                    mock.patch.object(time, "monotonic", new=frozen_monotonic)
                )
                for module, name, replacement in aliases:
                    self._stack.enter_context(
                        LocalTrustedExecutionBehaviorExpectedRedTests
                        ._patch_cached_alias(module, name, replacement)
                    )
                return self

            def __exit__(self, exc_type, exc, traceback):
                for module_name, module in tuple(sys.modules.items()):
                    if module is None or not (
                        module_name == "coding_workflow"
                        or module_name.startswith("coding_workflow.")
                    ):
                        continue
                    for name, candidate in tuple(vars(module).items()):
                        if candidate is frozen_monotonic:
                            setattr(module, name, real_monotonic)
                self._stack.close()
                return False

        return MonotonicContext()

    def _patched_processes(self, popen, run, *, kill=None, killpg=None):
        testcase = self
        safe_kill = kill or FakeSignalFactory()
        safe_killpg = killpg or FakeSignalFactory()
        popen_values = {
            self._real_popen: popen,
            self._trap_popen: popen,
            _IMPORT_TRAP_POPEN: popen,
        }
        run_values = {
            self._real_run: run,
            self._trap_run: run,
            _IMPORT_TRAP_RUN: run,
        }
        kill_key = (os, "kill")
        killpg_key = (os, "killpg")
        kill_values = {
            self._real_extra_boundaries[kill_key]: safe_kill,
            self._trap_extra_boundaries[kill_key]: safe_kill,
            _IMPORT_EXTRA_TRAPS[kill_key]: safe_kill,
        }
        killpg_values = {
            self._real_extra_boundaries[killpg_key]: safe_killpg,
            self._trap_extra_boundaries[killpg_key]: safe_killpg,
            _IMPORT_EXTRA_TRAPS[killpg_key]: safe_killpg,
        }

        class PatchContext:
            def __enter__(self):
                self._stack = contextlib.ExitStack()
                replacements = dict(popen_values)
                replacements.update(run_values)
                replacements.update(kill_values)
                replacements.update(killpg_values)
                aliases = (
                    LocalTrustedExecutionBehaviorExpectedRedTests
                    ._loaded_production_aliases(replacements)
                )
                self._stack.enter_context(
                    mock.patch.object(subprocess, "Popen", new=popen)
                )
                self._stack.enter_context(
                    mock.patch.object(subprocess, "run", new=run)
                )
                self._stack.enter_context(
                    mock.patch.object(os, "kill", new=safe_kill)
                )
                self._stack.enter_context(
                    mock.patch.object(os, "killpg", new=safe_killpg)
                )
                for module, name, replacement in aliases:
                    self._stack.enter_context(
                        testcase._patch_cached_alias(
                            module,
                            name,
                            replacement,
                        )
                    )
                return self

            def __exit__(self, exc_type, exc, traceback):
                # A production module lazily imported while the overlay is
                # active may bind the fake directly via ``from subprocess``.
                # Restore such late aliases to the fail-closed tripwire.
                late_replacements = {
                    popen: testcase._trap_popen,
                    run: testcase._trap_run,
                    safe_kill: testcase._trap_extra_boundaries[kill_key],
                    safe_killpg: testcase._trap_extra_boundaries[killpg_key],
                }
                for owner, name, replacement in (
                    LocalTrustedExecutionBehaviorExpectedRedTests
                    ._loaded_production_aliases(late_replacements)
                ):
                    setattr(owner, name, replacement)
                owned_streams: list[object] = []
                calls = getattr(popen, "calls", ())
                if isinstance(calls, (tuple, list)):
                    for call in calls:
                        if (
                            isinstance(call, tuple)
                            and len(call) == 2
                            and isinstance(call[1], Mapping)
                        ):
                            owned_streams.extend(
                                call[1].get(name)
                                for name in ("stdin", "stdout", "stderr")
                            )
                processes = getattr(popen, "processes", ())
                if isinstance(processes, (tuple, list)):
                    for process in processes:
                        owned_streams.extend(
                            getattr(process, name, None)
                            for name in ("stdin", "stdout", "stderr")
                        )
                closed: set[int] = set()
                for stream in owned_streams:
                    if not isinstance(stream, io.IOBase) or id(stream) in closed:
                        continue
                    closed.add(id(stream))
                    try:
                        stream.close()
                    except Exception:
                        pass
                self._stack.close()
                return False

            @property
            def kill_calls(self):
                return safe_kill.calls

            @property
            def killpg_calls(self):
                return safe_killpg.calls

        return PatchContext()

    @staticmethod
    def _production_python_paths() -> tuple[Path, ...]:
        """Every current/future demo Python source outside test fixtures."""
        paths = []
        for path in ROOT.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if "tests" in relative.parts or path.name.startswith("test_"):
                continue
            paths.append(path)
        return tuple(sorted(paths))

    @staticmethod
    def _unique_popen_callee_binding(
        popen_call: tuple[str, str, int, str],
    ) -> tuple[str, int, int, int, int] | None:
        """Return the exact unique binding used by the sole Popen call."""
        relative, api, call_line, _ = popen_call
        if api != "subprocess.Popen":
            return None
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def source_span(node: ast.AST) -> tuple[int, int, int, int]:
            line = getattr(node, "lineno", 0)
            column = getattr(node, "col_offset", 0)
            return (
                line,
                column,
                getattr(node, "end_lineno", line) or line,
                getattr(node, "end_col_offset", column) or column,
            )

        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        def lexical_scope(node: ast.AST):
            line = getattr(node, "lineno", 0)
            candidates = [
                function for function in functions
                if function.lineno <= line <= getattr(
                    function, "end_lineno", function.lineno
                )
            ]
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda function: getattr(
                    function, "end_lineno", function.lineno
                ) - function.lineno,
            )

        subprocess_modules = {"subprocess"}
        provenance: list[
            tuple[
                str,
                object,
                tuple[int, int, int, int],
                tuple[int, int, int, int],
            ]
        ] = []
        definitions: list[
            tuple[str, object, tuple[int, int, int, int]]
        ] = []
        definition_keys: set[tuple[str, int, tuple[int, int, int, int]]] = set()

        def add_definition(name: str, scope, node: ast.AST) -> None:
            span = source_span(node)
            key = (name, id(scope), span)
            if key not in definition_keys:
                definition_keys.add(key)
                definitions.append((name, scope, span))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    add_definition(
                        item.asname or item.name.split(".")[0],
                        lexical_scope(node),
                        node,
                    )
                    if item.name == "subprocess":
                        subprocess_modules.add(item.asname or item.name)
            elif isinstance(node, ast.ImportFrom):
                for item in node.names:
                    bound = item.asname or item.name
                    scope = lexical_scope(node)
                    add_definition(bound, scope, node)
                    if (
                        node.module == "subprocess"
                        and item.name == "Popen"
                        and len(node.names) == 1
                    ):
                        span = source_span(node)
                        provenance.append((bound, scope, span, span))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in subprocess_modules
                and value.attr == "Popen"
            ):
                continue
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
            ):
                scope = lexical_scope(node)
                provenance.append((
                    targets[0].id,
                    scope,
                    source_span(value),
                    source_span(targets[0]),
                ))

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                add_definition(node.id, lexical_scope(node), node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument in (
                    list(node.args.posonlyargs)
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                ):
                    add_definition(argument.arg, node, argument)
                if node.args.vararg is not None:
                    add_definition(node.args.vararg.arg, node, node.args.vararg)
                if node.args.kwarg is not None:
                    add_definition(node.args.kwarg.arg, node, node.args.kwarg)

        candidate_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and node.lineno == call_line
            and isinstance(node.func, ast.Name)
            and any(item[0] == node.func.id for item in provenance)
        ]
        if len(candidate_calls) != 1:
            return None
        call = candidate_calls[0]
        callee = call.func.id
        call_scope = lexical_scope(call)
        local_definitions = [
            item for item in definitions
            if item[0] == callee and item[1] is call_scope
        ]
        same_scope = [
            item for item in provenance
            if item[0] == callee and item[1] is call_scope
        ]
        if same_scope:
            candidates = same_scope
        else:
            if call_scope is not None and local_definitions:
                return None
            enclosing_shadow = any(
                item[0] == callee
                and item[1] is not None
                and item[1] is not call_scope
                and item[1].lineno <= call.lineno <= getattr(
                    item[1], "end_lineno", item[1].lineno
                )
                for item in definitions
            )
            if enclosing_shadow:
                return None
            candidates = [
                item for item in provenance
                if item[0] == callee and item[1] is None
            ]
        candidates = [
            item for item in candidates
            if item[2][:2] < source_span(call)[:2]
        ]
        if len(candidates) != 1:
            return None
        binding = candidates[0]
        binding_definitions = [
            item for item in definitions
            if item[0] == callee and item[1] is binding[1]
        ]
        if (
            len(binding_definitions) != 1
            or binding_definitions[0][2] != binding[3]
        ):
            return None
        return (relative, *binding[2])

    @staticmethod
    def _process_boundary_calls(
        path: Path,
    ) -> list[tuple[str, str, int, str]]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        known = {
            "subprocess": {
                "Popen", "run", "call", "check_call", "check_output",
                "getoutput", "getstatusoutput",
            },
            "os": {
                "posix_spawn", "posix_spawnp", "system", "popen", "fork",
                "forkpty", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl",
                "execle", "execlp", "execlpe", "execv", "execve",
                "execvp", "execvpe",
            },
            "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
            "posix": {
                "posix_spawn", "posix_spawnp", "system", "fork", "forkpty",
                "execv", "execve",
            },
            "_posixsubprocess": {"fork_exec"},
            "_socket": {"socket", "getaddrinfo", "socketpair"},
            "ctypes": {"CDLL", "PyDLL", "WinDLL", "OleDLL"},
            "cffi": {"FFI"},
        }
        process_api_names = set().union(*known.values())
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        classes = [
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]
        module_aliases: dict[str, str] = {}
        importlib_aliases: set[str] = set()
        import_module_functions: set[str] = set()
        star_modules: set[str] = set()
        function_aliases: dict[str, str] = {}
        class_api_aliases: dict[tuple[str, str], str] = {}
        container_api_aliases: dict[tuple[str, object], str] = {}
        default_api_aliases: dict[tuple[int, str], str] = {}
        default_string_aliases: dict[tuple[int, str], str] = {}
        default_importer_aliases: set[tuple[int, str]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name in known:
                        module_aliases[item.asname or item.name] = item.name
                    elif item.name == "importlib":
                        importlib_aliases.add(item.asname or item.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module in known:
                    for item in node.names:
                        if item.name == "*":
                            star_modules.add(node.module)
                        elif item.name in known[node.module]:
                            function_aliases[item.asname or item.name] = (
                                f"{node.module}.{item.name}"
                            )
                elif node.module == "importlib":
                    for item in node.names:
                        if item.name == "import_module":
                            import_module_functions.add(
                                item.asname or item.name
                            )

        def containing_function(lineno: int | None):
            if lineno is None:
                return None
            candidates = [
                node for node in functions
                if node.lineno <= lineno <= getattr(
                    node, "end_lineno", node.lineno
                )
            ]
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda node: getattr(node, "end_lineno", node.lineno)
                - node.lineno,
            )

        def containing_class(lineno: int | None):
            if lineno is None:
                return None
            candidates = [
                node for node in classes
                if node.lineno <= lineno <= getattr(
                    node, "end_lineno", node.lineno
                )
            ]
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda node: getattr(node, "end_lineno", node.lineno)
                - node.lineno,
            )

        def is_import_module_ref(expression: ast.AST) -> bool:
            return (
                isinstance(expression, ast.Name)
                and expression.id in import_module_functions
                or isinstance(expression, ast.Attribute)
                and expression.attr == "import_module"
                and isinstance(expression.value, ast.Name)
                and expression.value.id in importlib_aliases
            )

        module_string_values: dict[str, list[ast.AST]] = {}
        for assignment in ast.walk(tree):
            if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                continue
            if containing_function(getattr(assignment, "lineno", None)) is not None:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                module_string_values.setdefault(targets[0].id, []).append(
                    assignment.value
                )
        string_constants: dict[str, str] = {}
        changed_constants = True
        while changed_constants:
            changed_constants = False
            for name, values in module_string_values.items():
                if len(values) != 1 or name in string_constants:
                    continue
                value = values[0]
                resolved = (
                    value.value
                    if isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    else string_constants.get(value.id)
                    if isinstance(value, ast.Name)
                    else None
                )
                if isinstance(resolved, str):
                    string_constants[name] = resolved
                    changed_constants = True

        def global_string(expression: ast.AST) -> str | None:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                return expression.value
            if isinstance(expression, ast.Name):
                return string_constants.get(expression.id)
            return None

        for function in functions:
            positional = list(function.args.posonlyargs) + list(function.args.args)
            defaults = list(zip(
                positional[-len(function.args.defaults):],
                function.args.defaults,
            )) if function.args.defaults else []
            defaults.extend(
                (parameter, default)
                for parameter, default in zip(
                    function.args.kwonlyargs,
                    function.args.kw_defaults,
                )
                if default is not None
            )
            for parameter, default in defaults:
                text = global_string(default)
                if text is not None:
                    default_string_aliases[(id(function), parameter.arg)] = text
                if (
                    isinstance(default, ast.Name)
                    and default.id == "__import__"
                    or is_import_module_ref(default)
                ):
                    default_importer_aliases.add((id(function), parameter.arg))

        function_bound_names: dict[int, set[str]] = {}
        function_string_constants: dict[int, dict[str, str]] = {}
        for function in functions:
            bound = {
                parameter.arg
                for parameter in (
                    list(function.args.posonlyargs)
                    + list(function.args.args)
                    + list(function.args.kwonlyargs)
                )
            }
            if function.args.vararg is not None:
                bound.add(function.args.vararg.arg)
            if function.args.kwarg is not None:
                bound.add(function.args.kwarg.arg)
            local_values: dict[str, list[ast.AST]] = {}
            for candidate in ast.walk(function):
                if containing_function(
                    getattr(candidate, "lineno", None)
                ) is not function:
                    continue
                if isinstance(candidate, ast.Name) and isinstance(
                    candidate.ctx, ast.Store
                ):
                    bound.add(candidate.id)
                elif isinstance(candidate, (ast.Import, ast.ImportFrom)):
                    bound.update(
                        item.asname or item.name.split(".")[0]
                        for item in candidate.names
                    )
                if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    candidate.targets
                    if isinstance(candidate, ast.Assign)
                    else [candidate.target]
                )
                if (
                    len(targets) == 1
                    and isinstance(targets[0], ast.Name)
                    and candidate.value is not None
                ):
                    local_values.setdefault(targets[0].id, []).append(
                        candidate.value
                    )
            local_constants: dict[str, str] = {}
            local_changed = True
            while local_changed:
                local_changed = False
                for name, values in local_values.items():
                    if len(values) != 1 or name in local_constants:
                        continue
                    value = values[0]
                    resolved = (
                        value.value
                        if isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        else local_constants.get(value.id)
                        if isinstance(value, ast.Name)
                        and value.id in bound
                        else string_constants.get(value.id)
                        if isinstance(value, ast.Name)
                        else None
                    )
                    if isinstance(resolved, str):
                        local_constants[name] = resolved
                        local_changed = True
            function_bound_names[id(function)] = bound
            function_string_constants[id(function)] = local_constants

        def resolve_string(
            expression: ast.AST,
            lineno: int | None,
        ) -> str | None:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                return expression.value
            if isinstance(expression, ast.Name):
                owner = containing_function(lineno)
                if owner is not None:
                    default = default_string_aliases.get((id(owner), expression.id))
                    if default is not None:
                        return default
                    local = function_string_constants.get(id(owner), {}).get(
                        expression.id
                    )
                    if local is not None:
                        return local
                    if expression.id in function_bound_names.get(id(owner), set()):
                        return None
                return string_constants.get(expression.id)
            return None

        def is_importer_ref(
            expression: ast.AST,
            lineno: int | None,
        ) -> bool:
            if (
                isinstance(expression, ast.Name)
                and expression.id == "__import__"
                or is_import_module_ref(expression)
            ):
                return True
            if isinstance(expression, ast.Name):
                owner = containing_function(lineno)
                return owner is not None and (
                    id(owner), expression.id
                ) in default_importer_aliases
            return False

        def import_call_fact(
            expression: ast.AST,
            lineno: int | None,
        ) -> tuple[str | None, str | None]:
            if not (
                isinstance(expression, ast.Call)
                and is_importer_ref(expression.func, lineno)
                and expression.args
            ):
                return None, None
            module = resolve_string(expression.args[0], lineno)
            if module is None:
                return "dynamic", None
            return ("known" if module in known else "benign"), module

        def resolved_module(
            expression: ast.AST,
            lineno: int | None = None,
        ) -> str | None:
            if isinstance(expression, ast.Name):
                return module_aliases.get(expression.id)
            kind, module = import_call_fact(expression, lineno)
            if kind in {"known", "benign"}:
                return module
            return None

        def subscript_key(expression: ast.Subscript) -> object:
            key = expression.slice
            if isinstance(key, ast.Index):
                key = key.value
            if isinstance(key, ast.Constant):
                return key.value
            return _UNSET

        def resolved_api(
            expression: ast.AST,
            lineno: int | None = None,
        ) -> str | None:
            if isinstance(expression, ast.Name):
                owner = containing_function(lineno)
                if owner is not None:
                    default = default_api_aliases.get((id(owner), expression.id))
                    if default is not None:
                        return default
                direct = function_aliases.get(expression.id)
                if direct is not None:
                    return direct
                star_candidates = [
                    f"{module_name}.{expression.id}"
                    for module_name in star_modules
                    if expression.id in known[module_name]
                ]
                if len(star_candidates) == 1:
                    return star_candidates[0]
                return None
            if isinstance(expression, ast.Attribute):
                module_name = resolved_module(expression.value, lineno)
                if (
                    module_name in known
                    and expression.attr in known[module_name]
                ):
                    return f"{module_name}.{expression.attr}"
                if isinstance(expression.value, ast.Name):
                    direct = class_api_aliases.get((
                        expression.value.id,
                        expression.attr,
                    ))
                    if direct is not None:
                        return direct
                    if expression.value.id in {"self", "cls"}:
                        owner_class = containing_class(lineno)
                        if owner_class is not None:
                            bound = class_api_aliases.get((
                                owner_class.name,
                                expression.attr,
                            ))
                            if bound is not None:
                                return bound
                if expression.attr in {
                    "subprocess_exec",
                    "subprocess_shell",
                    "_make_subprocess_transport",
                }:
                    return f"asyncio.{expression.attr}"
                import_kind, _ = import_call_fact(expression.value, lineno)
                if (
                    import_kind == "dynamic"
                    and expression.attr in process_api_names
                ):
                    return f"<dynamic-import>.{expression.attr}"
            if isinstance(expression, ast.Subscript):
                if isinstance(expression.value, ast.Name):
                    return container_api_aliases.get((
                        expression.value.id,
                        subscript_key(expression),
                    ))
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id == "getattr"
                and len(expression.args) >= 2
            ):
                module_name = resolved_module(expression.args[0], lineno)
                attribute_name = resolve_string(expression.args[1], lineno)
                if module_name in known:
                    if attribute_name in known[module_name]:
                        return f"{module_name}.{attribute_name}"
                    if attribute_name is None:
                        return f"{module_name}.<dynamic-getattr>"
                import_kind, _ = import_call_fact(expression.args[0], lineno)
                if (
                    import_kind == "dynamic"
                    and attribute_name in process_api_names
                ):
                    return f"<dynamic-import>.{attribute_name}"
            return None

        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                module_name = resolved_module(value)
                api_name = resolved_api(
                    value,
                    getattr(assignment, "lineno", None),
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if (
                        is_import_module_ref(value)
                        and target.id not in import_module_functions
                    ):
                        import_module_functions.add(target.id)
                        changed = True
                    if (
                        module_name is not None
                        and module_aliases.get(target.id) != module_name
                    ):
                        module_aliases[target.id] = module_name
                        changed = True
                    if (
                        api_name is not None
                        and function_aliases.get(target.id) != api_name
                    ):
                        function_aliases[target.id] = api_name
                        changed = True

                    if isinstance(target, ast.Name) and isinstance(
                        value, (ast.Dict, ast.List, ast.Tuple)
                    ):
                        if isinstance(value, ast.Dict):
                            pairs = zip(value.keys, value.values)
                        else:
                            pairs = enumerate(value.elts)
                        for key_node, item in pairs:
                            key = (
                                key_node.value
                                if isinstance(key_node, ast.Constant)
                                else key_node
                            )
                            item_api = resolved_api(
                                item,
                                getattr(assignment, "lineno", None),
                            )
                            if item_api is not None:
                                container_api_aliases[(target.id, key)] = item_api

        for class_node in classes:
            for statement in class_node.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    continue
                value = statement.value
                if value is None:
                    continue
                api_name = resolved_api(value, statement.lineno)
                if api_name is None:
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        class_api_aliases[(class_node.name, target.id)] = api_name

        for function in functions:
            positional = list(function.args.posonlyargs) + list(function.args.args)
            default_parameters = positional[-len(function.args.defaults):]
            for parameter, default in zip(
                default_parameters,
                function.args.defaults,
            ):
                api_name = resolved_api(default, function.lineno)
                if api_name is not None:
                    default_api_aliases[(id(function), parameter.arg)] = api_name
            for parameter, default in zip(
                function.args.kwonlyargs,
                function.args.kw_defaults,
            ):
                if default is None:
                    continue
                api_name = resolved_api(default, function.lineno)
                if api_name is not None:
                    default_api_aliases[(id(function), parameter.arg)] = api_name

        def owner_function(lineno: int) -> str:
            candidates = [
                node for node in functions
                if node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno)
            ]
            if not candidates:
                return "<module>"
            owner = min(
                candidates,
                key=lambda node: getattr(node, "end_lineno", node.lineno)
                - node.lineno,
            )
            return owner.name

        calls: list[tuple[str, str, int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            api_name = resolved_api(node.func, node.lineno)
            if api_name is not None:
                try:
                    relative = str(path.resolve().relative_to(ROOT.resolve()))
                except ValueError:
                    relative = str(path)
                calls.append((
                    relative,
                    api_name,
                    node.lineno,
                    owner_function(node.lineno),
                ))
        return sorted(calls, key=lambda item: (item[0], item[2], item[1]))

    @staticmethod
    def _restricted_boundary_imports(
        path: Path,
    ) -> list[tuple[str, str, int, str, int, int, int]]:
        """Find dormant process API imports/exports, not benign module imports."""
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        known = {
            "subprocess": {
                "Popen", "run", "call", "check_call", "check_output",
                "getoutput", "getstatusoutput",
            },
            "os": {
                "posix_spawn", "posix_spawnp", "system", "popen", "fork",
                "forkpty", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl",
                "execle", "execlp", "execlpe", "execv", "execve",
                "execvp", "execvpe",
            },
            "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
            "posix": {
                "posix_spawn", "posix_spawnp", "system", "fork", "forkpty",
                "execv", "execve",
            },
            "_posixsubprocess": {"fork_exec"},
            "ctypes": {"CDLL", "PyDLL", "WinDLL", "OleDLL"},
            "cffi": {"FFI"},
        }
        process_api_names = set().union(*known.values())
        module_aliases: dict[str, str] = {}
        importlib_aliases: set[str] = set()
        import_module_functions: set[str] = set()
        api_aliases: dict[str, str] = {}
        string_constants: dict[str, str] = {}
        default_string_aliases: dict[tuple[int, str], str] = {}
        default_importer_aliases: set[tuple[int, str]] = set()
        functions = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        def owner(lineno: int) -> str:
            candidates = [
                node for node in functions
                if node.lineno <= lineno <= getattr(
                    node, "end_lineno", node.lineno
                )
            ]
            if not candidates:
                return "<module>"
            return min(
                candidates,
                key=lambda node: getattr(node, "end_lineno", node.lineno)
                - node.lineno,
            ).name

        def containing_function(lineno: int | None):
            if lineno is None:
                return None
            candidates = [
                node for node in functions
                if node.lineno <= lineno <= getattr(
                    node, "end_lineno", node.lineno
                )
            ]
            if not candidates:
                return None
            return min(
                candidates,
                key=lambda node: getattr(node, "end_lineno", node.lineno)
                - node.lineno,
            )

        try:
            relative = str(path.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            relative = str(path)
        findings: list[tuple[str, str, int, str, int, int, int]] = []
        finding_keys: set[tuple[str, int, int, int, int, str]] = set()

        def add(api: str, node: ast.AST) -> None:
            lineno = getattr(node, "lineno", 0)
            column = getattr(node, "col_offset", 0)
            end_lineno = getattr(node, "end_lineno", lineno)
            end_column = getattr(node, "end_col_offset", column)
            key = (
                api,
                lineno,
                column,
                end_lineno,
                end_column,
                owner(lineno),
            )
            if key in finding_keys:
                return
            finding_keys.add(key)
            findings.append((
                relative,
                f"{api}.<process-reference>",
                lineno,
                key[5],
                column,
                end_lineno,
                end_column,
            ))

        def is_import_module_ref(expression: ast.AST) -> bool:
            return (
                isinstance(expression, ast.Name)
                and expression.id in import_module_functions
                or isinstance(expression, ast.Attribute)
                and expression.attr == "import_module"
                and isinstance(expression.value, ast.Name)
                and expression.value.id in importlib_aliases
            )

        def resolve_string(
            expression: ast.AST,
            lineno: int | None,
        ) -> str | None:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                return expression.value
            if isinstance(expression, ast.Name):
                function = containing_function(lineno)
                if function is not None:
                    default = default_string_aliases.get((
                        id(function), expression.id,
                    ))
                    if default is not None:
                        return default
                    local = function_string_constants.get(
                        id(function), {}
                    ).get(expression.id)
                    if local is not None:
                        return local
                    if expression.id in function_bound_names.get(
                        id(function), set()
                    ):
                        return None
                return string_constants.get(expression.id)
            return None

        def is_importer_ref(
            expression: ast.AST,
            lineno: int | None,
        ) -> bool:
            if (
                isinstance(expression, ast.Name)
                and expression.id == "__import__"
                or is_import_module_ref(expression)
            ):
                return True
            if isinstance(expression, ast.Name):
                function = containing_function(lineno)
                return function is not None and (
                    id(function), expression.id
                ) in default_importer_aliases
            return False

        def import_call_fact(
            expression: ast.AST,
            lineno: int | None,
        ) -> tuple[str | None, str | None]:
            if not (
                isinstance(expression, ast.Call)
                and is_importer_ref(expression.func, lineno)
                and expression.args
            ):
                return None, None
            module = resolve_string(expression.args[0], lineno)
            if module is None:
                return "dynamic", None
            return ("known" if module in known else "benign"), module

        def module_name(
            expression: ast.AST,
            lineno: int | None = None,
        ) -> str | None:
            if isinstance(expression, ast.Name):
                return module_aliases.get(expression.id)
            kind, module = import_call_fact(expression, lineno)
            if kind in {"known", "benign"}:
                return module
            return None

        def api_name(
            expression: ast.AST,
            lineno: int | None = None,
        ) -> str | None:
            if isinstance(expression, ast.Name):
                return api_aliases.get(expression.id)
            if isinstance(expression, ast.Attribute):
                module = module_name(expression.value, lineno)
                if module in known and expression.attr in known[module]:
                    return f"{module}.{expression.attr}"
                import_kind, _ = import_call_fact(expression.value, lineno)
                if (
                    import_kind == "dynamic"
                    and expression.attr in process_api_names
                ):
                    return f"<dynamic-import>.{expression.attr}"
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id == "getattr"
                and len(expression.args) >= 2
            ):
                module = module_name(expression.args[0], lineno)
                attribute = resolve_string(expression.args[1], lineno)
                if module in known and attribute in known[module]:
                    return f"{module}.{attribute}"
                import_kind, _ = import_call_fact(expression.args[0], lineno)
                if (
                    import_kind == "dynamic"
                    and attribute in process_api_names
                ):
                    return f"<dynamic-import>.{attribute}"
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name in known:
                        module_aliases[item.asname or item.name] = item.name
                    elif item.name == "importlib":
                        importlib_aliases.add(item.asname or item.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module in known:
                    for item in node.names:
                        if item.name == "*":
                            add(f"{node.module}.*", node)
                        elif item.name in known[node.module]:
                            api = f"{node.module}.{item.name}"
                            api_aliases[item.asname or item.name] = api
                            add(api, node)
                elif node.module == "importlib":
                    for item in node.names:
                        if item.name == "import_module":
                            import_module_functions.add(
                                item.asname or item.name
                            )

        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]
        module_string_values: dict[str, list[ast.AST]] = {}
        for assignment in assignments:
            if containing_function(assignment.lineno) is not None:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and assignment.value is not None
            ):
                module_string_values.setdefault(targets[0].id, []).append(
                    assignment.value
                )
        changed_constants = True
        while changed_constants:
            changed_constants = False
            for name, values in module_string_values.items():
                if len(values) != 1 or name in string_constants:
                    continue
                value = values[0]
                resolved = (
                    value.value
                    if isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    else string_constants.get(value.id)
                    if isinstance(value, ast.Name)
                    else None
                )
                if isinstance(resolved, str):
                    string_constants[name] = resolved
                    changed_constants = True

        def global_string(expression: ast.AST) -> str | None:
            if isinstance(expression, ast.Constant) and isinstance(
                expression.value, str
            ):
                return expression.value
            if isinstance(expression, ast.Name):
                return string_constants.get(expression.id)
            return None

        for function in functions:
            positional = list(function.args.posonlyargs) + list(
                function.args.args
            )
            defaults = list(zip(
                positional[-len(function.args.defaults):],
                function.args.defaults,
            )) if function.args.defaults else []
            defaults.extend(
                (parameter, default)
                for parameter, default in zip(
                    function.args.kwonlyargs,
                    function.args.kw_defaults,
                )
                if default is not None
            )
            for parameter, default in defaults:
                text = global_string(default)
                if text is not None:
                    default_string_aliases[(id(function), parameter.arg)] = text
                if (
                    isinstance(default, ast.Name)
                    and default.id == "__import__"
                    or is_import_module_ref(default)
                ):
                    default_importer_aliases.add((id(function), parameter.arg))

        function_bound_names: dict[int, set[str]] = {}
        function_string_constants: dict[int, dict[str, str]] = {}
        for function in functions:
            bound = {
                parameter.arg
                for parameter in (
                    list(function.args.posonlyargs)
                    + list(function.args.args)
                    + list(function.args.kwonlyargs)
                )
            }
            if function.args.vararg is not None:
                bound.add(function.args.vararg.arg)
            if function.args.kwarg is not None:
                bound.add(function.args.kwarg.arg)
            local_values: dict[str, list[ast.AST]] = {}
            for candidate in ast.walk(function):
                if containing_function(
                    getattr(candidate, "lineno", None)
                ) is not function:
                    continue
                if isinstance(candidate, ast.Name) and isinstance(
                    candidate.ctx, ast.Store
                ):
                    bound.add(candidate.id)
                elif isinstance(candidate, (ast.Import, ast.ImportFrom)):
                    bound.update(
                        item.asname or item.name.split(".")[0]
                        for item in candidate.names
                    )
                if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    candidate.targets
                    if isinstance(candidate, ast.Assign)
                    else [candidate.target]
                )
                if (
                    len(targets) == 1
                    and isinstance(targets[0], ast.Name)
                    and candidate.value is not None
                ):
                    local_values.setdefault(targets[0].id, []).append(
                        candidate.value
                    )
            local_constants: dict[str, str] = {}
            local_changed = True
            while local_changed:
                local_changed = False
                for name, values in local_values.items():
                    if len(values) != 1 or name in local_constants:
                        continue
                    value = values[0]
                    resolved = (
                        value.value
                        if isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        else local_constants.get(value.id)
                        if isinstance(value, ast.Name)
                        and value.id in bound
                        else string_constants.get(value.id)
                        if isinstance(value, ast.Name)
                        else None
                    )
                    if isinstance(resolved, str):
                        local_constants[name] = resolved
                        local_changed = True
            function_bound_names[id(function)] = bound
            function_string_constants[id(function)] = local_constants

        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                value = assignment.value
                if value is None:
                    continue
                resolved_module = module_name(value, assignment.lineno)
                resolved_api = api_name(value, assignment.lineno)
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if (
                        is_import_module_ref(value)
                        and target.id not in import_module_functions
                    ):
                        import_module_functions.add(target.id)
                        changed = True
                    if (
                        resolved_module is not None
                        and module_aliases.get(target.id) != resolved_module
                    ):
                        module_aliases[target.id] = resolved_module
                        changed = True
                    if (
                        resolved_api is not None
                        and api_aliases.get(target.id) != resolved_api
                    ):
                        api_aliases[target.id] = resolved_api
                        add(resolved_api, value)
                        changed = True

        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        annotation_nodes: set[ast.AST] = set()
        for candidate in ast.walk(tree):
            annotations: list[ast.AST] = []
            if isinstance(candidate, ast.arg) and candidate.annotation is not None:
                annotations.append(candidate.annotation)
            elif isinstance(candidate, ast.AnnAssign):
                annotations.append(candidate.annotation)
            elif isinstance(
                candidate,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ) and candidate.returns is not None:
                annotations.append(candidate.returns)
            for annotation in annotations:
                annotation_nodes.update(ast.walk(annotation))
        for node in ast.walk(tree):
            if node in annotation_nodes:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                # The RHS is the boundary reference; the target name is only
                # its binding slot and must not create a duplicate finding.
                continue
            resolved_api = api_name(node, getattr(node, "lineno", None))
            if resolved_api is None:
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and parent.func is node:
                continue
            add(resolved_api, node)
        return sorted(findings, key=lambda item: (item[0], item[2], item[1]))

    _subprocess_calls = _process_boundary_calls


if __name__ == "__main__":
    unittest.main()
