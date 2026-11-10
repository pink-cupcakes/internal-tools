"""Metadata privacy, attribution safeguards and immutable snapshot tests."""

from contextlib import closing
import gc
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
import warnings

from internal_tools.router_observability import collect_report, write_snapshot
from internal_tools.router_observability.accounting import task_cost, validate_prices
from internal_tools.router_observability.ledger import mark_exported, pending_snapshots
from internal_tools.router_observability.outcomes import validate_outcomes


TOKENS = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
PRICES = {
    "version": "v1",
    "currency": "USD",
    "models": {"model": {"input": 2, "cached_input": 1, "output": 3}},
}


def outcome(task_id="a", retry=None):
    return {
        "task_id": task_id,
        "status": "accepted",
        "retry_of": retry,
        "validation": {"duration_ms": 5, "checks": [{"name": "unit", "passed": True}]},
    }


def fixture(root):
    traces, receipts, sessions = (root / n for n in ("traces", "receipts", "sessions"))
    for directory in (traces, receipts, sessions):
        directory.mkdir()
    trace = {
        "source": "live",
        "request_sent": True,
        "request": {"secret": "sensitive body /private/local"},
        "response": {"usage": dict(TOKENS, input_tokens_details={"cached_tokens": 4})},
        "policy": {"version": "routing-2"},
        "recommendation": {
            "model": {"model_id": "model", "tier": "strong"},
            "budgets": {"max_total_tokens": 10},
            "reason_codes": ["uncertain"],
            "uncertainty": {"capability_uncertain": True},
        },
    }
    path = traces / "a.json"
    path.write_text(json.dumps(trace))
    (traces / "copy.json").write_text(json.dumps(trace))
    (receipts / "a.json").write_text(
        json.dumps(
            {
                "trace_file": str(path),
                "agent_id": "worker",
                "requested_model": "model",
                "parent_agent_id": "parent",
            }
        )
    )
    events = [
        {"type": "session_meta", "payload": {"id": "worker"}},
        {"type": "turn_context", "payload": {"model": "model"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": dict(TOKENS, cached_input_tokens=4)},
            },
        },
    ]
    text = "\n".join(json.dumps(e) for e in events)
    (sessions / "a.jsonl").write_text(text)
    (sessions / "copy.jsonl").write_text(text)
    return traces, receipts, sessions


