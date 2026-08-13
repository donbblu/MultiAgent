import tempfile
import threading
import unittest
import json
from pathlib import Path

from coding_workflow.agents import (
    CodingAgent,
    CommandVerificationAgent,
    ReviewAgent,
    VerificationAgent,
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
    InvalidTaskTransition,
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
from coding_workflow.memory import (
    MemoryKind,
    MemoryManager,
    MemoryPolicy,
    MemoryRecord,
    MemoryStore,
)
from coding_workflow.memory_sqlite import SQLiteMemoryStore
from coding_workflow.artifacts import Artifact, ArtifactStore
from coding_workflow.integration import IntegrationError, PatchIntegrator
from coding_workflow.planning import StructuredTaskPlanner
from coding_workflow.dag_runner import run_dag_task
from coding_workflow.results import ResultEnvelope, StaleResultError
from coding_workflow.communication import AgentMessage, MessageType, MessageValidationError
from coding_workflow.harness import (
    CancellationToken,
    LifecycleController,
    LifecycleState,
    NodeSpec,
    TaskDispatcher,
    TaskExecutionState,
    TaskGraph,
    TaskGraphRuntime,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    WorkerRegistry,
    WorkflowSpec,
)
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

    def test_workflow_spec_rejects_cycles(self) -> None:
        with self.assertRaises(ValueError):
            WorkflowSpec(
                "invalid",
                (
                    NodeSpec("a", "planner", ("b",)),
                    NodeSpec("b", "implementer", ("a",)),
                ),
            )

    def test_task_graph_selects_parallel_non_conflicting_tasks(self) -> None:
        graph = TaskGraph(
            (
                TaskSpec("contract", "定义契约", "定义接口", "planner", acceptance_criteria=("契约完整",), output_artifacts=("api-contract",)),
                TaskSpec("backend", "后端", "实现后端", "implementer", dependencies=("contract",), acceptance_criteria=("后端测试通过",), write_scopes=("backend/**",), input_artifacts=("api-contract",), output_artifacts=("backend-patch",)),
                TaskSpec("frontend", "前端", "实现前端", "implementer", dependencies=("contract",), acceptance_criteria=("前端测试通过",), write_scopes=("frontend/**",), input_artifacts=("api-contract",), output_artifacts=("frontend-patch",)),
            )
        )
        states = {
            "contract": TaskExecutionState.SUCCEEDED,
            "backend": TaskExecutionState.PENDING,
            "frontend": TaskExecutionState.PENDING,
        }
        ready = graph.ready_tasks(states, available_artifacts=("api-contract",))
        self.assertEqual([task.task_id for task in ready], ["backend", "frontend"])

    def test_task_graph_serializes_overlapping_write_scopes(self) -> None:
        graph = TaskGraph(
            (
                TaskSpec("a", "任务 A", "修改模块", "implementer", acceptance_criteria=("完成",), write_scopes=("app.py",)),
                TaskSpec("b", "任务 B", "修改模块", "implementer", acceptance_criteria=("完成",), write_scopes=("app.py",)),
            )
        )
        ready = graph.ready_tasks({}, limit=2)
        self.assertEqual(len(ready), 1)

    def test_task_graph_requires_artifact_producer_dependency(self) -> None:
        with self.assertRaises(ValueError):
            TaskGraph(
                (
                    TaskSpec("a", "生产", "生产契约", "planner", acceptance_criteria=("完成",), output_artifacts=("contract",)),
                    TaskSpec("b", "消费", "使用契约", "implementer", acceptance_criteria=("完成",), input_artifacts=("contract",)),
                )
            )

    def test_task_graph_runtime_releases_dependents_after_artifacts(self) -> None:
        graph = TaskGraph(
            (
                TaskSpec("plan", "规划", "输出计划", "planner", acceptance_criteria=("完成",), output_artifacts=("plan",)),
                TaskSpec("code", "编码", "实现计划", "implementer", dependencies=("plan",), acceptance_criteria=("完成",), input_artifacts=("plan",), output_artifacts=("patch",)),
            )
        )
        runtime = TaskGraphRuntime(graph)
        self.assertEqual([item.task_id for item in runtime.claim_ready(2)], ["plan"])
        runtime.succeed("plan", {"plan": "artifact://plan/1"})
        self.assertEqual([item.task_id for item in runtime.claim_ready(2)], ["code"])
        runtime.succeed("code", {"patch": "artifact://patch/1"})
        self.assertTrue(runtime.finished)

    def test_task_graph_runtime_blocks_dependents_on_failure(self) -> None:
        graph = TaskGraph(
            (
                TaskSpec("plan", "规划", "输出计划", "planner", acceptance_criteria=("完成",)),
                TaskSpec("code", "编码", "实现计划", "implementer", dependencies=("plan",), acceptance_criteria=("完成",)),
            )
        )
        runtime = TaskGraphRuntime(graph)
        runtime.claim_ready(1)
        runtime.fail("plan", "无法规划")
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot.states["code"], TaskExecutionState.BLOCKED)
        self.assertTrue(runtime.finished)

    def test_worker_registry_decouples_role_from_worker(self) -> None:
        registry = WorkerRegistry()
        worker = StubCoder()
        registry.register("implementer", worker)
        self.assertIs(registry.resolve("implementer"), worker)
        with self.assertRaises(ValueError):
            registry.register("implementer", StubCoder())

    def test_harness_can_cancel_before_worker_execution(self) -> None:
        token = CancellationToken()
        token.cancel("用户停止任务")
        with tempfile.TemporaryDirectory() as temp:
            result = Coordinator(
                StubCoder(),
                CommandVerificationAgent(ProjectWorkspace(Path(temp))),
                cancellation=token,
            ).run(self.make_task())
        self.assertEqual(result.state, TaskState.CANCELLED)
        self.assertEqual(result.attempt, 0)
        self.assertIn("用户停止任务", result.history[-1])

    def test_task_state_machine_rejects_illegal_transition(self) -> None:
        task = self.make_task()
        with self.assertRaises(InvalidTaskTransition):
            task.transition(TaskState.COMPLETED, "禁止跳过工作流")

    def test_lifecycle_pause_blocks_checkpoint_until_resume(self) -> None:
        controller = LifecycleController()
        controller.mark_running()
        self.assertTrue(controller.request_pause("等待人工确认"))
        passed = threading.Event()

        def wait_at_checkpoint() -> None:
            controller.checkpoint()
            passed.set()

        thread = threading.Thread(target=wait_at_checkpoint)
        thread.start()
        self.assertFalse(passed.wait(0.05))
        self.assertEqual(controller.state, LifecycleState.PAUSED)
        self.assertTrue(controller.resume())
        self.assertTrue(passed.wait(1))
        thread.join(1)
        self.assertEqual(controller.state, LifecycleState.RUNNING)
        self.assertEqual(
            [event.current for event in controller.history()],
            [LifecycleState.CREATED, LifecycleState.RUNNING, LifecycleState.PAUSED, LifecycleState.RUNNING],
        )

    def test_dispatcher_submits_tracks_and_finishes_task(self) -> None:
        class PassingVerifier(VerificationAgent):
            def run(self, task):
                return VerificationResult(True, "验证通过")

        with tempfile.TemporaryDirectory() as temp:
            dispatcher = TaskDispatcher(
                lambda lifecycle: Coordinator(
                    StubCoder(),
                    PassingVerifier(),
                    lifecycle=lifecycle,
                )
            )
            handle = dispatcher.submit(self.make_task())
            result = handle.result(timeout=2)
            status = handle.status()
            dispatcher.shutdown()
        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertEqual(status.lifecycle.state, LifecycleState.COMPLETED)
        self.assertEqual(status.workflow_state, "completed")

    def test_dispatcher_cancels_running_task_at_checkpoint(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingCoder(CodingAgent):
            def run(self, task):
                started.set()
                release.wait(1)
                return AgentResult(True, "实现结束")

        class PassingVerifier(VerificationAgent):
            def run(self, task):
                return VerificationResult(True, "验证通过")

        dispatcher = TaskDispatcher(
            lambda lifecycle: Coordinator(
                BlockingCoder(), PassingVerifier(), lifecycle=lifecycle
            )
        )
        handle = dispatcher.submit(self.make_task())
        self.assertTrue(started.wait(1))
        self.assertTrue(handle.cancel("用户终止"))
        release.set()
        result = handle.result(timeout=2)
        dispatcher.shutdown()
        self.assertEqual(result.state, TaskState.CANCELLED)
        self.assertEqual(handle.status().lifecycle.state, LifecycleState.CANCELLED)

    def test_dispatcher_graceful_shutdown_stops_new_submissions(self) -> None:
        class PassingVerifier(VerificationAgent):
            def run(self, task):
                return VerificationResult(True, "验证通过")

        dispatcher = TaskDispatcher(
            lambda lifecycle: Coordinator(
                StubCoder(), PassingVerifier(), lifecycle=lifecycle
            )
        )
        first = dispatcher.submit(self.make_task())
        self.assertEqual(first.result(timeout=2).state, TaskState.COMPLETED)
        dispatcher.shutdown(wait=True)
        second = self.make_task()
        second.task_id = "T-2"
        with self.assertRaises(RuntimeError):
            dispatcher.submit(second)

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

    def test_memory_active_trigger_and_role_visibility(self) -> None:
        store = MemoryStore()
        manager = MemoryManager(store=store)
        task = self.make_task()
        visible = MemoryRecord.create(
            MemoryKind.LONG_TERM,
            "project_rule",
            "项目使用 unittest",
            task_id=task.task_id,
            visibility=(PLANNER.name,),
            evidence_refs=("pyproject.toml",),
        )
        manager.record(visible)
        planner_view = manager.build(task, PLANNER, trigger="task_created")
        fixer_view = manager.build(task, FIXER, trigger="task_created")
        self.assertEqual(planner_view.memories, (visible,))
        self.assertEqual(fixer_view.memories, ())
        self.assertIn(visible.memory_id, manager.working_memory(task.task_id).memory_refs)

    def test_working_memory_checkpoint_is_versioned(self) -> None:
        manager = MemoryManager()
        task = self.make_task()
        record = MemoryRecord.create(
            MemoryKind.WORKING,
            "node_result",
            "规划完成",
            task_id=task.task_id,
            visibility=(IMPLEMENTER.name,),
        )
        manager.record(record)
        checkpoint = manager.working_memory(task.task_id).checkpoint()
        self.assertEqual(checkpoint["version"], 1)
        self.assertEqual(checkpoint["memory_refs"], (record.memory_id,))

    def test_sqlite_memory_restores_records_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            first = MemoryManager(store=SQLiteMemoryStore(path))
            task = self.make_task()
            record = MemoryRecord.create(
                MemoryKind.WORKING, "progress", "完成规划",
                task_id=task.task_id, visibility=(PLANNER.name,),
            )
            first.record(record)
            first.working_memory(task.task_id).plan_summary = "两阶段实现"
            first.save_checkpoint(task.task_id)

            second = MemoryManager(store=SQLiteMemoryStore(path))
            restored = second.working_memory(task.task_id)
            queried = second.query(task, PLANNER, "完成")
            self.assertEqual(restored.plan_summary, "两阶段实现")
            self.assertEqual(queried[0].memory_id, record.memory_id)

    def test_artifact_store_uses_immutable_references(self) -> None:
        store = ArtifactStore()
        artifact = Artifact.create("contract", "plan", {"version": 1})
        reference = store.put(artifact)
        self.assertTrue(reference.startswith("artifact://"))
        self.assertIs(store.get(reference), artifact)

    def test_graph_executor_runs_independent_tasks_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=2)

        class Worker:
            def run_task(self, request):
                barrier.wait()
                return TaskRunResult(True, request.task.title, {request.task.output_artifacts[0]: request.task.task_id})

        graph = TaskGraph((
            TaskSpec("a", "A", "实现 A", "implementer", acceptance_criteria=("完成",), write_scopes=("a/**",), output_artifacts=("a-patch",)),
            TaskSpec("b", "B", "实现 B", "tester", acceptance_criteria=("完成",), write_scopes=("b/**",), output_artifacts=("b-report",)),
        ))
        registry = WorkerRegistry()
        registry.register("implementer", Worker())
        registry.register("tester", Worker())
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, MemoryManager(), max_workers=2
        ).run(self.make_task())
        self.assertTrue(result.succeeded)
        self.assertEqual(result.attempts, {"a": 1, "b": 1})

    def test_graph_executor_retries_only_failed_subtask(self) -> None:
        calls = {"plan": 0, "code": 0}

        class Worker:
            def run_task(self, request):
                calls[request.task.task_id] += 1
                if request.task.task_id == "code" and calls["code"] == 1:
                    return TaskRunResult(False, "暂时失败", error="暂时失败")
                return TaskRunResult(
                    True, "完成",
                    {name: request.task.task_id for name in request.task.output_artifacts},
                )

        graph = TaskGraph((
            TaskSpec("plan", "规划", "规划", "planner", acceptance_criteria=("完成",), output_artifacts=("plan",)),
            TaskSpec("code", "编码", "编码", "implementer", dependencies=("plan",), acceptance_criteria=("完成",), input_artifacts=("plan",), output_artifacts=("patch",), retry_limit=1),
        ))
        registry = WorkerRegistry()
        registry.register("planner", Worker())
        registry.register("implementer", Worker())
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, MemoryManager(), max_workers=2
        ).run(self.make_task())
        self.assertTrue(result.succeeded)
        self.assertEqual(calls, {"plan": 1, "code": 2})

    def test_graph_executor_persists_verified_long_term_memory(self) -> None:
        class Worker:
            def run_task(self, request):
                return TaskRunResult(True, "规划已验证", {"plan": "内容"})

        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteMemoryStore(Path(temp) / "memory.sqlite3")
            memory = MemoryManager(store=store)
            registry = WorkerRegistry()
            registry.register("planner", Worker())
            graph = TaskGraph((
                TaskSpec("plan", "规划", "规划", "planner", acceptance_criteria=("完成",), output_artifacts=("plan",)),
            ))
            result = TaskGraphExecutor(
                graph, registry, DEFAULT_ROLES, memory
            ).run(self.make_task())
            long_term = store.query(kinds=(MemoryKind.LONG_TERM,))
        self.assertTrue(result.succeeded)
        self.assertEqual(long_term[0].summary, "规划已验证")
        self.assertTrue(long_term[0].evidence_refs[0].startswith("artifact://"))

    def test_structured_planner_repairs_invalid_graph(self) -> None:
        class Client:
            def __init__(self): self.calls = 0
            def generate_json(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return {"tasks": []}
                return {"tasks": [{
                    "task_id": "code", "title": "编码", "objective": "实现功能",
                    "role": "implementer", "acceptance_criteria": ["完成"],
                    "write_scopes": ["app.py"], "output_artifacts": ["patch"],
                }]}

        client = Client()
        graph = StructuredTaskPlanner(client).create_graph(self.make_task())
        self.assertEqual(tuple(graph.tasks), ("code",))
        self.assertEqual(client.calls, 2)

    def test_patch_integrator_rejects_cross_artifact_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            integrator = PatchIntegrator(ProjectWorkspace(Path(temp)), ("*.py",))
            first = Artifact.create("a", "a", ImplementationPlan("a", [FileChange("app.py", "A=1\n", "a")]))
            second = Artifact.create("b", "b", ImplementationPlan("b", [FileChange("app.py", "A=2\n", "b")]))
            with self.assertRaises(IntegrationError):
                integrator.integrate((first, second))
            self.assertFalse((Path(temp) / "app.py").exists())

    def test_dag_runner_merges_then_verifies(self) -> None:
        class Client:
            def __init__(self): self.calls = 0
            def generate_json(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return {"tasks": [{
                        "task_id": "code", "title": "编码", "objective": "实现功能",
                        "role": "implementer", "acceptance_criteria": ["测试通过"],
                        "write_scopes": ["app.py", "test_app.py"],
                        "output_artifacts": ["patch"],
                    }]}
                return {
                    "summary": "实现完成",
                    "changes": [
                        {"path": "app.py", "content": "VALUE = 1\n", "reason": "实现"},
                        {"path": "test_app.py", "content": "import unittest\nfrom app import VALUE\nclass T(unittest.TestCase):\n def test_v(self): self.assertEqual(VALUE, 1)\n", "reason": "测试"},
                    ],
                    "suggested_checks": [],
                }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = ProjectWorkspace(root)
            task = TaskContext(
                "DAG-1", "实现功能", ["测试通过"],
                [["python3", "-m", "unittest", "-v"]],
                allowed_paths=["*.py"],
            )
            policy = CommandPolicy(allowed_commands=task.verification_commands)
            result = run_dag_task(
                task, Client(), workspace, memory_path=root / "memory.sqlite3",
                command_policy=policy,
            )
        self.assertEqual(result.task.state, TaskState.COMPLETED)
        self.assertTrue(result.task.verification.passed)

    def test_dag_runner_marks_integration_conflict_failed(self) -> None:
        class Client:
            def __init__(self): self.calls = 0
            def generate_json(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return {"tasks": [
                        {"task_id": "a", "title": "A", "objective": "A", "role": "implementer", "acceptance_criteria": ["完成"], "write_scopes": ["app.py"], "output_artifacts": ["a-patch"]},
                        {"task_id": "b", "title": "B", "objective": "B", "role": "implementer", "acceptance_criteria": ["完成"], "write_scopes": ["app.py"], "output_artifacts": ["b-patch"]},
                    ]}
                return {"summary": "修改", "changes": [{"path": "app.py", "content": "X=1\n", "reason": "实现"}]}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = TaskContext("DAG-C", "冲突", ["完成"], allowed_paths=["*.py"])
            result = run_dag_task(
                task, Client(), ProjectWorkspace(root), memory_path=root / "memory.sqlite3"
            )
        self.assertEqual(result.task.state, TaskState.FAILED)
        self.assertTrue(result.task.feedback)

    def test_memory_and_project_files_share_one_context_budget(self) -> None:
        policy = MemoryPolicy(
            frozenset({"task", "project_files", "long_term"}),
            frozenset(),
            max_context_chars=10,
            include_project_files=True,
        )
        manager = MemoryManager({IMPLEMENTER.name: policy})
        task = self.make_task()
        manager.record(
            MemoryRecord.create(
                MemoryKind.LONG_TERM,
                "rule",
                "123456",
                task_id=task.task_id,
                visibility=(IMPLEMENTER.name,),
            )
        )
        view = manager.build(
            task,
            IMPLEMENTER,
            [ProjectFile("app.py", "12345")],
        )
        self.assertEqual(view.memories, ())

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

    def test_agent_message_has_uniform_fields(self) -> None:
        message = AgentMessage.create(
            task_id="T-1",
            task_version=2,
            sender="tester",
            recipient="coordinator",
            message_type=MessageType.RESULT,
            summary="验证完成",
            payload={"passed": True},
            correlation_id="quality-stage",
        )
        self.assertEqual(message.message_type, MessageType.RESULT)
        self.assertEqual(message.correlation_id, "quality-stage")
        self.assertEqual(message.payload, {"passed": True})

    def test_agent_message_rejects_sensitive_payload_fields(self) -> None:
        with self.assertRaises(MessageValidationError):
            AgentMessage.create(
                task_id="T-1",
                task_version=0,
                sender="implementer",
                recipient="coordinator",
                message_type=MessageType.RESULT,
                summary="非法消息",
                payload={"nested": {"api_key": "secret"}},
            )

    def test_workflow_records_agent_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as project:
            workspace = ProjectWorkspace(Path(project))
            task = self.make_task()
            Coordinator(
                WorkspaceCodingAgent(FixingBackend(), workspace),
                CommandVerificationAgent(workspace),
                recorder=RunRecorder(Path(temp)),
            ).run(task)
            entries = [
                json.loads(line)
                for line in (Path(temp) / task.task_id / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        messages = [item["payload"] for item in entries if item["event"] == "agent_message"]
        self.assertGreaterEqual(len(messages), 6)
        required = {
            "message_id", "task_id", "task_version", "sender", "recipient",
            "message_type", "summary", "payload", "correlation_id", "created_at",
        }
        self.assertTrue(all(required.issubset(message) for message in messages))
        self.assertTrue(any(message["message_type"] == "final" for message in messages))

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
