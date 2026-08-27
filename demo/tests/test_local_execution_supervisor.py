from __future__ import annotations

import signal
import subprocess
import hashlib
import gc
import io
import os
import tempfile
import threading
import unittest
import weakref
from pathlib import Path
from unittest import mock

from coding_workflow.local_execution import (
    CLEANUP_FAILED,
    PROFILE_CORE,
    PROFILE_VISIONFORGE_DEV,
    SANDBOX_REQUIRED,
    LocalExecutionError,
    SupervisedBackground,
    _StreamingBoundedOutput,
    _finalize_process,
    _private_environment,
    _spawn,
    issue_trusted_local_confirmation,
    prepare_execution,
    run_prepared,
    redact_text,
    sanitize_output,
    start_prepared_background,
)
from coding_workflow.visionforge.browser import BrowserProcessRunner, ManagedProcess


class _TrackedStream:
    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.closed = False

    def close(self) -> None:
        self.trace.append(("close", None))
        self.closed = True


class _WaitFailureProcess:
    pid = 424260

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.stdout = _TrackedStream(trace)
        self.stderr = _TrackedStream(trace)
        self.returncode = None
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise OSError("synthetic wait failure")
        self.returncode = -signal.SIGKILL
        return self.returncode

    def poll(self):
        return self.returncode


class _DrainProcess:
    pid = 424261

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.stdout = _TrackedStream(trace)
        self.stderr = _TrackedStream(trace)
        self.returncode = None
        self.communicate_timeout = None

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        self.returncode = -signal.SIGTERM
        return self.returncode

    def communicate(self, timeout=None):
        self.communicate_timeout = timeout
        self.trace.append(("communicate", timeout))
        return "out", "err"

    def poll(self):
        return self.returncode


class _ReapedLeaderProcess:
    pid = 424267

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.stdout = _TrackedStream(trace)
        self.stderr = _TrackedStream(trace)
        self.returncode = 0

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        return self.returncode

    def poll(self):
        return self.returncode


class _ForegroundOrphanProcess:
    pid = 424268
    stdout = ""
    stderr = ""
    returncode = 0

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace

    def communicate(self, timeout=None):
        self.trace.append(("communicate", timeout))
        return "done", ""

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        return self.returncode

    def poll(self):
        return self.returncode


class _BackgroundProcess:
    pid = 424262

    def __init__(self) -> None:
        self.stdout = io.StringIO("ready\n")
        self.stderr = None
        self.returncode = None

    def wait(self, timeout=None):
        del timeout
        self.returncode = -signal.SIGTERM
        return self.returncode

    def poll(self):
        return self.returncode


class _PollFailureProcess:
    pid = 424263

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.stdout = _TrackedStream(trace)
        self.stderr = _TrackedStream(trace)
        self.returncode = None

    def communicate(self, timeout=None):
        self.trace.append(("communicate", timeout))
        raise RuntimeError("synthetic communicate failure")

    def poll(self):
        self.trace.append(("poll", None))
        raise OSError("synthetic poll failure")

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        raise OSError("synthetic reap failure")


class _TimeoutProbeFailureProcess:
    pid = 424269
    stdout = ""
    stderr = ""

    def __init__(self, trace: list[tuple[str, object]]) -> None:
        self.trace = trace
        self.returncode = None
        self.communicate_calls = 0

    def communicate(self, timeout=None):
        self.trace.append(("communicate", timeout))
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(("python3", "-V"), timeout)
        return "", ""

    def poll(self):
        self.trace.append(("poll", None))
        return self.returncode

    def wait(self, timeout=None):
        self.trace.append(("wait", timeout))
        self.returncode = -signal.SIGTERM
        return self.returncode


class _BlockingWaitProcess(_BackgroundProcess):
    pid = 424264

    def __init__(self) -> None:
        super().__init__()
        self.release_wait = threading.Event()

    def wait(self, timeout=None):
        del timeout
        self.release_wait.wait()
        self.returncode = -signal.SIGTERM
        return self.returncode


class _BlockingPipe:
    def __init__(self) -> None:
        self._released = threading.Event()
        self.closed = False

    def read(self, size=-1):
        del size
        self._released.wait()
        return ""

    def close(self) -> None:
        self.closed = True
        self._released.set()


class _BlockingReaderProcess(_BackgroundProcess):
    pid = 424265

    def __init__(self) -> None:
        super().__init__()
        self.stdout = _BlockingPipe()


