from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from coding_workflow.agent_executor import (
    AgentExecutionPermission,
    AgentExecutionContextPart,
    AgentExecutionRecoveryConfirmation,
    AgentExecutionRecoveryBlocked,
    AgentExecutionRecoveryContext,
    AgentExecutionRecoveryDecision,
    AgentExecutionRecoveryPrompt,
    AgentExecutionRecoveryStopped,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionRuntime,
    AgentExecutionStateEnvelope,
    AgentExecutionStateRejected,
    AgentExecutionStatus,
    AgentExecutionUsage,
    BackendSessionUnavailable,
    CodexCliAgentExecutor,
    CodexCliFailureKind,
    CodexCliProcessResult,
    FrozenAgentExecutionStateAuthority,
)
from coding_workflow.runtime_domain import ScopedRef, ScopedSnapshotRef
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteAgentExecutionStateStore,
    SQLiteRuntimeDatabase,
)


def _snapshot_ref(
    entity_type: str,
    entity_id: str,
    marker: str,
) -> ScopedSnapshotRef:
    return ScopedSnapshotRef(
        ScopedRef("scope-a", entity_type, entity_id),
        marker * 64,
    )


def _state_envelope() -> AgentExecutionStateEnvelope:
    return AgentExecutionStateEnvelope(
        scope_id="scope-a",
        task_ref=ScopedRef("scope-a", "core:task", "task-1"),
        snapshot_ref=_snapshot_ref(
            "core:task_snapshot", "snapshot-1", "a"
        ),
        permission_snapshot_ref=_snapshot_ref(
            "core:permission_snapshot", "permission-1", "b"
        ),
        artifact_refs=(
            _snapshot_ref("core:artifact", "artifact-1", "c"),
        ),
        permission=AgentExecutionPermission.READ_ONLY,
    )


def _recovery_fixture(
) -> tuple[AgentExecutionStateEnvelope, AgentExecutionRecoveryContext]:
    def part(
        entity_type: str,
        entity_id: str,
        content: str,
    ) -> AgentExecutionContextPart:
        return AgentExecutionContextPart(
            ref=ScopedSnapshotRef(
                ScopedRef("scope-a", entity_type, entity_id),
                sha256(content.encode("utf-8")).hexdigest(),
            ),
            content=content,
        )

    task_snapshot = part(
        "core:task_snapshot",
        "snapshot-recovery",
        "任务目标：评审通信协议并给出一个有依据的问题。",
    )
    permission_snapshot = part(
        "core:permission_snapshot",
        "permission-recovery",
        "权限：read-only；禁止网络和文件修改。",
    )
    message = part(
        "core:message",
        "message-recovery",
        "请检查SEND_MESSAGE协议的终止条件。",
    )
    artifact = part(
        "core:artifact",
        "artifact-recovery",
        "协议事实：Runtime最多允许一次自动hop。",
    )
    state = AgentExecutionStateEnvelope(
        scope_id="scope-a",
        task_ref=ScopedRef("scope-a", "core:task", "task-1"),
        snapshot_ref=task_snapshot.ref,
        permission_snapshot_ref=permission_snapshot.ref,
        artifact_refs=(artifact.ref,),
        permission=AgentExecutionPermission.READ_ONLY,
    )
    return state, AgentExecutionRecoveryContext(
        scope_id="scope-a",
        task_ref=state.task_ref,
        task_snapshot=task_snapshot,
        permission_snapshot=permission_snapshot,
        messages=(message,),
        artifacts=(artifact,),
    )


class _RecordingExecutor:
    def __init__(self, *, backend_session_id: str = "fake-session") -> None:
        self.requests: list[AgentExecutionRequest] = []
        self.backend_session_id = backend_session_id

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend_id=request.backend_id,
            cli_version="fake-1",
            backend_session_id=self.backend_session_id,
            sandbox=request.permission.value,
            final_message="done",
            events=(),
            usage=AgentExecutionUsage(),
            duration_ms=1,
        )


class _SessionUnavailableThenExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise BackendSessionUnavailable(
                backend_id=request.backend_id,
                backend_session_id=request.backend_session_id,
            )
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend_id=request.backend_id,
            cli_version="fake-1",
            backend_session_id="backend-session-rebuilt-2",
            sandbox=request.permission.value,
            final_message="recovered",
            events=(),
            usage=AgentExecutionUsage(),
            duration_ms=2,
        )


