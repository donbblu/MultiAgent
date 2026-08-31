from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Mapping, Sequence

from coding_workflow.agent_executor import (
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentExecutor,
    CodexCliAgentExecutor,
    CodexCliProcessRunner,
    SupervisedCodexCliTransport,
)
from coding_workflow.artifacts import ArtifactDraft, ArtifactStore
from coding_workflow.local_execution import workspace_digest
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.local_product_service import (
    LocalProductTaskService,
    ProductAgentConfig,
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


CODEX_EXECUTABLE = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
CODEX_CLI_VERSION = "0.149.0-alpha.4.3"
SMOKE_TASK_ID = "real-codex-double-agent-smoke-v1"
SMOKE_REVIEW_FINAL = (
    "REVIEW_OK runtime_routes_messages=true agent_self_acceptance=false"
)
SMOKE_TASK_PROMPT = f"""\
这是一次自包含、只读的双Agent协作验收。不要读取仓库，不要运行工具，不要修改文件，不要访问网络。
请把评审任务委派给规范Role ID `reviewer`。待评审规则只有两条：
1. Agent之间的消息必须由Runtime路由和持久化。
2. Agent提交的结果只是候选，不能自行宣布验收通过。
Reviewer确认两条规则一致后，候选结果必须只返回这一行：
{SMOKE_REVIEW_FINAL}
"""
SMOKE_TIME = "2026-09-01T12:00:00+00:00"


class _ExactSmokeValidator:
    def validate(self, request) -> ValidatorRunResult:
        result = next(iter(request.subjects.values()))
        exact = result.content == SMOKE_REVIEW_FINAL
        return ValidatorRunResult(
            (
                VerificationOutcome.PASSED
                if exact
                else VerificationOutcome.FAILED
            ),
            "Reviewer候选严格匹配固定验收结论。"
            if exact
            else "Reviewer候选未严格匹配固定验收结论。",
            (ArtifactDraft({"exact_match": exact}, kind="tool_result"),),
        )


class _ObservedExecutor:
    def __init__(self, delegate: AgentExecutor) -> None:
        self._delegate = delegate
        self.requests: list[AgentExecutionRequest] = []
        self.results: list[AgentExecutionResult] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        result = self._delegate.run(request)
        self.results.append(result)
        return result


def _database(path: Path) -> SQLiteRuntimeDatabase:
    value = SQLiteRuntimeDatabase(
        RuntimeSQLiteConfig(path),
        outbox_policy=OutboxPolicy(
            policy_version="outbox-policy/real-product-smoke-v1",
            destination="core:runtime_events",
            expected_sink_id="core:real-product-smoke-sink",
            claim_ttl_ms=60_000,
            batch_limit=10,
            retry_delays_ms=(1_000,),
        ),
    )
    value.initialize()
    return value


def _service(
    *,
    database: SQLiteRuntimeDatabase,
    executor: AgentExecutor,
    workspace_root: Path,
    artifacts: ArtifactStore,
) -> LocalProductTaskService:
    criterion = AcceptanceCriterion(
        "exact_smoke_result",
        "Reviewer必须返回固定的自包含评审结论",
        "core:product_smoke_result",
    )
    profile = ValidatorProfile(
        "real-product-smoke-v1",
        (ValidatorSpec(
            "exact-smoke-result",
            "core:product_smoke_result",
            (criterion.criterion_id,),
        ),),
        {criterion.criterion_id: criterion.digest},
    )
    validators = ValidatorRegistry()
    validators.register(
        "core:product_smoke_result",
        _ExactSmokeValidator(),
        principal_id="runtime-smoke-validator",
    )
    return LocalProductTaskService(
        database,
        executor=executor,
        workspace_root=workspace_root,
        agents=(
            ProductAgentConfig(
                role_id="planner",
                agent_id="planner-agent",
                session_id="planner-harness-session",
                profile_id="planner-profile",
                capabilities=("core:planning",),
            ),
            ProductAgentConfig(
                role_id="reviewer",
                agent_id="reviewer-agent",
                session_id="reviewer-harness-session",
                profile_id="reviewer-profile",
                capabilities=("core:code_review",),
            ),
        ),
        assignment_policy=RoleAssignmentPolicy(
            "role-assignment-policy/real-product-smoke-v1",
            0,
        ),
        validator_profile=profile,
        validator_registry=validators,
        artifacts=artifacts,
        clock=lambda: SMOKE_TIME,
    )


def _public_agent_results(
    observed: _ObservedExecutor,
) -> list[Mapping[str, object]]:
    values: list[Mapping[str, object]] = []
    for request, result in zip(observed.requests, observed.results):
        values.append({
            "agent_id": request.agent_id,
            "status": result.status.value,
            "sandbox": result.sandbox,
            "session_observed": bool(result.backend_session_id),
            "event_kinds": [event.kind for event in result.events],
            "usage": {
                "input_tokens": result.usage.input_tokens,
                "cached_input_tokens": result.usage.cached_input_tokens,
                "output_tokens": result.usage.output_tokens,
                "reasoning_output_tokens": (
                    result.usage.reasoning_output_tokens
                ),
            },
            "duration_ms": result.duration_ms,
        })
    return values


def _failed_report(
    *,
    observed: _ObservedExecutor,
    workspace_unchanged: bool,
    duration_ms: int,
) -> Mapping[str, object]:
    return {
        "status": "failed",
        "error_code": "PRODUCT_SMOKE_EXECUTION_FAILED",
        "backend_id": "codex_cli",
        "agent_invocations": len(observed.requests),
        "agents": _public_agent_results(observed),
        "result": {
            "status": "failed",
            "error_code": "PRODUCT_SMOKE_EXECUTION_FAILED",
            "recipient_agent": "",
            "assignment_created": False,
            "message_persisted": False,
            "artifact_persisted": False,
            "verification_persisted": False,
            "validation_outcome": "unknown",
        },
        "checks": {
            "two_isolated_agent_invocations": False,
            "planner_then_reviewer": False,
            "read_only_sandbox": False,
            "runtime_assignment_and_message": False,
            "artifact_and_verification_durable": False,
            "runtime_validator_passed": False,
            "same_task_replayed_without_extra_call": False,
            "workspace_unchanged": workspace_unchanged,
        },
        "usage": {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        "duration_ms": duration_ms,
    }


def run_codex_product_smoke(
    *,
    executor: AgentExecutor,
    workspace_root: Path,
) -> Mapping[str, object]:
    root = Path(workspace_root).resolve()
    if not root.is_dir():
        raise ValueError("workspace_root must be an existing directory")
    observed = _ObservedExecutor(executor)
    before_digest = workspace_digest(root)
    started = time.monotonic()

    with tempfile.TemporaryDirectory(
        prefix="multiagent-product-smoke-"
    ) as temporary:
        path = Path(temporary) / "runtime.sqlite3"
        database = _database(path)
        request = ProductTaskRequest(
            task_id=SMOKE_TASK_ID,
            prompt=SMOKE_TASK_PROMPT,
            permission=AgentExecutionPermission.READ_ONLY,
            timeout_seconds=120,
        )
        service = _service(
            database=database,
            executor=observed,
            workspace_root=root,
            artifacts=ArtifactStore(),
        )
        try:
            result = service.run(request)
            reopened = _service(
                database=_database(path),
                executor=observed,
                workspace_root=root,
                artifacts=ArtifactStore(),
            )
            replayed = reopened.run(request)
            candidate = (
                reopened.get_artifact(result.result_artifact_ref)
                if result.result_artifact_ref
                else None
            )
            verification = (
                reopened.get_verification(result.verification_ref)
                if result.verification_ref
                else None
            )
        except Exception:
            after_digest = workspace_digest(root)
            return _failed_report(
                observed=observed,
                workspace_unchanged=before_digest == after_digest,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    after_digest = workspace_digest(root)
    backend_sessions = tuple(
        value.backend_session_id for value in observed.results
    )
    checks = {
        "two_isolated_agent_invocations": (
            len(observed.requests) == 2
            and len(observed.results) == 2
            and all(backend_sessions)
            and len(set(backend_sessions)) == 2
        ),
        "planner_then_reviewer": [
            value.agent_id for value in observed.requests
        ] == ["planner-agent", "reviewer-agent"],
        "read_only_sandbox": all(
            value.permission is AgentExecutionPermission.READ_ONLY
            for value in observed.requests
        ) and all(
            value.sandbox == AgentExecutionPermission.READ_ONLY.value
            for value in observed.results
        ),
        "runtime_assignment_and_message": (
            result.recipient_agent_id == "reviewer-agent"
            and bool(result.assignment_id)
            and bool(result.message_id)
        ),
        "artifact_and_verification_durable": (
            candidate is not None
            and verification is not None
            and verification.subject_refs == (result.result_artifact_ref,)
        ),
        "runtime_validator_passed": (
            result.status is ProductTaskStatus.VALIDATED
            and result.validation_outcome is VerificationOutcome.PASSED
            and candidate is not None
            and candidate.content == SMOKE_REVIEW_FINAL
        ),
        "same_task_replayed_without_extra_call": (
            replayed == result and len(observed.requests) == 2
        ),
        "workspace_unchanged": before_digest == after_digest,
    }
    passed = all(checks.values())
    usage = {
        "input_tokens": sum(
            value.usage.input_tokens for value in observed.results
        ),
        "cached_input_tokens": sum(
            value.usage.cached_input_tokens for value in observed.results
        ),
        "output_tokens": sum(
            value.usage.output_tokens for value in observed.results
        ),
        "reasoning_output_tokens": sum(
            value.usage.reasoning_output_tokens for value in observed.results
        ),
    }
    return {
        "status": "passed" if passed else "failed",
        "error_code": "" if passed else (
            result.error_code or "PRODUCT_SMOKE_ACCEPTANCE_FAILED"
        ),
        "backend_id": "codex_cli",
        "agent_invocations": len(observed.requests),
        "agents": _public_agent_results(observed),
        "result": {
            "status": result.status.value,
            "error_code": result.error_code,
            "recipient_agent": result.recipient_agent_id,
            "assignment_created": bool(result.assignment_id),
            "message_persisted": bool(result.message_id),
            "artifact_persisted": bool(result.result_artifact_ref),
            "verification_persisted": bool(result.verification_ref),
            "validation_outcome": result.validation_outcome.value,
        },
        "checks": checks,
        "usage": usage,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _real_executor() -> CodexCliAgentExecutor:
    return CodexCliAgentExecutor(
        executable=CODEX_EXECUTABLE,
        cli_version=CODEX_CLI_VERSION,
        transport=SupervisedCodexCliTransport(
            runner=CodexCliProcessRunner(executable=CODEX_EXECUTABLE),
            approver_factory=lambda: LocalExecutionApprover(True),
        ),
    )


def _parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run one real read-only Codex double-Agent product smoke."
    )
    value.add_argument(
        "--trusted-real-double-agent",
        action="store_true",
        help="Confirm at most two real Codex Agent invocations.",
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.trusted_real_double_agent:
        parser.error(
            "真实产品smoke会使用ChatGPT订阅发起最多两次Codex Agent调用；"
            "确认后显式添加 --trusted-real-double-agent"
        )
    report = run_codex_product_smoke(
        executor=_real_executor(),
        workspace_root=Path(__file__).resolve().parent.parent,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
