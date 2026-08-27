from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_workflow import FixedCodingSuite, build_scripted_ablation_registry
from coding_workflow.portfolio_agent_runtime import PortfolioAgentAblationRunner
from coding_workflow.runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
)


DEMO_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = DEMO_ROOT / "coding_eval" / "v1"


class PortfolioAgentRuntimeTests(unittest.TestCase):
    def test_real_agents_consume_stage_work_and_close_with_handoff_evidence(self):
        full_suite = FixedCodingSuite.load(SUITE_PATH)
        suite = FixedCodingSuite(
            full_suite.suite_id,
            full_suite.schema_version,
            full_suite.root,
            (full_suite.task("python-inventory-cli"),),
            full_suite.manifest_sha256,
        )
        registry, _ = build_scripted_ablation_registry(suite)

        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "portfolio-runtime.sqlite3"
            result = PortfolioAgentAblationRunner(
                suite,
                registry,
                database_path=database_path,
                trusted_local_execution=True,
                max_parallel_trials=3,
            ).run()

            reopened = SQLiteRuntimeDatabase(
                RuntimeSQLiteConfig(database_path),
                outbox_policy=OutboxPolicy(
                    policy_version="outbox-policy/portfolio-agent-runtime-v1",
                    destination="core:runtime_events",
                    expected_sink_id="core:portfolio-agent-runtime-sink",
                    claim_ttl_ms=60_000,
                    batch_limit=10,
                    retry_delays_ms=(1_000, 5_000),
                ),
            )
            reopened.initialize()
            reopened.verify_integrity()

        self.assertEqual(len(result.report.trials), 3)
        self.assertEqual(
            sum(trial.scripted_calls for trial in result.report.trials),
            7,
        )
        runtime = result.runtime
        self.assertEqual(runtime["thread_count"], 3)
        self.assertEqual(runtime["agent_count"], 7)
        self.assertEqual(runtime["stage_message_count"], 7)
        self.assertEqual(runtime["handoff_count"], 4)
        self.assertEqual(runtime["mailbox"]["enqueued"], 14)
        self.assertEqual(runtime["mailbox"]["consumed"], 14)
        self.assertTrue(runtime["mailbox"]["all_consumed"])
        self.assertTrue(runtime["sessions"]["all_closed"])
        self.assertEqual(runtime["sessions"]["states"], {"closed": 7})
        self.assertTrue(runtime["lane_evidence"]["fifo_observed"])
        self.assertGreaterEqual(
            runtime["lane_evidence"]["max_parallel_agents"],
            2,
        )
        self.assertEqual(
            {agent["role"] for agent in runtime["agents"]},
            {"Planner", "Developer", "Tester", "Fixer"},
        )
        self.assertTrue(all(
            agent["lifecycle"] == [
                "created", "paused", "resumed", "closed",
            ]
            for agent in runtime["agents"]
        ))
        self.assertNotIn("Validator", {
            agent["role"] for agent in runtime["agents"]
        })
        self.assertEqual(runtime["validator"]["owner"], "runtime")
        self.assertFalse(runtime["validator"]["is_agent"])
        self.assertTrue(all(
            handoff["consumed"]
            and handoff["message_id"]
            and handoff["recipient_agent_id"]
            and handoff["output_artifact_ref"]
            for handoff in runtime["stage_messages"]
        ))
        self.assertTrue(all(
            handoff["lane_thread"].startswith("agent-lane")
            for handoff in runtime["stage_messages"]
        ))
        chained = [
            item for item in runtime["stage_messages"]
            if item["parent_message_id"] is not None
        ]
        self.assertTrue(chained)
        self.assertTrue(all(
            item["causation_message_id"] == item["parent_message_id"]
            for item in chained
        ))


if __name__ == "__main__":
    unittest.main()
