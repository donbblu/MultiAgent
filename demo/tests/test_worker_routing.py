import tempfile
import unittest
from pathlib import Path

from coding_workflow import (
    AcceptanceCriterion,
    Artifact,
    ArtifactDraft,
    ArtifactStore,
    DEFAULT_ROLES,
    MemoryManager,
    SQLiteRuntimeStore,
    TaskContext,
    TaskExecutionState,
    TaskGraph,
    TaskGraphExecutor,
    TaskRunResult,
    TaskSpec,
    ValidatorProfile,
    ValidatorProfileRunner,
    ValidatorRegistry,
    ValidatorRunResult,
    ValidatorSpec,
    VerificationOutcome,
    WorkerDescriptor,
    WorkerRegistry,
    WorkerSelectionCode,
    WorkerSelectionError,
    WorkerSelectionRequest,
)


def parent_task() -> TaskContext:
    return TaskContext("root-task", "完成编码任务", ["固定测试通过"])


class _Worker:
    def __init__(self, label: str = "worker") -> None:
        self.label = label
        self.calls = 0

    def run_task(self, request):
        self.calls += 1
        return TaskRunResult(
            True,
            self.label,
            {name: self.label for name in request.task.output_artifacts},
        )


class WorkerRoutingTests(unittest.TestCase):
    def test_role_first_hard_filters_and_stable_tie_break(self) -> None:
        registry = WorkerRegistry()
        registry.register_worker(
            WorkerDescriptor(
                "other-role", "planner", frozenset({"code"}),
                frozenset({"core:requirement"}),
                frozenset({"core:patch"}), frozenset({"sandboxed"}),
            ),
            object(),
        )
        registry.register_worker(
            WorkerDescriptor("missing-cap", "implementer"), object()
        )
        registry.register_worker(
            WorkerDescriptor(
                "offline", "implementer", frozenset({"code"}),
                frozenset({"core:requirement"}),
                frozenset({"core:patch"}), frozenset({"sandboxed"}),
                priority=100,
            ),
            object(),
            availability=lambda: False,
        )
        for worker_id in ("worker-b", "worker-a"):
            registry.register_worker(
                WorkerDescriptor(
                    worker_id, "implementer", frozenset({"code"}),
                    frozenset({"core:requirement"}),
                    frozenset({"core:patch"}), frozenset({"sandboxed"}),
                    priority=10,
                ),
                object(),
            )

        selection = registry.select(WorkerSelectionRequest(
            "implement",
            "implementer",
            frozenset({"code"}),
            frozenset({"core:requirement"}),
            frozenset({"core:patch"}),
            frozenset({"sandboxed"}),
        ))

        self.assertEqual(selection.descriptor.worker_id, "worker-a")
        decisions = {
            item.worker_id: item for item in selection.decision.candidates
        }
        self.assertEqual(decisions["other-role"].rejected_at, "role_unavailable")
        self.assertEqual(decisions["missing-cap"].rejected_at, "missing_capability")
        self.assertEqual(decisions["offline"].rejected_at, "unavailable")
        self.assertEqual(decisions["worker-b"].rejected_at, "tie_break")
        self.assertEqual(registry.audit_snapshot(), (selection.decision,))

    def test_no_eligible_worker_is_structured_and_never_crosses_role(self) -> None:
        registry = WorkerRegistry()
        registry.register_worker(
            WorkerDescriptor(
                "planner-with-code", "planner", frozenset({"code"})
            ),
            object(),
        )
        registry.register_worker(
            WorkerDescriptor("implementer-read-only", "implementer"), object()
        )

        with self.assertRaises(WorkerSelectionError) as caught:
            registry.select(WorkerSelectionRequest(
                "implement", "implementer", frozenset({"code"})
            ))

        decision = caught.exception.decision
        self.assertEqual(decision.code, WorkerSelectionCode.MISSING_CAPABILITY)
        self.assertFalse(decision.selected)
        self.assertEqual(decision.selected_worker_id, "")
        self.assertIn("did not cross Role", decision.reason)

    def test_legacy_registration_and_descriptor_snapshot_stay_compatible(self) -> None:
        registry = WorkerRegistry()
        worker = object()
        registry.register("implementer", worker)
        self.assertIs(registry.resolve("implementer"), worker)
        snapshot = registry.snapshot()
        self.assertEqual(snapshot.descriptors[0].worker_id, "implementer")
        self.assertTrue(snapshot.availability["implementer"])
        with self.assertRaises(TypeError):
            snapshot.availability["implementer"] = False

    def test_executor_blocks_missing_capability_without_invoking_worker(self) -> None:
        worker = _Worker()
        registry = WorkerRegistry()
        registry.register("implementer", worker)
        graph = TaskGraph((TaskSpec(
            "implement", "实现", "修改代码", "implementer",
            acceptance_criteria=("完成",), output_artifacts=("patch",),
            required_capabilities=("code_generation",),
        ),))

        artifacts = ArtifactStore()
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, MemoryManager(), artifacts=artifacts
        ).run(parent_task())

        self.assertEqual(
            result.snapshot.states["implement"], TaskExecutionState.BLOCKED
        )
        self.assertEqual(worker.calls, 0)
        self.assertEqual(
            result.worker_selections["implement"].code,
            WorkerSelectionCode.MISSING_CAPABILITY,
        )
        self.assertIn("missing_capability", result.snapshot.failures["implement"])

    def test_review_automatically_uses_independent_principal(self) -> None:
        registry = WorkerRegistry()
        implementer = _Worker("patch")
        self_reviewer = _Worker("self-review")
        independent = _Worker("independent-review")
        registry.register_worker(
            WorkerDescriptor(
                "implementer-main", "implementer", principal_id="agent-a"
            ),
            implementer,
        )
        registry.register_worker(
            WorkerDescriptor(
                "reviewer-self", "reviewer", principal_id="agent-a", priority=100
            ),
            self_reviewer,
        )
        registry.register_worker(
            WorkerDescriptor(
                "reviewer-independent", "reviewer", principal_id="agent-b"
            ),
            independent,
        )
        graph = TaskGraph((
            TaskSpec(
                "implement", "实现", "修改代码", "implementer",
                acceptance_criteria=("完成",), output_artifacts=("patch",),
            ),
            TaskSpec(
                "review", "审查", "独立审查", "reviewer",
                dependencies=("implement",), acceptance_criteria=("完成",),
                input_artifacts=("patch",), output_artifacts=("review",),
            ),
        ))

        artifacts = ArtifactStore()
        result = TaskGraphExecutor(
            graph, registry, DEFAULT_ROLES, MemoryManager(), artifacts=artifacts
        ).run(parent_task())

        self.assertTrue(result.succeeded)
        self.assertEqual(self_reviewer.calls, 0)
        self.assertEqual(independent.calls, 1)
        self.assertEqual(
            result.worker_selections["review"].selected_principal_id, "agent-b"
        )
        patch = result.snapshot.artifacts["patch"]
        self.assertEqual(
            artifacts.get(patch).metadata["runtime_provenance"]["principal_id"],
            "agent-a",
        )

    def test_worker_selection_audit_survives_runtime_checkpoint(self) -> None:
        registry = WorkerRegistry()
        registry.register_worker(
            WorkerDescriptor(
                "implementation", "implementer",
                frozenset({"code_generation"}),
                frozenset({"core:requirement"}),
                frozenset({"core:patch"}),
                frozenset({"sandboxed"}),
            ),
            _Worker(),
        )
        graph = TaskGraph((TaskSpec(
            "implement", "实现", "修改代码", "implementer",
            acceptance_criteria=("完成",), output_artifacts=("patch",),
            required_capabilities=("code_generation",),
            input_protocols=("core:requirement",),
            output_protocols=("core:patch",),
            required_policy_tags=("sandboxed",),
        ),))
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteRuntimeStore(Path(temp) / "runtime.sqlite3")
            TaskGraphExecutor(
                graph, registry, DEFAULT_ROLES, MemoryManager(),
                runtime_store=store, snapshot_id="routing",
            ).run(parent_task())
            restored = store.load("routing")

        decision = restored.runner_data["worker_selections"]["implement"]
        self.assertEqual(decision["selected_worker_id"], "implementation")
        self.assertEqual(decision["code"], "selected")
        spec = restored.graph.tasks["implement"]
        self.assertEqual(spec.required_capabilities, ("code_generation",))
        self.assertEqual(spec.input_protocols, ("core:requirement",))
        self.assertEqual(spec.output_protocols, ("core:patch",))
        self.assertEqual(spec.required_policy_tags, ("sandboxed",))

    def test_validator_cannot_verify_own_principal_artifact(self) -> None:
        class PassingValidator:
            def validate(self, request):
                return ValidatorRunResult(
                    VerificationOutcome.PASSED,
                    "通过",
                    (ArtifactDraft({"exit_code": 0}, kind="tool_result"),),
                )

        criterion = AcceptanceCriterion(
            "unit_tests", "固定测试", "core:test"
        )
        profile = ValidatorProfile(
            "default",
            (ValidatorSpec("unit", "core:test", ("unit_tests",)),),
            {criterion.criterion_id: criterion.digest},
        )
        artifacts = ArtifactStore()
        subject = artifacts.put(Artifact.create(
            "patch",
            "task",
            "candidate",
            metadata={
                "runtime_provenance": {
                    "worker_id": "implementation",
                    "principal_id": "agent-a",
                    "role": "implementer",
                    "task_id": "implement",
                }
            },
        ))
        validators = ValidatorRegistry()
        validators.register(
            "core:test", PassingValidator(), principal_id="agent-a"
        )

        result = ValidatorProfileRunner(
            profile, validators, artifacts
        ).run(task_id="task", subject_refs=(subject,))

        self.assertEqual(result.outcome, VerificationOutcome.UNKNOWN)
        self.assertIn("拒绝自证", result.validator_records[0].summary)


if __name__ == "__main__":
    unittest.main()
