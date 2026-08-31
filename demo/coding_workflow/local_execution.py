from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .harness.lifecycle import TaskCancelledError


CONTRACT_VERSION = "local_trusted_execution/v1"
FROZEN_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PROFILE_CORE = "core_validator"
PROFILE_LEGACY = "legacy_workspace_verify"
PROFILE_VISIONFORGE_BUILD = "visionforge_build"
PROFILE_VISIONFORGE_DEV = "visionforge_dev"
PROFILE_VISIONFORGE_BROWSER = "visionforge_browser"
PROFILE_CODEX_CLI = "codex_cli_agent"
PROFILE_IDS = frozenset({
    PROFILE_CORE,
    PROFILE_LEGACY,
    PROFILE_VISIONFORGE_BUILD,
    PROFILE_VISIONFORGE_DEV,
    PROFILE_VISIONFORGE_BROWSER,
    PROFILE_CODEX_CLI,
})
SANDBOX_REQUIRED = "SANDBOX_REQUIRED"
CLEANUP_FAILED = "CLEANUP_FAILED"
_CLEANUP_BARRIER_SECONDS = 5.0
_TERM_GRACE_SECONDS = 1.0
_CLEANUP_POLL_SECONDS = 0.05
_PRIVATE_ENVIRONMENT_SEAL = object()
_PRIVATE_HANDLE_SEAL = object()
_RUNTIME_PIPE_SEAL = object()
_BACKGROUND_OUTPUT_SEAL = object()
_BACKGROUND_LOG_SEAL = object()
_LOCK_TYPE = type(threading.Lock())

_LEGACY_COMMAND = ("python3", "-V")
_BUILD_COMMAND = ("pnpm", "run", "build")
_DEV_COMMAND = ("pnpm", "run", "dev", "--port", "4173")
_PROFILE_MAX_DEADLINE = MappingProxyType({
    PROFILE_CORE: 30.0,
    PROFILE_LEGACY: 60.0,
    PROFILE_VISIONFORGE_BUILD: 60.0,
    PROFILE_VISIONFORGE_BROWSER: 45.0,
    PROFILE_VISIONFORGE_DEV: 60.0,
    PROFILE_CODEX_CLI: 300.0,
})
_CODEX_CLI_MAX_STDIN_CHARS = 64_000
CODEX_CLI_SAFE_PREFIX_OPTIONS = (
    "--strict-config",
    "--ask-for-approval",
    "never",
    "-c",
    "shell_environment_policy.inherit=none",
    "-c",
    "shell_environment_policy.ignore_default_excludes=false",
    "-c",
    f'shell_environment_policy.set={{PATH="{FROZEN_PATH}"}}',
    "-c",
    'shell_environment_policy.filters={CODEX_HOME="exclude"}',
    "--sandbox",
)

def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _deep_freeze(item)
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def redact_text(value: str) -> str:
    emitted: list[str] = []
    redactor = _IncrementalRedactor(emitted.append)
    redactor.feed(str(value))
    redactor.finish()
    return "".join(emitted)


@dataclass(frozen=True)
class BoundedOutput:
    text: str
    truncated: bool
    raw_chars: int
    raw_sha256: str


_STREAM_START = re.compile(
    r"(?i)(?<!\w)(?:(?P<bearer>Bearer )|"
    r"(?P<key_quote>[\"']?)(?P<keyword>"
    r"api[_-]?key|access[_-]?token|token|password|passwd|secret))"
)
_PRIVATE_BEGIN = "-----BEGIN "
_PRIVATE_HEADER_END = "PRIVATE KEY-----"
_PRIVATE_END = re.compile(r"-----END [^-]*PRIVATE KEY-----")
_NORMAL_CARRY_CHARS = max(
    len(_PRIVATE_BEGIN),
    len('\"access_token'),
    len("Bearer "),
) - 1
_STREAM_HEADER_LIMIT = 256
_BEARER_CHARACTER = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~+/-"
)
_SECRET_DELIMITERS = frozenset(" \t\r\n,\"';")


