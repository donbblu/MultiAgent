from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from coding_workflow.agent_executor import (
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionRuntime,
    AgentExecutionStateEnvelope,
    AgentExecutionStateRejected,
    AgentExecutionStatus,
    AgentExecutionUsage,
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


class _RecordingExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend="fake_agent",
            cli_version="fake-1",
            session_id="fake-session",
            sandbox=request.permission.value,
            final_message="done",
            events=(),
            usage=AgentExecutionUsage(),
            duration_ms=1,
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
                prompt="评审已绑定的任务快照。",
                workspace_root=Path(temporary),
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
                state_envelope=state,
            )
            result = runtime.run(request)

        self.assertEqual("done", result.final_message)
        self.assertEqual([request], executor.requests)

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
