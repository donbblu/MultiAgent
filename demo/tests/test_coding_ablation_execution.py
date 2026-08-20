from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import core_coding_model_ablation_run as cli
from coding_workflow import (
    CoreAblationExperimentConfig,
    FixedCodingSuite,
    build_core_ablation_preflight,
    estimate_core_ablation_calls,
    real_model_ablation_profiles,
)
from coding_workflow.model import (
    BudgetedModelClient,
    ModelBudgetExceeded,
    ModelCallBudget,
    ModelClientFactory,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.capabilities = ModelClientFactory.config_for_provider(
            "dashscope"
        ).capabilities

    def generate_structured(self, request):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def generate_json(self, messages):
        raise AssertionError("必须使用 structured 调用")


class CodingAblationExecutionTests(unittest.TestCase):
    @property
    def suite(self):
        root = Path(__file__).resolve().parents[1] / "coding_eval" / "v1"
        return FixedCodingSuite.load(root)

    def model_config(self):
        return ModelClientFactory.config_for_provider(
            "dashscope",
            model="qwen3.7-plus",
            max_tokens=4_000,
            max_retries=0,
            temperature=0.0,
            enforce_max_tokens=True,
        )

    def experiment(self):
        return CoreAblationExperimentConfig.from_model_config(
            self.model_config(),
            task_count=3,
            max_tokens_per_call=30_000,
            max_total_tokens=300_000,
        )

    def test_preflight_freezes_21_requests_without_source_body(self):
        preflight = build_core_ablation_preflight(
            self.suite, self.experiment()
        )
        raw = json.dumps(dict(preflight.to_dict()), ensure_ascii=False)

        self.assertEqual(preflight.call_estimate.minimum_logical_calls, 15)
        self.assertEqual(preflight.call_estimate.maximum_logical_calls, 21)
        self.assertEqual(preflight.call_estimate.maximum_external_requests, 21)
        self.assertEqual(len(preflight.source_disclosures), 3)
        self.assertNotIn("return round(amount * rate, 2)", raw)
        self.assertNotIn("ROUND_HALF_UP", raw)
        self.assertNotIn("API_KEY=", raw)
        self.assertIn("tests/test_tax_public.py", raw)
        self.assertEqual(len(preflight.digest), 64)

    def test_experiment_rejects_http_retries_and_unbounded_output(self):
        retrying = ModelClientFactory.config_for_provider(
            "dashscope", max_retries=1
        )
        with self.assertRaisesRegex(ValueError, "关闭.*重试"):
            CoreAblationExperimentConfig.from_model_config(
                retrying,
                task_count=3,
                max_tokens_per_call=30_000,
                max_total_tokens=300_000,
            )

        unbounded = ModelClientFactory.config_for_provider(
            "dashscope", enforce_max_tokens=False
        )
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            CoreAblationExperimentConfig.from_model_config(
                unbounded,
                task_count=3,
                max_tokens_per_call=30_000,
                max_total_tokens=300_000,
            )

    def test_global_budget_stops_before_call_and_counts_missing_usage(self):
        request = ModelRequest.from_text_messages([
            {"role": "user", "content": "return a small JSON object"}
        ])
        response = ModelResponse(
            {"ok": True}, "fake", "fake", ModelUsage(), 1
        )
        inner = FakeClient([response, response])
        budget = ModelCallBudget(
            max_model_calls=2,
            max_total_tokens=20_000,
            max_tokens_per_call=15_000,
        )
        client = BudgetedModelClient(
            inner, budget, max_output_tokens=100
        )

        client.generate_structured(request)
        with self.assertRaisesRegex(ModelBudgetExceeded, "预留"):
            client.generate_structured(request)

        snapshot = budget.snapshot()
        self.assertEqual(inner.calls, 1)
        self.assertEqual(snapshot.attempted_calls, 1)
        self.assertEqual(snapshot.completed_calls, 1)
        self.assertEqual(snapshot.observed_tokens, 0)
        self.assertEqual(snapshot.accounted_tokens, 15_000)

    def test_global_call_limit_counts_failed_external_attempt(self):
        request = ModelRequest.from_text_messages([
            {"role": "user", "content": "return JSON"}
        ])
        inner = FakeClient([RuntimeError("network failed")])
        budget = ModelCallBudget(
            max_model_calls=1,
            max_total_tokens=30_000,
            max_tokens_per_call=15_000,
        )
        client = BudgetedModelClient(
            inner, budget, max_output_tokens=100
        )

        with self.assertRaisesRegex(RuntimeError, "network failed"):
            client.generate_structured(request)
        with self.assertRaisesRegex(ModelBudgetExceeded, "调用次数"):
            client.generate_structured(request)

        snapshot = budget.snapshot()
        self.assertEqual(inner.calls, 1)
        self.assertEqual(snapshot.attempted_calls, 1)
        self.assertEqual(snapshot.failed_calls, 1)
        self.assertEqual(snapshot.accounted_tokens, 15_000)

    def test_real_profiles_use_model_policy_and_declared_call_ceiling(self):
        profiles = real_model_ablation_profiles(self.experiment())

        self.assertEqual(len(profiles), 3)
        self.assertTrue(all(
            item.worker_policy_tag == "model-eval" for item in profiles
        ))
        self.assertTrue(all(
            stage.token_limit == 30_000
            for profile in profiles for stage in profile.stages
        ))
        self.assertEqual(
            estimate_core_ablation_calls(3).maximum_external_requests, 21
        )

    def test_cli_preflight_and_bad_authorization_never_load_env(self):
        with patch.object(cli, "load_env_file") as loader:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main([]), 0)
            loader.assert_not_called()
        data = json.loads(output.getvalue())
        self.assertFalse(data["will_call_external_models"])
        self.assertEqual(data["call_estimate"]["maximum_external_requests"], 21)

        with patch.object(cli, "load_env_file") as loader:
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    cli.main([
                        "--confirm-real-calls",
                        "--confirm-preflight-sha256", "wrong",
                    ])
            loader.assert_not_called()

    def test_temperature_is_part_of_frozen_provider_payload(self):
        model = self.model_config()
        self.assertEqual(model.temperature, 0.0)
        self.assertEqual(self.experiment().temperature, 0.0)
        self.assertTrue(model.include_max_tokens)
        self.assertEqual(model.max_retries, 0)


if __name__ == "__main__":
    unittest.main()
