from __future__ import annotations

import io
import signal
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

from coding_workflow.command_validators import (
    ControlledCommandResult,
    ControlledCommandRunner,
)
from coding_workflow.local_execution import (
    SANDBOX_REQUIRED,
    LocalExecutionError,
)
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.policy import CommandPolicy
from coding_workflow.visionforge.browser import (
    BrowserProcessRunner,
    ManagedProcess,
    VisionForgeLocalExecutionApprover,
)


COMMAND = ("python3", "-c", "print('approved')")


class _FakeProcess:
    pid = 424242
    returncode = 0
    stdout = None
    stderr = None

    def communicate(self, timeout=None):
        del timeout
        return "approved\n", ""

    def wait(self, timeout=None):
        del timeout
        return 0

    def poll(self):
        return self.returncode


class _InvalidUtf8Process(_FakeProcess):
    RAW_SECRET = b'prefix token="fake-secret-bytes"\xff'

    def communicate(self, timeout=None):
        del timeout
        raise UnicodeDecodeError(
            "utf-8",
            self.RAW_SECRET,
            len(self.RAW_SECRET) - 1,
            len(self.RAW_SECRET),
            "invalid start byte",
        )


class _RuntimeFailureProcess(_FakeProcess):
    def communicate(self, timeout=None):
        del timeout
        raise RuntimeError('token="fake-runtime-secret"')