class _IncrementalRedactor:
    """Bounded streaming redactor; raw secret bodies are never retained."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._state = "normal"
        self._pending = ""
        self._keyword = ""
        self._quote = ""
        self._key_quote_pending = False
        self._quoted_backslash_odd = False
        self._bearer_count = 0
        self._bearer_padding = False
        self._assignment_overflow = False
        self._finished = False

    def feed(self, value: str) -> None:
        if self._finished:
            raise RuntimeError("stream redactor is already finalized")
        data = value
        while data:
            if self._state == "normal":
                data = self._normal(data)
            elif self._state.startswith("assignment_"):
                data = self._assignment(data)
            elif self._state == "bearer_collect":
                data = self._bearer_collect(data)
            elif self._state == "bearer_suppress":
                data = self._bearer_suppress(data)
            elif self._state == "private_header":
                data = self._private_header(data)
            elif self._state == "private_body":
                data = self._private_body(data)
            else:
                raise RuntimeError("unknown streaming redaction state")

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._state == "normal":
            self._emit(self._pending)
        elif self._state.startswith("assignment_"):
            self._flush_assignment_benign()
        elif self._state == "bearer_collect":
            self._emit(self._pending)
        elif self._state == "private_header":
            self._emit(self._pending)
        self._pending = ""

    def _normal(self, data: str) -> str:
        combined = self._pending + data
        self._pending = ""
        match = _STREAM_START.search(combined)
        private_index = combined.find(_PRIVATE_BEGIN)
        if match is None and private_index < 0:
            if len(combined) <= _NORMAL_CARRY_CHARS:
                self._pending = combined
            else:
                self._emit(combined[:-_NORMAL_CARRY_CHARS])
                self._pending = combined[-_NORMAL_CARRY_CHARS:]
            return ""
        stream_index = len(combined) if match is None else match.start()
        start_index = min(stream_index, private_index if private_index >= 0 else len(combined))
        self._emit(combined[:start_index])
        if private_index >= 0 and private_index == start_index:
            self._state = "private_header"
            self._pending = _PRIVATE_BEGIN
            return combined[start_index + len(_PRIVATE_BEGIN):]
        assert match is not None
        token = match.group(0)
        remainder = combined[match.end():]
        if match.group("bearer") is not None:
            self._state = "bearer_collect"
            self._pending = token
            self._bearer_count = 0
            self._bearer_padding = False
        else:
            self._state = "assignment_separator"
            self._keyword = str(match.group("keyword"))
            self._pending = token
            self._quote = ""
            self._key_quote_pending = bool(match.group("key_quote"))
            self._quoted_backslash_odd = False
            self._assignment_overflow = False
        return remainder

    @staticmethod
    def _value_character(character: str) -> bool:
        return character not in _SECRET_DELIMITERS

    def _append_assignment(self, value: str) -> None:
        if self._assignment_overflow:
            return
        self._pending += value
        if len(self._pending) > _STREAM_HEADER_LIMIT:
            self._assignment_overflow = True
            self._pending = self._keyword

    def _flush_assignment_benign(self) -> None:
        if self._assignment_overflow:
            self._emit(f"{self._keyword}=[REDACTED]")
        else:
            self._emit(self._pending)
        self._pending = ""
        self._keyword = ""
        self._quote = ""
        self._key_quote_pending = False
        self._quoted_backslash_odd = False
        self._assignment_overflow = False
        self._state = "normal"

    def _assignment(self, data: str) -> str:
        index = 0
        while index < len(data):
            character = data[index]
            if self._state == "assignment_separator":
                if self._key_quote_pending:
                    if character.isspace():
                        self._append_assignment(character)
                        index += 1
                        continue
                    if character == self._pending[0]:
                        self._append_assignment(character)
                        self._key_quote_pending = False
                        index += 1
                        continue
                    if character in {":", "="}:
                        self._key_quote_pending = False
                        self._append_assignment(character)
                        self._state = "assignment_value"
                        index += 1
                        continue
                    self._flush_assignment_benign()
                    return data[index:]
                if character.isspace():
                    self._append_assignment(character)
                    index += 1
                    continue
                if character in {":", "="}:
                    self._append_assignment(character)
                    self._state = "assignment_value"
                    index += 1
                    continue
                self._flush_assignment_benign()
                return data[index:]
            if self._state == "assignment_value":
                if character.isspace():
                    self._append_assignment(character)
                    index += 1
                    continue
                if character in {"'", '"'}:
                    self._quote = character
                    self._emit(f"{self._keyword}=[REDACTED]")
                    self._pending = ""
                    self._state = "assignment_quoted_suppress"
                    self._quoted_backslash_odd = False
                    index += 1
                    continue
                if not self._value_character(character):
                    self._flush_assignment_benign()
                    return data[index:]
                self._emit(f"{self._keyword}=[REDACTED]")
                self._pending = ""
                self._state = "assignment_suppress"
                index += 1
                continue
            if self._state == "assignment_quoted_suppress":
                while index < len(data):
                    character = data[index]
                    if character == "\\":
                        self._quoted_backslash_odd = (
                            not self._quoted_backslash_odd
                        )
                        index += 1
                        continue
                    escaped = self._quoted_backslash_odd
                    self._quoted_backslash_odd = False
                    if character == self._quote and not escaped:
                        self._state = "normal"
                        self._keyword = ""
                        self._quote = ""
                        return data[index + 1:]
                    index += 1
                return ""
            if self._state == "assignment_suppress":
                delimiter = next(
                    (
                        offset
                        for offset, item in enumerate(data[index:], start=index)
                        if item in _SECRET_DELIMITERS
                    ),
                    -1,
                )
                if delimiter < 0:
                    return ""
                ending = data[delimiter]
                self._state = "normal"
                self._keyword = ""
                return data[delimiter:]
        return ""

    def _bearer_collect(self, data: str) -> str:
        index = 0
        while index < len(data):
            character = data[index]
            if character not in _BEARER_CHARACTER:
                self._emit(self._pending)
                self._pending = ""
                self._state = "normal"
                return data[index:]
            self._pending += character
            self._bearer_count += 1
            index += 1
            if self._bearer_count == 8:
                self._emit("Bearer [REDACTED]")
                self._pending = ""
                self._state = "bearer_suppress"
                return data[index:]
        return ""

    def _bearer_suppress(self, data: str) -> str:
        for index, character in enumerate(data):
            if character in _BEARER_CHARACTER and not self._bearer_padding:
                continue
            if character == "=":
                self._bearer_padding = True
                continue
            self._state = "normal"
            self._bearer_padding = False
            return data[index:]
        return ""

    def _private_header(self, data: str) -> str:
        combined = self._pending + data
        end_index = combined.find(_PRIVATE_HEADER_END, len(_PRIVATE_BEGIN))
        if end_index >= 0:
            header = combined[len(_PRIVATE_BEGIN):end_index]
            if "-" not in header:
                self._emit("[REDACTED PRIVATE KEY]")
                self._pending = ""
                self._state = "private_body"
                return combined[end_index + len(_PRIVATE_HEADER_END):]
        if len(combined) > _STREAM_HEADER_LIMIT:
            self._emit("[REDACTED PRIVATE KEY]")
            self._pending = ""
            self._state = "private_body"
            return ""
        self._pending = combined
        return ""

    def _private_body(self, data: str) -> str:
        combined = self._pending + data
        match = _PRIVATE_END.search(combined)
        if match is not None:
            self._pending = ""
            self._state = "normal"
            return combined[match.end():]
        marker = combined.rfind("-----END ")
        if marker >= 0:
            candidate = combined[marker:]
            self._pending = (
                candidate[-_STREAM_HEADER_LIMIT:]
                if len(candidate) > _STREAM_HEADER_LIMIT
                else candidate
            )
        else:
            carry = len("-----END ") - 1
            self._pending = combined[-carry:]
        return ""


class _StreamingBoundedOutput:
    def __init__(self, limit_chars: int) -> None:
        self.limit_chars = limit_chars
        self.head_limit = limit_chars // 2
        self.tail_limit = limit_chars - self.head_limit
        self.raw_chars = 0
        self.raw_hasher = hashlib.sha256()
        self.redacted_chars = 0
        self.short_text = ""
        self.head = ""
        self.tail = ""
        self.overflowed = False
        self.finished = False
        self.redactor = _IncrementalRedactor(self._accept_redacted)

    def feed(self, value: str) -> None:
        if self.finished:
            raise RuntimeError("stream output is already finalized")
        self.raw_chars += len(value)
        self.raw_hasher.update(value.encode("utf-8", errors="replace"))
        self.redactor.feed(value)

    def finish(self) -> None:
        if self.finished:
            return
        self.redactor.finish()
        self.finished = True

    def snapshot(self) -> BoundedOutput:
        digest = self.raw_hasher.copy().hexdigest()
        if not self.overflowed:
            return BoundedOutput(
                self.short_text,
                False,
                self.raw_chars,
                digest,
            )
        return BoundedOutput(
            self.head
            + f"\n... [TRUNCATED {self.redacted_chars - self.limit_chars} CHARS] ...\n"
            + self.tail,
            True,
            self.raw_chars,
            digest,
        )

    def retained_chars(self) -> int:
        return len(self.short_text) + len(self.head) + len(self.tail) + len(
            self.redactor._pending
        )

    def _accept_redacted(self, value: str) -> None:
        if not value:
            return
        self.redacted_chars += len(value)
        if not self.overflowed:
            combined = self.short_text + value
            if len(combined) <= self.limit_chars:
                self.short_text = combined
                return
            self.overflowed = True
            self.head = combined[:self.head_limit]
            self.tail = combined[-self.tail_limit:]
            self.short_text = ""
            return
        self.tail = (self.tail + value)[-self.tail_limit:]


def sanitize_output(
    value: str | bytes | None,
    *,
    limit_chars: int,
) -> BoundedOutput:
    if not isinstance(limit_chars, int) or isinstance(limit_chars, bool):
        raise ValueError("limit_chars must be an integer")
    if limit_chars <= 0:
        raise ValueError("limit_chars must be positive")
    if isinstance(value, bytes):
        raw = value.decode("utf-8", errors="replace")
    elif value is None:
        raw = ""
    else:
        raw = str(value)
    collector = _StreamingBoundedOutput(limit_chars)
    collector.feed(raw)
    collector.finish()
    return collector.snapshot()


def _public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            redact_text(str(key)): _public_value(item)
            for key, item in value.items()
        })
    if isinstance(value, tuple):
        return tuple(_public_value(item) for item in value)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, (str, bytes)):
        return sanitize_output(value, limit_chars=10_000).text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


class LocalExecutionError(RuntimeError):
    """Typed fail-closed result for admission and cleanup boundaries."""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        confirmation_request: Mapping[str, str] | None = None,
        cleanup_evidence: Mapping[str, object] | None = None,
        cleanup_evidence_digest: str = "",
        quarantine_id: str = "",
        quarantine_generation: int = 0,
        recovery_request: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.reason = redact_text(str(reason))
        self.message = self.reason
        if confirmation_request is not None:
            self.confirmation_request = MappingProxyType(
                dict(confirmation_request)
            )
        if cleanup_evidence is not None:
            self.cleanup_evidence = MappingProxyType(
                dict(_public_value(cleanup_evidence))
            )
            self.cleanup_evidence_digest = cleanup_evidence_digest
        if quarantine_id:
            self.quarantine_id = quarantine_id
            self.quarantine_generation = quarantine_generation
        if recovery_request is not None:
            self.recovery_request = MappingProxyType(dict(recovery_request))
        super().__init__(self.reason)

    def to_dict(self) -> Mapping[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "reason": self.reason,
        }
        if hasattr(self, "confirmation_request"):
            result["confirmation_request"] = dict(self.confirmation_request)
        if hasattr(self, "cleanup_evidence"):
            result["cleanup_evidence"] = dict(self.cleanup_evidence)
            result["cleanup_evidence_digest"] = self.cleanup_evidence_digest
        if hasattr(self, "quarantine_id"):
            result["quarantine_id"] = self.quarantine_id
            result["quarantine_generation"] = self.quarantine_generation
        if hasattr(self, "recovery_request"):
            result["recovery_request"] = dict(self.recovery_request)
        return MappingProxyType(result)

    evidence = to_dict


def _sanitized_post_spawn_exception(
    value: BaseException,
    *,
    cleanup_evidence: Mapping[str, object],
    cleanup_evidence_digest: str,
    profile_manifest: Mapping[str, object],
) -> BaseException:
    """Rebuild an output-adjacent failure without retaining its object graph."""
    if type(value) is TaskCancelledError:
        try:
            reason = sanitize_output(
                str(value),
                limit_chars=10_000,
            ).text
        except BaseException:
            reason = "local execution was cancelled"
        result: BaseException = TaskCancelledError(reason)
    elif type(value) is RuntimeError:
        try:
            reason = sanitize_output(
                str(value),
                limit_chars=10_000,
            ).text
        except BaseException:
            reason = "local execution Runtime failed after spawn"
        result = RuntimeError(reason)
    else:
        reason = (
            "local execution output decoding failed"
            if isinstance(value, UnicodeError)
            else "local execution failed after spawn"
        )
        result = LocalExecutionError(
            SANDBOX_REQUIRED,
            reason,
            cleanup_evidence=cleanup_evidence,
            cleanup_evidence_digest=cleanup_evidence_digest,
        )
    if not isinstance(result, LocalExecutionError):
        for name, item in (
            ("cleanup_evidence", _deep_freeze(cleanup_evidence)),
            ("cleanup_evidence_digest", str(cleanup_evidence_digest)),
            ("profile_manifest", _deep_freeze(profile_manifest)),
        ):
            try:
                setattr(result, name, item)
            except (AttributeError, TypeError):
                pass
    result.__cause__ = None
    result.__context__ = None
    result.__traceback__ = None
    return result


class _TrustedLocalConfirmation:
    __slots__ = ()


@dataclass
class _ConfirmationRecord:
    workspace_digest: str
    input_digest: str
    profile_digest: str
    expires_at_monotonic: float
    consumed: bool = False


_AUTH_LOCK = threading.RLock()
_CONFIRMATIONS: dict[_TrustedLocalConfirmation, _ConfirmationRecord] = {}
_CHALLENGE_LOCK = threading.RLock()
_ADMISSION_CHALLENGES: weakref.WeakKeyDictionary[
    LocalExecutionError,
    Mapping[str, str],
] = weakref.WeakKeyDictionary()


def issue_trusted_local_confirmation(
    *,
    workspace_digest: str,
    input_digest: str,
    profile_digest: str,
    expires_at_monotonic: float,
) -> object:
    for name, value in (
        ("workspace_digest", workspace_digest),
        ("input_digest", input_digest),
        ("profile_digest", profile_digest),
    ):
        if not _valid_digest(value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    if (
        not isinstance(expires_at_monotonic, (int, float))
        or isinstance(expires_at_monotonic, bool)
        or not math.isfinite(float(expires_at_monotonic))
    ):
        raise ValueError("expires_at_monotonic must be finite")
    token = _TrustedLocalConfirmation()
    with _AUTH_LOCK:
        _CONFIRMATIONS[token] = _ConfirmationRecord(
            workspace_digest,
            input_digest,
            profile_digest,
            float(expires_at_monotonic),
        )
    return token


def _retire_trusted_local_confirmation(token: object) -> bool:
    """Invalidate an issued token and report whether Runtime consumed it."""

    if not isinstance(token, _TrustedLocalConfirmation):
        return False
    with _AUTH_LOCK:
        record = _CONFIRMATIONS.pop(token, None)
        if record is None:
            return False
        consumed = record.consumed
        record.consumed = True
        return consumed


def consume_runtime_admission_challenge(
    rejection: LocalExecutionError,
) -> Mapping[str, str]:
    with _CHALLENGE_LOCK:
        request = _ADMISSION_CHALLENGES.pop(rejection, None)
    if request is None:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "admission challenge lacks Runtime provenance",
        )
    return request


def workspace_digest(root: Path) -> str:
    canonical_root = Path(root).resolve()
    entries: list[tuple[str, str]] = []
    if not canonical_root.is_dir():
        raise ValueError("execution Workspace does not exist")
    for path in sorted(canonical_root.rglob("*")):
        relative_path = path.relative_to(canonical_root)
        if (
            relative_path.parts
            and relative_path.parts[0]
            in {".git", ".runtime", ".runs", ".verification"}
        ):
            continue
        if "__pycache__" in relative_path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = str(relative_path)
        try:
            if path.is_symlink():
                entries.append((relative, f"symlink:{os.readlink(path)}"))
            elif path.is_file():
                entries.append((
                    relative,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ))
            elif path.is_dir():
                entries.append((relative + "/", "directory"))
        except OSError as exc:
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                f"Workspace digest unavailable: {exc}",
            ) from None
    return _canonical_digest({
        "schema": "local-execution-workspace/v1",
        "entries": entries,
    })


_ENVIRONMENT_SOURCES: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "PATH": MappingProxyType({"value_source": "profile_constant"}),
    "LANG": MappingProxyType({"value_source": "profile_constant"}),
    "LC_ALL": MappingProxyType({"value_source": "profile_constant"}),
    "HOME": MappingProxyType({"value_source": "runtime_private_0700"}),
    "TMPDIR": MappingProxyType({"value_source": "runtime_private_0700"}),
    "PYTHONDONTWRITEBYTECODE": MappingProxyType({
        "value_source": "profile_constant"
    }),
    "PYTHONUNBUFFERED": MappingProxyType({
        "value_source": "profile_constant"
    }),
    "CODEX_HOME": MappingProxyType({
        "value_source": "runtime_host_credential_cache"
    }),
})


@dataclass(frozen=True)
class PreparedExecution:
    profile_id: str
    workspace_root: Path
    executable: str
    requested_argv: tuple[str, ...]
    execution_argv: tuple[str, ...]
    wall_deadline_seconds: float
    output_limit_chars: int
    output_kind: str
    python_profile: bool
    workspace_digest: str
    input_digest: str
    profile_digest: str
    profile_manifest: Mapping[str, object]
    stdin_text: str = field(default="", repr=False, compare=False)

    @property
    def confirmation_request(self) -> Mapping[str, str]:
        return MappingProxyType({
            "workspace_digest": self.workspace_digest,
            "input_digest": self.input_digest,
            "profile_digest": self.profile_digest,
        })


@dataclass(frozen=True)
class _PreparedRecord:
    public_ref: object
    canonical: PreparedExecution
    public_signature: str


_PREPARED_LOCK = threading.RLock()
_PREPARED_RECORDS: dict[int, _PreparedRecord] = {}


def _prepared_signature(prepared: PreparedExecution) -> str:
    return _canonical_digest({
        "profile_id": prepared.profile_id,
        "workspace_root": str(prepared.workspace_root),
        "executable": prepared.executable,
        "requested_argv": prepared.requested_argv,
        "execution_argv": prepared.execution_argv,
        "wall_deadline_seconds": prepared.wall_deadline_seconds,
        "output_limit_chars": prepared.output_limit_chars,
        "output_kind": prepared.output_kind,
        "python_profile": prepared.python_profile,
        "workspace_digest": prepared.workspace_digest,
        "input_digest": prepared.input_digest,
        "profile_digest": prepared.profile_digest,
        "profile_manifest": _json_compatible(prepared.profile_manifest),
        "stdin_sha256": hashlib.sha256(
            prepared.stdin_text.encode("utf-8", errors="replace")
        ).hexdigest(),
    })


def _register_prepared(
    public: PreparedExecution,
    canonical: PreparedExecution,
) -> None:
    identifier = id(public)

    def discard(reference: object) -> None:
        with _PREPARED_LOCK:
            current = _PREPARED_RECORDS.get(identifier)
            if current is not None and current.public_ref is reference:
                _PREPARED_RECORDS.pop(identifier, None)

    reference = weakref.ref(public, discard)
    record = _PreparedRecord(
        reference,
        canonical,
        _prepared_signature(public),
    )
    with _PREPARED_LOCK:
        _PREPARED_RECORDS[identifier] = record


def _assert_prepared(value: object) -> PreparedExecution:
    if type(value) is not PreparedExecution:
        _reject("execution request is not Runtime prepared")
    with _PREPARED_LOCK:
        record = _PREPARED_RECORDS.get(id(value))
        if record is None or record.public_ref() is not value:
            _reject("execution request lacks a Runtime seal")
        try:
            current_signature = _prepared_signature(value)
        except BaseException:
            _reject("execution request seal validation failed")
        if current_signature != record.public_signature:
            _reject("execution request changed after preparation")
        return record.canonical


def _validate_profile_request(
    *,
    profile_id: str,
    root: Path,
    executable: Path,
    command: tuple[str, ...],
    wall_deadline_seconds: float,
    output_limit_chars: int,
    output_kind: str,
    python_profile: bool,
    stdin_text: str,
) -> None:
    expected_command: tuple[str, ...] | None = {
        PROFILE_LEGACY: _LEGACY_COMMAND,
        PROFILE_VISIONFORGE_BUILD: _BUILD_COMMAND,
        PROFILE_VISIONFORGE_DEV: _DEV_COMMAND,
    }.get(profile_id)
    if expected_command is not None and command != expected_command:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution argv is not registered for Profile",
        )
    if profile_id == PROFILE_VISIONFORGE_BROWSER:
        switches = tuple(command[index] for index in (2, 4, 6, 8)) \
            if len(command) == 10 else ()
        try:
            parsed_url = urlsplit(command[3]) if len(command) == 10 else None
            parsed_port = parsed_url.port if parsed_url is not None else None
        except ValueError:
            parsed_url = None
            parsed_port = None
        if (
            len(command) != 10
            or command[0] != "node"
            or switches != ("--url", "--spec", "--screenshot", "--result")
            or parsed_url is None
            or parsed_url.scheme != "http"
            or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_port is None
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Browser execution argv is not registered for Profile",
            )
    if profile_id == PROFILE_CODEX_CLI:
        new_session = (
            "exec",
            "--ignore-user-config",
            "--json",
            "-",
        )
        resume_prefix = (
            "exec",
            "--ignore-user-config",
            "resume",
            "--json",
        )
        fixed_prefix = (str(executable), *CODEX_CLI_SAFE_PREFIX_OPTIONS)
        permission_index = len(fixed_prefix)
        valid_sandbox = (
            len(command) > permission_index
            and command[permission_index] in {
            "read-only",
            "workspace-write",
            }
        )
        valid_root = command[
            permission_index + 1:permission_index + 3
        ] == (
            "-C",
            str(root),
        )
        tail = command[permission_index + 3:]
        valid_tail = tail == new_session or (
            len(tail) == 6
            and tail[:4] == resume_prefix
            and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tail[4]))
            and tail[5:] == ("-",)
        )
        if (
            command[:permission_index] != fixed_prefix
            or not valid_sandbox
            or not valid_root
            or not valid_tail
        ):
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Codex CLI argv is not registered for Profile",
            )
        if (
            not stdin_text
            or len(stdin_text) > _CODEX_CLI_MAX_STDIN_CHARS
            or "\0" in stdin_text
        ):
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Codex CLI stdin is outside the registered Profile",
            )
    elif stdin_text:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution Profile does not accept stdin",
        )
    maximum_deadline = _PROFILE_MAX_DEADLINE[profile_id]
    if wall_deadline_seconds > maximum_deadline:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution deadline exceeds Profile limit",
        )
    expected_python = profile_id in {PROFILE_CORE, PROFILE_LEGACY}
    expected_output_kind = (
        "server_log"
        if profile_id == PROFILE_VISIONFORGE_DEV
        else "stdout_stderr"
    )
    if python_profile is not expected_python or output_kind != expected_output_kind:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution boundary does not match Profile",
        )
    if profile_id != PROFILE_CORE and output_limit_chars != 10_000:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution output limit does not match Profile",
        )
    if output_limit_chars > 10_000:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution output limit exceeds Profile",
        )
    executable_matches = (
        command[0] == str(executable)
        if profile_id == PROFILE_CODEX_CLI
        else executable.name == command[0]
    )
    if not executable_matches:
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution executable does not match requested argv",
        )
    if executable.resolve().is_relative_to(root):
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            "execution executable is inside the mutable Workspace",
        )


def prepare_execution(
    *,
    profile_id: str,
    workspace_root: Path,
    executable: str,
    command: tuple[str, ...] | list[str],
    wall_deadline_seconds: float,
    output_limit_chars: int = 10_000,
    output_kind: str = "stdout_stderr",
    python_profile: bool = False,
    stdin_text: str = "",
) -> PreparedExecution:
    if profile_id not in PROFILE_IDS:
        raise LocalExecutionError(SANDBOX_REQUIRED, "unregistered execution Profile")
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise LocalExecutionError(SANDBOX_REQUIRED, "execution Workspace absent")
    if (
        not isinstance(command, (tuple, list))
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid execution argv")
    if any("\0" in part for part in command):
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid execution argv")
    resolved_executable = Path(executable)
    if not resolved_executable.is_absolute():
        raise LocalExecutionError(SANDBOX_REQUIRED, "executable is not absolute")
    if (
        not isinstance(wall_deadline_seconds, (int, float))
        or isinstance(wall_deadline_seconds, bool)
        or not math.isfinite(float(wall_deadline_seconds))
        or wall_deadline_seconds <= 0
    ):
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid wall deadline")
    if (
        not isinstance(output_limit_chars, int)
        or isinstance(output_limit_chars, bool)
        or output_limit_chars < 200
    ):
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid output limit")
    if output_kind not in {"stdout_stderr", "server_log"}:
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid output kind")
    if not isinstance(stdin_text, str):
        raise LocalExecutionError(SANDBOX_REQUIRED, "invalid execution stdin")

    requested = tuple(command)
    _validate_profile_request(
        profile_id=profile_id,
        root=root,
        executable=resolved_executable,
        command=requested,
        wall_deadline_seconds=float(wall_deadline_seconds),
        output_limit_chars=output_limit_chars,
        output_kind=output_kind,
        python_profile=python_profile,
        stdin_text=stdin_text,
    )
    executed = (str(resolved_executable), *requested[1:])
    limits: dict[str, object] = {
        "wall_deadline_seconds": wall_deadline_seconds,
        "term_grace_seconds": 1,
        "cleanup_barrier_seconds": 5,
    }
    if output_kind == "server_log":
        limits["server_log_limit_chars"] = output_limit_chars
    else:
        limits["stdout_limit_chars"] = output_limit_chars
        limits["stderr_limit_chars"] = output_limit_chars
    environment_names = ["PATH", "LANG", "LC_ALL", "HOME", "TMPDIR"]
    if python_profile:
        environment_names.extend((
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
        ))
    if profile_id == PROFILE_CODEX_CLI:
        environment_names.append("CODEX_HOME")
    environment_manifest = {
        name: dict(_ENVIRONMENT_SOURCES[name])
        for name in environment_names
    }
    input_summary = {
        "requested_argv": list(requested),
        "cwd": str(root),
        "stdin_chars": len(stdin_text),
        "stdin_sha256": hashlib.sha256(
            stdin_text.encode("utf-8", errors="replace")
        ).hexdigest(),
    }
    input_digest = _canonical_digest({
        "schema": "local-execution-input/v1",
        "requested_argv": requested,
        "cwd": str(root),
        "stdin_sha256": input_summary["stdin_sha256"],
    })
    profile_preimage = {
        "contract_version": CONTRACT_VERSION,
        "profile_id": profile_id,
        "executable": str(resolved_executable),
        "argv": executed,
        "cwd": str(root),
        "environment": environment_manifest,
        "limits": limits,
        "input_summary": input_summary,
    }
    profile_digest = _canonical_digest(profile_preimage)
    manifest = dict(profile_preimage)
    manifest["profile_digest"] = profile_digest
    frozen_manifest = _deep_freeze(manifest)
    current_workspace_digest = _bound_workspace_digest(root)
    canonical = PreparedExecution(
        profile_id,
        root,
        str(resolved_executable),
        requested,
        executed,
        float(wall_deadline_seconds),
        output_limit_chars,
        output_kind,
        python_profile,
        current_workspace_digest,
        input_digest,
        profile_digest,
        frozen_manifest,
        stdin_text,
    )
    public = PreparedExecution(
        profile_id,
        root,
        str(resolved_executable),
        requested,
        executed,
        float(wall_deadline_seconds),
        output_limit_chars,
        output_kind,
        python_profile,
        current_workspace_digest,
        input_digest,
        profile_digest,
        frozen_manifest,
        stdin_text,
    )
    _register_prepared(public, canonical)
    return public


def _reject(reason: str, *, challenge: PreparedExecution | None = None) -> None:
    request = challenge.confirmation_request if challenge is not None else None
    rejection = LocalExecutionError(
        SANDBOX_REQUIRED,
        reason,
        confirmation_request=request,
    )
    if request is not None:
        with _CHALLENGE_LOCK:
            _ADMISSION_CHALLENGES[rejection] = request
    raise rejection


def _consume_confirmation(
    prepared: PreparedExecution,
    trusted_local: object,
) -> None:
    workspace_key = str(prepared.workspace_root)
    with _STATE_LOCK:
        if (
            workspace_key in _QUARANTINE_BY_WORKSPACE
            or workspace_key in _CLEANUP_FENCE_BY_WORKSPACE
        ):
            if isinstance(trusted_local, _TrustedLocalConfirmation):
                with _AUTH_LOCK:
                    record = _CONFIRMATIONS.get(trusted_local)
                    if record is not None:
                        record.consumed = True
            _reject("execution Workspace is cleanup-fenced or quarantined")
    if trusted_local is None:
        _reject("trusted local confirmation required", challenge=prepared)
    if not isinstance(trusted_local, _TrustedLocalConfirmation):
        _reject("trusted local confirmation invalid")
    current_workspace_digest = _bound_workspace_digest(
        prepared.workspace_root
    )
    with _AUTH_LOCK:
        record = _CONFIRMATIONS.get(trusted_local)
        if record is None or record.consumed:
            _reject("trusted local confirmation unavailable or already consumed")
        if time.monotonic() > record.expires_at_monotonic:
            record.consumed = True
            _reject("trusted local confirmation expired")
        if (
            record.workspace_digest != prepared.workspace_digest
            or record.input_digest != prepared.input_digest
            or record.profile_digest != prepared.profile_digest
        ):
            _reject("trusted local confirmation binding mismatch")
        if current_workspace_digest != prepared.workspace_digest:
            _reject("execution Workspace changed after challenge")
        record.consumed = True


@dataclass(frozen=True)
class ExecutionOutcome:
    exit_code: int | None
    stdout: BoundedOutput
    stderr: BoundedOutput
    duration_ms: int
    timed_out: bool
    profile_manifest: Mapping[str, object]
    cleanup_evidence: Mapping[str, object]
    cleanup_evidence_digest: str
    assertion_results: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _PrivateEnvironment:
    root: Path
    home: Path
    tmpdir: Path
    values: Mapping[str, str]
    _runtime_seal: object = field(repr=False)

    def close(self) -> bool:
        return _remove_private_root(self.root)


@dataclass(frozen=True)
class _RuntimePrivateEnvironment:
    root: Path
    _runtime_seal: object = field(repr=False)


def _remove_private_root(root: Path) -> bool:
    if type(root) is not type(Path("/")):
        return False
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        return not Path.exists(root)
    except OSError:
        return False


def _claim_runtime_private_environment(
    environment: _PrivateEnvironment,
) -> _RuntimePrivateEnvironment:
    if (
        type(environment) is not _PrivateEnvironment
        or environment._runtime_seal is not _PRIVATE_ENVIRONMENT_SEAL
        or type(environment.root) is not type(Path("/"))
    ):
        raise TypeError("unsupported Runtime private environment")
    return _RuntimePrivateEnvironment(
        environment.root,
        _PRIVATE_HANDLE_SEAL,
    )


def _close_private_environment_unbound(
    environment: _PrivateEnvironment,
) -> bool:
    if (
        type(environment) is not _PrivateEnvironment
        or environment._runtime_seal is not _PRIVATE_ENVIRONMENT_SEAL
    ):
        return False
    return _PrivateEnvironment.close(environment)


def _private_environment(
    *,
    python_profile: bool,
    codex_profile: bool = False,
) -> _PrivateEnvironment:
    private_root = Path(tempfile.mkdtemp(prefix="local-trusted-execution-"))
    os.chmod(private_root, 0o700)
    home = private_root / "home"
    tmpdir = private_root / "tmp"
    home.mkdir(mode=0o700)
    tmpdir.mkdir(mode=0o700)
    values = {
        "PATH": FROZEN_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
    }
    if python_profile:
        values.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        })
    if codex_profile:
        codex_home = Path.home().joinpath(".codex").resolve()
        if not codex_home.is_dir():
            raise LocalExecutionError(
                SANDBOX_REQUIRED,
                "Codex credential cache is unavailable",
            )
        values["CODEX_HOME"] = str(codex_home)
    return _PrivateEnvironment(
        private_root,
        home,
        tmpdir,
        MappingProxyType(values),
        _PRIVATE_ENVIRONMENT_SEAL,
    )


def _spawn(
    prepared: PreparedExecution,
    environment: Mapping[str, str],
    *,
    background: bool,
) -> Any:
    return subprocess.Popen(
        prepared.execution_argv,
        cwd=prepared.workspace_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if background else subprocess.PIPE,
        stdin=(subprocess.PIPE if prepared.stdin_text else subprocess.DEVNULL),
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        env=dict(environment),
        start_new_session=True,
        close_fds=True,
        pass_fds=(),
        umask=0o077,
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _cleanup_evidence(
    *,
    pid: int,
    reaped: bool,
    verified: bool,
    duration_seconds: float,
    status: str = "terminal",
    phase_outcomes: Mapping[str, Mapping[str, object]] | None = None,
    owned_outcomes: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Mapping[str, object], str]:
    if phase_outcomes is None:
        phase_outcomes = {
            phase: {
                "attempted": False,
                "outcome": "not_recorded",
            }
            for phase in ("term", "kill", "wait_reap", "verify")
        }
    actions = []
    for phase in ("term", "kill", "wait_reap", "verify"):
        raw = phase_outcomes.get(phase, {})
        actions.append({
            "phase": phase,
            "attempted": raw.get("attempted") is True,
            "outcome": str(raw.get("outcome", "not_recorded")),
            **({
                "attempts": int(raw["attempts"]),
            } if isinstance(raw.get("attempts"), int) else {}),
        })
    evidence: dict[str, object] = {
        "status": status,
        "actions": actions,
        "phase_outcomes": {
            phase: dict(phase_outcomes.get(phase, {}))
            for phase in ("term", "kill", "wait_reap", "verify")
        },
        "owned_resource_outcomes": {
            str(name): dict(outcome)
            for name, outcome in (owned_outcomes or {}).items()
        },
        "resources": {"pid": pid, "pgid": pid},
        "direct_child_reaped": bool(reaped),
        "verified": bool(verified),
        "barrier_duration_seconds": max(0.0, duration_seconds),
    }
    digest = _canonical_digest(evidence)
    frozen = _deep_freeze(evidence)
    assert isinstance(frozen, Mapping)
    return frozen, digest


def _remaining_cleanup_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _wait_cleanup_poll(seconds: float) -> None:
    """Bounded non-busy wait seam for owned process-group convergence."""
    if seconds <= 0:
        return
    threading.Event().wait(timeout=seconds)


def _is_runtime_owned_pipe(stream: object) -> bool:
    """Accept only stdlib pipe objects created by Runtime's Popen boundary."""
    if type(stream) is io.StringIO:
        # Deterministic in-memory stand-in used by the pure-mock contract cards.
        return True
    if type(stream) is not io.TextIOWrapper:
        return False
    try:
        buffer = io.TextIOWrapper.buffer.__get__(stream, io.TextIOWrapper)
        raw = io.BufferedReader.raw.__get__(buffer, io.BufferedReader)
        descriptor = io.TextIOWrapper.fileno(stream)
        return (
            type(buffer) is io.BufferedReader
            and type(raw) is io.FileIO
            and io.FileIO.fileno(raw) == descriptor
            and stat.S_ISFIFO(os.fstat(descriptor).st_mode)
        )
    except (AttributeError, OSError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class _RuntimeOwnedPipe:
    stream: object | None
    supported: bool
    provenance: str
    _runtime_seal: object = field(repr=False)


def _claim_runtime_owned_pipes(
    process: Any,
    names: tuple[str, ...],
) -> tuple[_RuntimeOwnedPipe, ...]:
    claimed: list[_RuntimeOwnedPipe] = []
    seen: set[int] = set()
    for name in names:
        try:
            stream = getattr(process, name, None)
        except BaseException:
            claimed.append(_RuntimeOwnedPipe(
                None,
                False,
                f"{name}:unreadable",
                _RUNTIME_PIPE_SEAL,
            ))
            continue
        if stream is None:
            continue
        if id(stream) in seen:
            continue
        seen.add(id(stream))
        if type(stream) in {str, bytes}:
            claimed.append(_RuntimeOwnedPipe(
                stream,
                True,
                f"{name}:immutable-test-payload",
                _RUNTIME_PIPE_SEAL,
            ))
            continue
        supported = _is_runtime_owned_pipe(stream)
        claimed.append(_RuntimeOwnedPipe(
            stream,
            supported,
            f"{name}:runtime-popen-pipe" if supported else f"{name}:unsupported",
            _RUNTIME_PIPE_SEAL,
        ))
    return tuple(claimed)


def _read_runtime_owned_pipe(
    pipe: _RuntimeOwnedPipe,
    size: int,
) -> str | bytes | None:
    if (
        type(pipe) is not _RuntimeOwnedPipe
        or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
        or not pipe.supported
    ):
        raise TypeError("unsupported Runtime pipe")
    stream = pipe.stream
    if type(stream) is io.StringIO:
        return io.StringIO.read(stream, size)
    if type(stream) is io.TextIOWrapper:
        return io.TextIOWrapper.read(stream, size)
    raise TypeError("unsupported Runtime pipe")


def _close_runtime_owned_pipe(pipe: _RuntimeOwnedPipe) -> None:
    if (
        type(pipe) is not _RuntimeOwnedPipe
        or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
        or not pipe.supported
    ):
        raise TypeError("unsupported Runtime pipe")
    stream = pipe.stream
    if type(stream) is io.StringIO:
        io.StringIO.close(stream)
        return
    if type(stream) is io.TextIOWrapper:
        io.TextIOWrapper.close(stream)
        return
    raise TypeError("unsupported Runtime pipe")


def _runtime_owned_pipe_closed(pipe: _RuntimeOwnedPipe) -> bool:
    if (
        type(pipe) is not _RuntimeOwnedPipe
        or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
        or not pipe.supported
    ):
        return False
    stream = pipe.stream
    if type(stream) is io.StringIO:
        return bool(io.StringIO.closed.__get__(stream, io.StringIO))
    if type(stream) is io.TextIOWrapper:
        return bool(io.TextIOWrapper.closed.__get__(stream, io.TextIOWrapper))
    if type(stream) in {str, bytes, type(None)}:
        return True
    return False


def _close_runtime_owned_pipes(
    pipes: tuple[_RuntimeOwnedPipe, ...],
    deadline: float,
) -> tuple[bool, Mapping[str, object]]:
    if not pipes:
        return True, MappingProxyType({
            "attempted": False,
            "outcome": "skipped_no_handles",
        })
    attempted = 0
    failures = 0
    unsupported = 0
    deadline_failures = 0
    for pipe in pipes:
        if (
            type(pipe) is not _RuntimeOwnedPipe
            or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
            or not pipe.supported
        ):
            unsupported += 1
            continue
        stream = pipe.stream
        if stream is None:
            continue
        if type(stream) in {str, bytes}:
            continue
        if _remaining_cleanup_seconds(deadline) <= 0:
            deadline_failures += 1
            continue
        attempted += 1
        try:
            _close_runtime_owned_pipe(pipe)
        except (OSError, ValueError):
            failures += 1
        except BaseException:
            failures += 1
        if _remaining_cleanup_seconds(deadline) <= 0:
            deadline_failures += 1
    clean = unsupported == 0 and failures == 0 and deadline_failures == 0
    if unsupported:
        outcome = "unsupported_stream_not_called"
    elif deadline_failures:
        outcome = "deadline_exceeded"
    elif failures:
        outcome = "close_failed"
    elif attempted == 0:
        outcome = "no_close_required"
    else:
        outcome = "closed"
    return clean, MappingProxyType({
        "attempted": attempted > 0,
        "attempts": attempted,
        "outcome": outcome,
        "unsupported": unsupported,
    })


def _join_runtime_reader(
    reader: threading.Thread | None,
    reader_done: threading.Event | None,
    reader_failed: threading.Event | None,
    deadline: float,
) -> tuple[bool, Mapping[str, object]]:
    if reader is None:
        return True, MappingProxyType({
            "attempted": False,
            "outcome": "skipped_no_reader",
        })
    if type(reader) is not threading.Thread:
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "unsupported_reader_not_called",
        })
    attempted = False
    if reader.is_alive():
        remaining = _remaining_cleanup_seconds(deadline)
        if remaining <= 0:
            return False, MappingProxyType({
                "attempted": False,
                "outcome": "deadline_exceeded",
            })
        attempted = True
        reader.join(timeout=remaining)
    alive = reader.is_alive()
    done = type(reader_done) is threading.Event and reader_done.is_set()
    failed = type(reader_failed) is threading.Event and reader_failed.is_set()
    within_deadline = _remaining_cleanup_seconds(deadline) > 0
    clean = not alive and done and not failed and within_deadline
    if alive:
        outcome = "reader_live_at_deadline"
    elif not done:
        outcome = "reader_completion_unverified"
    elif failed:
        outcome = "reader_failed"
    elif not within_deadline:
        outcome = "completed_after_deadline"
    else:
        outcome = "joined"
    return clean, MappingProxyType({
        "attempted": attempted,
        "outcome": outcome,
    })


