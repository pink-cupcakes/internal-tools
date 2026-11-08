from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_tools import model_router as router
from internal_tools.model_router.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/model_router"


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.task = router.Task.model_validate_json(
            (EXAMPLES / "parser_bug.task.json").read_text()
        )
        self.saved = {
            "request": json.loads(json.dumps(router.build_request(self.task))),
            "response": json.loads(
                (EXAMPLES / "parser_bug.response.simulated.json").read_text()
            ),
        }

    def test_replay_preserves_original_request_when_prompts_change(self):
        self.saved["request"]["input"][0]["content"] = "Earlier routing prompt"
        with patch(
            "internal_tools.model_router.provider.send_decision",
            side_effect=AssertionError("replay must not call the API"),
        ):
            trace = router.replay(self.saved)
        self.assertEqual(trace.request, self.saved["request"])
        self.assertEqual(trace.response, self.saved["response"])
        self.assertEqual(trace.plan.workflow, "fix")
        self.assertFalse(trace.request_sent)

    def test_supplied_task_mismatch_has_actionable_error(self):
        other_task = router.Task(request="Explain different code")
        with self.assertRaisesRegex(ValueError, "Omit --task"):
            router.replay(self.saved, task=other_task)

    def test_optional_matching_task_accepts_json_lists(self):
        trace = router.replay(self.saved, task=self.task)
        self.assertEqual(trace.plan.workflow, "fix")
        self.assertEqual(trace.plan.acceptance_criteria, self.task.acceptance_criteria)

    def test_cli_replays_saved_evidence_without_current_task_file(self):
        saved_data = self.saved["request"]["input"][1]["content"][0]["data"]
        saved_data["evidence"][0]["content"] = "original snapshot of the code"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "trace.json"
            path.write_text(json.dumps(self.saved))
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = main(["--replay", str(path), "--json"])
        self.assertEqual(result, 0)
        trace = json.loads(stdout.getvalue())
        self.assertEqual(trace["request"], self.saved["request"])
        self.assertEqual(trace["source"], "replay")
        self.assertFalse(trace["request_sent"])

    def test_invalid_trace_has_clear_errors(self):
        for value in (None, {}, {"request": {}, "response": None}):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "request and a response"),
            ):
                router.replay(value)
        malformed = deepcopy(self.saved)
        malformed["request"]["input"] = []
        with self.assertRaisesRegex(ValueError, "exactly one task input_json"):
            router.replay(malformed)

    def test_missing_task_for_live_never_calls_api(self):
        with (
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            patch(
                "internal_tools.model_router.provider.send_decision",
                side_effect=AssertionError("task is required"),
            ),
        ):
            result = main(["--live"])
        self.assertEqual(result, 1)
        self.assertIn("--task is required", stderr.getvalue())