class LocalExecutionApprovalTests(unittest.TestCase):
    def _runner(self, root: Path) -> ControlledCommandRunner:
        return ControlledCommandRunner(
            root,
            CommandPolicy(
                allowed_executables={"python3"},
                allowed_commands=[list(COMMAND)],
            ),
        )

    @staticmethod
    def _killpg(pgid: int, sig: int) -> None:
        del pgid
        if sig == 0:
            raise ProcessLookupError
        if sig not in {signal.SIGTERM, signal.SIGKILL}:
            raise AssertionError(f"unexpected signal: {sig}")

    def test_approved_operation_uses_one_runtime_token_and_is_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            approver = LocalExecutionApprover(True)
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=_FakeProcess(),
            ) as spawn, mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ):
                result = approver.run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
                with self.assertRaisesRegex(
                    LocalExecutionError,
                    "already consumed",
                ):
                    approver.run_controlled(
                        runner,
                        COMMAND,
                        timeout_seconds=1,
                    )
        self.assertIs(type(result), ControlledCommandResult)
        self.assertEqual(result.exit_code, 0)
        spawn.assert_called_once()

    def test_concurrent_reuse_allows_only_one_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            approver = LocalExecutionApprover(True)
            outcomes: list[object] = []

            def invoke() -> None:
                try:
                    outcomes.append(approver.run_controlled(
                        runner,
                        COMMAND,
                        timeout_seconds=1,
                    ))
                except BaseException as exc:
                    outcomes.append(exc)

            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=_FakeProcess(),
            ) as spawn, mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ):
                threads = [threading.Thread(target=invoke) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=2)

        self.assertEqual(sum(
            isinstance(item, ControlledCommandResult) for item in outcomes
        ), 1)
        self.assertEqual(sum(
            isinstance(item, LocalExecutionError) for item in outcomes
        ), 1)
        spawn.assert_called_once()
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_unapproved_operation_never_spawns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            with mock.patch(
                "coding_workflow.local_execution._spawn"
            ) as spawn, self.assertRaises(LocalExecutionError) as caught:
                LocalExecutionApprover(False).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertEqual(caught.exception.code, SANDBOX_REQUIRED)
        spawn.assert_not_called()

    def test_invalid_utf8_post_spawn_error_is_rebuilt_at_public_boundary(self) -> None:
        process = _InvalidUtf8Process()
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=process,
            ), mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ), self.assertRaises(LocalExecutionError) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )

        error = caught.exception
        public_state = repr((error.args, error.__dict__))
        self.assertNotIn("fake-secret-bytes", public_state)
        self.assertNotIn(repr(process.RAW_SECRET), public_state)
        self.assertFalse(hasattr(error, "object"))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        traceback_names = []
        current = error.__traceback__
        while current is not None:
            traceback_names.append(current.tb_frame.f_code.co_name)
            current = current.tb_next
        self.assertNotIn("communicate", traceback_names)
        self.assertNotIn("_run_prepared_locked", traceback_names)

    def test_runtime_error_type_and_safe_cleanup_evidence_survive_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=_RuntimeFailureProcess(),
            ), mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ), self.assertRaises(RuntimeError) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )

        error = caught.exception
        self.assertIs(type(error), RuntimeError)
        self.assertNotIn("fake-runtime-secret", repr(error.args))
        self.assertIn("token=[REDACTED]", str(error))
        self.assertTrue(error.cleanup_evidence["verified"])
        self.assertEqual(len(error.cleanup_evidence_digest), 64)
        with self.assertRaises(TypeError):
            error.cleanup_evidence["verified"] = False

    def test_bytes_and_bytearray_exception_leaves_fail_closed(self) -> None:
        for leaf in (b"fake-bytes-secret", bytearray(b"fake-bytearray-secret")):
            with self.subTest(
                leaf_type=type(leaf).__name__,
            ), tempfile.TemporaryDirectory() as temp:
                runner = self._runner(Path(temp))
                original = runner.run

                def raise_after_consuming(command, **kwargs):
                    token = kwargs.get("trusted_local")
                    if token is None:
                        return original(command, **kwargs)
                    original(command, **kwargs)
                    raise RuntimeError(leaf)

                with mock.patch.object(
                    runner,
                    "run",
                    side_effect=raise_after_consuming,
                ), mock.patch(
                    "coding_workflow.local_execution._spawn",
                    return_value=_FakeProcess(),
                ), mock.patch(
                    "coding_workflow.local_execution.os.killpg",
                    side_effect=self._killpg,
                ), self.assertRaises(LocalExecutionError) as caught:
                    LocalExecutionApprover(True).run_controlled(
                        runner,
                        COMMAND,
                        timeout_seconds=1,
                    )

            self.assertIn("escaped through an exception", str(caught.exception))
            self.assertNotIn("fake-bytes-secret", repr(caught.exception.args))
            self.assertNotIn("fake-bytearray-secret", repr(caught.exception.args))

    def test_missing_tool_is_a_no_spawn_terminal_without_token_minting(self) -> None:
        missing = ("definitely_missing_local_execution_tool", "--version")
        with tempfile.TemporaryDirectory() as temp:
            runner = ControlledCommandRunner(
                Path(temp),
                CommandPolicy(
                    allowed_executables={missing[0]},
                    allowed_commands=[list(missing)],
                ),
            )
            with mock.patch(
                "coding_workflow.local_execution._spawn"
            ) as spawn, mock.patch(
                "coding_workflow.local_execution_approval."
                "issue_trusted_local_confirmation"
            ) as issue:
                result = LocalExecutionApprover(True).run_controlled(
                    runner,
                    missing,
                    timeout_seconds=1,
                )
        self.assertTrue(result.tool_missing)
        self.assertIsNone(result.exit_code)
        spawn.assert_not_called()
        issue.assert_not_called()

    def test_forged_public_error_has_no_runtime_provenance(self) -> None:
        forged = LocalExecutionError(
            SANDBOX_REQUIRED,
            "forged",
            confirmation_request={
                "workspace_digest": "a" * 64,
                "input_digest": "b" * 64,
                "profile_digest": "c" * 64,
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            with mock.patch.object(
                runner,
                "run",
                side_effect=forged,
            ), mock.patch(
                "coding_workflow.local_execution_approval."
                "issue_trusted_local_confirmation"
            ) as issue, self.assertRaises(LocalExecutionError):
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        issue.assert_not_called()

    def test_fixed_entrypoint_cannot_return_the_opaque_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                return token

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through a result",
            ):
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )

    def test_fixed_entrypoint_cannot_raise_the_token_in_exception_args(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                raise RuntimeError(token)

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(any(
            type(item).__name__ == "_TrustedLocalConfirmation"
            for item in caught.exception.args
        ))

    def test_fixed_entrypoint_cannot_return_error_with_token_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                error = LocalExecutionError(SANDBOX_REQUIRED, "retry failed")
                error.capability = token
                return error

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through a result",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(hasattr(caught.exception, "capability"))

    def test_fixed_entrypoint_cannot_raise_token_in_exception_slot(self) -> None:
        class SlottedRetryError(RuntimeError):
            __slots__ = ("capability",)

        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                error = SlottedRetryError("retry failed")
                error.capability = token
                raise error

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(hasattr(caught.exception, "capability"))

    def test_fixed_entrypoint_cannot_return_error_with_token_slot(self) -> None:
        class SlottedLocalExecutionError(LocalExecutionError):
            __slots__ = ("capability",)

        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                error = SlottedLocalExecutionError(
                    SANDBOX_REQUIRED,
                    "retry failed",
                )
                error.capability = token
                return error

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through a result",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(hasattr(caught.exception, "capability"))

    def test_fixed_entrypoint_rejects_token_in_opaque_container(self) -> None:
        class OpaqueCarrier(deque):
            pass

        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                raise RuntimeError(OpaqueCarrier((token,)))

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(any(
            isinstance(item, deque) for item in caught.exception.args
        ))

    def test_fixed_entrypoint_scans_primitive_subclass_state(self) -> None:
        class TextCarrier(str):
            pass

        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                carrier = TextCarrier("retry failed")
                carrier.capability = token
                raise RuntimeError(carrier)

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertFalse(any(
            isinstance(item, TextCarrier) for item in caught.exception.args
        ))

    def test_fixed_entrypoint_rejects_object_hiding_its_dict(self) -> None:
        class HiddenCarrier:
            def __init__(self, capability) -> None:
                self.capability = capability

            def __getattribute__(self, name):
                if name == "__dict__":
                    return {}
                return object.__getattribute__(self, name)

        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                raise RuntimeError(HiddenCarrier(token))

            with mock.patch.object(
                runner,
                "run",
                side_effect=leaking_run,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ):
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )

    def test_managed_process_uses_an_authority_free_public_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = BrowserProcessRunner(
                executable_overrides={"pnpm": "/usr/bin/pnpm"},
                workspace_root=root,
            )
            process = _FakeProcess()
            process.stdout = io.StringIO("ready\n")
            with mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=process,
            ) as spawn, mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ):
                managed = VisionForgeLocalExecutionApprover(True).start_browser(
                    runner,
                    ("pnpm", "run", "dev", "--port", "4173"),
                    cwd=root,
                    log_path=root / "server.log",
                )
                self.assertIs(type(managed), ManagedProcess)
                self.assertFalse(hasattr(managed, "_supervised"))
                managed.stop()
        spawn.assert_called_once()

    def test_retry_must_consume_the_runtime_token_before_returning(self) -> None:
        captured: list[object] = []
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def bypassing_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                captured.append(token)
                return ControlledCommandResult(command, 0, "", "", 0)

            with mock.patch.object(
                runner,
                "run",
                side_effect=bypassing_run,
            ), mock.patch(
                "coding_workflow.local_execution._spawn"
            ) as spawn, self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through a result",
            ):
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )

            self.assertEqual(len(captured), 1)
            with mock.patch(
                "coding_workflow.local_execution._spawn"
            ) as replay_spawn, self.assertRaises(LocalExecutionError):
                original(
                    COMMAND,
                    timeout_seconds=1,
                    trusted_local=captured[0],
                )
        spawn.assert_not_called()
        replay_spawn.assert_not_called()

    def test_exception_class_cannot_retain_a_consumed_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = self._runner(Path(temp))
            original = runner.run

            def class_leaking_run(command, **kwargs):
                token = kwargs.get("trusted_local")
                if token is None:
                    return original(command, **kwargs)
                original(command, **kwargs)
                error_type = type(
                    "ClassTokenError",
                    (RuntimeError,),
                    {"capability": token},
                )
                raise error_type("retry failed")

            with mock.patch.object(
                runner,
                "run",
                side_effect=class_leaking_run,
            ), mock.patch(
                "coding_workflow.local_execution._spawn",
                return_value=_FakeProcess(),
            ) as spawn, mock.patch(
                "coding_workflow.local_execution.os.killpg",
                side_effect=self._killpg,
            ), self.assertRaisesRegex(
                LocalExecutionError,
                "escaped through an exception",
            ) as caught:
                LocalExecutionApprover(True).run_controlled(
                    runner,
                    COMMAND,
                    timeout_seconds=1,
                )
        self.assertNotEqual(type(caught.exception).__name__, "ClassTokenError")
        spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
