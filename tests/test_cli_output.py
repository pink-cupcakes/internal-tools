import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_tools.model_router.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/model_router"
REQUEST = "Fix parse_numbers('') raising ValueError; empty input should return []."


class CliOutputTests(unittest.TestCase):
    def simulated_options(self):
        return [
            "--request",
            REQUEST,
            "--simulate",
            str(EXAMPLES / "parser_bug.response.simulated.json"),
        ]

    def test_inline_request_prints_only_recommendation(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = main(self.simulated_options())
        self.assertEqual(result, 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["task_type"], "fix")
        self.assertEqual(plan["model"], {"tier": "small", "model_id": "gpt-6-luna"})
        self.assertIn("budgets", plan)
        self.assertIn("uncertainty", plan)
        self.assertNotIn("steps", plan)
        self.assertNotIn("response", plan)
        self.assertNotIn("plan", plan)

    def test_trace_flag_and_json_alias_print_full_trace(self):
        for flag in ("--trace", "--json"):
            with self.subTest(flag=flag):
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    result = main(self.simulated_options() + [flag])
                self.assertEqual(result, 0)
                trace = json.loads(stdout.getvalue())
                self.assertEqual(trace["source"], "simulated")
                task = trace["request"]["input"][1]["content"][0]["data"]
                self.assertEqual(task["request"], REQUEST)
                self.assertEqual(trace["recommendation"]["task_type"], "fix")

    def test_trace_file_stays_complete_with_recommendation_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            trace_path = Path(folder) / "run.json"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = main(
                    self.simulated_options() + ["--trace-out", str(trace_path)]
                )
            self.assertEqual(result, 0)
            plan = json.loads(stdout.getvalue())
            trace = json.loads(trace_path.read_text())
        self.assertEqual(trace["recommendation"], plan)
        self.assertIn("response", trace)

    def test_inline_request_preview(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = main(["--request", REQUEST, "--request-only"])
        self.assertEqual(result, 0)
        request = json.loads(stdout.getvalue())
        self.assertEqual(request["input"][1]["content"][0]["data"]["request"], REQUEST)

    def test_task_file_and_inline_request_are_mutually_exclusive(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                main(
                    [
                        "--request",
                        REQUEST,
                        "--task",
                        str(EXAMPLES / "parser_bug.task.json"),
                        "--request-only",
                    ]
                )
        self.assertEqual(error.exception.code, 2)

    def test_executor_flags_are_not_available(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                main(["--live", "--request", REQUEST, "--execute"])
        self.assertEqual(error.exception.code, 2)