class CoreTests(unittest.TestCase):
    def test_dedup_privacy_cache_and_unknown_cost(self):
        with TemporaryDirectory() as directory:
            report = collect_report(*fixture(Path(directory)))
            self.assertEqual(len(report["tasks"]), 1)
            self.assertEqual(len(report["sessions"]), 2)
            self.assertEqual(
                report["sessions"][1]["cache_usage"]["cached_input_tokens"], 4
            )
            self.assertEqual(report["tasks"][0]["costs"]["status"], "unknown")
            rendered = json.dumps(report)
            self.assertNotIn(directory, rendered)
            self.assertNotIn("sensitive body", rendered)
            self.assertEqual(report["summary"]["worker_usage"]["known_tokens"], TOKENS)
            self.assertEqual(report["summary"]["baseline_comparison"]["savings"], None)

    def test_shared_session_does_not_duplicate_summary(self):
        with TemporaryDirectory() as directory:
            dirs = fixture(Path(directory))
            other = json.loads((dirs[0] / "a.json").read_text())
            other["policy"]["version"] = "other"
            path = dirs[0] / "b.json"
            path.write_text(json.dumps(other))
            receipt = json.loads((dirs[1] / "a.json").read_text())
            receipt["trace_file"] = str(path)
            (dirs[1] / "b.json").write_text(json.dumps(receipt))
            report = collect_report(*dirs)
            self.assertEqual(len(report["tasks"]), 2)
            self.assertTrue(
                all(t["metadata"]["reused_session"] for t in report["tasks"])
            )
            self.assertEqual(report["summary"]["worker_usage"]["known_tokens"], TOKENS)
            self.assertIn({"code": "repeated_strong_only"}, report["alerts"])

    def test_snapshot_idempotence_delivery_and_immutability(self):
        with TemporaryDirectory() as directory:
            report = collect_report(*fixture(Path(directory)))
            database = Path(directory) / "ledger.db"
            key = write_snapshot(database, report)
            self.assertEqual(key, write_snapshot(database, report))
            self.assertEqual(pending_snapshots(database), [(key, report)])
            mark_exported(database, key)
            self.assertEqual(pending_snapshots(database), [])
            with closing(sqlite3.connect(database)) as connection, connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM snapshots")

    def test_outcome_validation(self):
        bad = outcome()
        bad["validation"]["checks"][0]["passed"] = False
        with self.assertRaises(ValueError):
            validate_outcomes([bad])
        with self.assertRaises(ValueError):
            validate_outcomes([outcome(), outcome()])
        with self.assertRaises(ValueError):
            validate_prices(
                {
                    "version": "v1",
                    "currency": "USD",
                    "models": {"model": {"input": -1, "output": 2, "cached_input": 1}},
                }
            )

    def test_full_cost_requires_all_retry_roles_and_prices(self):
        first, second = outcome(), outcome("b", "a")
        first["status"] = "failed"
        records = [
            {
                "task_id": task,
                "role": role,
                "model": "model",
                "usage": TOKENS,
                "cached_input_tokens": 4,
            }
            for task in ("a", "b")
            for role in ("router", "worker", "coordinator")
        ]
        second["attribution"] = records
        outcomes = validate_outcomes([first, second])
        rates = validate_prices(PRICES)
        cost = task_cost("b", outcomes, rates, {"a", "b"})
        self.assertEqual(cost["attempt_count"], 2)
        self.assertAlmostEqual(cost["full_cost_per_accepted_task"], 0.000132)
        outcomes["b"]["attribution"].pop()
        self.assertEqual(
            task_cost("b", outcomes, rates, {"a", "b"})["status"], "unknown"
        )
        self.assertEqual(
            task_cost("b", outcomes, None, {"a", "b"})["status"], "unknown"
        )

    def test_retry_lineage_rejects_missing_cycles_and_accepted_ancestor(self):
        for records in (
            [outcome("b", "missing")],
            [outcome("a", "b"), outcome("b", "a")],
            [outcome(), outcome("b", "a")],
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                validate_outcomes(records)

    def test_explicit_baseline_requires_both_full_costs(self):
        from internal_tools.router_observability.core import _comparisons

        first, second = outcome(), outcome("b")
        second["baseline"] = {
            "cohort_id": "matched",
            "task_id": "a",
            "equivalence_asserted": True,
        }
        evidence = validate_outcomes([first, second])
        tasks = [
            {"task_id": key, "outcome": value, "costs": {"status": "unknown"}}
            for key, value in evidence.items()
        ]
        self.assertEqual(_comparisons(tasks), [])
        for task in tasks:
            task["costs"] = {
                "status": "known",
                "currency": "USD",
                "price_version": "v1",
                "full_cost_per_accepted_task": 1,
            }
        self.assertEqual(_comparisons(tasks)[0]["cost_difference"], 0)
        tasks[0]["costs"]["price_version"] = "different"
        self.assertEqual(_comparisons(tasks), [])
        second["baseline"]["equivalence_asserted"] = False
        with self.assertRaises(ValueError):
            validate_outcomes([first, second])

    def test_duplicate_receipted_trace_same_session_is_one_task(self):
        with TemporaryDirectory() as directory:
            dirs = fixture(Path(directory))
            receipt = json.loads((dirs[1] / "a.json").read_text())
            receipt["trace_file"] = str(dirs[0] / "copy.json")
            (dirs[1] / "copy.json").write_text(json.dumps(receipt))
            report = collect_report(*dirs)
            self.assertEqual(len(report["tasks"]), 1)
            self.assertEqual(report["summary"]["routing_usage"]["known_tokens"], TOKENS)
            task_id = report["tasks"][0]["task_id"]
            accepted = collect_report(*dirs, outcomes=[outcome(task_id)])
            self.assertEqual(accepted["tasks"][0]["outcome"]["status"], "accepted")
            self.assertEqual(report, collect_report(*dirs))

    def test_ledger_operations_release_database_connections(self):
        gc.collect()
        with TemporaryDirectory() as directory:
            database = Path(directory) / "ledger.db"
            with warnings.catch_warnings(record=True) as observed:
                warnings.simplefilter("always", ResourceWarning)
                key = write_snapshot(database, {"schema_version": 1})
                self.assertEqual(len(pending_snapshots(database)), 1)
                mark_exported(database, key)
                self.assertEqual(pending_snapshots(database), [])
                with self.assertRaises(sqlite3.IntegrityError):
                    mark_exported(database, "missing")
                gc.collect()
            leaked = [w for w in observed if issubclass(w.category, ResourceWarning)]
            self.assertEqual(leaked, [])
