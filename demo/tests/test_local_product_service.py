from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from coding_workflow.agent_executor import (
    AgentExecutionEvent,
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentExecutionUsage,
)
from coding_workflow.artifacts import ArtifactDraft, ArtifactStore
from coding_workflow.local_product_service import (
    LocalProductTaskService,
    ProductAgentConfig,
    ProductTaskConflictError,
    ProductTaskRequest,
    ProductTaskStatus,
)
from coding_workflow.requirements import (
    AcceptanceCriterion,
    ValidatorProfile,
    ValidatorSpec,
)
from coding_workflow.runtime_domain import RoleAssignmentPolicy
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
)
from coding_workflow.truth import VerificationOutcome
from coding_workflow.validator_runtime import (
    ValidatorRegistry,
    ValidatorRunResult,
)


T0 = "2026-09-01T10:00:00+00:00"


class _ProductExecutor:
    def __init__(
        self,
        *,
        planner_message: Optional[str] = None,
        planner_status: AgentExecutionStatus = AgentExecutionStatus.COMPLETED,
        recipient_status: AgentExecutionStatus = AgentExecutionStatus.COMPLETED,
    ) -> None:
        self.requests: list[AgentExecutionRequest] = []
        self._planner_message = planner_message
        self._planner_status = planner_status
        self._recipient_status = recipient_status

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        if request.agent_id == "planner-agent":
            final_message = self._planner_message or json.dumps(
                {
                    "schema_version": "planner-delegation/v1",
                    "action": "delegate_task",
                    "recipient_role": "reviewer",
                    "task_instruction": "检查通信方案并给出一句结论。",
                    "required_capabilities": ["core:code_review"],
                    "acceptance_summary": "结果必须给出明确结论。",
                },
                ensure_ascii=False,
            )
        elif request.agent_id == "reviewer-agent":
            final_message = "结论：通信必须经过 Runtime 路由和持久化。"
        else:
            raise AssertionError(f"unexpected agent: {request.agent_id}")
        return AgentExecutionResult(
            status=(
                self._recipient_status
                if request.agent_id == "reviewer-agent"
                else self._planner_status
            ),
            backend_id="codex_cli",
            cli_version="fake-product-cli",
            backend_session_id=f"session-{request.agent_id}",
            sandbox=request.permission.value,
            final_message=final_message,
            events=(AgentExecutionEvent("agent_message", {"text": final_message}),),
            usage=AgentExecutionUsage(input_tokens=10, output_tokens=5),
            duration_ms=1,
        )


class _PassingResultValidator:
    def validate(self, request) -> ValidatorRunResult:
        result = next(iter(request.subjects.values()))
        if not isinstance(result.content, str) or not result.content.strip():
            return ValidatorRunResult(
                VerificationOutcome.FAILED,
                "结果为空。",
                (ArtifactDraft({"nonempty": False}, kind="tool_result"),),
            )
        return ValidatorRunResult(
            VerificationOutcome.PASSED,
            "结果非空且包含明确结论。",
            (ArtifactDraft({"nonempty": True}, kind="tool_result"),),
        )


class _FixedOutcomeValidator:
    def __init__(self, outcome: VerificationOutcome) -> None:
        self._outcome = outcome

    def validate(self, request) -> ValidatorRunResult:
        evidence = ()
        if self._outcome is VerificationOutcome.FAILED:
            evidence = (ArtifactDraft(
                {"accepted": False},
                kind="tool_result",
            ),)
        return ValidatorRunResult(
            self._outcome,
            f"固定验收结果：{self._outcome.value}",
            evidence,
        )


