from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coding_workflow.agent_executor import (
    AgentExecutionEvent,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentExecutionUsage,
)
from product_agent_smoke import run_codex_product_smoke


class _FakeCodexProductExecutor:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []

    def run(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        if request.agent_id == "planner-agent":
            final_message = json.dumps({
                "schema_version": "planner-delegation/v1",
                "action": "delegate_task",
                "recipient_role": "reviewer",
                "task_instruction": (
                    "审查自包含规则并只返回要求的固定结论。"
                ),
                "required_capabilities": ["core:code_review"],
                "acceptance_summary": "必须返回固定结论。",
            })
        elif request.agent_id == "reviewer-agent":
            final_message = (
                "REVIEW_OK runtime_routes_messages=true "
                "agent_self_acceptance=false"
            )
        else:
            raise AssertionError(f"unexpected agent: {request.agent_id}")
        return AgentExecutionResult(
            status=AgentExecutionStatus.COMPLETED,
            backend_id="codex_cli",
            cli_version="fake-cli-secret-version",
            backend_session_id=f"private-session-{request.agent_id}",
            sandbox=request.permission.value,
            final_message=final_message,
            events=(
                AgentExecutionEvent("session_started"),
                AgentExecutionEvent("turn_started"),
                AgentExecutionEvent(
                    "agent_message",
                    {"text": final_message},
                ),
                AgentExecutionEvent("turn_completed"),
            ),
            usage=AgentExecutionUsage(
                input_tokens=100,
                cached_input_tokens=60,
                output_tokens=20,
                reasoning_output_tokens=5,
            ),
            duration_ms=25,
        )


class ProductAgentSmokeTests(unittest.TestCase):
    def test_public_report_proves_double_agent_chain_without_private_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture.txt").write_text("unchanged", encoding="utf-8")
            executor = _FakeCodexProductExecutor()

            report = run_codex_product_smoke(
                executor=executor,
                workspace_root=root,
            )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["agent_invocations"], 2)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(
            [item["agent_id"] for item in report["agents"]],
            ["planner-agent", "reviewer-agent"],
        )
        self.assertEqual(
            report["result"],
            {
                "status": "validated",
                "error_code": "",
                "recipient_agent": "reviewer-agent",
                "assignment_created": True,
                "message_persisted": True,
                "artifact_persisted": True,
                "verification_persisted": True,
                "validation_outcome": "passed",
            },
        )
        self.assertEqual(len(executor.requests), 2)
        public_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("private-session", public_json)
        self.assertNotIn("fake-cli-secret-version", public_json)
        self.assertNotIn(str(root), public_json)
        self.assertNotIn("planner-delegation/v1", public_json)
        self.assertNotIn("REVIEW_OK", public_json)
        self.assertNotIn("stdout", public_json)
        self.assertNotIn("stderr", public_json)


if __name__ == "__main__":
    unittest.main()
