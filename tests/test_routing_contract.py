import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from internal_tools import model_router as router

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/model_router"


class RoutingContractTests(unittest.TestCase):
    def setUp(self):
        self.task = router.Task(request="Fix empty input raising ValueError.")
        self.response = json.loads(
            (EXAMPLES / "parser_bug.response.simulated.json").read_text()
        )

    def test_custom_models_and_budgets_are_advisory(self):
        budgets = router.Budgets(max_calls=1)
        policy = router.RoutingPolicy(small_model="caller-small", small_budgets=budgets)
        result = router.recommend(self.task, self.response, policy)
        self.assertEqual(result.model.model_id, "caller-small")
        self.assertEqual(result.budgets, budgets)
        self.assertFalse(hasattr(router, "execute"))
        self.assertFalse(hasattr(router, "Plan"))
        self.assertFalse(hasattr(router, "assemble_plan"))

    def test_uncertain_capability_is_visible_when_strong_is_recommended(self):
        self.response["answers"][1]["probability"] = 0.5
        result = router.recommend(self.task, self.response)
        self.assertEqual(result.model.tier, "strong")
        self.assertEqual(result.model.model_id, "gpt-6.1-sol")
        self.assertTrue(result.uncertainty.capability_uncertain)
        self.assertEqual(result.uncertainty.needs_strong_model_probability, 0.5)

    def test_unknown_signals_are_null_not_fabricated_zeroes(self):
        result = router.recommend(self.task, {"answers": []})
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.model)
        self.assertIsNone(result.budgets)
        self.assertIsNone(result.uncertainty.task_type_probability)
        self.assertIsNone(result.uncertainty.needs_strong_model_probability)

    def test_old_trace_replays_without_old_plan_metadata(self):
        saved = {
            "request": json.loads(json.dumps(router.build_request(self.task))),
            "response": self.response,
            "plan": {"steps": ["patch"], "execution_enabled": True},
        }
        trace = router.replay(saved)
        self.assertEqual(trace.recommendation.model.tier, "small")
        self.assertNotIn("plan", trace.model_dump())
        self.assertNotIn("steps", trace.recommendation.model_dump())

    def test_output_schema_rejects_execution_fields_and_invalid_states(self):
        result = router.recommend(self.task, self.response).model_dump(mode="json")
        for update in (
            {"steps": ["patch"]},
            {"execution_enabled": True},
            {"status": "needs_review"},
            {"model": None},
            {"budgets": None},
            {"status": "executed"},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                router.Recommendation.model_validate_json(json.dumps(result | update))