class _AlwaysUnavailableExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        raise BackendSessionUnavailable(
            backend_id=request.backend_id,
            backend_session_id=request.backend_session_id,
        )


class _OrdinaryFailureExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        raise RuntimeError("ordinary_backend_failure")


class _FailedBeforeStartExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.FAILED,
            backend_id=request.backend_id,
            cli_version="fake-1",
            backend_session_id="",
            sandbox=request.permission.value,
            final_message="",
            events=(),
            usage=AgentExecutionUsage(),
            duration_ms=2,
        )


class _SessionUnavailableThenCodexTransport:
    def __init__(self) -> None:
        self.launches = []

    def run(self, launch):
        self.launches.append(launch)
        if len(self.launches) == 1:
            return CodexCliProcessResult(
                exit_code=1,
                stdout="",
                stderr="private session diagnostic",
                duration_ms=1,
                timed_out=False,
                failure_kind=(
                    CodexCliFailureKind.BACKEND_SESSION_UNAVAILABLE
                ),
            )
        return CodexCliProcessResult(
            exit_code=0,
            stdout="\n".join((
                '{"type":"thread.started","thread_id":'
                '"backend-session-rebuilt-codex"}',
                '{"type":"turn.started"}',
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"recovered"}}',
                '{"type":"turn.completed","usage":{}}',
            )),
            stderr="",
            duration_ms=2,
            timed_out=False,
        )