class LocalProductTaskServiceTests(unittest.TestCase):
    @staticmethod
    def _scenario_database(
        path: Path,
        policy_name: str,
    ) -> SQLiteRuntimeDatabase:
        value = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(path),
            outbox_policy=OutboxPolicy(
                policy_version=f"outbox-policy/{policy_name}-v1",
                destination="core:runtime_events",
                expected_sink_id=f"core:{policy_name}-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000,),
            ),
        )
        value.initialize()
        return value

    @staticmethod
    def _scenario_service(
        *,
        root: Path,
        database: SQLiteRuntimeDatabase,
        executor: _ProductExecutor,
        validator,
        agents: Optional[tuple[ProductAgentConfig, ...]] = None,
    ) -> LocalProductTaskService:
        criterion = AcceptanceCriterion(
            "result_present",
            "接收Agent必须返回明确结果",
            "core:product_result",
        )
        profile = ValidatorProfile(
            "product-result-v1",
            (ValidatorSpec(
                "product-result",
                "core:product_result",
                (criterion.criterion_id,),
            ),),
            {criterion.criterion_id: criterion.digest},
        )
        validators = ValidatorRegistry()
        validators.register(
            "core:product_result",
            validator,
            principal_id="runtime-validator",
        )
        return LocalProductTaskService(
            database,
            executor=executor,
            workspace_root=root,
            agents=agents or (
                ProductAgentConfig(
                    role_id="planner",
                    agent_id="planner-agent",
                    session_id="planner-session",
                    profile_id="planner-profile",
                    capabilities=("core:planning",),
                ),
                ProductAgentConfig(
                    role_id="reviewer",
                    agent_id="reviewer-agent",
                    session_id="reviewer-session",
                    profile_id="reviewer-profile",
                    capabilities=("core:code_review",),
                ),
            ),
            assignment_policy=RoleAssignmentPolicy(
                "role-assignment-policy/product-v1",
                0,
            ),
            validator_profile=profile,
            validator_registry=validators,
            artifacts=ArtifactStore(),
            clock=lambda: T0,
        )

    def test_user_task_reaches_runtime_validated_recipient_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = SQLiteRuntimeDatabase(
                RuntimeSQLiteConfig(root / "runtime.sqlite3"),
                outbox_policy=OutboxPolicy(
                    policy_version="outbox-policy/product-service-test-v1",
                    destination="core:runtime_events",
                    expected_sink_id="core:product-service-test-sink",
                    claim_ttl_ms=60_000,
                    batch_limit=10,
                    retry_delays_ms=(1_000,),
                ),
            )
            database.initialize()
            executor = _ProductExecutor()
            artifacts = ArtifactStore()
            criterion = AcceptanceCriterion(
                "result_present",
                "接收Agent必须返回明确结果",
                "core:product_result",
            )
            profile = ValidatorProfile(
                "product-result-v1",
                (ValidatorSpec(
                    "product-result",
                    "core:product_result",
                    (criterion.criterion_id,),
                ),),
                {criterion.criterion_id: criterion.digest},
            )
            validators = ValidatorRegistry()
            validators.register(
                "core:product_result",
                _PassingResultValidator(),
                principal_id="runtime-validator",
            )
            service = LocalProductTaskService(
                database,
                executor=executor,
                workspace_root=root,
                agents=(
                    ProductAgentConfig(
                        role_id="planner",
                        agent_id="planner-agent",
                        session_id="planner-session",
                        profile_id="planner-profile",
                        capabilities=("core:planning",),
                    ),
                    ProductAgentConfig(
                        role_id="reviewer",
                        agent_id="reviewer-agent",
                        session_id="reviewer-session",
                        profile_id="reviewer-profile",
                        capabilities=("core:code_review",),
                    ),
                ),
                assignment_policy=RoleAssignmentPolicy(
                    "role-assignment-policy/product-v1",
                    0,
                ),
                validator_profile=profile,
                validator_registry=validators,
                artifacts=artifacts,
                clock=lambda: T0,
            )

            result = service.run(ProductTaskRequest(
                task_id="task-communication-review",
                prompt="请让合适的Agent检查通信方案。",
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
            ))

        self.assertIs(result.status, ProductTaskStatus.VALIDATED)
        self.assertEqual(result.recipient_agent_id, "reviewer-agent")
        self.assertTrue(result.assignment_id)
        self.assertTrue(result.message_id)
        self.assertTrue(result.result_artifact_ref.startswith("artifact://"))
        self.assertTrue(result.verification_ref.startswith("verification://"))
        self.assertEqual(result.validation_outcome, VerificationOutcome.PASSED)
        self.assertEqual(
            [(item.agent_id, item.permission) for item in executor.requests],
            [
                ("planner-agent", AgentExecutionPermission.READ_ONLY),
                ("reviewer-agent", AgentExecutionPermission.READ_ONLY),
            ],
        )
        self.assertIn(
            "检查通信方案并给出一句结论。",
            executor.requests[1].prompt,
        )
        self.assertTrue(artifacts.is_verified(result.result_artifact_ref))

    def test_validated_result_and_evidence_reopen_without_executor_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime.sqlite3"

            def database() -> SQLiteRuntimeDatabase:
                value = SQLiteRuntimeDatabase(
                    RuntimeSQLiteConfig(path),
                    outbox_policy=OutboxPolicy(
                        policy_version="outbox-policy/product-history-test-v1",
                        destination="core:runtime_events",
                        expected_sink_id="core:product-history-test-sink",
                        claim_ttl_ms=60_000,
                        batch_limit=10,
                        retry_delays_ms=(1_000,),
                    ),
                )
                value.initialize()
                return value

            criterion = AcceptanceCriterion(
                "result_present",
                "接收Agent必须返回明确结果",
                "core:product_result",
            )
            profile = ValidatorProfile(
                "product-result-v1",
                (ValidatorSpec(
                    "product-result",
                    "core:product_result",
                    (criterion.criterion_id,),
                ),),
                {criterion.criterion_id: criterion.digest},
            )

            def service(
                current_database: SQLiteRuntimeDatabase,
                executor: _ProductExecutor,
                artifacts: ArtifactStore,
            ) -> LocalProductTaskService:
                validators = ValidatorRegistry()
                validators.register(
                    "core:product_result",
                    _PassingResultValidator(),
                    principal_id="runtime-validator",
                )
                return LocalProductTaskService(
                    current_database,
                    executor=executor,
                    workspace_root=root,
                    agents=(
                        ProductAgentConfig(
                            role_id="planner",
                            agent_id="planner-agent",
                            session_id="planner-session",
                            profile_id="planner-profile",
                            capabilities=("core:planning",),
                        ),
                        ProductAgentConfig(
                            role_id="reviewer",
                            agent_id="reviewer-agent",
                            session_id="reviewer-session",
                            profile_id="reviewer-profile",
                            capabilities=("core:code_review",),
                        ),
                    ),
                    assignment_policy=RoleAssignmentPolicy(
                        "role-assignment-policy/product-v1",
                        0,
                    ),
                    validator_profile=profile,
                    validator_registry=validators,
                    artifacts=artifacts,
                    clock=lambda: T0,
                )

            first_executor = _ProductExecutor()
            first = service(database(), first_executor, ArtifactStore())
            request = ProductTaskRequest(
                task_id="task-durable-history",
                prompt="请让合适的Agent检查通信方案。",
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
            )
            result = first.run(request)

            reopened_executor = _ProductExecutor()
            reopened = service(
                database(),
                reopened_executor,
                ArtifactStore(),
            )
            restored = reopened.get_task_result("task-durable-history")
            replayed = reopened.run(request)
            with self.assertRaisesRegex(
                ProductTaskConflictError,
                "task_request_conflict",
            ):
                reopened.run(ProductTaskRequest(
                    task_id=request.task_id,
                    prompt="同一个ID下的不同任务不能复用旧结果。",
                    permission=request.permission,
                    timeout_seconds=request.timeout_seconds,
                ))
            candidate = reopened.get_artifact(result.result_artifact_ref)
            report = reopened.get_artifact(result.validator_report_ref)
            verification = reopened.get_verification(result.verification_ref)

        self.assertEqual(restored, result)
        self.assertEqual(replayed, result)
        self.assertEqual(
            candidate.content,
            "结论：通信必须经过 Runtime 路由和持久化。",
        )
        self.assertEqual(report.content["outcome"], "passed")
        self.assertEqual(verification.outcome, VerificationOutcome.PASSED)
        self.assertEqual(
            verification.subject_refs,
            (result.result_artifact_ref,),
        )
        self.assertEqual(reopened_executor.requests, [])

    def test_invalid_planner_result_is_durable_and_replays_without_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime.sqlite3"

            def database() -> SQLiteRuntimeDatabase:
                value = SQLiteRuntimeDatabase(
                    RuntimeSQLiteConfig(path),
                    outbox_policy=OutboxPolicy(
                        policy_version="outbox-policy/product-failure-test-v1",
                        destination="core:runtime_events",
                        expected_sink_id="core:product-failure-test-sink",
                        claim_ttl_ms=60_000,
                        batch_limit=10,
                        retry_delays_ms=(1_000,),
                    ),
                )
                value.initialize()
                return value

            criterion = AcceptanceCriterion(
                "result_present",
                "接收Agent必须返回明确结果",
                "core:product_result",
            )
            profile = ValidatorProfile(
                "product-result-v1",
                (ValidatorSpec(
                    "product-result",
                    "core:product_result",
                    (criterion.criterion_id,),
                ),),
                {criterion.criterion_id: criterion.digest},
            )

            def service(
                current_database: SQLiteRuntimeDatabase,
                executor: _ProductExecutor,
            ) -> LocalProductTaskService:
                validators = ValidatorRegistry()
                validators.register(
                    "core:product_result",
                    _PassingResultValidator(),
                    principal_id="runtime-validator",
                )
                return LocalProductTaskService(
                    current_database,
                    executor=executor,
                    workspace_root=root,
                    agents=(
                        ProductAgentConfig(
                            role_id="planner",
                            agent_id="planner-agent",
                            session_id="planner-session",
                            profile_id="planner-profile",
                            capabilities=("core:planning",),
                        ),
                        ProductAgentConfig(
                            role_id="reviewer",
                            agent_id="reviewer-agent",
                            session_id="reviewer-session",
                            profile_id="reviewer-profile",
                            capabilities=("core:code_review",),
                        ),
                    ),
                    assignment_policy=RoleAssignmentPolicy(
                        "role-assignment-policy/product-v1",
                        0,
                    ),
                    validator_profile=profile,
                    validator_registry=validators,
                    artifacts=ArtifactStore(),
                    clock=lambda: T0,
                )

            request = ProductTaskRequest(
                task_id="task-invalid-planner",
                prompt="请让合适的Agent检查通信方案。",
                permission=AgentExecutionPermission.READ_ONLY,
                timeout_seconds=30,
            )
            first_executor = _ProductExecutor(planner_message="{}")
            first_result = service(database(), first_executor).run(request)

            reopened_executor = _ProductExecutor()
            reopened = service(database(), reopened_executor)
            restored = reopened.get_task_result(request.task_id)
            replayed = reopened.run(request)

        self.assertIs(first_result.status, ProductTaskStatus.NEEDS_INPUT)
        self.assertEqual(first_result.error_code, "invalid_planner_delegation")
        self.assertEqual(len(first_executor.requests), 1)
        self.assertEqual(restored, first_result)
        self.assertEqual(replayed, first_result)
        self.assertEqual(reopened_executor.requests, [])

    def test_assignment_and_recipient_failures_are_durable(self) -> None:
        missing_role_message = json.dumps({
            "schema_version": "planner-delegation/v1",
            "action": "delegate_task",
            "recipient_role": "developer",
            "task_instruction": "实现一个最小修复。",
            "required_capabilities": ["core:implementation"],
            "acceptance_summary": "必须给出实现结果。",
        })
        scenarios = (
            (
                "planner-failure",
                _ProductExecutor(
                    planner_status=AgentExecutionStatus.FAILED,
                ),
                ProductTaskStatus.FAILED,
                "planner_execution_failed",
                1,
            ),
            (
                "no-eligible-agent",
                _ProductExecutor(planner_message=missing_role_message),
                ProductTaskStatus.NEEDS_INPUT,
                "no_eligible_agent",
                1,
            ),
            (
                "recipient-failure",
                _ProductExecutor(
                    recipient_status=AgentExecutionStatus.FAILED,
                ),
                ProductTaskStatus.FAILED,
                "recipient_execution_failed",
                2,
            ),
        )
        for name, first_executor, status, error_code, expected_calls in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "runtime.sqlite3"
                request = ProductTaskRequest(
                    task_id=f"task-{name}",
                    prompt="请执行这个失败路径验收。",
                    permission=AgentExecutionPermission.READ_ONLY,
                    timeout_seconds=30,
                )
                first = self._scenario_service(
                    root=root,
                    database=self._scenario_database(path, name),
                    executor=first_executor,
                    validator=_PassingResultValidator(),
                )
                result = first.run(request)

                reopened_executor = _ProductExecutor()
                reopened = self._scenario_service(
                    root=root,
                    database=self._scenario_database(path, name),
                    executor=reopened_executor,
                    validator=_PassingResultValidator(),
                )
                restored = reopened.get_task_result(request.task_id)
                replayed = reopened.run(request)

                self.assertIs(result.status, status)
                self.assertEqual(result.error_code, error_code)
                self.assertEqual(len(first_executor.requests), expected_calls)
                self.assertEqual(restored, result)
                self.assertEqual(replayed, result)
                self.assertEqual(reopened_executor.requests, [])

    def test_failed_and_unknown_validation_history_reopens(self) -> None:
        scenarios = (
            (
                VerificationOutcome.FAILED,
                ProductTaskStatus.VALIDATION_FAILED,
            ),
            (
                VerificationOutcome.UNKNOWN,
                ProductTaskStatus.NEEDS_INPUT,
            ),
        )
        for outcome, expected_status in scenarios:
            with self.subTest(outcome=outcome.value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    path = root / "runtime.sqlite3"
                    policy_name = f"validator-{outcome.value}"
                    request = ProductTaskRequest(
                        task_id=f"task-validator-{outcome.value}",
                        prompt="请执行并由Runtime验收。",
                        permission=AgentExecutionPermission.READ_ONLY,
                        timeout_seconds=30,
                    )
                    first_executor = _ProductExecutor()
                    first = self._scenario_service(
                        root=root,
                        database=self._scenario_database(path, policy_name),
                        executor=first_executor,
                        validator=_FixedOutcomeValidator(outcome),
                    )
                    result = first.run(request)

                    reopened_executor = _ProductExecutor()
                    reopened = self._scenario_service(
                        root=root,
                        database=self._scenario_database(path, policy_name),
                        executor=reopened_executor,
                        validator=_FixedOutcomeValidator(outcome),
                    )
                    restored = reopened.get_task_result(request.task_id)
                    replayed = reopened.run(request)
                    verification = reopened.get_verification(
                        result.verification_ref
                    )
                    report = reopened.get_artifact(
                        result.validator_report_ref
                    )

                    self.assertIs(result.status, expected_status)
                    self.assertIs(result.validation_outcome, outcome)
                    self.assertEqual(restored, result)
                    self.assertEqual(replayed, result)
                    self.assertIs(verification.outcome, outcome)
                    self.assertEqual(report.content["outcome"], outcome.value)
                    self.assertEqual(len(first_executor.requests), 2)
                    self.assertEqual(reopened_executor.requests, [])


if __name__ == "__main__":
    unittest.main()
