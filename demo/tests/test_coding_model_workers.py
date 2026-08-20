from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from coding_workflow import (
    AblationStrategy,
    AblationWorkerRequest,
    Artifact,
    CodingAblationRunner,
    CodingModelWorkerError,
    FixedCodingSuite,
    ModelAblationWorker,
    ModelCapability,
    ModelCapabilityError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    UsageSource,
    VerificationOutcome,
    build_model_ablation_registry,
    default_ablation_profiles,
)


ALL_TEXT_CAPABILITIES = frozenset({
    ModelCapability.TEXT,
    ModelCapability.STRUCTURED_OUTPUT,
})
ALL_CODE_CAPABILITIES = ALL_TEXT_CAPABILITIES | frozenset({
    ModelCapability.TOOL_CALLING,
})


class QueueModelClient:
    def __init__(self, responses, capabilities=ALL_CODE_CAPABILITIES):
        self.responses = list(responses)
        self._capabilities = frozenset(capabilities)
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self):
        return self._capabilities

    def generate_structured(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Fake Model 响应已耗尽")
        return ModelResponse(
            MappingProxyType(dict(self.responses.pop(0))),
            "fake",
            "fake-structured-model",
            ModelUsage(5, 7, 12),
            3,
        )

    def generate_json(self, messages):
        raise AssertionError("模型 Worker 必须使用 generate_structured")


class CodingModelWorkerTests(unittest.TestCase):
    @property
    def suite(self) -> FixedCodingSuite:
        root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        return FixedCodingSuite.load(root)

    def artifacts(self, *, include_plan=False, include_feedback=False):
        values = {
            "requirement": Artifact.create(
                "requirement",
                "task-1",
                {
                    "objective": "修复舍入",
                    "allowed_write_paths": ["tax.py"],
                    "validator_kinds": ["core:test"],
                },
                kind="core:coding_requirement",
            ),
            "source": Artifact.create(
                "source",
                "task-1",
                {
                    "tax.py": "def calculate_tax(amount, rate):\n    return round(amount * rate, 2)\n",
                    "tests/test_public.py": "# public contract\n",
                },
                kind="core:source_snapshot",
            ),
        }
        if include_plan:
            values["plan"] = Artifact.create(
                "plan",
                "task-1",
                {
                    "schema_version": "1.0",
                    "summary": "use decimal",
                    "steps": [{
                        "step_id": "s1",
                        "objective": "replace float rounding",
                        "target_paths": ["tax.py"],
                        "acceptance_notes": ["runtime tests decide"],
                    }],
                    "risks": [],
                },
                kind="core:plan",
            )
        if include_feedback:
            values["feedback"] = Artifact.create(
                "feedback",
                "task-1",
                {
                    "outcome": "failed",
                    "validator_outcomes": {"core:test": "failed"},
                    "failure_summaries": ["hidden boundary failed"],
                },
                kind="core:validator_feedback",
            )
        return values

    def request(self, strategy, stage_id, artifacts):
        profile = next(
            item for item in default_ablation_profiles(
                worker_policy_tag="model-eval"
            )
            if item.strategy is strategy
        )
        return AblationWorkerRequest(
            "task-1",
            strategy,
            profile.stage(stage_id),
            artifacts,
            ("tax.py",),
        )

    def test_prepare_is_deterministic_auditable_and_does_not_call_model(self):
        client = QueueModelClient([])
        worker = ModelAblationWorker(
            "implementer", client, max_file_chars=8, max_context_chars=2_000
        )
        request = self.request(
            AblationStrategy.SINGLE_AGENT, "implement", self.artifacts()
        )

        first = worker.prepare(request)
        second = worker.prepare(request)
        audit = json.dumps(dict(first.audit_dict()), ensure_ascii=False)

        self.assertEqual(first.request_sha256, second.request_sha256)
        self.assertEqual(len(first.request_sha256), 64)
        self.assertEqual(client.requests, [])
        self.assertNotIn("calculate_tax", audit)
        source = next(
            item for item in first.disclosures
            if item.kind == "core:source_snapshot"
        )
        self.assertTrue(source.truncated)
        self.assertTrue(all(item["sha256"] for item in source.files))
        self.assertTrue(all(item["disclosed_chars"] <= 8 for item in source.files))

    def test_missing_capability_is_rejected_before_model_call(self):
        client = QueueModelClient([], capabilities={ModelCapability.TEXT})
        worker = ModelAblationWorker("implementer", client)
        request = self.request(
            AblationStrategy.SINGLE_AGENT, "implement", self.artifacts()
        )

        with self.assertRaises(ModelCapabilityError):
            worker.run_experiment(request)

        self.assertEqual(client.requests, [])

    def test_patch_path_is_rejected_before_runtime_integration(self):
        client = QueueModelClient([{
            "schema_version": "1.0",
            "summary": "tamper tests",
            "changes": [{
                "path": "tests/test_public.py",
                "content": "pass\n",
                "reason": "bypass",
            }],
            "assumptions": [],
        }])
        worker = ModelAblationWorker("implementer", client)

        with self.assertRaisesRegex(CodingModelWorkerError, "未获授权"):
            worker.run_experiment(self.request(
                AblationStrategy.SINGLE_AGENT, "implement", self.artifacts()
            ))

    def test_diagnosis_rejects_model_claiming_passed(self):
        diagnosis = {
            "schema_version": "1.0",
            "summary": "rounding mismatch",
            "root_causes": [{
                "evidence": "validator failed",
                "hypothesis": "binary float tie behavior",
                "affected_paths": ["tax.py"],
            }],
            "recommended_changes": ["use Decimal"],
            "uncertainty": "must be checked by Runtime",
            "passed": True,
        }
        client = QueueModelClient([diagnosis], ALL_TEXT_CAPABILITIES)
        worker = ModelAblationWorker("tester", client)

        with self.assertRaisesRegex(CodingModelWorkerError, "多出.*passed"):
            worker.run_experiment(self.request(
                AblationStrategy.TESTER_FIXER,
                "diagnose",
                self.artifacts(include_plan=True, include_feedback=True),
            ))

    def test_source_snapshot_rejects_secret_and_hidden_paths(self):
        artifacts = self.artifacts()
        artifacts["source"] = Artifact.create(
            "source",
            "task-1",
            {".env": "API_KEY=secret"},
            kind="core:source_snapshot",
        )
        worker = ModelAblationWorker("implementer", QueueModelClient([]))

        with self.assertRaisesRegex(CodingModelWorkerError, "受保护路径"):
            worker.prepare(self.request(
                AblationStrategy.SINGLE_AGENT, "implement", artifacts
            ))

    def test_fake_model_full_strategy_repairs_then_runtime_passes(self):
        task = self.suite.task("python-tax-rounding")
        starter = (task.task_root / "starter" / "tax.py").read_text(
            encoding="utf-8"
        )
        solution = (task.task_root / "solution" / "tax.py").read_text(
            encoding="utf-8"
        )
        plan = {
            "schema_version": "1.0",
            "summary": "replace float tie rounding",
            "steps": [{
                "step_id": "s1",
                "objective": "use Decimal ROUND_HALF_UP",
                "target_paths": ["tax.py"],
                "acceptance_notes": ["build and tests must pass"],
            }],
            "risks": ["float conversion"],
        }
        no_op_patch = {
            "schema_version": "1.0",
            "summary": "initial candidate",
            "changes": [{
                "path": "tax.py", "content": starter,
                "reason": "first candidate",
            }],
            "assumptions": [],
        }
        diagnosis = {
            "schema_version": "1.0",
            "summary": "hidden tie case exposes binary rounding",
            "root_causes": [{
                "evidence": "Runtime test failed after initial patch",
                "hypothesis": "built-in round differs from decimal half-up",
                "affected_paths": ["tax.py"],
            }],
            "recommended_changes": ["use Decimal and ROUND_HALF_UP"],
            "uncertainty": "Runtime must rerun validators",
        }
        fix = {
            "schema_version": "1.0",
            "summary": "decimal half-up repair",
            "changes": [{
                "path": "tax.py", "content": solution,
                "reason": "address Runtime failure evidence",
            }],
            "assumptions": ["amount and rate are numeric"],
        }
        clients = {
            "planner": QueueModelClient([plan], ALL_TEXT_CAPABILITIES),
            "implementer": QueueModelClient([no_op_patch]),
            "tester": QueueModelClient([diagnosis], ALL_TEXT_CAPABILITIES),
            "fixer": QueueModelClient([fix]),
        }
        registry, workers = build_model_ablation_registry(
            clients, usage_source=UsageSource.SCRIPTED
        )
        profiles = default_ablation_profiles(worker_policy_tag="model-eval")
        runner = CodingAblationRunner(self.suite, registry, profiles)

        with tempfile.TemporaryDirectory() as temp:
            result = runner.run_trial(task, profiles[2], Path(temp))

        self.assertEqual(result.outcome, VerificationOutcome.PASSED)
        self.assertFalse(result.first_passed)
        self.assertTrue(result.fix_succeeded)
        self.assertEqual(result.scripted_calls, 4)
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(
            {role: len(worker.prepared_invocations) for role, worker in workers.items()},
            {"planner": 1, "implementer": 1, "tester": 1, "fixer": 1},
        )

    def test_registry_requires_exact_roles_and_distinct_principals(self):
        with self.assertRaisesRegex(ValueError, "恰好"):
            build_model_ablation_registry({})

        clients = {
            "planner": QueueModelClient([], ALL_TEXT_CAPABILITIES),
            "implementer": QueueModelClient([]),
            "tester": QueueModelClient([], ALL_TEXT_CAPABILITIES),
            "fixer": QueueModelClient([]),
        }
        registry, _ = build_model_ablation_registry(clients)
        snapshot = registry.snapshot()

        self.assertEqual(len(snapshot.descriptors), 4)
        self.assertEqual(
            len({item.principal_id for item in snapshot.descriptors}), 4
        )
        self.assertTrue(all(
            "model-eval" in item.policy_tags for item in snapshot.descriptors
        ))


if __name__ == "__main__":
    unittest.main()
