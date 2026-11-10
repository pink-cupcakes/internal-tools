"""Offline checks for native experiment routing-label evaluators."""

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "router_evals", ROOT / "examples/model_router/evals.py"
)
evals = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evals)


def output(tier=None, status="recommended", error=None):
    return {"status": status, "model": {"tier": tier} if tier else None, "error": error}


class RouterEvalTests(unittest.TestCase):
    def test_confusion_scores(self):
        for expected, actual, match, over, missed in [
            ("small", "small", 1, 0, 0),
            ("small", "strong", 0, 1, 0),
            ("strong", "small", 0, 0, 1),
            ("strong", "strong", 1, 0, 0),
        ]:
            with self.subTest(expected=expected, actual=actual):
                self.assertEqual(
                    evals.scores(
                        output(actual),
                        {
                            "status": "recommended",
                            "tier": expected,
                        },
                    ),
                    {
                        "routing_label_match": match,
                        "over_escalation": over,
                        "missed_escalation": missed,
                    },
                )

    def test_stops_and_errors_never_match_small(self):
        for stopped in [
            output(status="needs_review"),
            output(status="needs_clarification"),
            output("strong", error="transport"),
        ]:
            self.assertEqual(
                evals.scores(
                    stopped,
                    {
                        "status": "recommended",
                        "tier": "small",
                    },
                )["routing_label_match"],
                0,
            )
            self.assertEqual(
                evals.scores(
                    stopped,
                    {
                        "status": "recommended",
                        "tier": "strong",
                    },
                )["missed_escalation"],
                1,
            )

    def test_clarification_is_explicit(self):
        expected = {"status": "needs_clarification", "tier": None}
        self.assertEqual(
            evals.scores(output(status="needs_clarification"), expected)[
                "routing_label_match"
            ],
            1,
        )
        self.assertEqual(
            evals.scores(output(status="needs_review"), expected)[
                "routing_label_match"
            ],
            0,
        )
        self.assertEqual(
            len(
                evals.evaluate(
                    output=output("small"),
                    expected_output={"status": "recommended", "tier": "small"},
                )
            ),
            3,
        )

    def test_baseline_requires_identical_cases(self):
        cases = json.loads(evals.CASES.read_text())
        baseline = {
            "labels_version": cases["version"],
            "cases": cases["data"],
            "case_ids": [i["metadata"]["id"] for i in cases["data"]],
        }
        evals.check_baseline(baseline, cases)
        for key, value in [
            ("labels_version", "other"),
            ("case_ids", []),
            ("cases", []),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                evals.check_baseline(baseline | {key: value}, cases)


if __name__ == "__main__":
    unittest.main()
