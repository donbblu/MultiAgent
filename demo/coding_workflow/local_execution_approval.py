from __future__ import annotations

import math
import threading
import time
from dataclasses import fields, is_dataclass
from types import MappingProxyType, MemberDescriptorType
from typing import Mapping

from .harness.lifecycle import TaskCancelledError
from .local_execution import (
    SANDBOX_REQUIRED,
    LocalExecutionError,
    _TrustedLocalConfirmation,
    _retire_trusted_local_confirmation,
    consume_runtime_admission_challenge,
    issue_trusted_local_confirmation,
    sanitize_output,
)


_CONFIRMATION_KEYS = frozenset({
    "workspace_digest",
    "input_digest",
    "profile_digest",
})


class LocalExecutionManagedResult:
    """Core-owned interface for an authority-free managed public result."""

    __slots__ = ()

    def local_execution_approval_state(self) -> Mapping[str, object]:
        raise NotImplementedError

    def discard_local_execution_result(self) -> None:
        raise NotImplementedError


def _safe_graph_is_unsafe(
    value: object,
    seen: set[int] | None = None,
) -> bool:
    if isinstance(value, _TrustedLocalConfirmation):
        return True
    if type(value) in {type(None), str, bool, int, float}:
        return False
    active = seen if seen is not None else set()
    identifier = id(value)
    if identifier in active:
        return True
    active.add(identifier)
    try:
        if type(value) in {dict, MappingProxyType}:
            return any(
                _safe_graph_is_unsafe(key, active)
                or _safe_graph_is_unsafe(item, active)
                for key, item in value.items()
            )
        if type(value) in {tuple, list, set, frozenset}:
            return any(_safe_graph_is_unsafe(item, active) for item in value)
        if isinstance(value, BaseException):
            return _exception_state_is_unsafe(value, active)
        return True
    finally:
        active.remove(identifier)


def _exception_state_is_unsafe(
    value: BaseException,
    seen: set[int],
) -> bool:
    try:
        args = object.__getattribute__(value, "args")
        attributes = object.__getattribute__(value, "__dict__")
        cause = object.__getattribute__(value, "__cause__")
        context = object.__getattribute__(value, "__context__")
    except (AttributeError, TypeError):
        return True
    if _safe_graph_is_unsafe(args, seen):
        return True
    if _safe_graph_is_unsafe(attributes, seen):
        return True
    if cause is not None and _safe_graph_is_unsafe(cause, seen):
        return True
    if context is not None and _safe_graph_is_unsafe(context, seen):
        return True
    for owner in type(value).__mro__:
        for descriptor in owner.__dict__.values():
            if not isinstance(descriptor, MemberDescriptorType):
                continue
            try:
                slot_value = descriptor.__get__(value, type(value))
            except (AttributeError, TypeError):
                continue
            if _safe_graph_is_unsafe(slot_value, seen):
                return True
    return _exception_class_state_is_unsafe(value, seen)


def _exception_class_state_is_unsafe(
    value: BaseException,
    seen: set[int],
) -> bool:
    ignored_names = {
        "__classcell__",
        "__dict__",
        "__doc__",
        "__module__",
        "__slots__",
        "__weakref__",
    }
    for owner in type(value).__mro__:
        if owner.__module__ == "builtins" or owner is LocalExecutionError:
            continue
        try:
            namespace = type.__getattribute__(owner, "__dict__")
        except (AttributeError, TypeError):
            return True
        for name, item in namespace.items():
            if name in ignored_names or isinstance(item, MemberDescriptorType):
                continue
            if _safe_graph_is_unsafe(item, seen):
                return True
    return False


def _result_state_is_unsafe(result: object, expected_type: type) -> bool:
    if type(result) is not expected_type:
        return True
    if is_dataclass(expected_type):
        expected_fields = {item.name for item in fields(expected_type)}
        try:
            attributes = object.__getattribute__(result, "__dict__")
        except (AttributeError, TypeError):
            return True
        if set(attributes) != expected_fields:
            return True
        return any(
            _safe_graph_is_unsafe(attributes[name])
            for name in expected_fields
        )
    if issubclass(expected_type, LocalExecutionManagedResult):
        try:
            safe_state = result.local_execution_approval_state()
        except BaseException:
            return True
        return _safe_graph_is_unsafe(safe_state)
    return True


def _discard_managed_result(result: object, expected_type: type) -> None:
    if type(result) is expected_type and isinstance(
        result, LocalExecutionManagedResult
    ):
        try:
            result.discard_local_execution_result()
        except BaseException:
            pass


