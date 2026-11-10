import ast
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from internal_tools import model_router as router

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/model_router"


class RoutingContractTests(unittest.TestCase):
    def test_debug_fields_use_native_langfuse_generation_fields(self):
        path = EXAMPLES.parents[1] / "src/internal_tools/model_router/telemetry.py"
        calls = [
            n
            for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        updates = [
            set(k.arg for k in n.keywords) for n in calls if n.func.attr == "update"
        ]
        self.assertTrue(
            any(
                {"input", "output", "model", "usage_details"} <= fields
                for fields in updates
            )
        )
        self.assertTrue(
            any(
                n.func.attr == "start_observation"
                and any(
                    k.arg == "as_type"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value == "generation"
                    for k in n.keywords
                )
                for n in calls
            )
        )

    def test_entrypoints_share_the_traced_route_without_monitor_code(self):
        source = EXAMPLES.parents[1] / "src/internal_tools"
        core = ast.parse((source / "model_router/core.py").read_text())
        route = next(
            n for n in core.body if isinstance(n, ast.FunctionDef) and n.name == "route"
        )
        self.assertIn(
            "trace_live_route", [ast.unparse(d) for d in route.decorator_list]
        )
        for filename, module, name in (
            ("model_router/cli.py", "core", "route"),
            ("model_router/__init__.py", "core", "route"),
            ("cli.py", "model_router.cli", "run_cli"),
            ("model_router/__main__.py", "cli", "main"),
        ):
            tree = ast.parse((source / filename).read_text())
            self.assertTrue(
                any(
                    isinstance(n, ast.ImportFrom)
                    and n.module == module
                    and any(a.name == name and a.asname is None for a in n.names)
                    for n in tree.body
                )
            )
        cli = ast.parse((source / "cli.py").read_text())
        commands = [
            n.args[0].value
            for n in ast.walk(cli)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_parser"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        ]
        self.assertNotIn("model-router-monitor", commands)
        self.assertEqual(list((source / "router_observability").rglob("*.py")), [])

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
