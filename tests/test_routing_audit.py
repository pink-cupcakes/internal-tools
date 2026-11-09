"""Accounting fixtures contain metadata only; no messages are required."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from internal_tools.model_router.audit import audit_routing


def usage(total):
    return {"input_tokens": total - 2, "output_tokens": 2, "total_tokens": total}


class RoutingAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.traces = self.root / "traces"
        self.receipts = self.root / "receipts"
        self.sessions = self.root / "sessions"
        for directory in (self.traces, self.receipts, self.sessions):
            directory.mkdir()

    def write(self, path, value):
        path.write_text(json.dumps(value))
        return path

    def trace(self, name="trace", tokens=None):
        return self.write(
            self.traces / f"{name}.json",
            {
                "source": "live",
                "request_sent": True,
                "request": {},
                "recommendation": {"model": {"model_id": "gpt-6-luna"}},
                "response": {"usage": tokens},
                "elapsed_ms": len(name),
            },
        )

    def receipt(self, trace, agent="worker", parent=None):
        value = {
            "trace_file": str(trace),
            "agent_id": agent,
            "requested_model": "gpt-6-luna",
        }
        if parent:
            value["parent_agent_id"] = parent
        self.write(self.receipts / trace.name, value)

    def session(self, agent="worker", models=("gpt-6.1-sol",), totals=(10, 20, 20)):
        events = [{"type": "session_meta", "payload": {"id": agent}}]
        events += [
            {"type": "turn_context", "payload": {"model": model}} for model in models
        ]
        events += [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": usage(total)},
                },
            }
            for total in totals
        ]
        path = self.sessions / f"{agent}.jsonl"
        path.write_text("\n".join(json.dumps(event) for event in events))

    def audit(self):
        return audit_routing(self.traces, self.receipts, self.sessions)

    def test_filters_copies_requests_tasks_and_replays(self):
        trace = self.trace(tokens=usage(5))
        original = json.loads(trace.read_text())
        self.write(self.traces / "copied.json", original)
        self.write(self.traces / "replay.json", original | {"source": "replay"})
        self.write(self.traces / "request.json", {"input": "private message"})
        self.write(self.traces / "task.json", {"request": "private task"})
        self.receipt(trace)
        report = self.audit()
        self.assertEqual(len(report["traces"]), 1)
        self.assertEqual(report["traces"][0]["trace_file"], str(trace))
        self.assertEqual(report["routing_usage"]["known_tokens"], usage(5))
        self.assertNotIn("private message", json.dumps(report))
        self.assertNotIn("private task", json.dumps(report))

    def test_unknown_usage_and_missing_agent_session(self):
        trace = self.trace()
        self.receipt(trace)
        row = self.audit()["traces"][0]
        self.assertIsNone(row["routing_usage"])
        self.assertIsNone(row["worker_usage"])
        self.assertEqual(row["model_match"], "unverified")
        self.assertEqual(row["success"], "unknown")
        self.assertEqual(self.audit()["worker_usage"]["unknown_count"], 1)

    def test_latest_cumulative_usage_and_model_mismatch(self):
        self.receipt(self.trace())
        self.session()
        report = self.audit()
        row = report["traces"][0]
        self.assertEqual(row["model_match"], "mismatch")
        self.assertEqual(row["observed_models"], ["gpt-6.1-sol"])
        self.assertEqual(row["worker_usage"], usage(20))
        self.assertEqual(report["worker_usage"]["known_tokens"], usage(20))

    def test_reused_agent_and_coordinator_are_deduplicated(self):
        for name in ("first", "second"):
            self.receipt(self.trace(name), parent="parent")
        self.session()
        self.session("parent", totals=(30, 40))
        report = self.audit()
        self.assertTrue(all(row["reused_agent"] for row in report["traces"]))
        self.assertEqual(report["worker_usage"]["known_tokens"], usage(20))
        self.assertEqual(report["coordinator_usage"]["known_tokens"], usage(40))
        self.assertEqual(report["worker_usage"]["attribution"], "session_cumulative")

    def test_multiple_models_are_not_claimed_as_exact_task_match(self):
        self.receipt(self.trace())
        self.session(models=("gpt-6-luna", "gpt-6.1-sol"))
        row = self.audit()["traces"][0]
        self.assertEqual(row["model_match"], "multiple_models")
        self.assertEqual(row["usage_attribution"], "session_cumulative")

    def test_malformed_receipt_is_reported_without_payload(self):
        self.trace()
        self.write(self.receipts / "bad.json", {"agent_id": "secret"})
        report = self.audit()
        self.assertEqual(report["issues"][0]["code"], "malformed_receipt")
        self.assertNotIn("secret", json.dumps(report))
        self.assertEqual(report["worker_usage"]["status"], "unknown")

    def test_conflicting_receipts_raise(self):
        trace = self.trace()
        self.receipt(trace)
        self.write(
            self.receipts / "conflict.json",
            {
                "trace_file": str(trace),
                "agent_id": "other",
                "requested_model": "gpt-6-luna",
            },
        )
        with self.assertRaises(ValueError):
            self.audit()

    def test_parent_that_is_worker_is_not_counted_twice(self):
        self.receipt(self.trace(), parent="worker")
        self.session()
        report = self.audit()
        self.assertEqual(report["coordinator_usage"]["agent_ids"], [])
        self.assertEqual(report["coordinator_worker_overlap"], ["worker"])

    def cache_session(self, cached):
        self.session(totals=(20,))
        path = self.sessions / "worker.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1]["payload"]["info"]["total_token_usage"]["cached_input_tokens"] = (
            cached
        )
        path.write_text("\n".join(json.dumps(event) for event in events))

    def test_cache_accounting_includes_cached_once(self):
        routing = usage(10) | {"input_tokens_details": {"cached_tokens": 6}}
        self.receipt(self.trace(tokens=routing))
        self.cache_session(16)
        report = self.audit()
        row = report["traces"][0]
        self.assertEqual(
            row["cache_usage"]["worker"],
            {"cached_input_tokens": 16, "uncached_input_tokens": 2},
        )
        self.assertEqual(report["worker_usage"]["known_tokens"], usage(20))
        self.assertEqual(
            report["routing_cache_usage"]["known_tokens"],
            {"cached_input_tokens": 6, "uncached_input_tokens": 2},
        )

    def test_missing_and_invalid_cache_usage_unknown(self):
        self.receipt(self.trace())
        self.session()
        report = self.audit()
        self.assertIsNone(report["traces"][0]["cache_usage"]["worker"])
        self.assertEqual(report["worker_usage"]["cache_usage"]["status"], "unknown")
        for invalid in (-1, 19, True, "16"):
            self.cache_session(invalid)
            self.assertIsNone(self.audit()["traces"][0]["cache_usage"]["worker"])

    def test_session_advisory_budget_and_reuse(self):
        trace = self.trace()
        value = json.loads(trace.read_text())
        value["recommendation"]["budgets"] = {"max_total_tokens": 10}
        self.write(trace, value)
        self.receipt(trace)
        self.session()
        row = self.audit()["traces"][0]
        self.assertEqual(row["advisory_budget_status"], "exceeded")
        value["recommendation"]["budgets"]["max_total_tokens"] = 20
        self.write(trace, value)
        self.assertEqual(self.audit()["traces"][0]["advisory_budget_status"], "within")
        self.receipt(self.trace("another"))
        rows = self.audit()["traces"]
        self.assertTrue(all(row["advisory_budget_status"] == "unknown" for row in rows))

    def test_receipted_replay_worker_counts_without_saved_routing_cost(self):
        trace = self.trace(tokens=usage(100))
        value = json.loads(trace.read_text())
        self.write(trace, value | {"source": "replay", "request_sent": False})
        self.receipt(trace)
        self.session()
        report = self.audit()
        self.assertEqual(report["traces"][0]["source"], "replay")
        self.assertEqual(report["routing_usage"]["known_tokens"]["total_tokens"], 0)
        self.assertEqual(report["worker_usage"]["known_tokens"]["total_tokens"], 20)

    def test_identical_receipted_copies_keep_workers_charge_routing_once(self):
        trace = self.trace(tokens=usage(10))
        copy = self.traces / "copy.json"
        copy.write_text(trace.read_text())
        self.receipt(trace, agent="first")
        self.receipt(copy, agent="second")
        self.session("first", totals=(20,))
        self.session("second", totals=(30,))
        report = self.audit()
        self.assertEqual(len(report["traces"]), 2)
        self.assertEqual(report["worker_usage"]["known_tokens"]["total_tokens"], 50)
        self.assertEqual(report["routing_usage"]["known_tokens"]["total_tokens"], 10)
        self.assertEqual(
            sum(row["routing_usage_deduplicated"] for row in report["traces"]), 1
        )
        duplicate = next(
            row for row in report["traces"] if row["routing_usage_deduplicated"]
        )
        self.assertEqual(
            duplicate["cache_usage"]["routing"],
            {"cached_input_tokens": 0, "uncached_input_tokens": 0},
        )

    def test_dangling_receipt_reported(self):
        missing = self.traces / "absent.json"
        self.receipt(missing)
        report = self.audit()
        self.assertEqual(report["traces"], [])
        self.assertEqual(
            report["issues"],
            [{"file": str(missing), "code": "receipt_trace_not_found"}],
        )

    def test_missing_directory_rejected(self):
        with self.assertRaises(ValueError):
            audit_routing(self.root / "missing")

    def test_usage_absent_in_real_session_stays_unknown(self):
        self.receipt(self.trace())
        self.session(totals=())
        report = self.audit()
        self.assertIsNone(report["traces"][0]["worker_usage"])
        self.assertEqual(report["worker_usage"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