def _close_runtime_private_environment(
    private_environment: _RuntimePrivateEnvironment | None,
    deadline: float,
) -> tuple[bool, Mapping[str, object]]:
    if private_environment is None:
        return True, MappingProxyType({
            "attempted": False,
            "outcome": "skipped_no_private_environment",
        })
    if (
        type(private_environment) is not _RuntimePrivateEnvironment
        or private_environment._runtime_seal is not _PRIVATE_HANDLE_SEAL
        or type(private_environment.root) is not type(Path("/"))
    ):
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "unsupported_private_environment_not_called",
        })
    if _remaining_cleanup_seconds(deadline) <= 0:
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "deadline_exceeded",
        })
    try:
        closed = _remove_private_root(private_environment.root)
    except BaseException:
        closed = False
    within_deadline = _remaining_cleanup_seconds(deadline) > 0
    return closed and within_deadline, MappingProxyType({
        "attempted": True,
        "outcome": (
            "closed"
            if closed and within_deadline
            else "completed_after_deadline"
            if closed
            else "close_failed"
        ),
    })


def _finalize_process(
    process: Any,
    *,
    terminate: bool,
    drain_output: bool = False,
    close_process_streams: bool = True,
    cleanup_started_monotonic: float | None = None,
    runtime_owned_pipes: tuple[_RuntimeOwnedPipe, ...] | None = None,
    runtime_private_environment: _RuntimePrivateEnvironment | None = None,
    runtime_background_log: _RuntimeBackgroundLog | None = None,
    reader: threading.Thread | None = None,
    reader_done: threading.Event | None = None,
    reader_failed: threading.Event | None = None,
) -> tuple[Mapping[str, object], str, bool, tuple[str, str]]:
    started = (
        time.monotonic()
        if cleanup_started_monotonic is None
        else float(cleanup_started_monotonic)
    )
    deadline = started + _CLEANUP_BARRIER_SECONDS
    reaped = False
    failed = False
    drained_stdout = ""
    drained_stderr = ""
    try:
        pid = int(process.pid)
    except BaseException:
        pid = -1
        failed = True
    phases: dict[str, dict[str, object]] = {
        "term": {
            "attempted": False,
            "outcome": "pending",
        },
        "kill": {
            "attempted": False,
            "outcome": "pending",
        },
        "wait_reap": {
            "attempted": False,
            "attempts": 0,
            "outcome": "pending",
        },
        "verify": {
            "attempted": False,
            "outcome": "pending",
        },
    }
    owned_outcomes: dict[str, Mapping[str, object]] = {}

    def signal_group(sig: int, phase: str) -> None:
        nonlocal failed
        if _remaining_cleanup_seconds(deadline) <= 0:
            phases[phase]["outcome"] = "deadline_exceeded"
            failed = True
            return
        phases[phase]["attempted"] = True
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            phases[phase]["outcome"] = "process_group_absent"
        except (PermissionError, OSError):
            phases[phase]["outcome"] = "signal_failed"
            failed = True
        except BaseException:
            phases[phase]["outcome"] = "signal_failed"
            failed = True
        else:
            phases[phase]["outcome"] = "signal_sent"
        if _remaining_cleanup_seconds(deadline) <= 0:
            phases[phase]["outcome"] = "completed_after_deadline"
            failed = True

    def wait_once(wait_deadline: float | None = None) -> None:
        nonlocal failed, reaped
        effective_deadline = min(
            deadline,
            deadline if wait_deadline is None else wait_deadline,
        )
        remaining = max(0.0, effective_deadline - time.monotonic())
        if remaining <= 0:
            cleanup_expired = _remaining_cleanup_seconds(deadline) <= 0
            phases["wait_reap"]["outcome"] = (
                "deadline_exceeded"
                if cleanup_expired
                else "term_grace_expired"
            )
            if cleanup_expired:
                failed = True
            return
        phases["wait_reap"]["attempted"] = True
        phases["wait_reap"]["attempts"] = (
            int(phases["wait_reap"]["attempts"]) + 1
        )
        try:
            process.wait(timeout=min(1.0, remaining))
            reaped = True
        except subprocess.TimeoutExpired:
            phases["wait_reap"]["outcome"] = "timed_out"
        except BaseException:
            phases["wait_reap"]["outcome"] = "wait_failed"
            failed = True
        else:
            phases["wait_reap"]["outcome"] = (
                "reaped_after_error" if failed else "reaped"
            )
        if _remaining_cleanup_seconds(deadline) <= 0:
            phases["wait_reap"]["outcome"] = "completed_after_deadline"
            failed = True

    def group_alive_for_escalation() -> bool | None:
        nonlocal failed
        if _remaining_cleanup_seconds(deadline) <= 0:
            failed = True
            return None
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            failed = True
            return None
        except BaseException:
            failed = True
            return None
        if _remaining_cleanup_seconds(deadline) <= 0:
            failed = True
            return None
        return True

    def wait_for_group_disappearance(
        group_deadline: float,
    ) -> bool | None:
        nonlocal failed
        effective_deadline = min(deadline, group_deadline)
        while True:
            group_alive = group_alive_for_escalation()
            if group_alive is False:
                return False
            if group_alive is None:
                return None
            remaining = max(
                0.0,
                effective_deadline - time.monotonic(),
            )
            if remaining <= 0:
                return group_alive
            try:
                _wait_cleanup_poll(min(_CLEANUP_POLL_SECONDS, remaining))
            except BaseException:
                failed = True
                return None

    if not terminate:
        # A completed session leader does not prove that descendants in the
        # Runtime-owned start_new_session PGID are gone.  Reap first, then
        # revoke that exact owned group if the capability is still live.
        wait_once()
        if not reaped:
            failed = True
        group_alive = group_alive_for_escalation()
        if group_alive is False:
            phases["term"]["outcome"] = "skipped_process_group_absent"
            phases["kill"]["outcome"] = "skipped_process_group_absent"
        else:
            term_started = time.monotonic()
            term_deadline = min(
                deadline,
                term_started + _TERM_GRACE_SECONDS,
            )
            signal_group(signal.SIGTERM, "term")
            if not reaped:
                wait_once(term_deadline)
            group_alive = (
                wait_for_group_disappearance(term_deadline)
                if reaped
                else True
            )
            if group_alive is False:
                phases["kill"]["outcome"] = "skipped_process_group_absent"
            else:
                signal_group(signal.SIGKILL, "kill")
                if not reaped:
                    wait_once()
    else:
        term_started = time.monotonic()
        term_deadline = min(
            deadline,
            term_started + _TERM_GRACE_SECONDS,
        )
        signal_group(signal.SIGTERM, "term")
        if not reaped:
            wait_once(term_deadline)
        group_alive = (
            True
            if not reaped
            else wait_for_group_disappearance(term_deadline)
        )
        if group_alive is not False:
            signal_group(signal.SIGKILL, "kill")
            if not reaped:
                wait_once()
        else:
            phases["kill"]["outcome"] = "skipped_process_group_absent"
        if not reaped:
            failed = True
    if drain_output:
        remaining = _remaining_cleanup_seconds(deadline)
        drain_attempted = False
        try:
            if remaining <= 0:
                failed = True
                drain_outcome = "deadline_exceeded"
            else:
                drain_attempted = True
                drained_stdout, drained_stderr = process.communicate(
                    timeout=remaining
                )
                reaped = True
                drain_outcome = "drained"
        except BaseException:
            drain_outcome = "communicate_failed"
            failed = True
        if _remaining_cleanup_seconds(deadline) <= 0:
            drain_outcome = "completed_after_deadline"
            failed = True
        owned_outcomes["output_drain"] = MappingProxyType({
            "attempted": drain_attempted,
            "outcome": drain_outcome,
        })
    else:
        owned_outcomes["output_drain"] = MappingProxyType({
            "attempted": False,
            "outcome": "skipped_not_requested",
        })
    if runtime_owned_pipes is None:
        runtime_owned_pipes = _claim_runtime_owned_pipes(
            process,
            ("stdout", "stderr"),
        )
    # StringIO is an exact, non-blocking test seam, so let its reader finish
    # before close. Runtime Popen pipes (and patched blocking fakes) are closed
    # first to request reader convergence; the synchronous close syscall is
    # checked before/after the same deadline but is not Python-preemptible.
    def cooperative_reader(pipe: _RuntimeOwnedPipe) -> bool:
        if (
            type(pipe) is not _RuntimeOwnedPipe
            or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
        ):
            return False
        if type(pipe.stream) in {str, bytes}:
            return True
        if type(pipe.stream) is io.StringIO:
            return True
        if type(pipe.stream) is io.TextIOWrapper:
            try:
                descriptor = io.TextIOWrapper.fileno(pipe.stream)
                return os.get_blocking(descriptor) is False
            except (OSError, ValueError):
                return False
        return False

    join_before_close = (
        reader is not None
        and bool(runtime_owned_pipes)
        and all(cooperative_reader(pipe) for pipe in runtime_owned_pipes)
    )
    if join_before_close:
        reader_clean, reader_outcome = _join_runtime_reader(
            reader,
            reader_done,
            reader_failed,
            deadline,
        )
    else:
        reader_clean = True
        reader_outcome = MappingProxyType({
            "attempted": False,
            "outcome": "pending_stream_close",
        })
    if close_process_streams:
        streams_clean, stream_outcome = _close_runtime_owned_pipes(
            runtime_owned_pipes,
            deadline,
        )
        owned_outcomes["streams"] = stream_outcome
        if not streams_clean:
            failed = True
    else:
        owned_outcomes["streams"] = MappingProxyType({
            "attempted": False,
            "outcome": "skipped_by_protocol",
        })
    if not join_before_close:
        reader_clean, reader_outcome = _join_runtime_reader(
            reader,
            reader_done,
            reader_failed,
            deadline,
        )
    owned_outcomes["reader"] = reader_outcome
    if not reader_clean:
        failed = True
    log_clean, log_outcome, _ = _persist_runtime_background_log(
        runtime_background_log,
        deadline,
    )
    owned_outcomes["background_log"] = log_outcome
    if not log_clean:
        failed = True
    private_clean, private_outcome = _close_runtime_private_environment(
        runtime_private_environment,
        deadline,
    )
    owned_outcomes["private_environment"] = private_outcome
    if not private_clean:
        failed = True
    verified = False
    if _remaining_cleanup_seconds(deadline) <= 0:
        phases["verify"]["outcome"] = "deadline_exceeded"
        failed = True
    else:
        phases["verify"]["attempted"] = True
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            verified = True
            phases["verify"]["outcome"] = "process_group_absent"
        except (PermissionError, OSError):
            phases["verify"]["outcome"] = "probe_failed"
            failed = True
        except BaseException:
            phases["verify"]["outcome"] = "probe_failed"
            failed = True
        else:
            phases["verify"]["outcome"] = "process_group_alive"
            failed = True
        if _remaining_cleanup_seconds(deadline) <= 0:
            phases["verify"]["outcome"] = "completed_after_deadline"
            failed = True
    duration_seconds = time.monotonic() - started
    if duration_seconds > _CLEANUP_BARRIER_SECONDS:
        failed = True
    evidence, digest = _cleanup_evidence(
        pid=pid,
        reaped=reaped,
        verified=verified and not failed,
        duration_seconds=duration_seconds,
        phase_outcomes=phases,
        owned_outcomes=owned_outcomes,
    )
    return (
        evidence,
        digest,
        not failed and reaped and verified,
        (_text(drained_stdout), _text(drained_stderr)),
    )