def _freeze_approved_value(value: object) -> object:
    if type(value) in {type(None), str, bool, int, float}:
        return value
    if type(value) in {dict, MappingProxyType}:
        return MappingProxyType({
            _freeze_approved_value(key): _freeze_approved_value(item)
            for key, item in value.items()
        })
    if type(value) in {tuple, list}:
        return tuple(_freeze_approved_value(item) for item in value)
    if type(value) in {set, frozenset}:
        return frozenset(_freeze_approved_value(item) for item in value)
    raise TypeError("value is outside the approved public graph")


def _rebuilt_public_exception(value: BaseException) -> BaseException:
    if type(value) is LocalExecutionError:
        cleanup = getattr(value, "cleanup_evidence", None)
        cleanup_digest = getattr(value, "cleanup_evidence_digest", "")
        quarantine_id = getattr(value, "quarantine_id", "")
        quarantine_generation = getattr(value, "quarantine_generation", 0)
        recovery_request = getattr(value, "recovery_request", None)
        result: BaseException = LocalExecutionError(
            value.code,
            value.reason,
            cleanup_evidence=(cleanup if isinstance(cleanup, Mapping) else None),
            cleanup_evidence_digest=(
                cleanup_digest if isinstance(cleanup_digest, str) else ""
            ),
            quarantine_id=(quarantine_id if isinstance(quarantine_id, str) else ""),
            quarantine_generation=(
                quarantine_generation
                if isinstance(quarantine_generation, int)
                and not isinstance(quarantine_generation, bool)
                else 0
            ),
            recovery_request=(
                recovery_request
                if isinstance(recovery_request, Mapping)
                else None
            ),
        )
    elif type(value) in {RuntimeError, TaskCancelledError}:
        try:
            reason = sanitize_output(
                str(value),
                limit_chars=10_000,
            ).text
        except BaseException:
            reason = "trusted Runtime entrypoint failed"
        result = type(value)(reason)
        for name, expected in (
            ("cleanup_evidence", Mapping),
            ("cleanup_evidence_digest", str),
            ("profile_manifest", Mapping),
        ):
            item = getattr(value, name, None)
            if isinstance(item, expected) and not _safe_graph_is_unsafe(item):
                try:
                    setattr(result, name, _freeze_approved_value(item))
                except (AttributeError, TypeError):
                    pass
    else:
        result = LocalExecutionError(
            SANDBOX_REQUIRED,
            "trusted Runtime entrypoint raised an unsafe exception",
        )
    result.__cause__ = None
    result.__context__ = None
    result.__traceback__ = None
    return result