class _ForeignClassProbe:
    def __init__(self) -> None:
        self.class_touches = 0
        self.close_touches = 0

    @property
    def __class__(self):
        self.class_touches += 1
        return str

    def close(self) -> None:
        self.close_touches += 1


class LocalExecutionSupervisorTests(unittest.TestCase):
    @staticmethod
    def _killpg(trace: list[tuple[str, object]], pgid: int, sig: int) -> None:
        del pgid
        trace.append(("signal", sig))
        if sig == 0:
            raise ProcessLookupError

    @staticmethod
    def _close_registered_test_pipe(pipe) -> None:
        pipe.stream.close()

    @staticmethod
    def _read_registered_test_pipe(pipe, size: int):
        return pipe.stream.read(size)

    def test_wait_failure_still_closes_streams_and_returns_typed_evidence(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _WaitFailureProcess(trace)
        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: self._killpg(trace, pgid, sig),
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ):
            evidence, digest, clean, _ = _finalize_process(
                process,
                terminate=True,
            )

        self.assertFalse(clean)
        self.assertFalse(evidence["verified"])
        self.assertTrue(evidence["direct_child_reaped"])
        self.assertEqual(len(digest), 64)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(trace[-1], ("signal", 0))
        self.assertIn(("signal", signal.SIGKILL), trace)
        self.assertTrue(evidence["actions"][0]["attempted"])
        self.assertTrue(evidence["actions"][1]["attempted"])
        with self.assertRaises(TypeError):
            evidence["resources"]["pid"] = 1

    def test_drain_uses_remaining_cleanup_deadline_before_final_probe(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _DrainProcess(trace)
        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: self._killpg(trace, pgid, sig),
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ):
            evidence, _, clean, drained = _finalize_process(
                process,
                terminate=True,
                drain_output=True,
            )

        self.assertTrue(clean)
        self.assertEqual(drained, ("out", "err"))
        self.assertIsInstance(process.communicate_timeout, float)
        self.assertGreater(process.communicate_timeout, 0)
        self.assertLessEqual(process.communicate_timeout, 5)
        self.assertLessEqual(evidence["barrier_duration_seconds"], 5)
        self.assertEqual(trace[-1], ("signal", 0))
        self.assertTrue(evidence["actions"][0]["attempted"])
        self.assertFalse(evidence["actions"][1]["attempted"])
        self.assertEqual(
            evidence["actions"][1]["outcome"],
            "skipped_process_group_absent",
        )

    def test_unknown_stream_is_not_called_and_fails_closed(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _DrainProcess(trace)
        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: self._killpg(trace, pgid, sig),
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=True,
            )

        self.assertFalse(clean)
        self.assertFalse(process.stdout.closed)
        self.assertFalse(process.stderr.closed)
        self.assertEqual(
            evidence["owned_resource_outcomes"]["streams"]["outcome"],
            "unsupported_stream_not_called",
        )

    def test_foreign_class_and_close_callbacks_are_never_touched(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _DrainProcess(trace)
        probe = _ForeignClassProbe()
        process.stdout = probe
        process.stderr = None
        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: self._killpg(trace, pgid, sig),
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=True,
            )

        self.assertFalse(clean)
        self.assertEqual(probe.class_touches, 0)
        self.assertEqual(probe.close_touches, 0)
        self.assertEqual(
            evidence["owned_resource_outcomes"]["streams"]["outcome"],
            "unsupported_stream_not_called",
        )

    def test_exact_stringio_uses_unbound_read_and_close_descriptors(self) -> None:
        poison_calls: list[str] = []
        stream = io.StringIO("ready\n")
        stream.read = lambda size=-1: poison_calls.append("read") or ""
        stream.close = lambda: poison_calls.append("close")
        process = _BackgroundProcess()
        process.stdout = stream
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            self.assertTrue(supervised.reader_done.wait(1))
            supervised.stop()
            self.assertIn("ready", supervised.log_tail())

        self.assertEqual(poison_calls, [])
        self.assertTrue(io.StringIO.closed.__get__(stream, io.StringIO))

    def test_reader_keeps_log_in_memory_until_owner_persists_once(self) -> None:
        process = _BackgroundProcess()
        poisoned_write = mock.Mock(side_effect=AssertionError(
            "reader must not call Path.write_text",
        ))
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ), mock.patch(
            "coding_workflow.local_execution.Path.write_text",
            new=poisoned_write,
        ):
            root = Path(temp)
            log_path = root / "server.log"
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=log_path,
            )
            self.assertTrue(supervised.reader_done.wait(1))
            self.assertFalse(log_path.exists())
            self.assertIn("ready", supervised.log_tail())
            supervised.stop()
            self.assertEqual(log_path.read_text(encoding="utf-8"), "ready\n")

        poisoned_write.assert_not_called()

    def test_log_tail_matches_the_exact_snapshot_persisted_during_a_race(self) -> None:
        process = _BackgroundProcess()
        real_write = os.write
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ):
            root = Path(temp)
            log_path = root / "server.log"
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=log_path,
            )
            self.assertTrue(supervised.reader_done.wait(1))
            advanced = False

            def write_then_advance(fd, payload):
                nonlocal advanced
                written = real_write(fd, payload)
                if not advanced:
                    advanced = True
                    with supervised._output_state.lock:
                        supervised._output_state.bounded_log = sanitize_output(
                            "ready\nlate\n",
                            limit_chars=10_000,
                        )
                return written

            with mock.patch(
                "coding_workflow.local_execution.os.write",
                side_effect=write_then_advance,
            ):
                first_tail = supervised.log_tail(10_000)

            self.assertTrue(advanced)
            self.assertEqual(
                first_tail,
                log_path.read_text(encoding="utf-8"),
            )
            second_tail = supervised.log_tail(10_000)
            self.assertEqual(second_tail, "ready\nlate\n")
            self.assertEqual(
                second_tail,
                log_path.read_text(encoding="utf-8"),
            )
            supervised.stop()

    def test_streaming_output_is_exact_and_memory_bounded(self) -> None:
        raw = "H" * 6000 + "M" * 2000 + "T" * 6000
        collector = _StreamingBoundedOutput(10_000)
        for index in range(0, len(raw), 4096):
            collector.feed(raw[index:index + 4096])
            self.assertLessEqual(collector.retained_chars(), 10_256)
        collector.finish()
        result = collector.snapshot()
        expected = (
            raw[:5000]
            + "\n... [TRUNCATED 4000 CHARS] ...\n"
            + raw[-5000:]
        )
        self.assertEqual(result.text, expected)
        self.assertEqual(result.raw_chars, len(raw))
        self.assertEqual(
            result.raw_sha256,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(result.truncated)

    def test_streaming_redaction_crosses_chunks_without_retaining_secret(self) -> None:
        chunks = (
            "prefix api_",
            "key=FAKE_",
            "SECRET\nsuffix Bea",
            "rer abcdefghijklmnop==\n-----BEGIN RSA PRI",
            "VATE KEY-----raw-private-material-----END RSA PRIVATE KEY-----\n",
        )
        raw = "".join(chunks)
        collector = _StreamingBoundedOutput(10_000)
        for chunk in chunks:
            collector.feed(chunk)
            self.assertLessEqual(collector.retained_chars(), 10_256)
        collector.finish()
        result = collector.snapshot()
        self.assertNotIn("FAKE_SECRET", result.text)
        self.assertNotIn("abcdefghijklmnop", result.text)
        self.assertNotIn("raw-private-material", result.text)
        self.assertIn("api_key=[REDACTED]", result.text)
        self.assertIn("Bearer [REDACTED]", result.text)
        self.assertIn("[REDACTED PRIVATE KEY]", result.text)
        self.assertEqual(result.raw_chars, len(raw))
        self.assertEqual(
            result.raw_sha256,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )

    def test_streaming_secret_body_has_constant_retained_memory(self) -> None:
        collector = _StreamingBoundedOutput(10_000)
        collector.feed("api_key=")
        for _ in range(32):
            collector.feed("S" * 4096)
            self.assertLessEqual(collector.retained_chars(), 10_256)
        collector.finish()
        result = collector.snapshot()
        self.assertEqual(result.text, "api_key=[REDACTED]")
        self.assertEqual(result.raw_chars, 8 + 32 * 4096)

    def test_quoted_assignment_is_suppressed_until_matching_quote(self) -> None:
        for chunks in (
            (
                'prefix token="alpha ',
                "beta,",
                "gamma;del",
                'ta" suffix',
            ),
            (
                "prefix password='alpha ",
                "beta,",
                "gamma;del",
                "ta' suffix",
            ),
        ):
            collector = _StreamingBoundedOutput(10_000)
            for chunk in chunks:
                collector.feed(chunk)
            collector.finish()
            streaming = collector.snapshot()
            foreground = sanitize_output("".join(chunks), limit_chars=10_000)

            for result in (streaming, foreground):
                expected_key = "token" if "token" in chunks[0] else "password"
                self.assertEqual(
                    result.text,
                    f"prefix {expected_key}=[REDACTED] suffix",
                )
                self.assertNotIn("alpha", result.text)
                self.assertNotIn("beta", result.text)
                self.assertNotIn("gamma", result.text)
                self.assertNotIn("delta", result.text)

    def test_escaped_quotes_json_keys_and_unterminated_values_never_leak(self) -> None:
        cases = (
            ('prefix {"token":"alpha\\\"still-secret"} suffix', "still-secret"),
            ("prefix {'password':'alpha\\'still-secret'} suffix", "still-secret"),
            ('prefix {"api_key" : "alpha\\\\" suffix', "alpha"),
            ('prefix {"access_token":"alpha-secret"} suffix', "alpha-secret"),
            ("print('token=alpha-secret suffix')", "alpha-secret"),
        )
        for raw, secret_fragment in cases:
            expected = sanitize_output(raw, limit_chars=10_000)
            self.assertNotIn(secret_fragment, expected.text)
            self.assertEqual(redact_text(raw), expected.text)
            for split in range(1, len(raw)):
                collector = _StreamingBoundedOutput(10_000)
                collector.feed(raw[:split])
                collector.feed(raw[split:])
                collector.finish()
                streamed = collector.snapshot()
                self.assertEqual(streamed.text, expected.text, (raw, split))
                self.assertNotIn(secret_fragment, streamed.text, (raw, split))

            collector = _StreamingBoundedOutput(10_000)
            for character in raw:
                collector.feed(character)
            collector.finish()
            self.assertEqual(collector.snapshot().text, expected.text)

        unterminated = 'prefix {"token":"alpha\\\"still-secret'
        collector = _StreamingBoundedOutput(10_000)
        for character in unterminated:
            collector.feed(character)
        collector.finish()
        result = collector.snapshot()
        self.assertNotIn("alpha", result.text)
        self.assertNotIn("still-secret", result.text)
        self.assertIn("token=[REDACTED]", result.text)

    def test_spawn_pins_utf8_replacement_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            prepared, _ = self._prepared_core(Path(temp))
            with mock.patch(
                "coding_workflow.local_execution.subprocess.Popen",
                return_value=object(),
            ) as popen:
                _spawn(prepared, {"PATH": "/usr/bin"}, background=False)

        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_reaped_leader_with_live_owned_group_is_revoked_before_verify(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _ReapedLeaderProcess(trace)
        killed = False

        def owned_group(pgid: int, sig: int) -> None:
            nonlocal killed
            self.assertEqual(pgid, process.pid)
            trace.append(("signal", sig))
            if sig == signal.SIGKILL:
                killed = True
            elif sig == 0 and killed:
                raise ProcessLookupError

        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=owned_group,
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ), mock.patch(
            "coding_workflow.local_execution._TERM_GRACE_SECONDS",
            0.0,
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=False,
            )

        self.assertTrue(clean)
        actions = {item["phase"]: item for item in evidence["actions"]}
        self.assertEqual(actions["term"]["outcome"], "signal_sent")
        self.assertTrue(actions["term"]["attempted"])
        self.assertEqual(actions["kill"]["outcome"], "signal_sent")
        self.assertTrue(actions["kill"]["attempted"])
        self.assertEqual(actions["wait_reap"]["attempts"], 1)
        self.assertEqual(actions["verify"]["outcome"], "process_group_absent")
        self.assertEqual(trace[-1], ("signal", 0))

    def test_reaped_leader_descendant_gets_term_grace_and_avoids_kill(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _ReapedLeaderProcess(trace)
        clock = 0.0
        term_sent = False
        waits: list[float] = []

        def monotonic() -> float:
            return clock

        def wait_poll(seconds: float) -> None:
            nonlocal clock
            waits.append(seconds)
            clock += seconds

        def owned_group(pgid: int, sig: int) -> None:
            nonlocal term_sent
            self.assertEqual(pgid, process.pid)
            trace.append(("signal", sig))
            if sig == signal.SIGTERM:
                term_sent = True
            elif sig == signal.SIGKILL:
                self.fail("descendant that exited during TERM grace was killed")
            elif sig == 0 and term_sent and clock >= 0.05:
                raise ProcessLookupError

        with mock.patch(
            "coding_workflow.local_execution.time.monotonic",
            side_effect=monotonic,
        ), mock.patch(
            "coding_workflow.local_execution._wait_cleanup_poll",
            side_effect=wait_poll,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=owned_group,
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=False,
            )

        self.assertTrue(clean)
        self.assertAlmostEqual(sum(waits), 0.05, places=6)
        self.assertNotIn(("signal", signal.SIGKILL), trace)
        actions = {item["phase"]: item for item in evidence["actions"]}
        self.assertTrue(actions["term"]["attempted"])
        self.assertFalse(actions["kill"]["attempted"])
        self.assertEqual(
            actions["kill"]["outcome"],
            "skipped_process_group_absent",
        )
        self.assertEqual(actions["wait_reap"]["attempts"], 1)
        self.assertEqual(trace[-1], ("signal", 0))

    def test_reaped_leader_kills_only_after_full_term_grace(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _ReapedLeaderProcess(trace)
        clock = 0.0
        killed = False
        waits: list[float] = []

        def monotonic() -> float:
            return clock

        def wait_poll(seconds: float) -> None:
            nonlocal clock
            waits.append(seconds)
            clock += seconds

        def owned_group(pgid: int, sig: int) -> None:
            nonlocal killed
            self.assertEqual(pgid, process.pid)
            trace.append(("signal", sig))
            if sig == signal.SIGKILL:
                self.assertGreaterEqual(clock, 1.0)
                killed = True
            elif sig == 0 and killed:
                raise ProcessLookupError

        with mock.patch(
            "coding_workflow.local_execution.time.monotonic",
            side_effect=monotonic,
        ), mock.patch(
            "coding_workflow.local_execution._wait_cleanup_poll",
            side_effect=wait_poll,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=owned_group,
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=False,
            )

        self.assertTrue(clean)
        self.assertAlmostEqual(sum(waits), 1.0, places=6)
        self.assertLessEqual(max(waits), 0.05)
        actions = {item["phase"]: item for item in evidence["actions"]}
        self.assertTrue(actions["term"]["attempted"])
        self.assertTrue(actions["kill"]["attempted"])
        self.assertEqual(actions["kill"]["outcome"], "signal_sent")
        self.assertEqual(actions["wait_reap"]["attempts"], 1)
        self.assertEqual(trace[-1], ("signal", 0))

    def test_background_natural_exit_revokes_live_owned_group(self) -> None:
        process = _BackgroundProcess()
        process.returncode = 0
        signals: list[int] = []
        killed = False

        def owned_group(pgid: int, sig: int) -> None:
            nonlocal killed
            self.assertEqual(pgid, process.pid)
            signals.append(sig)
            if sig == signal.SIGKILL:
                killed = True
            elif sig == 0 and killed:
                raise ProcessLookupError

        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=owned_group,
        ), mock.patch(
            "coding_workflow.local_execution._TERM_GRACE_SECONDS",
            0.0,
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            self.assertTrue(supervised._watchdog_done.wait(1))
            supervised.stop()

        self.assertTrue(supervised.cleanup_evidence["verified"])
        self.assertIn(signal.SIGTERM, signals)
        self.assertIn(signal.SIGKILL, signals)
        self.assertEqual(signals[-1], 0)

    def test_foreground_natural_exit_revokes_live_owned_group(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _ForegroundOrphanProcess(trace)
        killed = False

        def owned_group(pgid: int, sig: int) -> None:
            nonlocal killed
            self.assertEqual(pgid, process.pid)
            trace.append(("signal", sig))
            if sig == signal.SIGKILL:
                killed = True
            elif sig == 0 and killed:
                raise ProcessLookupError

        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=owned_group,
        ), mock.patch(
            "coding_workflow.local_execution._TERM_GRACE_SECONDS",
            0.0,
        ):
            prepared, token = self._prepared_core(Path(temp))
            outcome = run_prepared(prepared, trusted_local=token)

        self.assertEqual(outcome.exit_code, 0)
        self.assertTrue(outcome.cleanup_evidence["verified"])
        actions = {
            item["phase"]: item for item in outcome.cleanup_evidence["actions"]
        }
        self.assertTrue(actions["term"]["attempted"])
        self.assertTrue(actions["kill"]["attempted"])
        self.assertEqual(actions["verify"]["outcome"], "process_group_absent")
        self.assertEqual(trace[-1], ("signal", 0))

    def test_natural_exit_records_skipped_term_and_kill_phases(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _DrainProcess(trace)
        with mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: self._killpg(trace, pgid, sig),
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ):
            evidence, _, clean, _ = _finalize_process(
                process,
                terminate=False,
            )

        self.assertTrue(clean)
        self.assertEqual(
            [action["phase"] for action in evidence["actions"]],
            ["term", "kill", "wait_reap", "verify"],
        )
        self.assertFalse(evidence["actions"][0]["attempted"])
        self.assertEqual(
            evidence["actions"][0]["outcome"],
            "skipped_process_group_absent",
        )
        self.assertFalse(evidence["actions"][1]["attempted"])
        self.assertEqual(
            evidence["actions"][1]["outcome"],
            "skipped_process_group_absent",
        )

    @staticmethod
    def _prepared_core(root: Path):
        prepared = prepare_execution(
            profile_id=PROFILE_CORE,
            workspace_root=root,
            executable="/usr/bin/python3",
            command=("python3", "-V"),
            wall_deadline_seconds=1,
            output_limit_chars=10_000,
            output_kind="stdout_stderr",
            python_profile=True,
        )
        token = issue_trusted_local_confirmation(
            workspace_digest=prepared.workspace_digest,
            input_digest=prepared.input_digest,
            profile_digest=prepared.profile_digest,
            expires_at_monotonic=10**12,
        )
        return prepared, token

    def test_post_spawn_poll_failure_enters_finalizer_and_quarantine(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _PollFailureProcess(trace)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: trace.append(("signal", sig)),
        ):
            prepared, token = self._prepared_core(Path(temp))
            with self.assertRaises(LocalExecutionError) as raised:
                run_prepared(prepared, trusted_local=token)

        self.assertEqual(raised.exception.code, CLEANUP_FAILED)
        self.assertTrue(raised.exception.quarantine_id)
        self.assertGreater(raised.exception.quarantine_generation, 0)
        self.assertIn(("signal", signal.SIGTERM), trace)
        self.assertIn(("signal", signal.SIGKILL), trace)

    def test_quarantine_fences_same_workspace_before_second_spawn(self) -> None:
        trace: list[tuple[str, object]] = []
        process = _TimeoutProbeFailureProcess(trace)
        spawn = mock.Mock(return_value=process)

        def signal_group(_pgid: int, sig: int) -> None:
            trace.append(("signal", sig))
            if sig == 0:
                raise PermissionError("synthetic ownership probe failure")

        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            new=spawn,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=signal_group,
        ):
            root = Path(temp)
            prepared, token = self._prepared_core(root)
            with self.assertRaises(LocalExecutionError) as first:
                run_prepared(prepared, trusted_local=token)

            second_prepared, second_token = self._prepared_core(root)
            with self.assertRaises(LocalExecutionError) as blocked:
                run_prepared(second_prepared, trusted_local=second_token)

        self.assertEqual(first.exception.code, CLEANUP_FAILED)
        self.assertTrue(first.exception.quarantine_id)
        self.assertGreater(first.exception.quarantine_generation, 0)
        self.assertFalse(first.exception.cleanup_evidence["verified"])
        self.assertEqual(
            first.exception.cleanup_evidence["actions"][-1]["outcome"],
            "probe_failed",
        )
        self.assertEqual(len(first.exception.cleanup_evidence_digest), 64)
        self.assertEqual(blocked.exception.code, SANDBOX_REQUIRED)
        self.assertEqual(spawn.call_count, 1)
        self.assertIn(("signal", signal.SIGTERM), trace)
        self.assertIn(("signal", signal.SIGKILL), trace)

    def test_private_cleanup_uses_captured_root_and_unbound_method(self) -> None:
        poison_calls: list[str] = []
        private = _private_environment(python_profile=True)
        original_root = private.root

        def cleanup_private() -> None:
            object.__setattr__(private, "root", original_root)
            type(private).close(private)

        self.addCleanup(cleanup_private)

        class MutatingProcess:
            pid = 424266
            stdout = ""
            stderr = ""
            returncode = None

            def communicate(self, timeout=None):
                del timeout
                object.__setattr__(private, "root", foreign_root)
                self.returncode = 0
                return "", ""

            def wait(self, timeout=None):
                del timeout
                self.returncode = 0
                return 0

            def poll(self):
                return self.returncode

        object.__setattr__(
            private,
            "close",
            lambda: poison_calls.append("close") or False,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            foreign_root = root / "must-not-remove"
            foreign_root.mkdir()
            with mock.patch(
                "coding_workflow.local_execution._private_environment",
                return_value=private,
            ), mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=MutatingProcess(),
            ), mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=lambda pgid, sig: (
                    (_ for _ in ()).throw(ProcessLookupError())
                    if sig == 0
                    else None
                ),
            ):
                prepared, token = self._prepared_core(root)
                outcome = run_prepared(prepared, trusted_local=token)

            self.assertEqual(outcome.exit_code, 0)
            self.assertTrue(foreign_root.exists())
        self.assertFalse(original_root.exists())
        self.assertEqual(poison_calls, [])

    @staticmethod
    def _prepared_dev(root: Path, deadline: float):
        prepared = prepare_execution(
            profile_id=PROFILE_VISIONFORGE_DEV,
            workspace_root=root,
            executable="/usr/bin/pnpm",
            command=("pnpm", "run", "dev", "--port", "4173"),
            wall_deadline_seconds=deadline,
            output_limit_chars=10_000,
            output_kind="server_log",
        )
        token = issue_trusted_local_confirmation(
            workspace_digest=prepared.workspace_digest,
            input_digest=prepared.input_digest,
            profile_digest=prepared.profile_digest,
            expires_at_monotonic=10**12,
        )
        return prepared, token

    def test_background_wall_deadline_cleans_without_explicit_stop(self) -> None:
        signals: list[int] = []
        process = _BackgroundProcess()
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else signals.append(sig)
            ),
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 0.03)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            private_root = supervised.private_environment.root
            self.assertTrue(supervised._cleanup_done.wait(1))
            self.assertTrue(supervised._watchdog_done.is_set())
            supervised.stop()
            self.assertFalse(supervised.running)
            self.assertFalse(private_root.exists())
            self.assertTrue(supervised.cleanup_evidence["verified"])
            self.assertEqual(signals.count(signal.SIGTERM), 1)

    def test_cleanup_evidence_is_deeply_immutable_in_all_states(self) -> None:
        process = _BlockingWaitProcess()
        self.addCleanup(process.release_wait.set)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ), mock.patch(
            "coding_workflow.local_execution._CLEANUP_BARRIER_SECONDS",
            0.05,
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            with self.assertRaises(TypeError):
                supervised.cleanup_evidence["state"] = "mutable"

            supervised.request_stop("test")
            self.assertTrue(process.release_wait.wait(0.01) is False)
            running = supervised.cleanup_evidence
            self.assertEqual(running["status"], "running")
            with self.assertRaises(TypeError):
                running["actions"][0]["outcome"] = "forged"

            with self.assertRaises(LocalExecutionError):
                supervised.stop()
            process.release_wait.set()
            self.assertTrue(supervised._watchdog_done.wait(1))
            terminal = supervised.cleanup_evidence
            self.assertEqual(terminal["status"], "terminal")
            with self.assertRaises(TypeError):
                terminal["resources"]["pid"] = 1

    def test_stop_timeout_publishes_one_quarantine_generation(self) -> None:
        process = _BlockingWaitProcess()
        self.addCleanup(process.release_wait.set)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ), mock.patch(
            "coding_workflow.local_execution._CLEANUP_BARRIER_SECONDS",
            0.05,
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            with self.assertRaises(LocalExecutionError) as first:
                supervised.stop()
            self.assertEqual(first.exception.code, CLEANUP_FAILED)
            self.assertTrue(first.exception.quarantine_id)
            first_identity = (
                first.exception.quarantine_id,
                first.exception.quarantine_generation,
            )

            process.release_wait.set()
            self.assertTrue(supervised._watchdog_done.wait(1))
            with self.assertRaises(LocalExecutionError) as second:
                supervised.stop()
            self.assertEqual(
                (
                    second.exception.quarantine_id,
                    second.exception.quarantine_generation,
                ),
                first_identity,
            )

    def test_cleanup_claim_fences_new_workspace_admission(self) -> None:
        process = _BlockingWaitProcess()
        self.addCleanup(process.release_wait.set)
        spawn = mock.Mock(return_value=process)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            new=spawn,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            supervised.request_stop("test")

            next_prepared, next_token = self._prepared_core(root)
            with self.assertRaises(LocalExecutionError) as blocked:
                run_prepared(next_prepared, trusted_local=next_token)
            self.assertEqual(blocked.exception.code, SANDBOX_REQUIRED)
            self.assertEqual(spawn.call_count, 1)

            process.release_wait.set()
            supervised.stop()
            self.assertTrue(supervised.cleanup_terminal)

    def test_reader_does_not_retain_supervisor_and_abandon_converges(self) -> None:
        process = _BlockingReaderProcess()
        self.addCleanup(process.stdout.close)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
            create=True,
        ), mock.patch(
            "coding_workflow.local_execution._read_runtime_owned_pipe",
            side_effect=self._read_registered_test_pipe,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            side_effect=self._close_registered_test_pipe,
        ), mock.patch(
            "coding_workflow.local_execution._CLEANUP_BARRIER_SECONDS",
            0.2,
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            closure_values = []
            target = supervised.reader._target
            if target is not None and target.__closure__ is not None:
                closure_values = [cell.cell_contents for cell in target.__closure__]
            self.assertFalse(any(
                value is supervised
                or (
                    isinstance(value, dict)
                    and supervised in value.values()
                )
                for value in closure_values
            ))

            runner = BrowserProcessRunner(
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
                workspace_root=root,
            )
            managed = ManagedProcess(supervised, runner)
            reference = weakref.ref(managed)
            del managed
            gc.collect()
            self.assertIsNone(reference())
            self.assertTrue(supervised._watchdog_done.wait(1))
            self.assertFalse(supervised.reader.is_alive())
            self.assertTrue(process.stdout.closed)
            self.assertTrue(supervised.cleanup_terminal)

    def test_cleanup_terminal_stays_false_while_reader_is_alive(self) -> None:
        process = _BlockingReaderProcess()
        self.addCleanup(process.stdout.close)
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ), mock.patch(
            "coding_workflow.local_execution._is_runtime_owned_pipe",
            return_value=True,
        ), mock.patch(
            "coding_workflow.local_execution._read_runtime_owned_pipe",
            side_effect=self._read_registered_test_pipe,
        ), mock.patch(
            "coding_workflow.local_execution._close_runtime_owned_pipe",
            return_value=None,
        ), mock.patch(
            "coding_workflow.local_execution._CLEANUP_BARRIER_SECONDS",
            0.05,
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 30)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            with self.assertRaises(LocalExecutionError):
                supervised.stop()
            self.assertTrue(supervised._watchdog_done.wait(1))
            assert supervised._watchdog is not None
            supervised._watchdog.join(timeout=1)
            self.assertTrue(supervised.reader.is_alive())
            self.assertFalse(supervised.cleanup_terminal)

            process.stdout.close()
            self.assertTrue(supervised.reader_done.wait(1))
            supervised.reader.join(timeout=1)
            self.assertFalse(supervised.reader.is_alive())
            self.assertTrue(supervised.cleanup_terminal)

    def test_abandoned_public_handle_requests_supervisor_cleanup(self) -> None:
        process = _BackgroundProcess()
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "coding_workflow.local_execution._spawn",
            return_value=process,
        ), mock.patch(
            "coding_workflow.local_execution.os.killpg",
            side_effect=lambda pgid, sig: (
                (_ for _ in ()).throw(ProcessLookupError())
                if sig == 0
                else None
            ),
        ):
            root = Path(temp)
            prepared, token = self._prepared_dev(root, 1)
            supervised = start_prepared_background(
                prepared,
                trusted_local=token,
                log_path=root / "server.log",
            )
            runner = BrowserProcessRunner(
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
                workspace_root=root,
            )
            managed = ManagedProcess(supervised, runner)
            reference = weakref.ref(managed)
            del managed
            gc.collect()
            self.assertIsNone(reference())
            self.assertTrue(supervised._cleanup_done.wait(1))
            self.assertTrue(supervised._watchdog_done.is_set())
            self.assertFalse(supervised.running)


if __name__ == "__main__":
    unittest.main()