class AgentExecutionStateRuntimeTests(unittest.TestCase):
    @staticmethod
    def _database(path: Path) -> SQLiteRuntimeDatabase:
        return SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/agent-execution-test-v1",
                destination="core:runtime_events",
                expected_sink_id="core:test-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000, 5_000, 30_000),
            ),
        )

    def test_authorized_explicit_state_reaches_agent_executor(self) -> None:
        state = _state_envelope()
        authority = FrozenAgentExecutionStateAuthority({"invocation-1": state})
        executor = _RecordingExecutor()
        runtime = AgentExecutionRuntime(
            executor=executor,
            state_authority=authority,
        )

        with tempfile.TemporaryDirectory() as temporary:
            request = AgentExecutionRequest(
                invocation_id="invocation-1",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="评审已绑定的任务快照。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            result = runtime.run(request)

        self.assertEqual("done", result.final_message)
        self.assertEqual([request], executor.requests)

    def test_backend_session_is_captured_and_resumed_after_runtime_restart(
        self,
    ) -> None:
        state = _state_envelope()
        executor = _RecordingExecutor(
            backend_session_id="backend-session-private-1"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-first", state)
                store.record_expected(uow, "invocation-second", state)
                uow.commit()
            first_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
            )
            first_runtime.run(AgentExecutionRequest(
                invocation_id="invocation-first",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="首次执行。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            second_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=reopened_store,
                session_store=reopened_store,
            )
            second_runtime.run(AgentExecutionRequest(
                invocation_id="invocation-second",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="继续执行。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))

        self.assertEqual(2, len(executor.requests))
        self.assertEqual("", executor.requests[0].backend_session_id)
        self.assertEqual(
            "backend-session-private-1",
            executor.requests[1].backend_session_id,
        )

    def test_unavailable_session_rebuilds_persisted_context_once(self) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _SessionUnavailableThenExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-recovery", state)
                store.record_expected(uow, "invocation-after-recovery", state)
                store.record_recovery_context(
                    uow,
                    "invocation-recovery",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-1",
            )
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            recovery_request = AgentExecutionRequest(
                invocation_id="invocation-recovery",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="旧Session中的简短继续指令。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            prompt = runtime.run(recovery_request)
            result = runtime.confirm_session_recovery(
                recovery_request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=recovery_request.invocation_id,
                    decision=(
                        AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                    ),
                ),
            )

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            followup_executor = _RecordingExecutor(
                backend_session_id="backend-session-rebuilt-2"
            )
            AgentExecutionRuntime(
                executor=followup_executor,
                state_authority=reopened_store,
                session_store=reopened_store,
            ).run(AgentExecutionRequest(
                invocation_id="invocation-after-recovery",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="恢复后的下一轮。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))

        self.assertEqual("recovered", result.final_message)
        self.assertEqual(2, len(executor.requests))
        self.assertEqual(
            "backend-session-stale-1",
            executor.requests[0].backend_session_id,
        )
        self.assertEqual("", executor.requests[1].backend_session_id)
        rebuilt = executor.requests[1].prompt
        self.assertIn("评审通信协议", rebuilt)
        self.assertIn("read-only", rebuilt)
        self.assertIn("SEND_MESSAGE协议", rebuilt)
        self.assertIn("最多允许一次自动hop", rebuilt)
        self.assertNotIn("backend-session-stale-1", rebuilt)
        self.assertNotIn("backend-session-rebuilt-2", rebuilt)
        self.assertEqual(
            "backend-session-rebuilt-2",
            followup_executor.requests[0].backend_session_id,
        )

    def test_unavailable_session_waits_for_confirmation_before_rebuild(
        self,
    ) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _SessionUnavailableThenExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-confirm-recovery", state)
                store.record_recovery_context(
                    uow,
                    "invocation-confirm-recovery",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-private",
            )
            request = AgentExecutionRequest(
                invocation_id="invocation-confirm-recovery",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="旧Session中的继续指令。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            prompt = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                replay_store=store,
                session_store=store,
                recovery_context_store=store,
            ).run(request)

            self.assertIsInstance(prompt, AgentExecutionRecoveryPrompt)
            self.assertEqual(1, len(executor.requests))
            self.assertEqual(
                {
                    "schema_version": "agent-execution-recovery-prompt/v1",
                    "status": "awaiting_user_confirmation",
                    "confirmation_id": prompt.confirmation_id,
                    "invocation_id": "invocation-confirm-recovery",
                    "message": (
                        "上次 Agent 会话无法恢复。是否创建新会话继续？"
                    ),
                    "allowed_decisions": [
                        "create_new_session",
                        "stop_task",
                    ],
                },
                dict(prompt.to_dict()),
            )
            public_prompt = repr(prompt.to_dict())
            self.assertNotIn("backend-session-stale-private", public_prompt)
            self.assertNotIn("SEND_MESSAGE协议", public_prompt)
            self.assertNotIn(str(root), public_prompt)

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            reopened_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=reopened_store,
                replay_store=reopened_store,
                session_store=reopened_store,
                recovery_context_store=reopened_store,
            )
            same_prompt = reopened_runtime.run(request)
            self.assertEqual(prompt, same_prompt)
            self.assertEqual(1, len(executor.requests))
            result = reopened_runtime.confirm_session_recovery(
                request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=request.invocation_id,
                    decision=(
                        AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                    ),
                ),
            )
            replayed = reopened_runtime.confirm_session_recovery(
                request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=request.invocation_id,
                    decision=(
                        AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                    ),
                ),
            )

        self.assertEqual("recovered", result.final_message)
        self.assertEqual(result, replayed)
        self.assertEqual(2, len(executor.requests))
        self.assertEqual("", executor.requests[1].backend_session_id)
        self.assertIn("SEND_MESSAGE协议", executor.requests[1].prompt)
        self.assertNotIn(
            "backend-session-stale-private", executor.requests[1].prompt
        )

    def test_user_can_stop_pending_recovery_without_backend_call(self) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _AlwaysUnavailableExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-stop-recovery", state)
                store.record_recovery_context(
                    uow,
                    "invocation-stop-recovery",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stop-private",
            )
            request = AgentExecutionRequest(
                invocation_id="invocation-stop-recovery",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="继续。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            prompt = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            ).run(request)

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            reopened_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=reopened_store,
                session_store=reopened_store,
                recovery_context_store=reopened_store,
            )
            stopped = reopened_runtime.confirm_session_recovery(
                request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=request.invocation_id,
                    decision=AgentExecutionRecoveryDecision.STOP_TASK,
                ),
            )
            stopped_replay = reopened_runtime.run(request)

        self.assertIsInstance(stopped, AgentExecutionRecoveryStopped)
        self.assertEqual(stopped, stopped_replay)
        self.assertEqual(
            {
                "schema_version": "agent-execution-recovery-stopped/v1",
                "status": "stopped",
                "invocation_id": "invocation-stop-recovery",
            },
            dict(stopped.to_dict()),
        )
        self.assertEqual(1, len(executor.requests))

    def test_wrong_recovery_confirmation_is_rejected_before_backend(self) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _AlwaysUnavailableExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-wrong-confirmation", state)
                store.record_recovery_context(
                    uow,
                    "invocation-wrong-confirmation",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-wrong-private",
            )
            request = AgentExecutionRequest(
                invocation_id="invocation-wrong-confirmation",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="继续。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            runtime.run(request)
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.confirm_session_recovery(
                    request,
                    AgentExecutionRecoveryConfirmation(
                        confirmation_id="session-recovery-wrong",
                        invocation_id=request.invocation_id,
                        decision=(
                            AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                        ),
                    ),
                )

        self.assertEqual(
            "recovery_confirmation_mismatch", raised.exception.code
        )
        self.assertEqual(1, len(executor.requests))

    def test_failed_resume_before_any_event_waits_for_user_confirmation(
        self,
    ) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _FailedBeforeStartExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-prestart-failure", state)
                store.record_recovery_context(
                    uow,
                    "invocation-prestart-failure",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-prestart-private",
            )
            prompt = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            ).run(AgentExecutionRequest(
                invocation_id="invocation-prestart-failure",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="继续。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))

        self.assertIsInstance(prompt, AgentExecutionRecoveryPrompt)
        self.assertEqual("awaiting_user_confirmation", prompt.status)
        self.assertEqual(1, len(executor.requests))

    def test_unavailable_session_without_context_fails_without_retry(
        self,
    ) -> None:
        state, _ = _recovery_fixture()
        executor = _AlwaysUnavailableExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-no-context", state)
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-1",
            )
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.run(AgentExecutionRequest(
                    invocation_id="invocation-no-context",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="codex_cli",
                    prompt="继续。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
                ))

        self.assertEqual("recovery_context_not_found", raised.exception.code)
        self.assertEqual(1, len(executor.requests))

    def test_recovery_stops_after_second_unavailable_session(self) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _AlwaysUnavailableExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-double-failure", state)
                store.record_recovery_context(
                    uow,
                    "invocation-double-failure",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-1",
            )
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            request = AgentExecutionRequest(
                    invocation_id="invocation-double-failure",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="codex_cli",
                    prompt="继续。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
            )
            prompt = runtime.run(request)
            self.assertEqual(1, len(executor.requests))
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.confirm_session_recovery(
                    request,
                    AgentExecutionRecoveryConfirmation(
                        confirmation_id=prompt.confirmation_id,
                        invocation_id=request.invocation_id,
                        decision=(
                            AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                        ),
                    ),
                )
            with self.assertRaises(AgentExecutionStateRejected) as repeated:
                runtime.confirm_session_recovery(
                    request,
                    AgentExecutionRecoveryConfirmation(
                        confirmation_id=prompt.confirmation_id,
                        invocation_id=request.invocation_id,
                        decision=(
                            AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                        ),
                    ),
                )
            blocked = runtime.run(request)

        self.assertEqual(
            "backend_session_recovery_failed", raised.exception.code
        )
        self.assertEqual(
            "recovery_confirmation_already_used", repeated.exception.code
        )
        self.assertIsInstance(blocked, AgentExecutionRecoveryBlocked)
        self.assertEqual(
            {
                "schema_version": "agent-execution-recovery-blocked/v1",
                "status": "recovery_attempt_unresolved",
                "invocation_id": "invocation-double-failure",
            },
            dict(blocked.to_dict()),
        )
        self.assertEqual(2, len(executor.requests))
        self.assertEqual("", executor.requests[1].backend_session_id)

    def test_ordinary_backend_failure_is_not_treated_as_session_loss(
        self,
    ) -> None:
        state, recovery_context = _recovery_fixture()
        executor = _OrdinaryFailureExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-ordinary-failure", state)
                store.record_recovery_context(
                    uow,
                    "invocation-ordinary-failure",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-1",
            )
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            with self.assertRaisesRegex(
                RuntimeError, "ordinary_backend_failure"
            ):
                runtime.run(AgentExecutionRequest(
                    invocation_id="invocation-ordinary-failure",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="codex_cli",
                    prompt="继续。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
                ))

        self.assertEqual(1, len(executor.requests))

    def test_codex_adapter_signal_rebuilds_once_through_runtime(self) -> None:
        state, recovery_context = _recovery_fixture()
        transport = _SessionUnavailableThenCodexTransport()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-codex-recovery", state)
                store.record_recovery_context(
                    uow,
                    "invocation-codex-recovery",
                    state,
                    recovery_context,
                )
                uow.commit()
            store.record_session_binding(
                scope_id="scope-a",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                backend_session_id="backend-session-stale-codex",
            )
            runtime = AgentExecutionRuntime(
                executor=CodexCliAgentExecutor(
                    executable=Path(
                        "/Applications/ChatGPT.app/Contents/Resources/codex"
                    ),
                    cli_version="0.149.0-alpha.4.3",
                    transport=transport,
                ),
                state_authority=store,
                session_store=store,
                recovery_context_store=store,
            )
            request = AgentExecutionRequest(
                invocation_id="invocation-codex-recovery",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="继续旧Session。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            prompt = runtime.run(request)
            self.assertIsInstance(prompt, AgentExecutionRecoveryPrompt)
            self.assertEqual(1, len(transport.launches))
            result = runtime.confirm_session_recovery(
                request,
                AgentExecutionRecoveryConfirmation(
                    confirmation_id=prompt.confirmation_id,
                    invocation_id=request.invocation_id,
                    decision=(
                        AgentExecutionRecoveryDecision.CREATE_NEW_SESSION
                    ),
                ),
            )

        self.assertEqual("recovered", result.final_message)
        self.assertEqual(2, len(transport.launches))
        self.assertIn("resume", transport.launches[0].argv)
        self.assertIn(
            "backend-session-stale-codex", transport.launches[0].argv
        )
        self.assertNotIn("resume", transport.launches[1].argv)
        self.assertNotIn(
            "backend-session-stale-codex", transport.launches[1].argv
        )
        self.assertIn("SEND_MESSAGE协议", transport.launches[1].stdin_text)

    def test_persisted_recovery_context_passes_runtime_integrity_check(
        self,
    ) -> None:
        state, recovery_context = _recovery_fixture()

        with tempfile.TemporaryDirectory() as temporary:
            database = self._database(Path(temporary) / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-recovery", state)
                store.record_recovery_context(
                    uow,
                    "invocation-recovery",
                    state,
                    recovery_context,
                )
                uow.commit()

            database.verify_integrity()

    def test_wrong_backend_session_is_rejected_before_agent_executor(
        self,
    ) -> None:
        state = _state_envelope()
        executor = _RecordingExecutor(
            backend_session_id="backend-session-private-1"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-first", state)
                store.record_expected(uow, "invocation-second", state)
                uow.commit()
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                session_store=store,
            )
            runtime.run(AgentExecutionRequest(
                invocation_id="invocation-first",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="首次执行。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.run(AgentExecutionRequest(
                    invocation_id="invocation-second",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="codex_cli",
                    backend_session_id="untrusted-other-session",
                    prompt="不应执行。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
                ))

        self.assertEqual("backend_session_mismatch", raised.exception.code)
        self.assertEqual(1, len(executor.requests))

    def test_backend_session_cannot_cross_agent_thread_or_backend(self) -> None:
        state = _state_envelope()
        cases = (
            ("thread-1", "other-agent", "codex_cli"),
            ("other-thread", "reviewer-agent", "codex_cli"),
            ("thread-1", "reviewer-agent", "other_backend"),
        )

        for thread_id, agent_id, backend_id in cases:
            with self.subTest(
                thread_id=thread_id,
                agent_id=agent_id,
                backend_id=backend_id,
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = self._database(root / "runtime.sqlite3")
                database.initialize()
                store = SQLiteAgentExecutionStateStore(database)
                with database.unit_of_work() as uow:
                    store.record_expected(uow, "invocation-first", state)
                    store.record_expected(uow, "invocation-cross", state)
                    uow.commit()
                executor = _RecordingExecutor(
                    backend_session_id="backend-session-private-1"
                )
                runtime = AgentExecutionRuntime(
                    executor=executor,
                    state_authority=store,
                    session_store=store,
                )
                runtime.run(AgentExecutionRequest(
                    invocation_id="invocation-first",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="codex_cli",
                    prompt="首次执行。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
                ))
                with self.assertRaises(AgentExecutionStateRejected) as raised:
                    runtime.run(AgentExecutionRequest(
                        invocation_id="invocation-cross",
                        thread_id=thread_id,
                        agent_id=agent_id,
                        backend_id=backend_id,
                        backend_session_id="backend-session-private-1",
                        prompt="不应跨绑定执行。",
                        workspace_root=root,
                        permission=AgentExecutionPermission.READ_ONLY,
                        timeout_seconds=30,
                        state_envelope=state,
                    ))

                self.assertEqual(
                    "backend_session_mismatch",
                    raised.exception.code,
                )
                self.assertEqual(1, len(executor.requests))

    def test_completed_replay_cannot_switch_backend(self) -> None:
        state = _state_envelope()
        executor = _RecordingExecutor(
            backend_session_id="backend-session-private-1"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self._database(root / "runtime.sqlite3")
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-1", state)
                uow.commit()
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                replay_store=store,
                session_store=store,
            )
            runtime.run(AgentExecutionRequest(
                invocation_id="invocation-1",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                backend_id="codex_cli",
                prompt="首次执行。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            ))
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.run(AgentExecutionRequest(
                    invocation_id="invocation-1",
                    thread_id="thread-1",
                    agent_id="reviewer-agent",
                    backend_id="other_backend",
                    prompt="不得切换Backend重放。",
                    workspace_root=root,
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                    state_envelope=state,
                ))

        self.assertEqual("backend_mismatch", raised.exception.code)
        self.assertEqual(1, len(executor.requests))

    def test_mismatched_state_is_rejected_before_agent_executor(self) -> None:
        authorized = _state_envelope()
        authority = FrozenAgentExecutionStateAuthority({
            "invocation-1": authorized,
        })
        executor = _RecordingExecutor()
        runtime = AgentExecutionRuntime(
            executor=executor,
            state_authority=authority,
        )
        mismatched = replace(
            authorized,
            permission_snapshot_ref=_snapshot_ref(
                "core:permission_snapshot", "permission-2", "d"
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            request = AgentExecutionRequest(
                invocation_id="invocation-1",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="不应到达Agent。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=mismatched,
            )
            with self.assertRaises(AgentExecutionStateRejected) as raised:
                runtime.run(request)

        self.assertEqual("state_mismatch", raised.exception.code)
        self.assertEqual([], executor.requests)

    def test_completed_snapshot_replays_after_runtime_restart_without_backend_recall(
        self,
    ) -> None:
        state = _state_envelope()
        executor = _RecordingExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-1", state)
                uow.commit()
            request = AgentExecutionRequest(
                invocation_id="invocation-1",
                thread_id="thread-1",
                agent_id="reviewer-agent",
                prompt="评审已绑定的任务快照。",
                workspace_root=root,
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            first_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=store,
                replay_store=store,
            )
            first = first_runtime.run(request)

            reopened = self._database(database_path)
            reopened.initialize()
            reopened_store = SQLiteAgentExecutionStateStore(reopened)
            second_runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=reopened_store,
                replay_store=reopened_store,
            )
            second = second_runtime.run(request)

        self.assertEqual(first, second)
        self.assertEqual([request], executor.requests)

    def test_persisted_authority_rejects_every_state_ref_mismatch_before_backend(
        self,
    ) -> None:
        authorized = _state_envelope()
        mismatches = {
            "task": replace(
                authorized,
                task_ref=ScopedRef("scope-a", "core:task", "task-2"),
            ),
            "snapshot": replace(
                authorized,
                snapshot_ref=_snapshot_ref(
                    "core:task_snapshot", "snapshot-2", "d"
                ),
            ),
            "permission": replace(
                authorized,
                permission_snapshot_ref=_snapshot_ref(
                    "core:permission_snapshot", "permission-2", "e"
                ),
            ),
            "artifact": replace(
                authorized,
                artifact_refs=(
                    _snapshot_ref("core:artifact", "artifact-2", "f"),
                ),
            ),
        }
        executor = _RecordingExecutor()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "runtime.sqlite3"
            database = self._database(database_path)
            database.initialize()
            store = SQLiteAgentExecutionStateStore(database)
            with database.unit_of_work() as uow:
                store.record_expected(uow, "invocation-1", authorized)
                uow.commit()

            reopened = self._database(database_path)
            reopened.initialize()
            runtime = AgentExecutionRuntime(
                executor=executor,
                state_authority=SQLiteAgentExecutionStateStore(reopened),
            )
            for mismatch_name, state in mismatches.items():
                with self.subTest(mismatch=mismatch_name):
                    request = AgentExecutionRequest(
                        invocation_id="invocation-1",
                        thread_id="thread-1",
                        agent_id="reviewer-agent",
                        prompt="不应到达Agent。",
                        workspace_root=root,
                        permission=AgentExecutionPermission.READ_ONLY,
                        timeout_seconds=30,
                        state_envelope=state,
                    )
                    with self.assertRaises(
                        AgentExecutionStateRejected
                    ) as raised:
                        runtime.run(request)
                    self.assertEqual("state_mismatch", raised.exception.code)

        self.assertEqual([], executor.requests)


if __name__ == "__main__":
    unittest.main()
