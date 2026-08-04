import tempfile
import threading
import unittest
from pathlib import Path

from coding_workflow.agents import (
    CodingAgent,
    CommandVerificationAgent,
    ReviewAgent,
    WorkspaceCodingAgent,
)
from coding_workflow.coordinator import Coordinator
from coding_workflow.models import (
    AgentResult,
    FileChange,
    ImplementationPlan,
    ProjectFile,
    ReviewResult,
    TaskContext,
    TaskState,
    VerificationResult,
)
from coding_workflow.workspace import ProjectWorkspace, WorkspaceError
from coding_workflow.context import ProjectContextBuilder
from coding_workflow.policy import CommandPolicy
from coding_workflow.recording import RunRecorder
from coding_workflow.validation import PlanValidator, SchemaValidationError
from coding_workflow.backends import StructuredCodingBackend, StructuredReviewBackend
from coding_workflow.model import ModelClientFactory, ProviderPreset
from coding_workflow.roles import (
    Capability,
    DEFAULT_ROLES,
    IMPLEMENTER,
    PLANNER,
    RoleSpec,
    FIXER,
    TESTER,
)
from coding_workflow.memory import MemoryManager, MemoryPolicy
from coding_workflow.results import ResultEnvelope, StaleResultError
from coding_agent_cli import parse_command, safe_output_path


class StubCoder(CodingAgent):
    def run(self, task: TaskContext) -> AgentResult:
        return AgentResult(True, f"attempt {task.attempt}")


class FixingBackend:
    def create_plan(self, memory):
        value = "ok" if memory.attempt >= 2 else "wrong"
        return ImplementationPlan(
            "实现功能",
            [
                FileChange("app.py", f'VALUE = "{value}"\n', "实现功能"),
                FileChange(
                    "test_app.py",
                    "import unittest\n"
                    "from app import VALUE\n"
                    "class TestValue(unittest.TestCase):\n"
                    "    def test_value(self): self.assertEqual(VALUE, 'ok')\n",
                    "添加验收测试",
                ),
            ],
        )