@dataclass(frozen=True)
class _OwnedResources:
    process: Any
    reader: threading.Thread | None = None
    reader_done: threading.Event | None = None
    streams: tuple[_RuntimeOwnedPipe, ...] = ()
    private_root: Path | None = None


def _owned_resource_snapshot(
    owned: _OwnedResources,
) -> tuple[Mapping[str, object], bool]:
    direct_child_live = True
    try:
        direct_child_live = owned.process.poll() is None
    except BaseException:
        direct_child_live = True
    process_group_live = True
    try:
        os.killpg(owned.process.pid, 0)
    except ProcessLookupError:
        process_group_live = False
    except (PermissionError, OSError, AttributeError, TypeError):
        process_group_live = True
    reader_live = False
    if owned.reader is not None:
        try:
            reader_live = owned.reader.is_alive()
        except BaseException:
            reader_live = True
    reader_incomplete = False
    if owned.reader_done is not None:
        try:
            reader_incomplete = not owned.reader_done.is_set()
        except BaseException:
            reader_incomplete = True
    open_streams = 0
    for pipe in owned.streams:
        if (
            type(pipe) is not _RuntimeOwnedPipe
            or pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
            or not pipe.supported
        ):
            open_streams += 1
            continue
        stream = pipe.stream
        if stream is None or type(stream) in {str, bytes}:
            continue
        if type(stream) not in {io.StringIO, io.TextIOWrapper}:
            open_streams += 1
            continue
        closed = _runtime_owned_pipe_closed(pipe)
        if not closed:
            open_streams += 1
    private_root_present = False
    if owned.private_root is not None:
        if type(owned.private_root) is not type(Path("/")):
            private_root_present = True
        else:
            try:
                private_root_present = Path.exists(owned.private_root)
            except OSError:
                private_root_present = True
    snapshot = MappingProxyType({
        "direct_child_live": direct_child_live,
        "process_group_live": process_group_live,
        "reader_live": reader_live,
        "reader_incomplete": reader_incomplete,
        "open_stream_count": open_streams,
        "private_root_present": private_root_present,
    })
    live = (
        direct_child_live
        or process_group_live
        or reader_live
        or reader_incomplete
        or open_streams > 0
        or private_root_present
    )
    return snapshot, live


