"""Regression coverage for autonomous conservative routing."""

import unittest
from internal_tools import model_router as router


def response(choice, distribution, capability=0.08):
    return {
        "answers": [
            {
                "type": "choice",
                "name": "workflow",
                "choice": choice,
                "confidence": 0.51,
                "probabilities": [
                    {"value": key, "probability": value}
                    for key, value in distribution.items()
                ],
            },
            {
                "type": "predicate",
                "name": "needs_strong_model",
                "probability": capability,
            },
        ]
    }


class UncertainRoutingTests(unittest.TestCase):
    def setUp(self):
        self.task = router.Task(request="Implement a physical stocking prototype")
        self.response = response(
            "clarify",
            {
                "explain": 0.17,
                "fix": 0.02,
                "implement": 0.18,
                "clarify": 0.63,
            },
        )

    def test_previous_planning_flag_escalates_to_worker(self):
        advice = router.recommend(self.task, self.response)
        self.assertEqual(advice.status, "recommended")
        self.assertEqual(advice.model.tier, "strong")
        self.assertEqual(advice.task_type, "clarify")
        self.assertEqual(advice.uncertainty.task_type_probability, 0.63)
        self.assertEqual(advice.budgets, router.RoutingPolicy().strong_budgets)
        self.assertEqual(advice.clarification_items, ())

    def test_strict_policy_can_restore_review(self):
        policy = router.RoutingPolicy(uncertain_task_type_action="review")
        advice = router.recommend(self.task, self.response, policy)
        self.assertEqual(advice.status, "needs_review")
        self.assertIsNone(advice.model)

    def test_uncertain_actionable_type_forces_strong_even_if_capability_low(self):
        decision = response(
            "implement",
            {
                "explain": 0.20,
                "fix": 0.10,
                "implement": 0.60,
                "clarify": 0.10,
            },
        )
        advice = router.recommend(self.task, decision)
        self.assertEqual(advice.model.tier, "strong")
        self.assertEqual(advice.task_type, "implement")

    def test_confident_clarification_and_declared_requirements_still_stop(self):
        decision = response(
            "clarify",
            {
                "explain": 0.01,
                "fix": 0.01,
                "implement": 0.01,
                "clarify": 0.97,
            },
        )
        self.assertEqual(
            router.recommend(self.task, decision).status, "needs_clarification"
        )
        task = router.Task(
            request="Change permissions",
            missing_requirements=("Which roles may edit?",),
        )
        self.assertEqual(
            router.recommend(task, self.response).status, "needs_clarification"
        )
