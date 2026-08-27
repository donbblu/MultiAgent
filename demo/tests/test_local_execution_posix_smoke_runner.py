from __future__ import annotations

import ast
import hashlib
import json
import os
import signal
import stat
import sys
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Optional
from unittest import mock

from tests import _local_execution_posix_smoke_runner as runner


class _FakeSignal:
    SIGALRM = 14
    SIG_DFL = 0
    SIG_UNBLOCK = 1
    ITIMER_REAL = 2

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def signal(self, signum: int, handler: object) -> None:
        self.calls.append(("signal", signum, handler))

    def pthread_sigmask(self, operation: int, signals: set[int]) -> None:
        self.calls.append(("pthread_sigmask", operation, signals))

    def setitimer(
        self,
        which: int,
        seconds: float,
        interval: float,
    ) -> None:
        self.calls.append(("setitimer", which, seconds, interval))


class LocalExecutionPosixSmokeRunnerSafetyTests(unittest.TestCase):
    def _environment(
        self,
        case_name: str = runner.WATCHDOG_ONLY,
        runner_hash: str = "a" * 64,
    ) -> dict[str, str]:
        return {
            "PATH": runner.FROZEN_PATH,
            "LANG": runner.FROZEN_LOCALE,
            "LC_ALL": runner.FROZEN_LOCALE,
            "HOME": runner.BOOTSTRAP_DIRECTORY,
            "TMPDIR": runner.BOOTSTRAP_DIRECTORY,
            runner.CASE_ENV: case_name,
            runner.RUN_ID_ENV: "0123456789abcdef0123456789abcdef",
            runner.RUNNER_HASH_ENV: runner_hash,
            runner.CF_USER_TEXT_ENCODING_ENV: (
                runner.FROZEN_CF_USER_TEXT_ENCODING
            ),
        }

    def _runtime(
        self,
        case_name: str = runner.WATCHDOG_ONLY,
    ) -> runner.RuntimeSnapshot:
        test_id = runner.TEST_IDS[case_name]
        return runner.RuntimeSnapshot(
            argv=(str(runner.RUNNER_PATH), test_id),
            environ=self._environment(case_name),
            executable=runner.FROZEN_EXECUTABLE,
            implementation=runner.FROZEN_IMPLEMENTATION,
            version=runner.FROZEN_VERSION,
            isolated=1,
            dont_write_bytecode=1,
            check_hash_based_pycs="default",
            debug=0,
            inspect=0,
            interactive=0,
            optimize=0,
            no_user_site=1,
            no_site=0,
            ignore_environment=1,
            verbose=0,
            bytes_warning=0,
            quiet=0,
            hash_randomization=1,
            dev_mode=False,
            utf8_mode=0,
            warnoptions=(),
            xoptions={},
            loaded_tests_modules=(),
            stdout_write_through=True,
            stderr_write_through=True,
            cwd=runner.DEMO_ROOT,
            self_path=runner.RUNNER_PATH,
            alarm_handler=signal.SIG_DFL,
            alarm_timer=(0.0, 0.0),
            blocked_signals=frozenset(),
            tempfile_tempdir=None,
        )

    def _hashes(self, runner_hash: str = "a" * 64) -> dict[str, str]:
        return {
            "runner": runner_hash,
            **runner.EXPECTED_DEPENDENCY_HASHES,
        }

    def _successful_outcome(self, test_id: str) -> runner.RecordingOutcome:
        return runner.RecordingOutcome(
            tests_run=1,
            started_ids=(test_id,),
            success_ids=(test_id,),
            skipped=0,
            failures=0,
            errors=0,
            expected_failures=0,
            unexpected_successes=0,
        )

    def _scope(self) -> runner.ScopeLayout:
        root = Path(
            "/private/tmp/"
            "sec-exec-posix-smoke-0123456789abcdef0123456789abcdef-x"
        )
        uid = os.getuid()
        identities = {
            "root": runner.NodeIdentity("directory", 1, 10, uid, 0o700),
            "home": runner.NodeIdentity("directory", 1, 11, uid, 0o700),
            "tmp": runner.NodeIdentity("directory", 1, 12, uid, 0o700),
            "logs": runner.NodeIdentity("directory", 1, 13, uid, 0o700),
            "stdout": runner.NodeIdentity("file", 1, 14, uid, 0o600),
            "stderr": runner.NodeIdentity("file", 1, 15, uid, 0o600),
        }
        return runner.ScopeLayout(
            root=root,
            home=root / "home",
            tmp=root / "tmp",
            logs=root / "logs",
            stdout_log=root / "logs" / "test.stdout.log",
            stderr_log=root / "logs" / "test.stderr.log",
            pass_receipt=root / runner.PASS_RECEIPT_NAME,
            identities=identities,
        )

    def _scope_snapshot(
        self,
        scope: runner.ScopeLayout,
    ) -> runner.ScopeSnapshot:
        return runner.ScopeSnapshot(
            identities=dict(scope.identities),
            entries={
                "root": ("home", "logs", "tmp"),
                "home": (),
                "tmp": (),
                "logs": ("test.stderr.log", "test.stdout.log"),
            },
            log_sizes={"stdout": 0, "stderr": 0},
        )

    def _verify_request(self) -> runner.RunRequest:
        runtime = self._runtime()
        scope = self._scope()
        runtime = replace(
            runtime,
            argv=(
                str(runner.RUNNER_PATH),
                "--verify-clean",
                str(scope.root),
            ),
        )
        return runner._validate_runtime(runtime)

    def _retained_scope_snapshot(
        self,
        request: runner.RunRequest,
        scope: runner.ScopeLayout,
    ) -> runner.RetainedScopeSnapshot:
        receipt_identity = runner.NodeIdentity(
            "file",
            scope.identities["root"].device,
            16,
            os.getuid(),
            0o600,
        )
        receipt = runner._canonical_bytes(
            runner._receipt_payload(
                request,
                self._successful_outcome(request.test_id),
                scope.identities["root"],
            )
        )
        return runner.RetainedScopeSnapshot(
            scope=scope,
            receipt_identity=receipt_identity,
            entries={
                "root": ("home", "logs", runner.PASS_RECEIPT_NAME, "tmp"),
                "home": (),
                "tmp": (),
                "logs": ("test.stderr.log", "test.stdout.log"),
            },
            log_sizes={"stdout": 0, "stderr": 0},
            receipt_bytes=receipt,
        )

    def test_runtime_preflight_accepts_only_two_exact_requests(self) -> None:
        for case_name in (runner.WATCHDOG_ONLY, runner.ARM_DISARM):
            with self.subTest(case_name=case_name):
                request = runner._validate_runtime(self._runtime(case_name))
                self.assertEqual(request.case_name, case_name)
                self.assertEqual(request.test_id, runner.TEST_IDS[case_name])
                self.assertEqual(
                    request.run_id,
                    "0123456789abcdef0123456789abcdef",
                )
                self.assertEqual(request.runner_hash, "a" * 64)
                self.assertEqual(request.mode, runner.RUN_MODE)
                self.assertIsNone(request.scope_root)

        verify = self._verify_request()
        self.assertEqual(verify.mode, runner.VERIFY_CLEAN_MODE)
        self.assertEqual(verify.scope_root, self._scope().root)

    def test_platform_snapshot_accepts_frozen_launcher_shape(self) -> None:
        environment = dict(self._environment())
        environment["__CF_USER_TEXT_ENCODING"] = "0x1F5:0x19:0x34"
        executable = (
            "/Library/Developer/CommandLineTools/Library/Frameworks/"
            "Python3.framework/Versions/3.9/bin/python3.9"
        )
        snapshot = replace(
            self._runtime(),
            environ=environment,
            executable=executable,
        )
        self.assertEqual(frozenset(environment), runner.EXPECTED_ENV_KEYS)
        self.assertEqual(len(environment), 9)
        self.assertEqual(executable, runner.FROZEN_EXECUTABLE)
        request = runner._validate_runtime(snapshot)
        self.assertEqual(request.mode, runner.RUN_MODE)

    def test_verify_clean_rejects_wrong_raw_argv0(self) -> None:
        valid = self._runtime()
        for wrong_argv0 in (
            runner.RUNNER_PATH.name,
            "/private/tmp/not-the-reviewed-runner.py",
            str(runner.RUNNER_PATH.with_name("runner-symlink.py")),
        ):
            with self.subTest(argv0=wrong_argv0):
                wrong = replace(
                    valid,
                    argv=(
                        wrong_argv0,
                        "--verify-clean",
                        str(self._scope().root),
                    ),
                )
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_runtime(wrong)

    def test_hash_based_pyc_policy_is_explicit_and_never_rejected(self) -> None:
        fields = runner.RuntimeSnapshot.__dataclass_fields__
        self.assertIn("check_hash_based_pycs", fields)
        valid = self._runtime()
        with self.assertRaises(runner.RunnerRejected):
            runner._validate_runtime(
                replace(valid, check_hash_based_pycs="never")
            )

    def test_runtime_snapshot_reads_live_imp_hash_pyc_policy(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                runner._imp,
                "check_hash_based_pycs",
                "never",
            ))
            stack.enter_context(mock.patch.object(
                runner.signal,
                "pthread_sigmask",
                return_value=set(),
            ))
            stack.enter_context(mock.patch.object(
                runner.signal,
                "getsignal",
                return_value=signal.SIG_DFL,
            ))
            stack.enter_context(mock.patch.object(
                runner.signal,
                "getitimer",
                return_value=(0.0, 0.0),
            ))
            snapshot = runner._runtime_snapshot()
        self.assertEqual(snapshot.check_hash_based_pycs, "never")

    def test_runtime_preflight_rejects_every_noncanonical_input(self) -> None:
        valid = self._runtime()
        invalid: list[tuple[str, runner.RuntimeSnapshot]] = [
            ("default environment", replace(valid, environ={})),
        ]

        for label, mutate in (
            ("missing env", lambda env: env.pop("LANG")),
            (
                "missing CF encoding",
                lambda env: env.pop(runner.CF_USER_TEXT_ENCODING_ENV),
            ),
            ("extra env", lambda env: env.__setitem__("EXTRA", "1")),
            ("wrong path", lambda env: env.__setitem__("PATH", "/usr/bin")),
            ("wrong locale", lambda env: env.__setitem__("LC_ALL", "C")),
            ("wrong home", lambda env: env.__setitem__("HOME", "/tmp")),
            ("wrong tmp", lambda env: env.__setitem__("TMPDIR", "/tmp")),
            (
                "wrong CF encoding",
                lambda env: env.__setitem__(
                    runner.CF_USER_TEXT_ENCODING_ENV,
                    "0x0:0x0:0x0",
                ),
            ),
            ("forbidden CPATH", lambda env: env.__setitem__("CPATH", "/tmp")),
            (
                "forbidden LIBRARY_PATH",
                lambda env: env.__setitem__("LIBRARY_PATH", "/tmp"),
            ),
            (
                "forbidden MANPATH",
                lambda env: env.__setitem__("MANPATH", "/tmp"),
            ),
            (
                "forbidden SDKROOT",
                lambda env: env.__setitem__("SDKROOT", "/tmp"),
            ),
            (
                "unknown case",
                lambda env: env.__setitem__(runner.CASE_ENV, "unknown"),
            ),
            (
                "bad run id",
                lambda env: env.__setitem__(runner.RUN_ID_ENV, "A" * 32),
            ),
            (
                "bad runner hash",
                lambda env: env.__setitem__(runner.RUNNER_HASH_ENV, "g" * 64),
            ),
        ):
            env = dict(valid.environ)
            mutate(env)
            invalid.append((label, replace(valid, environ=env)))

        fq_id = runner.TEST_IDS[runner.WATCHDOG_ONLY]
        invalid.extend(
            (
                ("missing FQ", replace(valid, argv=(str(runner.RUNNER_PATH),))),
                (
                    "duplicate FQ",
                    replace(
                        valid,
                        argv=(str(runner.RUNNER_PATH), fq_id, fq_id),
                    ),
                ),
                (
                    "-k FQ",
                    replace(
                        valid,
                        argv=(str(runner.RUNNER_PATH), "-k", fq_id),
                    ),
                ),
                (
                    "--locals FQ",
                    replace(
                        valid,
                        argv=(str(runner.RUNNER_PATH), "--locals", fq_id),
                    ),
                ),
                (
                    "extra positional",
                    replace(
                        valid,
                        argv=(str(runner.RUNNER_PATH), fq_id, "extra"),
                    ),
                ),
                (
                    "discover",
                    replace(
                        valid,
                        argv=(str(runner.RUNNER_PATH), "discover"),
                    ),
                ),
                (
                    "module ID",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            "tests.test_local_execution_posix_smoke",
                        ),
                    ),
                ),
                (
                    "class ID",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            "tests.test_local_execution_posix_smoke."
                            "LocalExecutionPosixSmokeTests",
                        ),
                    ),
                ),
                (
                    "wrong case FQ",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            runner.TEST_IDS[runner.ARM_DISARM],
                        ),
                    ),
                ),
                (
                    "relative runner argv0",
                    replace(valid, argv=(runner.RUNNER_PATH.name, fq_id)),
                ),
                (
                    "wrong executable",
                    replace(valid, executable="/usr/local/bin/python3"),
                ),
                (
                    "launcher symlink executable",
                    replace(valid, executable="/usr/bin/python3"),
                ),
                (
                    "observed CLT symlink executable",
                    replace(
                        valid,
                        executable=(
                            "/Library/Developer/CommandLineTools/usr/bin/"
                            "python3"
                        ),
                    ),
                ),
                ("wrong implementation", replace(valid, implementation="PyPy")),
                ("wrong version", replace(valid, version=(3, 9, 7))),
                ("missing -I", replace(valid, isolated=0)),
                ("missing -B", replace(valid, dont_write_bytecode=0)),
                (
                    "hash-pyc never",
                    replace(valid, check_hash_based_pycs="never"),
                ),
                (
                    "hash-pyc always",
                    replace(valid, check_hash_based_pycs="always"),
                ),
                (
                    "hash-pyc non-string",
                    replace(valid, check_hash_based_pycs=1),
                ),
                ("debug", replace(valid, debug=1)),
                ("-i inspect", replace(valid, inspect=1)),
                ("-i interactive", replace(valid, interactive=1)),
                ("-O", replace(valid, optimize=1)),
                ("missing no-user-site", replace(valid, no_user_site=0)),
                ("-S", replace(valid, no_site=1)),
                ("ignore-env drift", replace(valid, ignore_environment=0)),
                ("-v", replace(valid, verbose=1)),
                ("-b", replace(valid, bytes_warning=1)),
                ("-q", replace(valid, quiet=1)),
                ("hash randomization off", replace(valid, hash_randomization=0)),
                ("-X dev", replace(valid, dev_mode=True)),
                ("-X utf8", replace(valid, utf8_mode=1)),
                ("-W", replace(valid, warnoptions=("error",))),
                ("-X", replace(valid, xoptions={"dev": True})),
                (
                    "preloaded tests",
                    replace(valid, loaded_tests_modules=("tests.foreign",)),
                ),
                (
                    "missing stdout -u",
                    replace(valid, stdout_write_through=False),
                ),
                (
                    "missing stderr -u",
                    replace(valid, stderr_write_through=False),
                ),
                ("wrong cwd", replace(valid, cwd=Path("/private/tmp"))),
                (
                    "wrong self path",
                    replace(valid, self_path=Path("/private/tmp/runner.py")),
                ),
                (
                    "ignored SIGALRM",
                    replace(valid, alarm_handler=signal.SIG_IGN),
                ),
                (
                    "existing timer",
                    replace(valid, alarm_timer=(1.0, 0.0)),
                ),
                (
                    "blocked SIGALRM",
                    replace(valid, blocked_signals=frozenset({signal.SIGALRM})),
                ),
                (
                    "preinitialized tempfile",
                    replace(valid, tempfile_tempdir="/private/tmp/other"),
                ),
                (
                    "verify relative root",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            "--verify-clean",
                            self._scope().root.name,
                        ),
                    ),
                ),
                (
                    "verify wrong run id",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            "--verify-clean",
                            "/private/tmp/sec-exec-posix-smoke-"
                            "ffffffffffffffffffffffffffffffff-x",
                        ),
                    ),
                ),
                (
                    "verify extra arg",
                    replace(
                        valid,
                        argv=(
                            str(runner.RUNNER_PATH),
                            "--verify-clean",
                            str(self._scope().root),
                            "extra",
                        ),
                    ),
                ),
            )
        )

        for label, snapshot in invalid:
            with self.subTest(invalid=label):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_runtime(snapshot)

    def test_dispatch_has_no_import_or_scope_before_all_preflight(self) -> None:
        calls: list[str] = []

        def read_hashes() -> dict[str, str]:
            calls.append("hashes")
            return self._hashes()

        def execute(
            request: runner.RunRequest,
            hashes: dict[str, str],
        ) -> int:
            del request, hashes
            calls.append("execute")
            return 0

        invalid = replace(self._runtime(), isolated=0)
        with self.assertRaises(runner.RunnerRejected):
            runner._dispatch(invalid, read_hashes, execute)
        self.assertEqual(calls, [])

        bad_hashes = self._hashes()
        bad_hashes["smoke"] = "b" * 64
        with self.assertRaises(runner.RunnerRejected):
            runner._dispatch(
                self._runtime(),
                lambda: bad_hashes,
                execute,
            )
        self.assertEqual(calls, [])

        self.assertEqual(
            runner._dispatch(self._runtime(), read_hashes, execute),
            0,
        )
        self.assertEqual(calls, ["hashes", "execute"])

    def test_hash_gate_rejects_missing_extra_malformed_and_drift(self) -> None:
        request = runner._validate_runtime(self._runtime())
        valid = self._hashes()
        self.assertEqual(
            runner._validate_artifact_hashes(valid, request),
            valid,
        )

        mutations = []
        missing = dict(valid)
        missing.pop("fixture")
        mutations.append(("missing", missing))
        extra = dict(valid)
        extra["extra"] = "c" * 64
        mutations.append(("extra", extra))
        wrong_runner = dict(valid)
        wrong_runner["runner"] = "b" * 64
        mutations.append(("runner", wrong_runner))
        for name in runner.EXPECTED_DEPENDENCY_HASHES:
            changed = dict(valid)
            changed[name] = "b" * 64
            mutations.append((name, changed))

        for label, hashes in mutations:
            with self.subTest(hash_case=label):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_artifact_hashes(hashes, request)

        with self.assertRaises(runner.RunnerRejected):
            runner._validate_post_hashes(valid, {**valid, "runner": "b" * 64})

    def test_alarm_is_defaulted_unblocked_and_armed_in_order(self) -> None:
        fake = _FakeSignal()
        runner._arm_hard_alarm(fake)
        self.assertEqual(
            fake.calls,
            [
                ("signal", fake.SIGALRM, fake.SIG_DFL),
                ("pthread_sigmask", fake.SIG_UNBLOCK, {fake.SIGALRM}),
                (
                    "setitimer",
                    fake.ITIMER_REAL,
                    runner.HARD_TIMEOUT_SECONDS,
                    0.0,
                ),
            ],
        )

    def test_recording_outcome_rejects_every_false_green(self) -> None:
        test_id = runner.TEST_IDS[runner.WATCHDOG_ONLY]
        valid = self._successful_outcome(test_id)
        runner._validate_recording_outcome(valid, test_id)

        invalid = (
            replace(valid, tests_run=0),
            replace(valid, tests_run=2),
            replace(valid, tests_run=True),
            replace(valid, started_ids=()),
            replace(valid, started_ids=(test_id, test_id)),
            replace(valid, started_ids=("wrong",)),
            replace(valid, success_ids=()),
            replace(valid, success_ids=("wrong",)),
            replace(valid, skipped=1),
            replace(valid, skipped=False),
            replace(valid, failures=1),
            replace(valid, errors=1),
            replace(valid, expected_failures=1),
            replace(valid, unexpected_successes=1),
        )
        for outcome in invalid:
            with self.subTest(outcome=outcome):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_recording_outcome(outcome, test_id)

    def test_programmatic_result_records_started_and_success_ids(self) -> None:
        class PassingTest(unittest.TestCase):
            def runTest(self) -> None:
                pass

        test = PassingTest()
        result = runner._RecordingResult()
        test.run(result)
        outcome = runner._recording_outcome(result)
        self.assertEqual(outcome.tests_run, 1)
        self.assertEqual(outcome.started_ids, (test.id(),))
        self.assertEqual(outcome.success_ids, (test.id(),))
        runner._validate_recording_outcome(outcome, test.id())

    def test_loader_suite_must_be_unique_and_match_before_run(self) -> None:
        class PassingTest(unittest.TestCase):
            def runTest(self) -> None:
                pass

        first = PassingTest()
        exact = unittest.TestSuite([unittest.TestSuite([first])])
        runner._validate_loaded_suite(exact, first.id())

        with self.assertRaises(runner.RunnerRejected):
            runner._validate_loaded_suite(exact, "wrong.test.id")
        with self.assertRaises(runner.RunnerRejected):
            runner._validate_loaded_suite(
                unittest.TestSuite([PassingTest(), PassingTest()]),
                first.id(),
            )

    def test_run_receipt_requires_exact_empty_known_tree(self) -> None:
        request = runner._validate_runtime(self._runtime())
        scope = self._scope()
        snapshot = self._scope_snapshot(scope)
        runner._validate_empty_scope_for_receipt(request, scope, snapshot)

        bad_snapshots = []
        entries = dict(snapshot.entries)
        entries["root"] = (*entries["root"], "unknown")
        bad_snapshots.append(replace(snapshot, entries=entries))
        entries = dict(snapshot.entries)
        entries["tmp"] = ("target-left-behind",)
        bad_snapshots.append(replace(snapshot, entries=entries))
        sizes = dict(snapshot.log_sizes)
        sizes["stdout"] = 1
        bad_snapshots.append(replace(snapshot, log_sizes=sizes))
        identities = dict(snapshot.identities)
        identities["root"] = replace(identities["root"], inode=999)
        bad_snapshots.append(replace(snapshot, identities=identities))
        identities = dict(snapshot.identities)
        identities["stderr"] = replace(identities["stderr"], kind="symlink")
        bad_snapshots.append(replace(snapshot, identities=identities))

        for bad in bad_snapshots:
            with self.subTest(snapshot=bad):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_empty_scope_for_receipt(
                        request,
                        scope,
                        bad,
                    )

        with self.assertRaises(runner.RunnerRejected):
            runner._validate_empty_scope_for_receipt(
                request,
                replace(
                    scope,
                    pass_receipt=scope.root / "wrong-receipt.json",
                ),
                snapshot,
            )

    def test_verify_clean_requires_exact_retained_scope_and_receipt(self) -> None:
        request = self._verify_request()
        scope = self._scope()
        retained = self._retained_scope_snapshot(request, scope)
        verified = runner._validate_retained_scope(request, retained)
        self.assertEqual(verified.root_identity, scope.identities["root"])
        self.assertEqual(
            verified.receipt_sha256,
            hashlib.sha256(retained.receipt_bytes).hexdigest(),
        )

        bad: list[tuple[str, runner.RetainedScopeSnapshot]] = []
        entries = dict(retained.entries)
        entries["root"] = (*entries["root"], "unknown")
        bad.append(("unknown root entry", replace(retained, entries=entries)))
        entries = dict(retained.entries)
        entries["tmp"] = ("target",)
        bad.append(("nonempty tmp", replace(retained, entries=entries)))
        entries = dict(retained.entries)
        entries["logs"] = ("test.stdout.log",)
        bad.append(("missing log", replace(retained, entries=entries)))
        sizes = dict(retained.log_sizes)
        sizes["stderr"] = 1
        bad.append(("nonempty log", replace(retained, log_sizes=sizes)))
        identities = dict(scope.identities)
        identities["root"] = replace(identities["root"], inode=999)
        bad.append((
            "receipt root identity mismatch",
            replace(retained, scope=replace(scope, identities=identities)),
        ))
        bad.append((
            "receipt wrong mode",
            replace(
                retained,
                receipt_identity=replace(retained.receipt_identity, mode=0o644),
            ),
        ))
        bad.append((
            "receipt different device",
            replace(
                retained,
                receipt_identity=replace(retained.receipt_identity, device=2),
            ),
        ))
        bad.append((
            "receipt inode aliases log",
            replace(
                retained,
                receipt_identity=replace(
                    retained.receipt_identity,
                    inode=scope.identities["stdout"].inode,
                ),
            ),
        ))
        bad.append((
            "noncanonical receipt",
            replace(retained, receipt_bytes=b" " + retained.receipt_bytes),
        ))
        parsed = json.loads(retained.receipt_bytes)
        parsed["status"] = "CLEANUP_COMPLETE"
        bad.append((
            "wrong receipt status",
            replace(retained, receipt_bytes=runner._canonical_bytes(parsed)),
        ))
        parsed = json.loads(retained.receipt_bytes)
        parsed["runner_sha256"] = "b" * 64
        bad.append((
            "producer runner hash mismatch",
            replace(retained, receipt_bytes=runner._canonical_bytes(parsed)),
        ))
        parsed = json.loads(retained.receipt_bytes)
        parsed["tests_run"] = True
        bad.append((
            "boolean count",
            replace(retained, receipt_bytes=runner._canonical_bytes(parsed)),
        ))

        for label, candidate in bad:
            with self.subTest(retained=label):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_retained_scope(request, candidate)

        run_request = runner._validate_runtime(self._runtime())
        with self.assertRaises(runner.RunnerRejected):
            runner._validate_retained_scope(run_request, retained)

    def test_verify_clean_dirfd_boundary_validates_before_known_deletes(
        self,
    ) -> None:
        request = self._verify_request()
        scope = self._scope()
        retained = self._retained_scope_snapshot(request, scope)
        original_validate = runner._validate_retained_scope

        def exercise(
            root_entries: tuple[str, ...],
            changed_log: Optional[str] = None,
        ) -> tuple[
            Optional[runner.VerifiedRetainedScope],
            list[tuple[str, str]],
            Optional[BaseException],
        ]:
            events: list[tuple[str, str]] = []
            fd_for = {
                "bootstrap": 100,
                "root": 101,
                "home": 102,
                "tmp": 103,
                "logs": 104,
                "stdout": 105,
                "stderr": 106,
                "receipt": 107,
            }
            identity_for = {
                **scope.identities,
                "receipt": retained.receipt_identity,
                "bootstrap": runner.NodeIdentity(
                    "directory",
                    1,
                    9,
                    os.getuid(),
                    0o700,
                ),
            }
            name_for_fd = {value: key for key, value in fd_for.items()}
            entries = {
                "root": list(root_entries),
                "home": [],
                "tmp": [],
                "logs": ["test.stderr.log", "test.stdout.log"],
                "bootstrap": [scope.root.name],
            }

            def info(identity: runner.NodeIdentity) -> SimpleNamespace:
                kind_bits = (
                    stat.S_IFDIR
                    if identity.kind == "directory"
                    else stat.S_IFREG
                )
                return SimpleNamespace(
                    st_mode=kind_bits | identity.mode,
                    st_dev=identity.device,
                    st_ino=identity.inode,
                    st_uid=identity.uid,
                    st_size=0,
                    st_mtime_ns=1,
                )

            def open_directory(
                path: str,
                *,
                parent_fd: Optional[int] = None,
            ) -> int:
                if parent_fd is None:
                    key = "bootstrap"
                elif parent_fd == fd_for["bootstrap"]:
                    key = "root"
                else:
                    key = path
                events.append(("open-directory", key))
                return fd_for[key]

            def open_regular(name: str, *, parent_fd: int) -> int:
                del parent_fd
                key = {
                    "test.stdout.log": "stdout",
                    "test.stderr.log": "stderr",
                    runner.PASS_RECEIPT_NAME: "receipt",
                }[name]
                events.append(("open-file", key))
                return fd_for[key]

            def fstat(descriptor: int) -> SimpleNamespace:
                return info(identity_for[name_for_fd[descriptor]])

            def list_entries(descriptor: int, label: str) -> tuple[str, ...]:
                key = name_for_fd[descriptor]
                events.append(("list", label))
                return tuple(sorted(entries[key]))

            def read_bytes(
                descriptor: int,
                label: str,
                *,
                maximum_size: int,
            ) -> bytes:
                del descriptor, maximum_size
                events.append(("read", label))
                if label == runner.PASS_RECEIPT_NAME:
                    return retained.receipt_bytes
                if label == changed_log:
                    return b"late output"
                return b""

            def named_identity(parent_fd: int, name: str) -> runner.NodeIdentity:
                if parent_fd == fd_for["bootstrap"]:
                    return identity_for["root"]
                key = {
                    "home": "home",
                    "tmp": "tmp",
                    "logs": "logs",
                    "test.stdout.log": "stdout",
                    "test.stderr.log": "stderr",
                    runner.PASS_RECEIPT_NAME: "receipt",
                }[name]
                return identity_for[key]

            def require_bound(
                parent_fd: int,
                name: str,
                descriptor: int,
                expected: runner.NodeIdentity,
            ) -> None:
                del parent_fd, descriptor, expected
                events.append(("bind", name))

            def unlink(parent_fd: int, name: str) -> None:
                key = name_for_fd[parent_fd]
                events.append(("unlink", name))
                entries[key].remove(name)

            def rmdir(parent_fd: int, name: str) -> None:
                key = name_for_fd[parent_fd]
                events.append(("rmdir", name))
                entries[key].remove(name)

            def validate(
                candidate_request: runner.RunRequest,
                candidate: runner.RetainedScopeSnapshot,
            ) -> runner.VerifiedRetainedScope:
                events.append(("validate", "retained"))
                return original_validate(candidate_request, candidate)

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    runner,
                    "_require_dirfd_cleanup_support",
                    side_effect=lambda: events.append(("support", "dirfd")),
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_open_directory_fd",
                    side_effect=open_directory,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_open_regular_fd",
                    side_effect=open_regular,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_stable_directory_entries",
                    side_effect=list_entries,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_stable_file_bytes",
                    side_effect=read_bytes,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_identity_at_fd_entry",
                    side_effect=named_identity,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_require_bound_entry",
                    side_effect=require_bound,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_unlink_known_entry",
                    side_effect=unlink,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_rmdir_known_entry",
                    side_effect=rmdir,
                ))
                stack.enter_context(mock.patch.object(
                    runner,
                    "_validate_retained_scope",
                    side_effect=validate,
                ))
                stack.enter_context(mock.patch.object(
                    runner.os,
                    "fstat",
                    side_effect=fstat,
                ))
                stack.enter_context(mock.patch.object(runner.os, "close"))
                try:
                    verified = runner._verify_and_delete_scope(request)
                except RunnerFailure as exc:
                    return None, events, exc
                except runner.RunnerRejected as exc:
                    return None, events, exc
            return verified, events, None

        verified, events, error = exercise(retained.entries["root"])
        self.assertIsNone(error)
        self.assertIsNotNone(verified)
        assert verified is not None
        self.assertEqual(verified.root_identity, scope.identities["root"])
        validate_index = events.index(("validate", "retained"))
        first_delete = min(
            index
            for index, event in enumerate(events)
            if event[0] in {"unlink", "rmdir"}
        )
        self.assertLess(validate_index, first_delete)
        self.assertIn(("read", runner.PASS_RECEIPT_NAME), events[:first_delete])
        self.assertEqual(
            [event for event in events if event[0] in {"unlink", "rmdir"}],
            [
                ("unlink", "test.stdout.log"),
                ("unlink", "test.stderr.log"),
                ("rmdir", "logs"),
                ("rmdir", "home"),
                ("rmdir", "tmp"),
                ("unlink", runner.PASS_RECEIPT_NAME),
                ("rmdir", scope.root.name),
            ],
        )

        _, invalid_events, invalid_error = exercise(
            (*retained.entries["root"], "unknown")
        )
        self.assertIsInstance(invalid_error, runner.RunnerRejected)
        self.assertEqual(
            [
                event
                for event in invalid_events
                if event[0] in {"unlink", "rmdir"}
            ],
            [],
        )
        _, drift_events, drift_error = exercise(
            retained.entries["root"],
            changed_log="test.stdout.log",
        )
        self.assertIsInstance(drift_error, runner.RunnerRejected)
        self.assertIn(("read", "test.stdout.log"), drift_events)
        self.assertEqual(
            [
                event
                for event in drift_events
                if event[0] in {"unlink", "rmdir"}
            ],
            [],
        )

    def test_verify_mode_routes_without_scope_creation_or_source_execution(
        self,
    ) -> None:
        request = self._verify_request()
        hashes = self._hashes()
        retained = self._retained_scope_snapshot(request, self._scope())
        verified = runner._validate_retained_scope(request, retained)
        events: list[str] = []
        emitted: list[bytes] = []

        def emit(payload: bytes) -> None:
            events.append("emit")
            emitted.append(payload)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                runner,
                "_arm_hard_alarm",
                side_effect=lambda: events.append("arm"),
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_read_artifact_hashes",
                side_effect=lambda: events.append("rehash") or dict(hashes),
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_verify_and_delete_scope",
                side_effect=lambda _: events.append("delete") or verified,
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_stdout_pipe_buf",
                side_effect=lambda: events.append("pipe-buf") or 512,
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_emit_receipt",
                side_effect=emit,
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_real_execution_seams",
                side_effect=AssertionError("run-mode scope creation reached"),
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_execute_frozen_source_modules",
                side_effect=AssertionError("source execution reached"),
            ))
            self.assertEqual(runner._execute_request(request, hashes), 0)

        self.assertEqual(events, ["arm", "rehash", "delete", "pipe-buf", "emit"])
        self.assertEqual(len(emitted), 1)
        payload = json.loads(emitted[0])
        self.assertEqual(payload["status"], "CLEANUP_COMPLETE")
        self.assertEqual(payload["runner_sha256"], request.runner_hash)
        self.assertEqual(
            payload["retained_receipt_sha256"],
            verified.receipt_sha256,
        )
        self.assertEqual(emitted[0], runner._canonical_bytes(payload))

    def test_cleanup_output_failure_occurs_only_after_verified_delete(self) -> None:
        request = self._verify_request()
        hashes = self._hashes()
        retained = self._retained_scope_snapshot(request, self._scope())
        verified = runner._validate_retained_scope(request, retained)
        events: list[str] = []

        def fail_emit(_: bytes) -> None:
            events.append("emit-failed")
            raise RunnerFailure("cleanup receipt write failed")

        with mock.patch.object(runner, "_arm_hard_alarm", return_value=None), \
                mock.patch.object(
                    runner,
                    "_read_artifact_hashes",
                    return_value=dict(hashes),
                ), mock.patch.object(
                    runner,
                    "_verify_and_delete_scope",
                    side_effect=lambda _: events.append("deleted") or verified,
                ), mock.patch.object(
                    runner,
                    "_stdout_pipe_buf",
                    return_value=512,
                ), mock.patch.object(
                    runner,
                    "_emit_receipt",
                    side_effect=fail_emit,
                ):
            with self.assertRaisesRegex(RunnerFailure, "cleanup receipt"):
                runner._execute_request(request, hashes)
        self.assertEqual(events, ["deleted", "emit-failed"])

    def test_runner_cleanup_calls_are_dirfd_only_and_boundary_free(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
            "importlib",
            "shutil",
            "socket",
            "subprocess",
            "threading",
        }
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertEqual(imports & forbidden_imports, set())
        forbidden_calls = {"kill", "killpg", "Popen", "rmtree"}
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(called_attributes & forbidden_calls, set())

        delete_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"unlink", "rmdir"}
        ]
        self.assertGreaterEqual(len(delete_calls), 2)
        for call in delete_calls:
            self.assertIsInstance(call.func.value, ast.Name)
            self.assertEqual(call.func.value.id, "os")
            self.assertIn("dir_fd", {keyword.arg for keyword in call.keywords})

    def test_receipt_is_canonical_single_write_below_pipe_buf(self) -> None:
        for case_name in (runner.WATCHDOG_ONLY, runner.ARM_DISARM):
            with self.subTest(case_name=case_name):
                request = runner._validate_runtime(self._runtime(case_name))
                outcome = self._successful_outcome(request.test_id)
                payload = runner._receipt_payload(
                    request,
                    outcome,
                    self._scope().identities["root"],
                )
                encoded = runner._encode_receipt(payload, pipe_buf=512)
                self.assertLess(len(encoded), 512)
                self.assertEqual(encoded.count(b"\n"), 1)
                self.assertTrue(encoded.endswith(b"\n"))
                self.assertEqual(
                    encoded,
                    (
                        json.dumps(
                            payload,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("ascii"),
                )
                with self.assertRaises(runner.RunnerRejected):
                    runner._encode_receipt(payload, pipe_buf=len(encoded))

                with mock.patch.object(
                    runner.os,
                    "write",
                    return_value=len(encoded),
                ) as write:
                    runner._emit_receipt(encoded)
                write.assert_called_once_with(1, encoded)

                with mock.patch.object(
                    runner.os,
                    "write",
                    return_value=len(encoded) - 1,
                ):
                    with self.assertRaises(runner.RunnerRejected):
                        runner._emit_receipt(encoded)

        verify_request = self._verify_request()
        retained = self._retained_scope_snapshot(verify_request, self._scope())
        verified = runner._validate_retained_scope(verify_request, retained)
        cleanup_payload = runner._cleanup_receipt_payload(
            verify_request,
            verified,
        )
        cleanup_receipt = runner._encode_receipt(cleanup_payload, pipe_buf=512)
        self.assertLess(len(cleanup_receipt), 512)

        worst_identity = runner.NodeIdentity(
            "directory",
            (1 << 64) - 1,
            (1 << 64) - 1,
            (1 << 32) - 1,
            0o700,
        )
        worst_request = runner._validate_runtime(self._runtime(runner.ARM_DISARM))
        worst_pass = runner._encode_receipt(
            runner._receipt_payload(
                worst_request,
                self._successful_outcome(worst_request.test_id),
                worst_identity,
            ),
            pipe_buf=512,
        )
        worst_verified = runner.VerifiedRetainedScope(
            root_identity=worst_identity,
            receipt_sha256="f" * 64,
        )
        worst_cleanup = runner._encode_receipt(
            runner._cleanup_receipt_payload(worst_request, worst_verified),
            pipe_buf=512,
        )
        self.assertLess(len(worst_pass), 512)
        self.assertLess(len(worst_cleanup), 512)

    def test_execute_persists_receipt_before_stdout_and_never_cleans(self) -> None:
        request = runner._validate_runtime(self._runtime())
        hashes = self._hashes()
        scope = self._scope()
        snapshot = self._scope_snapshot(scope)
        calls: list[str] = []
        persisted: list[bytes] = []
        emitted: list[bytes] = []

        @contextmanager
        def isolate(_: runner.ScopeLayout) -> Iterator[None]:
            calls.append("isolate-enter")
            try:
                yield
            finally:
                calls.append("isolate-exit")

        seams = runner.ExecutionSeams(
            create_scope=lambda _: calls.append("create") or scope,
            announce_scope=lambda _scope, _request: calls.append("announce"),
            arm_alarm=lambda: calls.append("arm"),
            isolate_output=isolate,
            run_test=lambda _: calls.append("run-test")
            or self._successful_outcome(request.test_id),
            read_hashes=lambda: calls.append("post-hash") or dict(hashes),
            inspect_scope=lambda _: calls.append("inspect") or snapshot,
            pipe_buf=lambda: calls.append("pipe-buf") or 512,
            persist_receipt=lambda _scope, payload: (
                calls.append("persist"),
                persisted.append(payload),
            ),
            emit_receipt=lambda payload: (
                calls.append("receipt"),
                emitted.append(payload),
            ),
        )

        self.assertEqual(
            runner._execute_validated(request, hashes, seams),
            0,
        )
        self.assertEqual(
            calls,
            [
                "create",
                "announce",
                "arm",
                "isolate-enter",
                "run-test",
                "isolate-exit",
                "post-hash",
                "inspect",
                "pipe-buf",
                "persist",
                "receipt",
            ],
        )
        self.assertEqual(persisted, emitted)
        self.assertEqual(len(emitted), 1)
        self.assertLess(len(emitted[0]), 512)

    def test_pass_receipt_publication_is_atomic_and_dirfd_anchored(self) -> None:
        scope = self._scope()
        payload = b'{"status":"PASS_NO_TARGET_SCOPE_RETAINED"}\n'
        events: list[tuple[object, ...]] = []
        root_info = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=scope.identities["root"].device,
            st_ino=scope.identities["root"].inode,
            st_uid=scope.identities["root"].uid,
            st_size=0,
            st_mtime_ns=1,
        )
        receipt_identity = runner.NodeIdentity(
            "file",
            scope.identities["root"].device,
            16,
            scope.identities["root"].uid,
            0o600,
        )
        receipt_info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_dev=receipt_identity.device,
            st_ino=receipt_identity.inode,
            st_uid=receipt_identity.uid,
            st_size=len(payload),
            st_mtime_ns=2,
        )

        def open_fd(path: str, flags: int, *args: object, **kwargs: object) -> int:
            events.append(("open", path, flags, args, kwargs))
            if path == str(scope.root):
                return 100
            self.assertEqual(kwargs, {"dir_fd": 100})
            if path == f".{runner.PASS_RECEIPT_NAME}.tmp":
                return 101
            self.assertEqual(path, runner.PASS_RECEIPT_NAME)
            return 102

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                runner,
                "_require_dirfd_receipt_support",
                return_value=None,
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "open",
                side_effect=open_fd,
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "fstat",
                side_effect=lambda descriptor: root_info
                if descriptor == 100
                else receipt_info,
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_identity_at_fd_entry",
                side_effect=lambda parent_fd, name: events.append(
                    ("identity", parent_fd, name)
                ) or receipt_identity,
            ))
            stack.enter_context(mock.patch.object(
                runner,
                "_stable_file_bytes",
                side_effect=lambda descriptor, name, maximum_size: events.append(
                    ("read-back", descriptor, name, maximum_size)
                ) or payload,
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "write",
                side_effect=lambda descriptor, data: events.append(
                    ("write", descriptor, data)
                ) or len(data),
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "fchmod",
                side_effect=lambda descriptor, mode: events.append(
                    ("fchmod", descriptor, mode)
                ),
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "fsync",
                side_effect=lambda descriptor: events.append(
                    ("fsync", descriptor)
                ),
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "link",
                side_effect=lambda source, target, **kwargs: events.append(
                    ("link", source, target, kwargs)
                ),
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "unlink",
                side_effect=lambda name, **kwargs: events.append(
                    ("unlink", name, kwargs)
                ),
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "close",
                side_effect=lambda descriptor: events.append(
                    ("close", descriptor)
                ),
            ))
            runner._persist_atomic_pass_receipt(scope, payload)

        operations = [event[0] for event in events]
        self.assertLess(operations.index("write"), operations.index("link"))
        self.assertLess(operations.index("link"), operations.index("unlink"))
        self.assertEqual(operations.count("read-back"), 2)
        self.assertLess(operations.index("read-back"), operations.index("unlink"))
        link = next(event for event in events if event[0] == "link")
        self.assertEqual(link[1:3], (
            f".{runner.PASS_RECEIPT_NAME}.tmp",
            runner.PASS_RECEIPT_NAME,
        ))
        self.assertEqual(
            link[3],
            {
                "src_dir_fd": 100,
                "dst_dir_fd": 100,
                "follow_symlinks": False,
            },
        )
        unlink = next(event for event in events if event[0] == "unlink")
        self.assertEqual(unlink[2], {"dir_fd": 100})
        self.assertEqual(operations[-1], "close")

    def test_pass_receipt_refuses_temp_name_identity_replacement(self) -> None:
        scope = self._scope()
        payload = b'{"status":"PASS_NO_TARGET_SCOPE_RETAINED"}\n'
        root = scope.identities["root"]
        root_info = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=root.device,
            st_ino=root.inode,
            st_uid=root.uid,
            st_size=0,
            st_mtime_ns=1,
        )
        temp_info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_dev=root.device,
            st_ino=16,
            st_uid=root.uid,
            st_size=len(payload),
            st_mtime_ns=2,
        )
        replacement = runner.NodeIdentity("file", root.device, 17, root.uid, 0o600)

        def open_fd(path: str, *args: object, **kwargs: object) -> int:
            del args, kwargs
            return 100 if path == str(scope.root) else 101

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                runner,
                "_require_dirfd_receipt_support",
                return_value=None,
            ))
            stack.enter_context(mock.patch.object(runner.os, "open", side_effect=open_fd))
            stack.enter_context(mock.patch.object(
                runner.os,
                "fstat",
                side_effect=lambda descriptor: root_info
                if descriptor == 100
                else temp_info,
            ))
            stack.enter_context(mock.patch.object(
                runner.os,
                "write",
                side_effect=lambda _descriptor, data: len(data),
            ))
            stack.enter_context(mock.patch.object(runner.os, "fchmod"))
            stack.enter_context(mock.patch.object(runner.os, "fsync"))
            stack.enter_context(mock.patch.object(runner.os, "close"))
            stack.enter_context(mock.patch.object(
                runner,
                "_identity_at_fd_entry",
                return_value=replacement,
            ))
            link = stack.enter_context(mock.patch.object(runner.os, "link"))
            with self.assertRaisesRegex(runner.RunnerRejected, "name drifted"):
                runner._persist_atomic_pass_receipt(scope, payload)
        link.assert_not_called()

    def test_false_green_cannot_persist_or_emit_receipt(self) -> None:
        request = runner._validate_runtime(self._runtime())
        hashes = self._hashes()
        scope = self._scope()
        persisted: list[bytes] = []
        emitted: list[bytes] = []
        post_hash_reads: list[object] = []
        skipped = replace(
            self._successful_outcome(request.test_id),
            success_ids=(),
            skipped=1,
        )

        @contextmanager
        def isolate(_: runner.ScopeLayout) -> Iterator[None]:
            yield

        seams = runner.ExecutionSeams(
            create_scope=lambda _: scope,
            announce_scope=lambda _scope, _request: None,
            arm_alarm=lambda: None,
            isolate_output=isolate,
            run_test=lambda _: skipped,
            read_hashes=lambda: post_hash_reads.append(True) or dict(hashes),
            inspect_scope=lambda _: self._scope_snapshot(scope),
            persist_receipt=lambda _scope, payload: persisted.append(payload),
            pipe_buf=lambda: 512,
            emit_receipt=emitted.append,
        )
        with self.assertRaises(runner.RunnerRejected):
            runner._execute_validated(request, hashes, seams)
        self.assertEqual(post_hash_reads, [])
        self.assertEqual(persisted, [])
        self.assertEqual(emitted, [])

    def test_pre_persist_failure_sweep_never_produces_pass_receipt(self) -> None:
        request = runner._validate_runtime(self._runtime())
        hashes = self._hashes()
        scope = self._scope()
        snapshot = self._scope_snapshot(scope)

        for stage in (
            "create",
            "announce",
            "arm",
            "isolate",
            "run-test",
            "post-hash",
            "inspect",
            "pipe-buf",
            "persist",
        ):
            failure = RunnerFailure(stage)
            with self.subTest(stage=stage):
                persisted: list[bytes] = []
                emitted: list[bytes] = []

                @contextmanager
                def isolate(_: runner.ScopeLayout) -> Iterator[None]:
                    if stage == "isolate":
                        raise failure
                    yield

                def create(_: runner.RunRequest) -> runner.ScopeLayout:
                    if stage == "create":
                        raise failure
                    return scope

                def announce(
                    _: runner.ScopeLayout,
                    __: runner.RunRequest,
                ) -> None:
                    if stage == "announce":
                        raise failure

                def arm() -> None:
                    if stage == "arm":
                        raise failure

                def run_test(_: str) -> runner.RecordingOutcome:
                    if stage == "run-test":
                        raise failure
                    return self._successful_outcome(request.test_id)

                def read_hashes() -> dict[str, str]:
                    if stage == "post-hash":
                        raise failure
                    return dict(hashes)

                def inspect(_: runner.ScopeLayout) -> runner.ScopeSnapshot:
                    if stage == "inspect":
                        raise failure
                    return snapshot

                def pipe_buf() -> int:
                    if stage == "pipe-buf":
                        raise failure
                    return 512

                def persist(_: runner.ScopeLayout, payload: bytes) -> None:
                    if stage == "persist":
                        raise failure
                    persisted.append(payload)

                seams = runner.ExecutionSeams(
                    create_scope=create,
                    announce_scope=announce,
                    arm_alarm=arm,
                    isolate_output=isolate,
                    run_test=run_test,
                    read_hashes=read_hashes,
                    inspect_scope=inspect,
                    persist_receipt=persist,
                    pipe_buf=pipe_buf,
                    emit_receipt=emitted.append,
                )
                with self.assertRaisesRegex(RunnerFailure, stage):
                    runner._execute_validated(request, hashes, seams)
                self.assertEqual(persisted, [])
                self.assertEqual(emitted, [])

    def test_test_output_spoof_refuses_persisted_and_stdout_receipt(self) -> None:
        request = runner._validate_runtime(self._runtime())
        hashes = self._hashes()
        scope = self._scope()
        snapshot = self._scope_snapshot(scope)
        sizes = dict(snapshot.log_sizes)
        sizes["stdout"] = len(b'{"clean":true}\n')
        spoofed = replace(snapshot, log_sizes=sizes)
        persisted: list[bytes] = []
        emitted: list[bytes] = []

        @contextmanager
        def isolate(_: runner.ScopeLayout) -> Iterator[None]:
            yield

        seams = runner.ExecutionSeams(
            create_scope=lambda _: scope,
            announce_scope=lambda _scope, _request: None,
            arm_alarm=lambda: None,
            isolate_output=isolate,
            run_test=lambda _: self._successful_outcome(request.test_id),
            read_hashes=lambda: dict(hashes),
            inspect_scope=lambda _: spoofed,
            persist_receipt=lambda _scope, payload: persisted.append(payload),
            pipe_buf=lambda: 512,
            emit_receipt=emitted.append,
        )
        with self.assertRaises(runner.RunnerRejected):
            runner._execute_validated(request, hashes, seams)
        self.assertEqual(persisted, [])
        self.assertEqual(emitted, [])

    def test_controlled_source_exec_rejects_preloaded_tests_without_side_effect(
        self,
    ) -> None:
        helper_source = b"VALUE = 41\n"
        smoke_source = (
            b"from tests._local_execution_posix import VALUE\n"
            b"RESULT = VALUE + 1\n"
        )
        sources = {"helper": helper_source, "smoke": smoke_source}
        digests = {
            name: hashlib.sha256(source).hexdigest()
            for name, source in sources.items()
        }
        modules = {"tests.foreign": object()}
        compiled: list[str] = []

        def compile_source(
            source: bytes,
            filename: str,
            mode: str,
            *,
            dont_inherit: bool,
            optimize: int,
        ) -> object:
            del source, filename, mode, dont_inherit, optimize
            compiled.append("compiled")
            return object()

        with self.assertRaises(runner.RunnerRejected):
            runner._execute_frozen_source_modules(
                sources,
                digests,
                modules,
                compile_source=compile_source,
            )
        self.assertEqual(compiled, [])
        self.assertEqual(set(modules), {"tests.foreign"})

    def test_controlled_source_exec_compiles_the_exact_hashed_bytes_in_order(
        self,
    ) -> None:
        helper_source = b"HELPER_VALUE = 41\n"
        smoke_source = b"SMOKE_VALUE = 42\n"
        sources = {"helper": helper_source, "smoke": smoke_source}
        digests = {
            name: hashlib.sha256(source).hexdigest()
            for name, source in sources.items()
        }
        modules: dict[str, object] = {}
        compiled: list[tuple[str, int, bool, int]] = []

        def compile_source(
            source: bytes,
            filename: str,
            mode: str,
            *,
            dont_inherit: bool,
            optimize: int,
        ) -> object:
            compiled.append((filename, id(source), dont_inherit, optimize))
            return compile(
                source,
                filename,
                mode,
                dont_inherit=dont_inherit,
                optimize=optimize,
            )

        smoke = runner._execute_frozen_source_modules(
            sources,
            digests,
            modules,
            compile_source=compile_source,
        )
        self.assertEqual(
            compiled,
            [
                (str(runner.HELPER_PATH), id(helper_source), True, 0),
                (str(runner.SMOKE_PATH), id(smoke_source), True, 0),
            ],
        )
        self.assertEqual(
            tuple(modules),
            (
                "tests",
                "tests._local_execution_posix",
                "tests.test_local_execution_posix_smoke",
            ),
        )
        package = modules["tests"]
        helper = modules["tests._local_execution_posix"]
        self.assertIs(smoke, modules["tests.test_local_execution_posix_smoke"])
        self.assertEqual(getattr(package, "__path__"), ())
        self.assertIsNone(getattr(package, "__loader__"))
        self.assertIsNone(getattr(package, "__spec__"))
        self.assertEqual(getattr(helper, "HELPER_VALUE"), 41)
        self.assertEqual(getattr(smoke, "SMOKE_VALUE"), 42)
        for module in (helper, smoke):
            self.assertIsNone(getattr(module, "__loader__"))
            self.assertIsNone(getattr(module, "__cached__"))
            self.assertIsNone(getattr(module, "__spec__"))

    def test_controlled_empty_path_package_resolves_only_preinstalled_helper(
        self,
    ) -> None:
        helper_source = b"VALUE = 41\n"
        smoke_source = (
            b"from tests._local_execution_posix import VALUE\n"
            b"RESULT = VALUE + 1\n"
        )
        sources = {"helper": helper_source, "smoke": smoke_source}
        digests = {
            name: hashlib.sha256(source).hexdigest()
            for name, source in sources.items()
        }
        with mock.patch.dict(sys.modules, {}, clear=True):
            smoke = runner._execute_frozen_source_modules(
                sources,
                digests,
                sys.modules,
            )
            self.assertEqual(smoke.RESULT, 42)
            self.assertEqual(sys.modules["tests"].__path__, ())
            self.assertEqual(
                set(sys.modules),
                {
                    "tests",
                    "tests._local_execution_posix",
                    "tests.test_local_execution_posix_smoke",
                },
            )

    def test_controlled_module_metadata_drift_removes_all_created_modules(
        self,
    ) -> None:
        sources = {
            "helper": b"VALUE = 41\n",
            "smoke": b"__loader__ = object()\n",
        }
        digests = {
            name: hashlib.sha256(source).hexdigest()
            for name, source in sources.items()
        }
        modules: dict[str, object] = {}
        with self.assertRaisesRegex(runner.RunnerRejected, "metadata drifted"):
            runner._execute_frozen_source_modules(sources, digests, modules)
        self.assertEqual(modules, {})

    def test_source_loader_uses_hashed_bytes_without_name_or_pyc_import(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("importlib", imports)
        self.assertNotIn("import_module", calls)
        self.assertNotIn("SourceFileLoader", source)
        self.assertNotIn("SourcelessFileLoader", source)

    def test_run_output_failure_retains_scope_and_atomic_pass_receipt(self) -> None:
        request = runner._validate_runtime(self._runtime())
        hashes = self._hashes()
        scope = self._scope()
        snapshot = self._scope_snapshot(scope)

        @contextmanager
        def isolate(_: runner.ScopeLayout) -> Iterator[None]:
            yield

        class SimulatedTimeout(BaseException):
            pass

        for failure in (
            RunnerFailure("partial stdout"),
            SimulatedTimeout("alarm during stdout"),
        ):
            with self.subTest(failure=type(failure).__name__):
                persisted: list[bytes] = []

                def emit(_: bytes) -> None:
                    raise failure

                seams = runner.ExecutionSeams(
                    create_scope=lambda _: scope,
                    announce_scope=lambda _scope, _request: None,
                    arm_alarm=lambda: None,
                    isolate_output=isolate,
                    run_test=lambda _: self._successful_outcome(request.test_id),
                    read_hashes=lambda: dict(hashes),
                    inspect_scope=lambda _: snapshot,
                    persist_receipt=lambda _scope, payload: persisted.append(
                        payload
                    ),
                    pipe_buf=lambda: 512,
                    emit_receipt=emit,
                )
                with self.assertRaises(type(failure)):
                    runner._execute_validated(request, hashes, seams)
                self.assertEqual(len(persisted), 1)
                parsed = json.loads(persisted[0])
                self.assertEqual(
                    parsed["status"],
                    "PASS_NO_TARGET_SCOPE_RETAINED",
                )
                self.assertEqual(parsed["runner_sha256"], request.runner_hash)
                self.assertEqual(
                    persisted[0],
                    runner._canonical_bytes(parsed),
                )

    def test_material_interpreter_state_is_part_of_runtime_snapshot(self) -> None:
        required = {
            "check_hash_based_pycs",
            "debug",
            "inspect",
            "interactive",
            "optimize",
            "no_user_site",
            "no_site",
            "ignore_environment",
            "verbose",
            "bytes_warning",
            "quiet",
            "hash_randomization",
            "dev_mode",
            "utf8_mode",
            "warnoptions",
            "xoptions",
            "loaded_tests_modules",
        }
        actual = set(runner.RuntimeSnapshot.__dataclass_fields__)
        self.assertEqual(required - actual, set())
        valid = self._runtime()
        for name, value in (
            ("optimize", 1),
            ("inspect", 1),
            ("interactive", 1),
            ("no_site", 1),
            ("verbose", 1),
            ("bytes_warning", 1),
            ("quiet", 1),
            ("dev_mode", True),
            ("utf8_mode", 1),
            ("warnoptions", ("error",)),
            ("xoptions", {"dev": True}),
            ("loaded_tests_modules", ("tests.foreign",)),
        ):
            with self.subTest(material=name):
                with self.assertRaises(runner.RunnerRejected):
                    runner._validate_runtime(replace(valid, **{name: value}))


class RunnerFailure(RuntimeError):
    pass


if __name__ == "__main__":
    unittest.main()
