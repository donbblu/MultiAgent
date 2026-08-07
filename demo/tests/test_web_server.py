import unittest

from web_server import finalize_node_states, initial_nodes, public_event


class WebVisualizationTests(unittest.TestCase):
    def test_initial_nodes_expose_safe_visualization_metadata(self) -> None:
        nodes = initial_nodes()
        self.assertEqual(
            set(nodes), {"planner", "implementer", "tester", "reviewer", "fixer"}
        )
        self.assertTrue(all(node["status"] == "pending" for node in nodes.values()))
        self.assertTrue(all("permissions" in node for node in nodes.values()))

    def test_public_message_is_linked_to_agent_node(self) -> None:
        event = public_event(
            {
                "event": "agent_message",
                "timestamp": "2026-08-07T00:00:00+00:00",
                "payload": {
                    "sender": "tester",
                    "recipient": "coordinator",
                    "message_type": "result",
                    "summary": "测试通过",
                    "payload": {"passed": True},
                },
            },
            3,
        )
        self.assertEqual(event["id"], "event-3")
        self.assertEqual(event["node_id"], "tester")
        self.assertEqual(event["title"], "tester → coordinator · result")

    def test_unused_fixer_is_marked_as_not_triggered(self) -> None:
        nodes = initial_nodes()
        finalize_node_states(nodes, "completed")
        self.assertEqual(nodes["fixer"]["status"], "skipped")
        self.assertIn("无需返工", nodes["fixer"]["last_summary"])

    def test_fixer_explains_exhausted_attempt_budget(self) -> None:
        nodes = initial_nodes()
        finalize_node_states(nodes, "failed")
        self.assertEqual(nodes["fixer"]["status"], "skipped")
        self.assertIn("尝试上限", nodes["fixer"]["last_summary"])


if __name__ == "__main__":
    unittest.main()