@dataclass
class _QuarantineRecord:
    quarantine_id: str
    generation: int
    workspace_root: Path
    process: Any
    owned_resources: _OwnedResources
    cleanup_evidence: Mapping[str, object]
    cleanup_evidence_digest: str
    recovery_request: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _CleanupFenceRecord:
    execution_ids: frozenset[str]
    workspace_root: Path


_STATE_LOCK = threading.RLock()
_QUARANTINE_BY_ID: dict[str, _QuarantineRecord] = {}
_QUARANTINE_BY_WORKSPACE: dict[str, _QuarantineRecord] = {}
_CLEANUP_FENCE_BY_WORKSPACE: dict[str, _CleanupFenceRecord] = {}
_WORKSPACE_GENERATION: dict[str, int] = {}
_WORKSPACE_ADMISSION_EPOCH: dict[str, int] = {}
_WORKSPACE_GATES: dict[str, threading.Lock] = {}


def _publish_cleanup_fence(
    prepared: PreparedExecution,
    execution_id: str,
) -> None:
    workspace_key = str(prepared.workspace_root)
    with _STATE_LOCK:
        current = _CLEANUP_FENCE_BY_WORKSPACE.get(workspace_key)
        execution_ids = (
            frozenset({execution_id})
            if current is None
            else current.execution_ids | {execution_id}
        )
        if current is None or execution_id not in current.execution_ids:
            _CLEANUP_FENCE_BY_WORKSPACE[workspace_key] = _CleanupFenceRecord(
                execution_ids,
                prepared.workspace_root,
            )
            _WORKSPACE_ADMISSION_EPOCH[workspace_key] = (
                _WORKSPACE_ADMISSION_EPOCH.get(workspace_key, 0) + 1
            )


