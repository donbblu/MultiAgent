import tempfile
import threading
import unittest
import json
import sqlite3
from pathlib import Path

from coding_workflow.agents import (
    CommandVerificationAgent,
)
from coding_workflow.models import (
    FileChange,
    ImplementationPlan,
    ProjectFile,
    TaskContext,
    TaskState,
    InvalidTaskTransition,
    VerificationResult,
)
from coding_workflow.workspace import ProjectWorkspace, WorkspaceError
from coding_workflow.policy import CommandPolicy
from coding_workflow.validation import PlanValidator, SchemaValidationError
from coding_workflow.backends import StructuredCodingBackend
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
    EntityIndexer,
    EntityRef,
    FailureObservation,
    MemoryKind,
    MemoryManager,
    MemoryPermissionError,
    MemoryPolicy,
    MemoryRecord,
    MemoryStatus,
    QualityGateState,
    MemoryStore,
    WorkingArtifactState,
    WorkingNodeState,
    TokenCounter,
)
from coding_workflow.memory_sqlite import SQLiteMemoryStore
from coding_workflow.artifacts import (
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    ArtifactValidationState,
)
from coding_workflow.integration import IntegrationError, PatchIntegrator
from coding_workflow.planning import StructuredTaskPlanner
from coding_workflow.dag_runner import run_dag_task
from coding_workflow.runtime_sqlite import (
    RuntimeRecoveryError,
    RuntimeSnapshot,
    SQLiteRuntimeStore,
)
from coding_workflow.harness import (
    LifecycleController,
    LifecycleState,
    TaskExecutionState,
    TaskGraph,
    TaskGraphRuntime,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    WorkerRegistry,
)
from coding_agent_cli import parse_command, safe_output_path


