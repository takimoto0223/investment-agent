import unittest

from agents.discussion import DiscussionOrchestratorAgent


class OrganizationReorgTests(unittest.TestCase):
    def setUp(self):
        self.agent = DiscussionOrchestratorAgent()

    def test_reorganization_recommendation_is_generated_for_low_scoring_team(self):
        evaluations = [
            {
                "agent": "EquityAgent",
                "cooperation": 0.35,
                "communication": 0.40,
                "quality": 0.55,
                "issues": ["feedback loop slow", "overlap with CriticEquityAgent"],
            },
            {
                "agent": "CriticEquityAgent",
                "cooperation": 0.72,
                "communication": 0.78,
                "quality": 0.82,
                "issues": [],
            },
        ]

        result = self.agent.suggest_reorganization(evaluations)

        self.assertIn("proposals", result)
        self.assertGreaterEqual(len(result["proposals"]), 1)
        self.assertTrue(any(p.get("action") in {"merge", "reassign", "split"} for p in result["proposals"]))
        self.assertIn("EquityAgent", result["summary"])

    def test_reorganization_returns_stable_plan_for_healthy_team(self):
        evaluations = [
            {
                "agent": "EquityAgent",
                "cooperation": 0.86,
                "communication": 0.88,
                "quality": 0.90,
                "issues": [],
            },
            {
                "agent": "DaytradeAgent",
                "cooperation": 0.82,
                "communication": 0.85,
                "quality": 0.88,
                "issues": [],
            },
        ]

        result = self.agent.suggest_reorganization(evaluations)

        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["recommendation"], "keep_current_structure")


if __name__ == "__main__":
    unittest.main()