class LocalExecutionApprover:
    """One-shot, Composition-owned approval for one fixed execution call."""

    __slots__ = ("_approved", "_ttl_seconds", "_lock", "_consumed")

    def __init__(self, approved: bool, *, ttl_seconds: float = 5.0) -> None:
        if not isinstance(approved, bool):
            raise TypeError("approved must be a bool")
        if (
            not isinstance(ttl_seconds, (int, float))
            or isinstance(ttl_seconds, bool)
            or not math.isfinite(float(ttl_seconds))
            or ttl_seconds <= 0
            or ttl_seconds > 30
        ):
            raise ValueError("ttl_seconds must be in (0, 30]")
        self._approved = approved
        self._ttl_seconds = float(ttl_seconds)
        self._lock = threading.Lock()
        self._consumed = False

    def run_controlled(
        self,
        runner: object,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        stdout_contains: tuple[str, ...] = (),
        stderr_contains: tuple[str, ...] = (),
        reject_zero_tests: bool = False,
    ) -> object:
        from .command_validators import (
            ControlledCommandResult,
            ControlledCommandRunner,
        )

        if type(runner) is not ControlledCommandRunner:
            raise TypeError("runner is not a controlled Runtime entrypoint")
        return self._invoke_fixed(lambda trusted_local: runner.run(
            command,
            timeout_seconds=timeout_seconds,
            trusted_local=trusted_local,
            stdout_contains=stdout_contains,
            stderr_contains=stderr_contains,
            reject_zero_tests=reject_zero_tests,
        ), expected_type=ControlledCommandResult, allow_tool_missing=True)

    def run_workspace(
        self,
        workspace: object,
        command: list[str],
    ) -> object:
        from .models import CommandResult
        from .workspace import ProjectWorkspace

        if type(workspace) is not ProjectWorkspace:
            raise TypeError("workspace is not a registered Runtime entrypoint")
        frozen_command = list(command)
        return self._invoke_fixed(lambda trusted_local: workspace.run(
            list(frozen_command),
            trusted_local=trusted_local,
        ), expected_type=CommandResult)

    def run_codex(self, runner: object, launch: object) -> object:
        from .agent_executor import (
            CodexCliLaunch,
            CodexCliProcessResult,
            CodexCliProcessRunner,
        )

        if type(runner) is not CodexCliProcessRunner:
            raise TypeError("runner is not a registered Codex Runtime entrypoint")
        if type(launch) is not CodexCliLaunch:
            raise TypeError("launch is not a registered Codex request")
        return self._invoke_fixed(
            lambda trusted_local: runner.run(
                launch,
                trusted_local=trusted_local,
            ),
            expected_type=CodexCliProcessResult,
        )

    def _invoke_fixed(
        self,
        operation,
        *,
        expected_type: type,
        allow_tool_missing: bool = False,
    ):
        with self._lock:
            if self._consumed:
                raise LocalExecutionError(
                    SANDBOX_REQUIRED,
                    "local execution approval was already consumed",
                )
            self._consumed = True

        try:
            initial = operation(None)
        except LocalExecutionError as exc:
            rejection = exc
        else:
            if isinstance(initial, LocalExecutionError):
                rejection = initial
            elif (
                allow_tool_missing
                and type(initial) is expected_type
                and getattr(initial, "tool_missing", False) is True
                and getattr(initial, "exit_code", object()) is None
            ):
                if _result_state_is_unsafe(initial, expected_type):
                    raise LocalExecutionError(
                        SANDBOX_REQUIRED,
                        "trusted preflight result failed its public schema",
                    )
                return initial
            else:
                raise RuntimeError(
                    "local execution reached a backend without admission"
                )

        public_request = getattr(rejection, "confirmation_request", None)
        try:
            request = consume_runtime_admission_challenge(rejection)
        except LocalExecutionError:
            raise rejection.with_traceback(None) from None
        if (
            rejection.code != SANDBOX_REQUIRED
            or not isinstance(public_request, Mapping)
            or dict(public_request) != dict(request)
            or set(request) != _CONFIRMATION_KEYS
            or not all(
                isinstance(request[key], str)
                and len(request[key]) == 64
                and all(char in "0123456789abcdef" for char in request[key])
                for key in _CONFIRMATION_KEYS
            )
        ):
            raise rejection.with_traceback(None) from None
        if not self._approved:
            raise rejection.with_traceback(None) from None

        token = issue_trusted_local_confirmation(
            workspace_digest=request["workspace_digest"],
            input_digest=request["input_digest"],
            profile_digest=request["profile_digest"],
            expires_at_monotonic=time.monotonic() + self._ttl_seconds,
        )
        public_exception: BaseException | None = None
        try:
            result = operation(token)
        except BaseException as exc:
            consumed = _retire_trusted_local_confirmation(token)
            token = None
            if not consumed:
                public_exception = LocalExecutionError(
                    SANDBOX_REQUIRED,
                    "trusted local capability escaped through an exception",
                )
            elif _safe_graph_is_unsafe(exc):
                public_exception = LocalExecutionError(
                    SANDBOX_REQUIRED,
                    "trusted local capability escaped through an exception",
                )
            else:
                public_exception = _rebuilt_public_exception(exc)
            exc = None
        if public_exception is not None:
            raise public_exception from None
        consumed = _retire_trusted_local_confirmation(token)
        token = None
        if not consumed:
            _discard_managed_result(result, expected_type)
            result = None
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "trusted local capability escaped through a result",
            ) from None
        if isinstance(result, _TrustedLocalConfirmation):
            result = None
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "trusted local capability escaped through a result",
            ) from None
        if isinstance(result, LocalExecutionError):
            if _safe_graph_is_unsafe(result):
                result = None
                raise LocalExecutionError(
                    SANDBOX_REQUIRED,
                    "trusted local capability escaped through a result",
                ) from None
            public_exception = _rebuilt_public_exception(result)
            result = None
            raise public_exception from None
        if type(result) is not expected_type:
            result = None
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "trusted Runtime entrypoint returned an invalid result type",
            )
        if _result_state_is_unsafe(result, expected_type):
            _discard_managed_result(result, expected_type)
            result = None
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "trusted local capability escaped through a result",
            ) from None
        return result