class WorkflowTests(unittest.TestCase):
    def make_task(self) -> TaskContext:
        return TaskContext(
            "T-1",
            "实现功能",
            ["测试通过"],
            [["python3", "-m", "unittest", "-v"]],
        )

    def test_default_roles_are_registered_and_separate_from_agents(self) -> None:
        self.assertEqual(
            DEFAULT_ROLES.names(),
            ("fixer", "implementer", "planner", "reviewer", "tester"),
        )
        self.assertTrue(IMPLEMENTER.allows(Capability.WRITE_PROJECT))
        self.assertFalse(PLANNER.allows(Capability.WRITE_PROJECT))

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
        worker = object()
        registry.register("implementer", worker)
        self.assertIs(registry.resolve("implementer"), worker)
        with self.assertRaises(ValueError):
            registry.register("implementer", object())

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

    def test_structured_working_memory_is_role_scoped(self) -> None:
        manager = MemoryManager()
        task = self.make_task()
        manager.update_node(task.task_id, WorkingNodeState(
            "code", "implementer", "succeeded", attempt=1, summary="实现完成"
        ))
        manager.update_node(task.task_id, WorkingNodeState(
            "review", "reviewer", "pending"
        ))
        manager.update_artifact(task.task_id, WorkingArtifactState(
            "artifact://patch", "code", "failed", affected_paths=("app.py",)
        ))
        manager.observe_failure(task.task_id, FailureObservation(
            "verification:1", "tester", "测试失败",
            feedback=("空输入错误",), affected_paths=("app.py",),
            affected_artifacts=("artifact://patch",),
        ))
        manager.update_quality_gate(task.task_id, QualityGateState(
            full_gate_completed=True, passed=False, summary="测试失败"
        ))

        fixer = manager.build(task, FIXER)
        implementer = manager.build(task, IMPLEMENTER)
        tester = manager.build(task, TESTER)
        self.assertEqual(fixer.feedback, ("空输入错误",))
        self.assertEqual(
            fixer.working_progress["unresolved_failures"][0]["failure_id"],
            "verification:1",
        )
        self.assertEqual(
            [item["node_id"] for item in implementer.working_progress["nodes"]],
            ["code"],
        )
        self.assertNotIn("unresolved_failures", tester.working_progress)
        self.assertFalse(tester.working_progress["quality_gate"]["passed"])

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

    def test_artifact_validation_lifecycle_tracks_superseded_result(self) -> None:
        store = ArtifactStore()
        old = store.put(Artifact.create("old", "code", "错误实现"))
        new = store.put(Artifact.create("fix", "fix-1", "修复实现"))
        store.mark_failed((old,), ("verification://failed",))
        store.supersede((old,), new)
        store.mark_verified((new,), ("verification://passed",))
        self.assertEqual(
            store.validation(old).state, ArtifactValidationState.SUPERSEDED
        )
        self.assertEqual(store.validation(old).superseded_by, new)
        self.assertEqual(
            store.validation(new).state, ArtifactValidationState.VERIFIED
        )

    def test_executor_accepts_external_and_existing_typed_artifacts(self) -> None:
        class Worker:
            def run_task(self, request):
                self.input_kind = request.inputs["reference"].kind
                return TaskRunResult(
                    True, "完成", {"ui_spec": ArtifactDraft(
                        {"schema_version": "1.0"}, kind="ui_spec"
                    )}
                )

        graph = TaskGraph((TaskSpec(
            "analyze", "分析", "分析参考图", "planner",
            acceptance_criteria=("生成 UI Spec",),
            input_artifacts=("reference",), output_artifacts=("ui_spec",),
        ),), external_artifacts=("reference",))
        store = ArtifactStore()
        reference = store.put(Artifact.create(
            "reference", "T-1", {"asset_id": "image"}, kind="reference_image"
        ))
        worker = Worker()
        registry = WorkerRegistry()
        registry.register("planner", worker)
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, MemoryManager(), artifacts=store,
            initial_artifacts={"reference": reference},
        ).run(self.make_task())

        self.assertTrue(result.succeeded)
        self.assertEqual(worker.input_kind, "reference_image")
        self.assertEqual(
            store.get(result.snapshot.artifacts["ui_spec"]).kind, "ui_spec"
        )

    def test_runtime_snapshot_restores_artifacts_and_requeues_running_node(self) -> None:
        class Worker:
            def __init__(self):
                self.calls = []

            def run_task(self, request):
                self.calls.append(request.task.task_id)
                self.assert_input = request.inputs["a-patch"].content
                return TaskRunResult(True, "B 完成", {"b-patch": "B"})

        graph = TaskGraph((
            TaskSpec(
                "a", "A", "实现 A", "implementer",
                acceptance_criteria=("完成",), output_artifacts=("a-patch",),
            ),
            TaskSpec(
                "b", "B", "实现 B", "implementer", dependencies=("a",),
                acceptance_criteria=("完成",), input_artifacts=("a-patch",),
                output_artifacts=("b-patch",),
            ),
        ))
        artifact_store = ArtifactStore()
        a_reference = artifact_store.put(Artifact.create("a-patch", "a", "A"))
        runtime = TaskGraphRuntime(graph)
        runtime.claim_ready(1)
        runtime.succeed("a", {"a-patch": a_reference})
        runtime.claim_ready(1)
        lifecycle = LifecycleController()
        lifecycle.mark_running()

        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRuntimeStore(Path(temp) / "runtime.sqlite3")
            store.save(RuntimeSnapshot(
                "resume", "T-1", "project-a", "executing",
                graph, runtime.snapshot(), {"a": 1, "b": 0},
                lifecycle.snapshot(), artifact_store, {"app.py": "hash"},
            ))
            restored = store.load("resume")
            self.assertIsNotNone(restored)
            worker = Worker()
            registry = WorkerRegistry()
            registry.register("implementer", worker)
            task = self.make_task()
            task.project_id = "project-a"
            result = TaskGraphExecutor(
                restored.graph, registry, DEFAULT_ROLES, MemoryManager(),
                artifacts=restored.artifacts, runtime_snapshot=restored,
                finalize_lifecycle=False,
            ).run(task)

        self.assertTrue(result.succeeded)
        self.assertEqual(worker.calls, ["b"])
        self.assertEqual(worker.assert_input, "A")
        self.assertEqual(restored.lifecycle.state, LifecycleState.RUNNING)

    def test_runtime_snapshot_preserves_external_artifact_inputs(self) -> None:
        graph = TaskGraph((TaskSpec(
            "analyze", "分析", "分析参考图", "planner",
            acceptance_criteria=("完成",), input_artifacts=("reference",),
            output_artifacts=("ui_spec",),
        ),), external_artifacts=("reference",))
        artifacts = ArtifactStore()
        reference = artifacts.put(Artifact.create(
            "reference", "T-1", {"asset_id": "image"}, kind="reference_image"
        ))
        runtime = TaskGraphRuntime(
            graph, initial_artifacts={"reference": reference}
        )
        lifecycle = LifecycleController()
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRuntimeStore(Path(temp) / "runtime.sqlite3")
            store.save(RuntimeSnapshot(
                "external", "T-1", "project", "executing", graph,
                runtime.snapshot(), {"analyze": 0}, lifecycle.snapshot(),
                artifacts, {},
            ))
            restored = store.load("external")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.graph.external_artifacts, {"reference"})
        self.assertEqual(
            restored.graph_snapshot.artifacts["reference"], reference
        )

    def test_runtime_recovery_rejects_workspace_changes(self) -> None:
        graph = TaskGraph((TaskSpec(
            "a", "A", "实现 A", "implementer",
            acceptance_criteria=("完成",), output_artifacts=("patch",),
        ),))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = ProjectWorkspace(root)
            workspace.apply_changes([FileChange("app.py", "A=1\n", "初始")])
            lifecycle = LifecycleController()
            snapshot = RuntimeSnapshot(
                "tamper", "T", "project-a", "executing", graph,
                TaskGraphRuntime(graph).snapshot(), {"a": 0},
                lifecycle.snapshot(), ArtifactStore(), workspace.content_hashes(),
            )
            workspace.apply_changes([FileChange("app.py", "A=2\n", "外部修改")])
            with self.assertRaises(RuntimeRecoveryError):
                SQLiteRuntimeStore.validate_workspace(
                    snapshot, workspace.content_hashes()
                )

    def test_memory_enforces_scope_and_project_isolation(self) -> None:
        manager = MemoryManager()
        project_memory = MemoryRecord.create(
            MemoryKind.LONG_TERM,
            "project_rule",
            "项目 A 的规则",
            project_id="project-a",
            scope="project",
        )
        manager.record(project_memory, include_in_working=False)
        project_a = self.make_task()
        project_a.project_id = "project-a"
        project_b = self.make_task()
        project_b.project_id = "project-b"
        self.assertEqual(
            manager.build(project_a, PLANNER, trigger="task_created").memories,
            (project_memory,),
        )
        self.assertEqual(
            manager.build(project_b, PLANNER, trigger="task_created").memories,
            (),
        )
        self.assertEqual(
            manager.build(project_a, FIXER, trigger="task_created").memories,
            (),
        )
        with self.assertRaises(MemoryPermissionError):
            manager.record_for_role(project_a, FIXER, project_memory)
        foreign_task_memory = MemoryRecord.create(
            MemoryKind.WORKING,
            "node_result",
            "伪造的其他项目结果",
            project_id="project-b",
            task_id=project_a.task_id,
            scope="task",
        )
        with self.assertRaises(MemoryPermissionError):
            manager.record_for_role(project_a, FIXER, foreign_task_memory)

    def test_entity_index_extracts_files_symbols_tests_and_artifacts(self) -> None:
        plan = ImplementationPlan("实现", [
            FileChange(
                "app.py",
                "def greet(name):\n return name\n",
                "实现函数",
            ),
            FileChange(
                "test_app.py",
                "def test_empty():\n assert True\n",
                "增加测试",
            ),
        ])
        entities = set(EntityIndexer.from_plan(plan, ("artifact://patch",)))
        self.assertTrue({
            EntityRef("file", "app.py"),
            EntityRef("symbol", "app.py:greet"),
            EntityRef("test_file", "test_app.py"),
            EntityRef("test", "test_app.py:test_empty"),
            EntityRef("artifact", "artifact://patch"),
        } <= entities)

    def test_entity_and_chinese_search_are_precise_in_both_stores(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stores = (
                MemoryStore(),
                SQLiteMemoryStore(Path(temp) / "entities.sqlite3"),
            )
            target = EntityRef("symbol", "app.py:greet")
            for store in stores:
                relevant = store.append(MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "verified_behavior",
                    "问候函数需要正确处理空输入",
                    project_id="project-a", scope="project",
                    entity_refs=(target, EntityRef("file", "app.py")),
                ))
                store.append(MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "verified_behavior",
                    "数据库连接使用只读事务",
                    project_id="project-a", scope="project",
                    entity_refs=(EntityRef("file", "db.py"),),
                ))
                by_entity = store.query(
                    project_id="project-a", entity_refs=(target,)
                )
                by_chinese = store.query(
                    project_id="project-a", text="空输入错误"
                )
                self.assertEqual(by_entity, (relevant,))
                self.assertEqual(by_chinese, (relevant,))

    def test_context_files_prioritize_exact_entity_memories(self) -> None:
        manager = MemoryManager()
        task = self.make_task()
        relevant = manager.record(MemoryRecord.create(
            MemoryKind.LONG_TERM,
            "verified_behavior",
            "app.py 的空输入规则",
            project_id=task.project_id, scope="project", confidence=0.5,
            entity_refs=(EntityRef("file", "app.py"),),
        ), include_in_working=False)
        manager.record(MemoryRecord.create(
            MemoryKind.LONG_TERM,
            "verified_behavior",
            "不相关但置信度更高的数据库规则",
            project_id=task.project_id, scope="project", confidence=1.0,
            entity_refs=(EntityRef("file", "db.py"),),
        ), include_in_working=False)
        view = manager.build(
            task, IMPLEMENTER, [ProjectFile("app.py", "VALUE = 1\n")]
        )
        self.assertEqual(view.memories[0].memory_id, relevant.memory_id)

    def test_token_budget_counts_final_payload_and_drops_low_priority_memory(self) -> None:
        policy = MemoryPolicy(
            frozenset({"task", "project"}), frozenset({"task"}),
            max_context_chars=20_000, include_project_files=True,
            max_context_tokens=1_000,
        )
        manager = MemoryManager({IMPLEMENTER.name: policy})
        task = self.make_task()
        manager.record(MemoryRecord.create(
            MemoryKind.LONG_TERM,
            "large_note",
            "低优先级历史信息" * 300,
            project_id=task.project_id,
            scope="project",
        ), include_in_working=False)
        view = manager.build(
            task, IMPLEMENTER,
            [ProjectFile("app.py", "VALUE = 1\n" * 1000)],
        )
        payload = {
            "task": view.model_input(),
            "context_files": [
                {"path": item.path, "content": item.content,
                 "truncated": item.truncated}
                for item in view.project_files
            ],
        }
        actual = TokenCounter().count(payload)
        self.assertLessEqual(actual, 1_000)
        self.assertEqual(view.memories, ())
        self.assertTrue(view.project_files)
        self.assertTrue(view.project_files[0].truncated)

    def test_long_term_memory_is_idempotent_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stores = (
                MemoryStore(),
                SQLiteMemoryStore(Path(temp) / "versions.sqlite3"),
            )
            for store in stores:
                first = MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "project_rule",
                    "统一使用 unittest",
                    project_id="project-a",
                    scope="project",
                    semantic_key="testing-framework",
                    evidence_refs=("artifact://one",),
                )
                duplicate = MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "project_rule",
                    "统一使用 unittest",
                    project_id="project-a",
                    scope="project",
                    semantic_key="testing-framework",
                    evidence_refs=("artifact://two",),
                )
                stored_first = store.append(first)
                stored_duplicate = store.append(duplicate)
                self.assertEqual(stored_duplicate.memory_id, stored_first.memory_id)
                self.assertEqual(
                    stored_duplicate.evidence_refs,
                    ("artifact://one", "artifact://two"),
                )

                replacement = MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "project_rule",
                    "统一使用 pytest",
                    project_id="project-a",
                    scope="project",
                    semantic_key="testing-framework",
                )
                stored_replacement = store.append(replacement)
                active = store.query(
                    project_id="project-a", kinds=(MemoryKind.LONG_TERM,)
                )
                history = store.query(
                    project_id="project-a",
                    kinds=(MemoryKind.LONG_TERM,),
                    include_inactive=True,
                )
                self.assertEqual(active, (stored_replacement,))
                self.assertEqual(stored_replacement.version, 2)
                self.assertEqual(stored_replacement.supersedes, stored_first.memory_id)
                self.assertEqual(
                    {item.status for item in history},
                    {MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED},
                )

    def test_invalidated_and_expired_memories_are_not_retrieved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stores = (
                MemoryStore(),
                SQLiteMemoryStore(Path(temp) / "expiry.sqlite3"),
            )
            for store in stores:
                manager = MemoryManager(store=store)
                invalid = manager.record(MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "rule",
                    "已经失效的规则",
                    project_id="project-a",
                    scope="project",
                ))
                manager.invalidate(invalid.memory_id, "项目已经迁移")
                expired = store.append(MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "rule",
                    "已经过期的规则",
                    project_id="project-a",
                    scope="project",
                    expires_at="2000-01-01T00:00:00+00:00",
                ))
                self.assertEqual(store.query(project_id="project-a"), ())
                history = store.query(project_id="project-a", include_inactive=True)
                statuses = {item.memory_id: item.status for item in history}
                self.assertEqual(
                    statuses[invalid.memory_id], MemoryStatus.INVALIDATED
                )
                self.assertEqual(statuses[expired.memory_id], MemoryStatus.EXPIRED)

    def test_memory_store_redacts_common_secrets_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stores = (
                MemoryStore(),
                SQLiteMemoryStore(Path(temp) / "secrets.sqlite3"),
            )
            for store in stores:
                stored = store.append(MemoryRecord.create(
                    MemoryKind.LONG_TERM,
                    "diagnostic",
                    "password=hunter2",
                    project_id="project-a",
                    scope="project",
                    content={
                        "header": "Bearer abcdefghijklmnop",
                        "api_key": "api_key=sk-secret-value",
                        "password": "plain-secret",
                    },
                ))
                persisted = store.query(
                    project_id="project-a", kinds=(MemoryKind.LONG_TERM,)
                )[0]
                self.assertEqual(persisted.memory_id, stored.memory_id)
                serialized = json.dumps(
                    {"summary": persisted.summary, "content": dict(persisted.content)},
                    ensure_ascii=False,
                )
                self.assertNotIn("hunter2", serialized)
                self.assertNotIn("abcdefghijklmnop", serialized)
                self.assertNotIn("sk-secret-value", serialized)
                self.assertNotIn("plain-secret", serialized)
                self.assertIn("REDACTED", serialized)

    def test_sqlite_memory_migrates_legacy_project_id_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """CREATE TABLE memories (
                        memory_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        subtype TEXT NOT NULL, summary TEXT NOT NULL,
                        content TEXT NOT NULL, source TEXT NOT NULL,
                        scope TEXT NOT NULL, visibility TEXT NOT NULL,
                        task_id TEXT, source_ref TEXT NOT NULL,
                        evidence_refs TEXT NOT NULL, sensitivity TEXT NOT NULL,
                        confidence REAL NOT NULL, created_at TEXT NOT NULL,
                        expires_at TEXT, version INTEGER NOT NULL, supersedes TEXT
                    )"""
                )
            SQLiteMemoryStore(path)
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(memories)")
                }
            self.assertTrue({
                "project_id", "semantic_key", "status", "invalidated_at",
                "invalidated_reason", "last_confirmed_at", "entity_refs",
            } <= columns)
            with sqlite3.connect(path) as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertIn("memory_entities", tables)

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
        memory = MemoryManager()
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, memory, max_workers=2
        ).run(self.make_task())
        self.assertTrue(result.succeeded)
        self.assertEqual(calls, {"plan": 1, "code": 2})
        working = memory.working_memory("T-1")
        self.assertEqual(working.nodes["code"].attempt, 2)
        self.assertEqual(working.nodes["code"].state, "succeeded")
        self.assertEqual(working.failures["worker:code:1"].resolved_by, "code")

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
            memory.consolidate(
                "T-1",
                project_id="",
                verified_artifacts=tuple(result.snapshot.artifacts.values()),
            )
            long_term = store.query(kinds=(MemoryKind.LONG_TERM,))
        self.assertTrue(result.succeeded)
        self.assertEqual(len(long_term), 1)
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
            class NoCallClient:
                def generate_json(self, messages):
                    raise AssertionError("已完成恢复不应再次调用模型")

            resumed_task = TaskContext(
                "DAG-1", "实现功能", ["测试通过"],
                [["python3", "-m", "unittest", "-v"]],
                allowed_paths=["*.py"],
            )
            resumed = run_dag_task(
                resumed_task, NoCallClient(), workspace,
                memory_path=root / "memory.sqlite3", command_policy=policy,
            )
            long_term = SQLiteMemoryStore(root / "memory.sqlite3").query(
                project_id=result.task.project_id,
                kinds=(MemoryKind.LONG_TERM,),
            )
            workspace.apply_changes([
                FileChange("app.py", "VALUE = 999\n", "模拟外部修改")
            ])
            tampered_task = TaskContext(
                "DAG-1", "实现功能", ["测试通过"],
                [["python3", "-m", "unittest", "-v"]],
                allowed_paths=["*.py"],
            )
            with self.assertRaises(RuntimeRecoveryError):
                run_dag_task(
                    tampered_task, NoCallClient(), workspace,
                    memory_path=root / "memory.sqlite3", command_policy=policy,
                )
        self.assertEqual(result.task.state, TaskState.COMPLETED)
        self.assertTrue(result.task.verification.passed)
        self.assertEqual(resumed.task.state, TaskState.COMPLETED)
        self.assertTrue(resumed.task.verification.passed)
        self.assertEqual(len(long_term), 1)

    def test_dag_runner_resumes_interrupted_graph_without_replanning(self) -> None:
        class ResumeClient:
            def __init__(self):
                self.calls = 0

            def generate_json(self, messages):
                self.calls += 1
                return {
                    "summary": "补充测试",
                    "changes": [{
                        "path": "test_app.py",
                        "content": (
                            "import unittest\nfrom app import VALUE\n"
                            "class T(unittest.TestCase):\n"
                            " def test_v(self): self.assertEqual(VALUE, 1)\n"
                        ),
                        "reason": "验收测试",
                    }],
                }

        graph = TaskGraph((
            TaskSpec(
                "code", "代码", "实现代码", "implementer",
                acceptance_criteria=("完成",), write_scopes=("app.py",),
                output_artifacts=("code-patch",),
            ),
            TaskSpec(
                "tests", "测试", "补充测试", "implementer",
                dependencies=("code",), acceptance_criteria=("测试通过",),
                write_scopes=("test_app.py",), input_artifacts=("code-patch",),
                output_artifacts=("test-patch",),
            ),
        ))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = ProjectWorkspace(root)
            memory_path = root / "memory.sqlite3"
            task = TaskContext(
                "DAG-RESUME", "实现功能", ["测试通过"],
                [["python3", "-m", "unittest", "-v"]],
                project_root=str(root), allowed_paths=["*.py"],
            )
            artifact_store = ArtifactStore()
            code_ref = artifact_store.put(Artifact.create(
                "code-patch", "code", ImplementationPlan(
                    "代码完成", [FileChange("app.py", "VALUE = 1\n", "实现")]
                ),
            ))
            runtime = TaskGraphRuntime(graph)
            runtime.claim_ready(1)
            runtime.succeed("code", {"code-patch": code_ref})
            runtime.claim_ready(1)
            lifecycle = LifecycleController()
            lifecycle.mark_running()
            runtime_store = SQLiteRuntimeStore(memory_path)
            runtime_store.save(RuntimeSnapshot(
                "DAG-RESUME:dag", task.task_id, task.project_id, "executing",
                graph, runtime.snapshot(), {"code": 1, "tests": 0},
                lifecycle.snapshot(), artifact_store,
                workspace.content_hashes(exclude={"memory.sqlite3"}),
            ))
            client = ResumeClient()
            result = run_dag_task(
                task, client, workspace, memory_path=memory_path,
                command_policy=CommandPolicy(
                    allowed_commands=task.verification_commands
                ),
            )

        self.assertEqual(result.task.state, TaskState.COMPLETED)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.graph_states, {
            "code": "succeeded", "tests": "succeeded",
        })

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

    def test_dag_runner_creates_local_fix_task_after_verification_failure(self) -> None:
        class Client:
            def __init__(self):
                self.calls = 0
                self.fix_input = None

            def generate_json(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return {"tasks": [{
                        "task_id": "code", "title": "编码", "objective": "实现功能",
                        "role": "implementer", "acceptance_criteria": ["值正确"],
                        "write_scopes": ["app.py", "test_app.py"],
                        "output_artifacts": ["patch"],
                    }]}
                if self.calls == 2:
                    return {
                        "summary": "首次实现",
                        "changes": [
                            {"path": "app.py", "content": "VALUE = 0\n", "reason": "实现"},
                            {"path": "test_app.py", "content": (
                                "import unittest\nfrom app import VALUE\n"
                                "class T(unittest.TestCase):\n"
                                " def test_v(self): self.assertEqual(VALUE, 1)\n"
                            ), "reason": "验收测试"},
                        ],
                    }
                self.fix_input = json.loads(messages[-1]["content"])
                return {
                    "summary": "局部修复",
                    "changes": [
                        {
                            "path": "app.py",
                            "content": "VALUE = 1  # repaired\n",
                            "reason": "修复失败",
                        }
                    ],
                    "suggested_checks": [["python3", "-m", "unittest", "-v"]],
                }

        events = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = Client()
            task = TaskContext(
                "DAG-FIX", "实现功能", ["值正确"],
                [["python3", "-m", "unittest", "-v"]],
                allowed_paths=["*.py"],
            )
            result = run_dag_task(
                task, client, ProjectWorkspace(root),
                memory_path=root / "memory.sqlite3",
                command_policy=CommandPolicy(allowed_commands=task.verification_commands),
                event_listener=events.append,
            )
            long_term = SQLiteMemoryStore(root / "memory.sqlite3").query(
                project_id=result.task.project_id,
                kinds=(MemoryKind.LONG_TERM,),
            )
            restored_working = SQLiteMemoryStore(
                root / "memory.sqlite3"
            ).load_checkpoint(task.task_id)

        self.assertEqual(
            result.task.state,
            TaskState.COMPLETED,
            msg={
                "history": result.task.history,
                "feedback": result.task.feedback,
                "states": result.graph_states,
                "model_calls": client.calls,
            },
        )
        self.assertEqual(
            result.task.attempt,
            2,
            msg={
                "history": result.task.history,
                "feedback": result.task.feedback,
                "states": result.graph_states,
                "model_calls": client.calls,
            },
        )
        self.assertEqual(result.graph_states["fix-1"], "succeeded")
        self.assertIn("fixer", result.task.role_history)
        self.assertTrue(any(event["event"] == "fix_task_created" for event in events))
        self.assertTrue(any(event["event"] == "affected_tests_finished" for event in events))
        self.assertEqual(
            {item["path"] for item in client.fix_input["context_files"]},
            {"app.py", "test_app.py"},
        )
        self.assertTrue(client.fix_input["task"]["feedback"])
        self.assertEqual(
            client.fix_input["task"]["working_progress"]
            ["unresolved_failures"][0]["failure_id"],
            "verification:1",
        )
        self.assertEqual([item.summary for item in long_term], ["局部修复"])
        self.assertIn(EntityRef("file", "app.py"), long_term[0].entity_refs)
        self.assertTrue(any(
            item.entity_type == "artifact" for item in long_term[0].entity_refs
        ))
        self.assertTrue(
            any(ref.startswith("verification://") for ref in long_term[0].evidence_refs)
        )
        self.assertIsNotNone(restored_working)
        self.assertEqual(restored_working.nodes["code"].state, "succeeded")
        self.assertEqual(restored_working.nodes["fix-1"].state, "succeeded")
        self.assertEqual(
            {item.state for item in restored_working.artifacts.values()},
            {"superseded", "verified"},
        )
        self.assertEqual(
            restored_working.failures["verification:1"].resolved_by, "fix-1"
        )
        self.assertTrue(restored_working.quality_gate.affected_checks_completed)
        self.assertTrue(restored_working.quality_gate.affected_checks_passed)
        self.assertTrue(restored_working.quality_gate.full_gate_completed)
        self.assertTrue(restored_working.quality_gate.passed)

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


if __name__ == "__main__":
    unittest.main()