class WorkflowTests(unittest.TestCase):
    def make_task(self) -> TaskContext:
        return TaskContext(
            "T-1",
            "实现功能",
            ["测试通过"],
            [["python3", "-m", "unittest", "-v"]],
        )

    def test_real_rework_then_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            result = Coordinator(
                WorkspaceCodingAgent(FixingBackend(), workspace),
                CommandVerificationAgent(workspace),
            ).run(self.make_task())
            self.assertEqual(result.state, TaskState.COMPLETED)
            self.assertEqual(result.attempt, 2)
            self.assertEqual(workspace.read_text("app.py"), 'VALUE = "ok"\n')
            self.assertTrue(all(item.passed for item in result.verification.criteria_results))
            self.assertEqual(
                result.role_history,
                ["planner", "implementer", "tester", "fixer", "tester"],
            )

    def test_default_roles_are_registered_and_separate_from_agents(self) -> None:
        self.assertEqual(
            DEFAULT_ROLES.names(),
            ("fixer", "implementer", "planner", "reviewer", "tester"),
        )
        self.assertTrue(IMPLEMENTER.allows(Capability.WRITE_PROJECT))
        self.assertFalse(PLANNER.allows(Capability.WRITE_PROJECT))

    def test_coding_worker_rejects_role_without_write_capability(self) -> None:
        read_only = RoleSpec(
            "read-only", "只读分析", frozenset({Capability.READ_PROJECT})
        )
        task = self.make_task()
        task.assign_role(read_only)
        with tempfile.TemporaryDirectory() as temp:
            result = WorkspaceCodingAgent(
                FixingBackend(), ProjectWorkspace(Path(temp))
            ).run(task)
        self.assertFalse(result.success)
        self.assertIn("无写入能力", result.error)

    def test_active_role_is_exposed_to_model_input(self) -> None:
        task = self.make_task()
        task.assign_role(IMPLEMENTER)
        self.assertEqual(task.model_input()["role"]["name"], "implementer")

    def test_role_memory_view_is_minimized_by_role(self) -> None:
        task = self.make_task()
        task.feedback = ["测试失败"]
        files = [ProjectFile("app.py", "VALUE = 1\n")]
        manager = MemoryManager()

        tester = manager.build(task, TESTER, files)
        fixer = manager.build(task, FIXER, files)

        self.assertEqual(tester.project_files, ())
        self.assertEqual(tester.feedback, ())
        self.assertEqual(
            tester.verification_commands,
            (("python3", "-m", "unittest", "-v"),),
        )
        self.assertEqual(fixer.feedback, ("测试失败",))
        self.assertEqual(fixer.project_files[0].path, "app.py")
        self.assertEqual(fixer.verification_commands, ())

    def test_role_memory_enforces_context_budget(self) -> None:
        policy = MemoryPolicy(
            frozenset({"task", "project_files"}),
            frozenset({"implementation_result"}),
            max_context_chars=5,
            include_project_files=True,
        )
        manager = MemoryManager({IMPLEMENTER.name: policy})
        view = manager.build(
            self.make_task(), IMPLEMENTER, [ProjectFile("app.py", "123456789")]
        )
        self.assertEqual(view.project_files[0].content, "12345")
        self.assertTrue(view.project_files[0].truncated)

    def test_memory_policy_rejects_secret_access(self) -> None:
        with self.assertRaises(ValueError):
            MemoryPolicy(frozenset(), frozenset(), 100, secret_access=True)

    def test_tester_and_reviewer_run_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=2)

        class ConcurrentVerifier(CommandVerificationAgent):
            def run(self, task):
                barrier.wait()
                return VerificationResult(True, "验证通过")

        class ConcurrentReviewer(ReviewAgent):
            def run(self, task):
                barrier.wait()
                return ReviewResult(True, "审查通过")

        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            result = Coordinator(
                StubCoder(),
                ConcurrentVerifier(workspace),
                review_agent=ConcurrentReviewer(),
            ).run(self.make_task())
        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertEqual(result.role_history, ["planner", "implementer", "tester", "reviewer"])
        self.assertTrue(result.review.passed)

    def test_result_envelope_rejects_stale_version(self) -> None:
        envelope = ResultEnvelope.create("T-1", 2, "tester", "verification", "ok")
        with self.assertRaises(StaleResultError):
            envelope.validate_for("T-1", 3)

    def test_structured_reviewer_blocks_high_severity_findings(self) -> None:
        class FakeReviewClient:
            def generate_json(self, messages):
                self.messages = messages
                return {
                    "passed": True,
                    "summary": "发现阻断问题",
                    "findings": [
                        {
                            "severity": "high",
                            "path": "app.py",
                            "message": "边界条件未处理",
                        }
                    ],
                }

        memory = MemoryManager().build(
            self.make_task(),
            DEFAULT_ROLES.get("reviewer"),
            [ProjectFile("app.py", "VALUE = 1\n")],
        )
        result = StructuredReviewBackend(FakeReviewClient()).review(memory)
        self.assertFalse(result.passed)
        self.assertIn("边界条件未处理", result.feedback[0])

    def test_stops_when_implementation_keeps_failing(self) -> None:
        class FailingCoder(CodingAgent):
            def run(self, task):
                return AgentResult(False, "失败", error="模型不可用")

        with tempfile.TemporaryDirectory() as temp:
            result = Coordinator(
                FailingCoder(), CommandVerificationAgent(ProjectWorkspace(Path(temp))), 2
            ).run(self.make_task())
        self.assertEqual(result.state, TaskState.FAILED)
        self.assertEqual(result.attempt, 2)
        self.assertEqual(result.feedback, ["模型不可用"])

    def test_rejects_empty_objective(self) -> None:
        task = self.make_task()
        task.objective = " "
        with tempfile.TemporaryDirectory() as temp:
            result = Coordinator(
                StubCoder(), CommandVerificationAgent(ProjectWorkspace(Path(temp)))
            ).run(task)
        self.assertEqual(result.state, TaskState.FAILED)
        self.assertEqual(result.attempt, 0)

    def test_invalid_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                Coordinator(
                    StubCoder(), CommandVerificationAgent(ProjectWorkspace(Path(temp))), 0
                )

    def test_workspace_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            with self.assertRaises(WorkspaceError):
                workspace.apply_changes([FileChange("../escape.py", "", "非法路径")])

    def test_verifier_requires_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            verifier = CommandVerificationAgent(ProjectWorkspace(Path(temp)))
            task = TaskContext("T", "目标", ["标准"])
            result = verifier.run(task)
        self.assertFalse(result.passed)
        self.assertIn("verification_commands", result.feedback[0])

    def test_verifier_rejects_zero_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            command = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
            task = TaskContext("T", "目标", ["至少有测试"], [command])
            result = CommandVerificationAgent(
                workspace,
                CommandPolicy(allowed_executables={"python3"}, allowed_commands=[command]),
            ).run(task)
        self.assertFalse(result.passed)

    def test_command_policy_rejects_non_whitelisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            task = self.make_task()
            task.verification_commands = [["curl", "https://example.com"]]
            result = CommandVerificationAgent(workspace, CommandPolicy()).run(task)
        self.assertFalse(result.passed)
        self.assertIn("白名单", result.feedback[0])

    def test_command_policy_rejects_unapproved_arguments(self) -> None:
        policy = CommandPolicy(
            allowed_executables={"python3"},
            allowed_commands=[["python3", "safe_test.py"]],
        )
        with self.assertRaises(Exception):
            policy.validate(["python3", "-c", "print('unsafe')"])

    def test_generic_cli_rejects_unsafe_output_and_command(self) -> None:
        with self.assertRaises(ValueError):
            safe_output_path("../escape")
        with self.assertRaises(Exception):
            parse_command("python3 -c 'print(1)'")

    def test_plan_validator_enforces_allowed_paths(self) -> None:
        task = self.make_task()
        task.allowed_paths = ["src/*.py"]
        plan = ImplementationPlan(
            "越权修改", [FileChange("secrets.txt", "x", "不允许的文件")]
        )
        with self.assertRaises(SchemaValidationError):
            PlanValidator().validate(plan, task)

    def test_plan_validator_rejects_protected_paths(self) -> None:
        task = self.make_task()
        task.allowed_paths = ["**"]
        for path in [".env", ".git/config", ".verification/test.py", ".runs/log"]:
            plan = ImplementationPlan("非法修改", [FileChange(path, "x", "越权")])
            with self.subTest(path=path), self.assertRaises(SchemaValidationError):
                PlanValidator().validate(plan, task)

    def test_context_builder_prioritizes_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            workspace.apply_changes([
                FileChange("src/app.py", "VALUE = 1", "fixture"),
                FileChange("README.md", "project docs", "fixture"),
            ])
            selected = ProjectContextBuilder(workspace, max_files=1).select(self.make_task())
        self.assertEqual(selected[0].path, "README.md")
        self.assertEqual(selected[0].content, "project docs")

    def test_context_builder_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            workspace.apply_changes([
                FileChange(".env", "API_KEY=secret", "fixture"),
                FileChange("app.py", "VALUE = 1", "fixture"),
            ])
            selected = ProjectContextBuilder(workspace).select(self.make_task())
        self.assertEqual([item.path for item in selected], ["app.py"])

    def test_structured_backend_parses_model_plan(self) -> None:
        class FakeClient:
            def generate_json(self, messages):
                self.messages = messages
                return {
                    "summary": "实现完成",
                    "changes": [
                        {"path": "app.py", "content": "VALUE = 1\n", "reason": "实现"}
                    ],
                    "suggested_checks": [["python3", "safe_test.py"]],
                }

        client = FakeClient()
        task = self.make_task()
        task.assign_role(IMPLEMENTER)
        memory = MemoryManager().build(task, IMPLEMENTER)
        plan = StructuredCodingBackend(client).create_plan(memory)
        self.assertEqual(plan.changes[0].path, "app.py")
        self.assertIn("只输出 JSON", client.messages[0]["content"])

    def test_model_factory_supports_registered_provider(self) -> None:
        ModelClientFactory.register(
            ProviderPreset("test-provider", "https://models.example.test", "TEST_API_KEY", "test-model")
        )
        config = ModelClientFactory.config_from_env("test-provider")
        self.assertEqual(config.provider, "test-provider")
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.api_key_env, "TEST_API_KEY")

    def test_run_recorder_writes_events_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as project:
            workspace = ProjectWorkspace(Path(project))
            task = self.make_task()
            result = Coordinator(
                WorkspaceCodingAgent(FixingBackend(), workspace),
                CommandVerificationAgent(workspace),
                recorder=RunRecorder(Path(temp)),
            ).run(task)
            run_dir = Path(temp) / task.task_id
            self.assertEqual(result.state, TaskState.COMPLETED)
            self.assertTrue((run_dir / "events.jsonl").is_file())
            self.assertTrue((run_dir / "task.json").is_file())


if __name__ == "__main__":
    unittest.main()
