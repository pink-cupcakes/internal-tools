import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from internal_tools import model_router as router
from internal_tools.model_router.cli import main


class ModelTests(unittest.TestCase):
    def test_task_parses_nested_json_without_stripping_text(self):
        task_data = {
            "request": " Explain this code ",
            "evidence": [{"ref": "parser.py", "content": "  return 1\n"}],
            "acceptance_criteria": ["Describe the return value."],
            "missing_requirements": [],
        }
        coding_task = router.Task.model_validate_json(json.dumps(task_data))
        self.assertEqual(coding_task.model_dump(mode="json"), task_data)
        self.assertIsInstance(coding_task.evidence, tuple)
        self.assertIsInstance(coding_task.evidence[0], router.Evidence)
        self.assertEqual(router.Task.from_dict(task_data), coding_task)

    def test_task_rejects_blank_text_duplicate_refs_and_extra_fields(self):
        for fields in (
            {"request": " \n"},
            {"request": 123},
            {"request": "explain", "unexpected": True},
            {"request": "explain", "acceptance_criteria": [" "]},
            {"request": "explain", "missing_requirements": "Which file?"},
            {"request": "explain", "acceptance_criteria": {"one", "two"}},
            {
                "request": "explain",
                "evidence": [
                    {"ref": "same", "content": "one"},
                    {"ref": "same", "content": "two"},
                ],
            },
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                router.Task.model_validate(fields)

    def test_budgets_reject_coercion_and_nonpositive_values(self):
        for value in (True, "2", 2.0, 0, -1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                router.Budgets(max_calls=value)
        with self.assertRaisesRegex(ValidationError, "fit within the total"):
            router.Budgets(max_total_tokens=7999)

    def test_policy_rejects_invalid_probabilities_and_threshold_order(self):
        for value in (True, "0.75", -0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                router.RoutingPolicy(min_task_type_probability=value)
        with self.assertRaisesRegex(ValidationError, "thresholds must be ordered"):
            router.RoutingPolicy(max_small_probability=0.75)
        self.assertEqual(
            router.RoutingPolicy(min_task_type_probability=1).min_task_type_probability,
            1,
        )

    def test_models_remain_frozen(self):
        coding_task = router.Task(request="Explain this code")
        with self.assertRaises(ValidationError):
            coding_task.request = "Modify this code"

    def test_trace_roundtrips_through_json(self):
        coding_task = router.Task(request="Explain this code")
        trace = router.route(
            coding_task, send=lambda request: {"answers": []}, source="simulated"
        )
        restored = router.RoutingTrace.model_validate_json(trace.model_dump_json())
        self.assertEqual(
            restored.model_dump(mode="json"), trace.model_dump(mode="json")
        )
        self.assertEqual(trace.to_dict(), trace.model_dump())
        self.assertIsInstance(restored.policy.small_budgets, router.Budgets)

    def test_each_request_gets_independent_questions(self):
        coding_task = router.Task(request="Explain this code")
        request_body = router.build_request(coding_task)
        request_body["questions"][0]["choices"].clear()
        self.assertEqual(
            len(router.build_request(coding_task)["questions"][0]["choices"]), 4
        )

    def test_cli_reports_validation_errors_without_sending_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            task_path = Path(directory) / "task.json"
            task_path.write_text('{"request": 123}')
            with (
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
                patch(
                    "internal_tools.model_router.provider.send_decision",
                    new_callable=Mock,
                ) as send,
            ):
                exit_code = main(["--task", str(task_path), "--request-only"])
        self.assertEqual(exit_code, 1)
        self.assertIn("model-router:", stderr.getvalue())
        send.assert_not_called()