def _clear_cleanup_fence(
    prepared: PreparedExecution,
    execution_id: str,
) -> None:
    workspace_key = str(prepared.workspace_root)
    with _STATE_LOCK:
        current = _CLEANUP_FENCE_BY_WORKSPACE.get(workspace_key)
        if (
            current is not None
            and execution_id in current.execution_ids
            and workspace_key not in _QUARANTINE_BY_WORKSPACE
        ):
            remaining = current.execution_ids - {execution_id}
            if remaining:
                _CLEANUP_FENCE_BY_WORKSPACE[workspace_key] = (
                    _CleanupFenceRecord(remaining, current.workspace_root)
                )
            else:
                _CLEANUP_FENCE_BY_WORKSPACE.pop(workspace_key, None)


def _workspace_gate(root: Path) -> threading.Lock:
    key = str(Path(root).resolve())
    with _STATE_LOCK:
        gate = _WORKSPACE_GATES.get(key)
        if gate is None:
            gate = threading.Lock()
            _WORKSPACE_GATES[key] = gate
        return gate


def _bound_workspace_digest(root: Path) -> str:
    canonical_root = Path(root).resolve()
    key = str(canonical_root)
    with _STATE_LOCK:
        epoch = _WORKSPACE_ADMISSION_EPOCH.get(key, 0)
    return _canonical_digest({
        "schema": "local-execution-workspace-binding/v1",
        "workspace_digest": workspace_digest(canonical_root),
        "admission_epoch": epoch,
    })


def _quarantine(
    prepared: PreparedExecution,
    process: Any,
    evidence: Mapping[str, object],
    digest: str,
    *,
    owned_resources: _OwnedResources | None = None,
) -> LocalExecutionError:
    workspace_key = str(prepared.workspace_root)
    with _STATE_LOCK:
        _CLEANUP_FENCE_BY_WORKSPACE.pop(workspace_key, None)
        generation = _WORKSPACE_GENERATION.get(workspace_key, 0) + 1
        _WORKSPACE_GENERATION[workspace_key] = generation
        _WORKSPACE_ADMISSION_EPOCH[workspace_key] = (
            _WORKSPACE_ADMISSION_EPOCH.get(workspace_key, 0) + 1
        )
        quarantine_id = "local-exec-quarantine-" + uuid.uuid4().hex
        record = _QuarantineRecord(
            quarantine_id=quarantine_id,
            generation=generation,
            workspace_root=prepared.workspace_root,
            process=process,
            owned_resources=(
                owned_resources
                if owned_resources is not None
                else _OwnedResources(process)
            ),
            cleanup_evidence=evidence,
            cleanup_evidence_digest=digest,
        )
        _QUARANTINE_BY_ID[quarantine_id] = record
        _QUARANTINE_BY_WORKSPACE[workspace_key] = record
    return LocalExecutionError(
        CLEANUP_FAILED,
        "local execution cleanup could not be verified",
        cleanup_evidence=evidence,
        cleanup_evidence_digest=digest,
        quarantine_id=quarantine_id,
        quarantine_generation=generation,
    )


def run_prepared(
    prepared: PreparedExecution,
    *,
    trusted_local: object = None,
    lifecycle: object | None = None,
    poll_interval: float | None = None,
    stdout_contains: tuple[str, ...] = (),
    stderr_contains: tuple[str, ...] = (),
    reject_zero_tests: bool = False,
) -> ExecutionOutcome:
    prepared = _assert_prepared(prepared)
    gate = _workspace_gate(prepared.workspace_root)
    with gate:
        return _run_prepared_locked(
            prepared,
            trusted_local=trusted_local,
            lifecycle=lifecycle,
            poll_interval=poll_interval,
            stdout_contains=stdout_contains,
            stderr_contains=stderr_contains,
            reject_zero_tests=reject_zero_tests,
        )


def _run_prepared_locked(
    prepared: PreparedExecution,
    *,
    trusted_local: object = None,
    lifecycle: object | None = None,
    poll_interval: float | None = None,
    stdout_contains: tuple[str, ...] = (),
    stderr_contains: tuple[str, ...] = (),
    reject_zero_tests: bool = False,
) -> ExecutionOutcome:
    if (
        not isinstance(stdout_contains, tuple)
        or not all(isinstance(item, str) and item for item in stdout_contains)
        or not isinstance(stderr_contains, tuple)
        or not all(isinstance(item, str) and item for item in stderr_contains)
        or not isinstance(reject_zero_tests, bool)
    ):
        _reject("invalid internal output assertion request")
    _consume_confirmation(prepared, trusted_local)
    private = _private_environment(
        python_profile=prepared.python_profile,
        codex_profile=prepared.profile_id == PROFILE_CODEX_CLI,
    )
    private_handle = _claim_runtime_private_environment(private)
    started = time.monotonic()
    process = None
    raw_stdout = ""
    raw_stderr = ""
    timed_out = False
    pending_error: BaseException | None = None
    pending_cleanup_error: LocalExecutionError | None = None
    cleanup: Mapping[str, object] = MappingProxyType({})
    cleanup_digest = ""
    private_closed = False
    runtime_pipes: tuple[_RuntimeOwnedPipe, ...] = ()

    try:
        process = _spawn(prepared, private.values, background=False)
        runtime_pipes = _claim_runtime_owned_pipes(
            process,
            ("stdout", "stderr"),
        )

        def owned_resources() -> _OwnedResources:
            return _OwnedResources(
                process,
                streams=runtime_pipes,
                private_root=private_handle.root,
            )

        try:
            if poll_interval is None:
                communicate_kwargs: dict[str, object] = {
                    "timeout": prepared.wall_deadline_seconds,
                }
                if prepared.stdin_text:
                    communicate_kwargs["input"] = prepared.stdin_text
                raw_stdout, raw_stderr = process.communicate(
                    **communicate_kwargs
                )
            else:
                pending_stdin = prepared.stdin_text or None
                while True:
                    checkpoint = getattr(lifecycle, "checkpoint", None)
                    if callable(checkpoint):
                        checkpoint()
                    remaining = prepared.wall_deadline_seconds - (
                        time.monotonic() - started
                    )
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(
                            prepared.execution_argv,
                            prepared.wall_deadline_seconds,
                        )
                    try:
                        communicate_kwargs = {
                            "timeout": min(poll_interval, remaining),
                        }
                        if pending_stdin is not None:
                            communicate_kwargs["input"] = pending_stdin
                        raw_stdout, raw_stderr = process.communicate(
                            **communicate_kwargs
                        )
                        break
                    except subprocess.TimeoutExpired:
                        pending_stdin = None
                        continue
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            partial_stdout = _text(exc.stdout)
            partial_stderr = _text(exc.stderr)
            cleanup, cleanup_digest, clean, drained = _finalize_process(
                process,
                terminate=True,
                drain_output=True,
                runtime_owned_pipes=runtime_pipes,
                runtime_private_environment=private_handle,
            )
            if not clean:
                pending_cleanup_error = _quarantine(
                    prepared,
                    process,
                    cleanup,
                    cleanup_digest,
                    owned_resources=owned_resources(),
                )
            else:
                raw_stdout = partial_stdout + drained[0]
                raw_stderr = partial_stderr + drained[1]
        except BaseException as exc:
            try:
                terminate = process.poll() is None
            except BaseException:
                terminate = True
            cleanup, cleanup_digest, clean, _ = _finalize_process(
                process,
                terminate=terminate,
                runtime_owned_pipes=runtime_pipes,
                runtime_private_environment=private_handle,
            )
            if not clean:
                pending_cleanup_error = _quarantine(
                    prepared,
                    process,
                    cleanup,
                    cleanup_digest,
                    owned_resources=owned_resources(),
                )
            else:
                public_manifest = _public_value(prepared.profile_manifest)
                assert isinstance(public_manifest, Mapping)
                pending_error = _sanitized_post_spawn_exception(
                    exc,
                    cleanup_evidence=cleanup,
                    cleanup_evidence_digest=cleanup_digest,
                    profile_manifest=public_manifest,
                )
            exc = None
        else:
            cleanup, cleanup_digest, clean, _ = _finalize_process(
                process,
                terminate=False,
                runtime_owned_pipes=runtime_pipes,
                runtime_private_environment=private_handle,
            )
            if not clean:
                raise _quarantine(
                    prepared,
                    process,
                    cleanup,
                    cleanup_digest,
                    owned_resources=owned_resources(),
                )
        if pending_cleanup_error is not None:
            raise pending_cleanup_error from None
        if pending_error is not None:
            raise pending_error from None
        stdout = sanitize_output(
            raw_stdout,
            limit_chars=prepared.output_limit_chars,
        )
        stderr = sanitize_output(
            raw_stderr,
            limit_chars=prepared.output_limit_chars,
        )
        assertion_results = MappingProxyType({
            "stdout_contains": tuple(
                expected in raw_stdout for expected in stdout_contains
            ),
            "stderr_contains": tuple(
                expected in raw_stderr for expected in stderr_contains
            ),
            "zero_tests_absent": not (
                reject_zero_tests
                and (
                    "Ran 0 tests" in raw_stdout
                    or "Ran 0 tests" in raw_stderr
                )
            ),
        })
        return ExecutionOutcome(
            process.returncode,
            stdout,
            stderr,
            max(0, int((time.monotonic() - started) * 1000)),
            timed_out,
            _public_value(prepared.profile_manifest),
            cleanup,
            cleanup_digest,
            assertion_results,
        )
    except OSError as exc:
        if process is not None:
            raise
        safe_reason = redact_text(str(exc))
        try:
            exc.args = (safe_reason,)
        except (AttributeError, TypeError):
            pass
        raise LocalExecutionError(
            SANDBOX_REQUIRED,
            f"local execution spawn failed: {safe_reason}",
        ) from None
    finally:
        # Once spawn succeeds, the unified Finalizer owns private cleanup and
        # quarantine evidence.  Never retry rmtree outside its absolute
        # deadline, because that would make a reported barrier meaningless.
        if process is None and not private_closed:
            _close_private_environment_unbound(private)


@dataclass
class _BackgroundOutputState:
    stream_output: _StreamingBoundedOutput
    bounded_log: BoundedOutput
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _runtime_seal: object = field(default=None, repr=False)

    def publish(self, raw: str) -> None:
        with self.lock:
            self.stream_output.feed(raw)
            bounded = self.stream_output.snapshot()
            self.bounded_log = bounded

    def finish(self) -> None:
        with self.lock:
            self.stream_output.finish()
            bounded = self.stream_output.snapshot()
            self.bounded_log = bounded

    def snapshot(self) -> BoundedOutput:
        with self.lock:
            return self.bounded_log


@dataclass(frozen=True)
class _RuntimeBackgroundLog:
    path: Path
    output_state: _BackgroundOutputState
    _runtime_seal: object = field(repr=False)
    persist_lock: object = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )


