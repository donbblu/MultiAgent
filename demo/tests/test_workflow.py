import tempfile
import unittest
from pathlib import Path

from coding_workflow.agents import (
    CodingAgent,
    CommandVerificationAgent,
    WorkspaceCodingAgent,
)
from coding_workflow.coordinator import Coordinator
from coding_workflow.models import (
    AgentResult,
    FileChange,
    ImplementationPlan,
    TaskContext,
    TaskState,
)
from coding_workflow.workspace import ProjectWorkspace, WorkspaceError
from coding_workflow.context import ProjectContextBuilder
from coding_workflow.policy import CommandPolicy
from coding_workflow.recording import RunRecorder
from coding_workflow.validation import PlanValidator, SchemaValidationError


class StubCoder(CodingAgent):
    def run(self, task: TaskContext) -> AgentResult:
        return AgentResult(True, f"attempt {task.attempt}")


class FixingBackend:
    def create_plan(self, task, existing_files):
        del existing_files
        value = "ok" if task.attempt >= 2 else "wrong"
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

    def test_command_policy_rejects_non_whitelisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = ProjectWorkspace(Path(temp))
            task = self.make_task()
            task.verification_commands = [["curl", "https://example.com"]]
            result = CommandVerificationAgent(workspace, CommandPolicy()).run(task)
        self.assertFalse(result.passed)
        self.assertIn("白名单", result.feedback[0])

    def test_plan_validator_enforces_allowed_paths(self) -> None:
        task = self.make_task()
        task.allowed_paths = ["src/*.py"]
        plan = ImplementationPlan(
            "越权修改", [FileChange("secrets.txt", "x", "不允许的文件")]
        )
        with self.assertRaises(SchemaValidationError):
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