def _persist_runtime_background_log(
    runtime_log: _RuntimeBackgroundLog | None,
    deadline: float,
) -> tuple[bool, Mapping[str, object], BoundedOutput | None]:
    if runtime_log is None:
        return True, MappingProxyType({
            "attempted": False,
            "outcome": "skipped_no_background_log",
        }), None
    if (
        type(runtime_log) is not _RuntimeBackgroundLog
        or runtime_log._runtime_seal is not _BACKGROUND_LOG_SEAL
        or type(runtime_log.path) is not type(Path("/"))
        or type(runtime_log.output_state) is not _BackgroundOutputState
        or runtime_log.output_state._runtime_seal is not _BACKGROUND_OUTPUT_SEAL
        or type(runtime_log.persist_lock) is not _LOCK_TYPE
    ):
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "unsupported_background_log_not_called",
        }), None
    if _remaining_cleanup_seconds(deadline) <= 0:
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "deadline_exceeded",
        }), None
    remaining = _remaining_cleanup_seconds(deadline)
    if not runtime_log.persist_lock.acquire(timeout=remaining):
        return False, MappingProxyType({
            "attempted": False,
            "outcome": "persist_lock_deadline_exceeded",
        }), None
    descriptor = -1
    attempted = False
    closed = False
    try:
        bounded = _BackgroundOutputState.snapshot(runtime_log.output_state)
        payload = bounded.text.encode("utf-8", errors="replace")
        Path.mkdir(runtime_log.path.parent, parents=True, exist_ok=True)
        if _remaining_cleanup_seconds(deadline) <= 0:
            return False, MappingProxyType({
                "attempted": False,
                "outcome": "completed_after_deadline",
            }), None
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        attempted = True
        descriptor = os.open(str(runtime_log.path), flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False, MappingProxyType({
                "attempted": True,
                "outcome": "non_regular_log_rejected",
            }), None
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            if _remaining_cleanup_seconds(deadline) <= 0:
                return False, MappingProxyType({
                    "attempted": True,
                    "outcome": "deadline_exceeded",
                }), None
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                return False, MappingProxyType({
                    "attempted": True,
                    "outcome": "write_made_no_progress",
                }), None
            offset += written
        if _remaining_cleanup_seconds(deadline) <= 0:
            return False, MappingProxyType({
                "attempted": True,
                "outcome": "completed_after_deadline",
            }), None
    except BaseException:
        return False, MappingProxyType({
            "attempted": attempted,
            "outcome": "persist_failed",
        }), None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
                closed = True
            except OSError:
                closed = False
        runtime_log.persist_lock.release()
    within_deadline = _remaining_cleanup_seconds(deadline) > 0
    persisted = closed and within_deadline
    return persisted, MappingProxyType({
        "attempted": attempted,
        "outcome": (
            "persisted"
            if persisted
            else "close_failed"
            if not closed
            else "completed_after_deadline"
        ),
    }), bounded if persisted else None


@dataclass
class SupervisedBackground:
    prepared: PreparedExecution
    process: Any
    log_path: Path
    private_environment: _PrivateEnvironment
    _runtime_private_environment: _RuntimePrivateEnvironment
    reader: threading.Thread
    reader_done: threading.Event
    reader_failed: threading.Event
    started_monotonic: float
    wall_deadline_monotonic: float
    _runtime_pipe: _RuntimeOwnedPipe
    _output_state: _BackgroundOutputState = field(repr=False)
    _runtime_log: _RuntimeBackgroundLog = field(repr=False)
    _stop_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
    )
    _stop_requested: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
    )
    _cleanup_done: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
    )
    _watchdog_done: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
    )
    _watchdog: threading.Thread | None = field(default=None, repr=False)
    _cleanup_started_monotonic: float | None = field(default=None, repr=False)
    _terminal_reason: str = field(default="", repr=False)
    _cleanup_error: LocalExecutionError | None = field(default=None, repr=False)
    _stopped: bool = False
    _cleanup_evidence_state: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _cleanup_evidence_digest_state: str = field(default="", repr=False)
    _execution_id: str = field(
        default_factory=lambda: "local-execution-" + uuid.uuid4().hex,
        repr=False,
    )

    def __post_init__(self) -> None:
        phases = {
            phase: {
                "attempted": False,
                "outcome": "not_started",
            }
            for phase in ("term", "kill", "wait_reap", "verify")
        }
        evidence, digest = _cleanup_evidence(
            pid=getattr(self.process, "pid", -1),
            reaped=False,
            verified=False,
            duration_seconds=0.0,
            status="idle",
            phase_outcomes=phases,
        )
        self._cleanup_evidence_state = evidence
        self._cleanup_evidence_digest_state = digest

    @property
    def running(self) -> bool:
        with self._stop_lock:
            if self._stopped or self._stop_requested.is_set():
                return False
        try:
            return self.process.poll() is None
        except BaseException:
            return False

    @property
    def cleanup_evidence(self) -> Mapping[str, object]:
        with self._stop_lock:
            return self._cleanup_evidence_state

    @property
    def cleanup_evidence_digest(self) -> str:
        with self._stop_lock:
            return self._cleanup_evidence_digest_state

    @property
    def cleanup_terminal(self) -> bool:
        """Monotonic public seam for safe snapshot/detach decisions."""
        with self._stop_lock:
            watchdog = self._watchdog
            return (
                self._stopped
                and self._cleanup_done.is_set()
                and self._watchdog_done.is_set()
                and (watchdog is None or not watchdog.is_alive())
                and not self.reader.is_alive()
                and self.reader_done.is_set()
                and _valid_digest(self._cleanup_evidence_digest_state)
                and self._cleanup_evidence_state.get("status") == "terminal"
            )

    @property
    def profile_manifest(self) -> Mapping[str, object]:
        manifest = _public_value(self.prepared.profile_manifest)
        if not isinstance(manifest, Mapping):
            return MappingProxyType({})
        return manifest

    @property
    def server_log(self) -> Mapping[str, object]:
        bounded = _BackgroundOutputState.snapshot(self._output_state)
        return MappingProxyType({
            "chars": bounded.raw_chars,
            "sha256": bounded.raw_sha256,
            "truncated": bounded.truncated,
        })

    @property
    def server_log_chars(self) -> int:
        return int(self.server_log["chars"])

    @property
    def server_log_sha256(self) -> str:
        return str(self.server_log["sha256"])

    @property
    def server_log_truncated(self) -> bool:
        return bool(self.server_log["truncated"])

    def log_tail(self, limit: int = 4000) -> str:
        if not self.cleanup_terminal:
            with self._stop_lock:
                cleanup_started = self._cleanup_started_monotonic
            persist_deadline = (
                self.wall_deadline_monotonic
                if cleanup_started is None
                else cleanup_started + _CLEANUP_BARRIER_SECONDS
            )
            persisted, _, durable_snapshot = _persist_runtime_background_log(
                self._runtime_log,
                persist_deadline,
            )
            if not persisted:
                self.request_stop("log_persist_failed")
                text = _BackgroundOutputState.snapshot(self._output_state).text
            elif durable_snapshot is not None:
                text = durable_snapshot.text
            else:
                text = _BackgroundOutputState.snapshot(self._output_state).text
        else:
            text = _BackgroundOutputState.snapshot(self._output_state).text
        if limit >= self.prepared.output_limit_chars:
            return text
        return text[-limit:]

    def _owned_resources(self) -> _OwnedResources:
        return _OwnedResources(
            self.process,
            reader=self.reader,
            reader_done=self.reader_done,
            streams=(self._runtime_pipe,),
            private_root=self._runtime_private_environment.root,
        )

    @staticmethod
    def _running_phases(terminate: bool) -> Mapping[str, Mapping[str, object]]:
        del terminate
        return MappingProxyType({
            "term": MappingProxyType({
                "attempted": False,
                "outcome": "pending",
            }),
            "kill": MappingProxyType({
                "attempted": False,
                "outcome": "pending",
            }),
            "wait_reap": MappingProxyType({
                "attempted": False,
                "attempts": 0,
                "outcome": "pending",
            }),
            "verify": MappingProxyType({
                "attempted": False,
                "outcome": "pending",
            }),
        })

    def _publish_cleanup_evidence_locked(
        self,
        evidence: Mapping[str, object],
        digest: str,
    ) -> None:
        frozen = _deep_freeze(evidence)
        assert isinstance(frozen, Mapping)
        self._cleanup_evidence_state = frozen
        self._cleanup_evidence_digest_state = str(digest)

    def _publish_running_cleanup_locked(
        self,
        started: float,
        terminate: bool,
    ) -> None:
        evidence, digest = _cleanup_evidence(
            pid=getattr(self.process, "pid", -1),
            reaped=False,
            verified=False,
            duration_seconds=max(0.0, time.monotonic() - started),
            status="running",
            phase_outcomes=self._running_phases(terminate),
            owned_outcomes={
                "output_drain": {
                    "attempted": False,
                    "outcome": "pending",
                },
                "streams": {
                    "attempted": False,
                    "outcome": "pending",
                },
                "reader": {
                    "attempted": False,
                    "outcome": "pending",
                },
                "background_log": {
                    "attempted": False,
                    "outcome": "pending",
                },
                "private_environment": {
                    "attempted": False,
                    "outcome": "pending",
                },
            },
        )
        self._publish_cleanup_evidence_locked(evidence, digest)

    def _ensure_quarantine_locked(self) -> LocalExecutionError:
        if self._cleanup_error is None:
            if not _valid_digest(self._cleanup_evidence_digest_state):
                started = self._cleanup_started_monotonic or time.monotonic()
                self._publish_running_cleanup_locked(started, True)
            self._cleanup_error = _quarantine(
                self.prepared,
                self.process,
                self._cleanup_evidence_state,
                self._cleanup_evidence_digest_state,
                owned_resources=self._owned_resources(),
            )
        return self._cleanup_error

    def _publish_terminal_cleanup_locked(
        self,
        evidence: Mapping[str, object],
        digest: str,
    ) -> None:
        if self._cleanup_error is None:
            self._publish_cleanup_evidence_locked(evidence, digest)
            return
        terminal = _json_compatible(evidence)
        assert isinstance(terminal, dict)
        terminal["status"] = "terminal"
        terminal["terminal_cleanup_verified"] = terminal.get("verified") is True
        terminal["verified"] = False
        terminal["quarantined"] = True
        terminal_digest = _canonical_digest(terminal)
        frozen = _deep_freeze(terminal)
        assert isinstance(frozen, Mapping)
        quarantine_id = self._cleanup_error.quarantine_id
        generation = self._cleanup_error.quarantine_generation
        with _STATE_LOCK:
            record = _QUARANTINE_BY_ID.get(quarantine_id)
            if record is not None and record.generation == generation:
                record.cleanup_evidence = frozen
                record.cleanup_evidence_digest = terminal_digest
        self._publish_cleanup_evidence_locked(frozen, terminal_digest)
        self._cleanup_error = LocalExecutionError(
            CLEANUP_FAILED,
            "local execution cleanup could not be verified",
            cleanup_evidence=frozen,
            cleanup_evidence_digest=terminal_digest,
            quarantine_id=quarantine_id,
            quarantine_generation=generation,
        )

    def _claim_cleanup(self, reason: str) -> float:
        with self._stop_lock:
            if self._cleanup_started_monotonic is None:
                self._cleanup_started_monotonic = time.monotonic()
                self._terminal_reason = reason
                _publish_cleanup_fence(self.prepared, self._execution_id)
            return self._cleanup_started_monotonic

    def request_stop(self, reason: str = "explicit") -> None:
        self._claim_cleanup(reason)
        self._stop_requested.set()

    def _watchdog_main(self) -> None:
        try:
            while self._cleanup_started_monotonic is None:
                try:
                    if self.process.poll() is not None:
                        self._claim_cleanup("natural_exit")
                        break
                except BaseException:
                    self._claim_cleanup("poll_failed")
                    break
                remaining = self.wall_deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    self._claim_cleanup("wall_deadline")
                    break
                if self._stop_requested.wait(min(0.05, remaining)):
                    break
            self._perform_cleanup()
        except BaseException:
            started = self._claim_cleanup("watchdog_failed")
            duration = max(0.0, time.monotonic() - started)
            evidence, digest = _cleanup_evidence(
                pid=getattr(self.process, "pid", -1),
                reaped=False,
                verified=False,
                duration_seconds=duration,
                phase_outcomes={
                    phase: {
                        "attempted": False,
                        "outcome": "watchdog_failed_before_completion",
                    }
                    for phase in ("term", "kill", "wait_reap", "verify")
                },
            )
            with self._stop_lock:
                self._publish_cleanup_evidence_locked(evidence, digest)
                self._stopped = True
                self._ensure_quarantine_locked()
        finally:
            with self._stop_lock:
                cleanup_succeeded = (
                    self._cleanup_error is None
                    and self._stopped
                    and self._cleanup_evidence_state.get("verified") is True
                )
            self._cleanup_done.set()
            self._watchdog_done.set()
            if cleanup_succeeded:
                _clear_cleanup_fence(self.prepared, self._execution_id)

    def _perform_cleanup(self) -> None:
        started = self._claim_cleanup("watchdog")
        terminate = self._terminal_reason != "natural_exit"
        with self._stop_lock:
            self._publish_running_cleanup_locked(started, terminate)

        cleanup, digest, clean, _ = _finalize_process(
            self.process,
            terminate=terminate,
            cleanup_started_monotonic=started,
            runtime_owned_pipes=(self._runtime_pipe,),
            runtime_private_environment=self._runtime_private_environment,
            runtime_background_log=self._runtime_log,
            reader=self.reader,
            reader_done=self.reader_done,
            reader_failed=self.reader_failed,
        )
        with self._stop_lock:
            self._publish_terminal_cleanup_locked(cleanup, digest)
            self._stopped = True
            if (
                not clean
                or self.reader.is_alive()
                or self.reader_failed.is_set()
            ):
                self._ensure_quarantine_locked()

    def stop(self) -> None:
        self.request_stop("explicit")
        with self._stop_lock:
            started = self._cleanup_started_monotonic
            watchdog = self._watchdog
        assert started is not None
        deadline = started + _CLEANUP_BARRIER_SECONDS
        remaining = _remaining_cleanup_seconds(deadline)
        if remaining > 0:
            self._cleanup_done.wait(timeout=remaining)
        if (
            watchdog is not None
            and watchdog is not threading.current_thread()
            and watchdog.is_alive()
        ):
            remaining = _remaining_cleanup_seconds(deadline)
            if remaining > 0:
                watchdog.join(timeout=remaining)
        with self._stop_lock:
            error = self._cleanup_error
            complete = self._cleanup_done.is_set() and (
                watchdog is None or not watchdog.is_alive()
            )
            if not complete:
                error = self._ensure_quarantine_locked()
        if not complete:
            assert error is not None
            raise error
        if error is not None:
            raise error

    def evidence(self) -> Mapping[str, object]:
        return MappingProxyType({
            "profile_manifest": self.profile_manifest,
            "cleanup_evidence": self.cleanup_evidence,
            "cleanup_evidence_digest": self.cleanup_evidence_digest,
            "server_log": self.server_log,
        })


def start_prepared_background(
    prepared: PreparedExecution,
    *,
    trusted_local: object = None,
    log_path: Path,
) -> SupervisedBackground:
    prepared = _assert_prepared(prepared)
    gate = _workspace_gate(prepared.workspace_root)
    with gate:
        return _start_prepared_background_locked(
            prepared,
            trusted_local=trusted_local,
            log_path=log_path,
        )


def _start_prepared_background_locked(
    prepared: PreparedExecution,
    *,
    trusted_local: object,
    log_path: Path,
) -> SupervisedBackground:
    _consume_confirmation(prepared, trusted_local)
    private = _private_environment(python_profile=prepared.python_profile)
    private_handle = _claim_runtime_private_environment(private)
    process = None
    reader: threading.Thread | None = None
    runtime_pipe = _RuntimeOwnedPipe(
        None,
        False,
        "stdout:not-claimed",
        _RUNTIME_PIPE_SEAL,
    )
    runtime_log: _RuntimeBackgroundLog | None = None
    done = threading.Event()
    reader_failed = threading.Event()
    pending_failure: BaseException | None = None
    try:
        process = _spawn(prepared, private.values, background=True)
        background_started = time.monotonic()
        claimed = _claim_runtime_owned_pipes(process, ("stdout",))
        runtime_pipe = (
            claimed[0]
            if claimed
            else _RuntimeOwnedPipe(
                None,
                False,
                "stdout:missing",
                _RUNTIME_PIPE_SEAL,
            )
        )
        output_state = _BackgroundOutputState(
            _StreamingBoundedOutput(prepared.output_limit_chars),
            sanitize_output("", limit_chars=prepared.output_limit_chars),
            _runtime_seal=_BACKGROUND_OUTPUT_SEAL,
        )
        runtime_log = _RuntimeBackgroundLog(
            Path(log_path),
            output_state,
            _BACKGROUND_LOG_SEAL,
        )

        def read_output() -> None:
            try:
                if (
                    type(runtime_pipe) is not _RuntimeOwnedPipe
                    or runtime_pipe._runtime_seal is not _RUNTIME_PIPE_SEAL
                    or not runtime_pipe.supported
                ):
                    reader_failed.set()
                    return
                stream = runtime_pipe.stream
                if stream is None:
                    reader_failed.set()
                    return
                if type(stream) in {str, bytes}:
                    _BackgroundOutputState.publish(output_state, _text(stream))
                    return
                while True:
                    chunk = _read_runtime_owned_pipe(runtime_pipe, 4096)
                    if not chunk:
                        break
                    _BackgroundOutputState.publish(output_state, _text(chunk))
            except BaseException:
                reader_failed.set()
            finally:
                try:
                    _BackgroundOutputState.finish(output_state)
                except BaseException:
                    reader_failed.set()
                done.set()

        reader = threading.Thread(
            target=read_output,
            name="local-execution-log-reader",
            daemon=False,
        )
        managed = SupervisedBackground(
            prepared,
            process,
            Path(log_path),
            private,
            private_handle,
            reader,
            done,
            reader_failed,
            background_started,
            background_started + prepared.wall_deadline_seconds,
            runtime_pipe,
            output_state,
            runtime_log,
        )
        watchdog = threading.Thread(
            target=managed._watchdog_main,
            name="local-execution-lifetime-watchdog",
            daemon=False,
        )
        managed._watchdog = watchdog
        reader.start()
        watchdog.start()
        return managed
    except BaseException as exc:
        if process is None:
            _close_private_environment_unbound(private)
            raise
        if reader is None or reader.ident is None:
            done.set()

        cleanup, digest, clean, _ = _finalize_process(
            process,
            terminate=True,
            runtime_owned_pipes=(runtime_pipe,),
            runtime_private_environment=private_handle,
            runtime_background_log=runtime_log,
            reader=reader,
            reader_done=done,
            reader_failed=reader_failed,
        )
        if not clean:
            pending_failure = _quarantine(
                prepared,
                process,
                cleanup,
                digest,
                owned_resources=_OwnedResources(
                    process,
                    reader=reader,
                    reader_done=done,
                    streams=(runtime_pipe,),
                    private_root=private_handle.root,
                ),
            )
        else:
            public_manifest = _public_value(prepared.profile_manifest)
            assert isinstance(public_manifest, Mapping)
            pending_failure = _sanitized_post_spawn_exception(
                exc,
                cleanup_evidence=cleanup,
                cleanup_evidence_digest=digest,
                profile_manifest=public_manifest,
            )
        exc = None
    assert pending_failure is not None
    raise pending_failure from None


def _resource_alive(record: _QuarantineRecord) -> bool:
    _, live = _owned_resource_snapshot(record.owned_resources)
    return live


def _make_recovery_request(
    record: _QuarantineRecord,
    *,
    resource_snapshot: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    if resource_snapshot is None:
        resource_snapshot, live = _owned_resource_snapshot(
            record.owned_resources
        )
        if live:
            _reject("quarantined owned resource is still live")
    recovery_evidence_digest = _canonical_digest({
        "schema": "local-execution-recovery-evidence/v1",
        "quarantine_id": record.quarantine_id,
        "quarantine_generation": record.generation,
        "pid": getattr(record.process, "pid", None),
        "pgid": getattr(record.process, "pid", None),
        "resource_snapshot": dict(resource_snapshot),
        "owned_resources_absent": True,
    })
    current_workspace_digest = _bound_workspace_digest(
        record.workspace_root
    )
    recovery_input_digest = _canonical_digest({
        "schema": "local-execution-recovery-input/v1",
        "quarantine_id": record.quarantine_id,
        "quarantine_generation": record.generation,
        "cleanup_evidence_digest": record.cleanup_evidence_digest,
    })
    recovery_profile_digest = _canonical_digest({
        "schema": "local-execution-recovery-profile/v1",
        "contract_version": CONTRACT_VERSION,
        "quarantine_id": record.quarantine_id,
        "quarantine_generation": record.generation,
        "recovery_evidence_digest": recovery_evidence_digest,
    })
    return MappingProxyType({
        "quarantine_id": record.quarantine_id,
        "quarantine_generation": record.generation,
        "workspace_digest": current_workspace_digest,
        "input_digest": recovery_input_digest,
        "profile_digest": recovery_profile_digest,
        "cleanup_evidence_digest": record.cleanup_evidence_digest,
        "recovery_evidence_digest": recovery_evidence_digest,
    })


def request_local_execution_recovery(*, quarantine_id: str) -> Mapping[str, object]:
    with _STATE_LOCK:
        candidate = _QUARANTINE_BY_ID.get(quarantine_id)
        if candidate is None:
            _reject("unknown local execution quarantine")
        workspace_root = candidate.workspace_root
    gate = _workspace_gate(workspace_root)
    with gate:
        with _STATE_LOCK:
            record = _QUARANTINE_BY_ID.get(quarantine_id)
            workspace_key = str(workspace_root)
            if (
                record is None
                or record is not candidate
                or _QUARANTINE_BY_WORKSPACE.get(workspace_key) is not record
            ):
                _reject("local execution quarantine changed")
            resource_snapshot, live = _owned_resource_snapshot(
                record.owned_resources
            )
            if live:
                _reject("quarantined owned resource is still live")
            request = _make_recovery_request(
                record,
                resource_snapshot=resource_snapshot,
            )
            record.recovery_request = request
            return MappingProxyType({
                "status": "recovery_ready",
                "recovery_request": request,
            })


def recover_local_execution_quarantine(
    *,
    quarantine_id: str,
    recovery_confirmation: object,
) -> Mapping[str, object]:
    with _STATE_LOCK:
        candidate = _QUARANTINE_BY_ID.get(quarantine_id)
        if candidate is None:
            _reject("local execution quarantine is not recoverable")
        workspace_root = candidate.workspace_root
    gate = _workspace_gate(workspace_root)
    with gate:
        return _recover_local_execution_quarantine_locked(
            candidate,
            quarantine_id=quarantine_id,
            recovery_confirmation=recovery_confirmation,
        )


def _recover_local_execution_quarantine_locked(
    candidate: _QuarantineRecord,
    *,
    quarantine_id: str,
    recovery_confirmation: object,
) -> Mapping[str, object]:
    with _STATE_LOCK:
        record = _QUARANTINE_BY_ID.get(quarantine_id)
        workspace_key = str(candidate.workspace_root)
        if (
            record is None
            or record is not candidate
            or _QUARANTINE_BY_WORKSPACE.get(workspace_key) is not record
            or record.recovery_request is None
        ):
            _reject("local execution quarantine is not recoverable")
        request = record.recovery_request
        if not isinstance(recovery_confirmation, _TrustedLocalConfirmation):
            _reject("recovery confirmation invalid")
        with _AUTH_LOCK:
            confirmation = _CONFIRMATIONS.get(recovery_confirmation)
            if confirmation is None or confirmation.consumed:
                _reject("recovery confirmation unavailable or consumed")
            if time.monotonic() > confirmation.expires_at_monotonic:
                confirmation.consumed = True
                _reject("recovery confirmation expired")
            if (
                confirmation.workspace_digest != request["workspace_digest"]
                or confirmation.input_digest != request["input_digest"]
                or confirmation.profile_digest != request["profile_digest"]
            ):
                _reject("recovery confirmation binding mismatch")
            confirmation.consumed = True
        resource_snapshot, live = _owned_resource_snapshot(
            record.owned_resources
        )
        if live:
            _reject("quarantined owned resource reappeared")
        current = _make_recovery_request(
            record,
            resource_snapshot=resource_snapshot,
        )
        if dict(current) != dict(request):
            _reject("quarantine recovery evidence changed")
        del _QUARANTINE_BY_ID[quarantine_id]
        _QUARANTINE_BY_WORKSPACE.pop(str(record.workspace_root), None)
        _WORKSPACE_ADMISSION_EPOCH[workspace_key] = (
            _WORKSPACE_ADMISSION_EPOCH.get(workspace_key, 0) + 1
        )
        return MappingProxyType({
            "status": "recovered",
            "recovered": True,
            "quarantine_id": quarantine_id,
            "quarantine_generation": record.generation,
        })
