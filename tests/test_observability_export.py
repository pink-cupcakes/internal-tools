"""Offline durability, deterministic OTLP, and defensive HTTP delivery."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from internal_tools.router_observability.cli import configure_parser, run_cli
from internal_tools.router_observability.exporter import (
    ExportError,
    endpoint_url,
    export_report,
)
from internal_tools.router_observability.ledger import pending_snapshots
from internal_tools.router_observability.otlp import build_payload

ENV = {"LANGFUSE_PUBLIC_KEY": "public", "LANGFUSE_SECRET_KEY": "secret"}
REPORT = {
    "schema_version": 1,
    "tasks": [
        {
            "task_id": "abc",
            "costs": {"status": "unknown", "full_cost_per_accepted_task": None},
        }
    ],
    "sessions": [{"session_id": "one", "role": "worker"}],
    "summary": {},
    "alerts": [],
}


class ExportTests(unittest.TestCase):
    def test_stability_privacy_and_separate_sessions(self):
        report = json.loads(json.dumps(REPORT))
        report["tasks"][0].update(
            {
                "request": "secret body",
                "response": "secret",
                "metadata": {"path": "/Users/secret"},
            }
        )
        payload = build_payload(report, "snapshot")
        self.assertEqual(payload, build_payload(report, "snapshot"))
        encoded = json.dumps(payload)
        self.assertNotIn("secret", encoded)
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertNotEqual(spans[0]["traceId"], spans[1]["traceId"])
        self.assertEqual(len(spans[0]["traceId"]), 32)
        self.assertNotIn("gen_ai.request.model", encoded)
        self.assertIn("null", encoded)

    def test_unknown_differs_from_zero(self):
        known = json.loads(json.dumps(REPORT))
        known["tasks"][0]["costs"] = {
            "status": "known",
            "full_cost_per_accepted_task": 0,
        }
        self.assertNotEqual(build_payload(REPORT, "s"), build_payload(known, "s"))

    def test_url_and_auth(self):
        for url in (
            "http://example.com",
            "https://u:p@example.com",
            "https://a/?k=x",
            "https://a/path",
            "file:///tmp/x",
            "https://a/#x",
        ):
            with self.subTest(url=url), self.assertRaises(ExportError):
                endpoint_url(url)
        self.assertTrue(endpoint_url("http://127.0.0.1:3000").endswith("/v1/traces"))
        with self.assertRaises(ExportError):
            export_report(REPORT, "s", environment={})

    def test_ack_and_transport_failures(self):
        for body in (
            b'{"partialSuccess":{"rejectedSpans":1}}',
            b"[]",
            b"bad",
            b'{"partialSuccess":{"errorMessage":"secret"}}',
        ):
            response = MagicMock(status=200)
            response.read.return_value = body
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value = response
            with patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ExportError) as caught:
                    export_report(REPORT, "s", environment=ENV)
                self.assertNotIn("secret", str(caught.exception))
        with patch("urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = TimeoutError("secret")
            with self.assertRaisesRegex(ExportError, "transport_error"):
                export_report(REPORT, "s", environment=ENV)

    def test_offline_and_failed_delivery_preserve_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            parser = argparse.ArgumentParser()
            configure_parser(parser)
            options = parser.parse_args(
                ["--traces", directory, "--ledger", str(Path(directory) / "ledger.db")]
            )
            with (
                patch(
                    "internal_tools.router_observability.collect_report",
                    return_value=REPORT,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with patch("urllib.request.build_opener") as network:
                    self.assertEqual(run_cli(options), 0)
                    network.assert_not_called()
                self.assertEqual(len(pending_snapshots(options.ledger)), 1)
                options.send = True
                with patch(
                    "internal_tools.router_observability.cli.export_report",
                    side_effect=ExportError("transport_error"),
                ):
                    self.assertEqual(run_cli(options), 1)
                self.assertEqual(len(pending_snapshots(options.ledger)), 1)

    def test_success_headers_and_redirect_refusal(self):
        from internal_tools.router_observability.exporter import _NoRedirect

        response = MagicMock(status=200)
        response.read.return_value = b'{"partialSuccess":{}}'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch("urllib.request.build_opener", return_value=opener):
            export_report(REPORT, "s", environment=ENV)
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Basic cHVibGljOnNlY3JldA=="
        )
        self.assertEqual(request.get_header("X-langfuse-ingestion-version"), "4")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10.0)
        with self.assertRaisesRegex(ExportError, "redirect_refused"):
            _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil")

    def test_revisions_and_snapshot_summary(self):
        current = build_payload(REPORT, "revision-one")
        changed = build_payload(REPORT, "revision-two")
        spans = current["resourceSpans"][0]["scopeSpans"][0]["spans"]
        revised = changed["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertNotEqual(spans[0]["traceId"], revised[0]["traceId"])
        self.assertEqual(spans[-1]["name"], "router.snapshot")
        self.assertIn("summary", json.dumps(spans[-1]))
        self.assertIn("alerts", json.dumps(spans[-1]))

    def test_explicit_validation_and_unknown_duration(self):
        report = json.loads(json.dumps(REPORT))
        report["tasks"][0]["outcome"] = {
            "status": "accepted",
            "retry_of": "a" * 64,
            "validation": {
                "duration_ms": 0,
                "checks": [
                    {"name": "unit-tests", "passed": True, "output": "secret body"},
                    {"name": "/Users/private", "passed": True},
                    {"name": "invalid", "passed": "true"},
                ],
            },
        }
        span = build_payload(report, "s")["resourceSpans"][0]["scopeSpans"][0]["spans"][
            0
        ]
        metadata = {
            item["key"].rsplit(".", 1)[-1]: item["value"] for item in span["attributes"]
        }
        self.assertEqual(metadata["validation_duration_ms"], {"intValue": "0"})
        self.assertEqual(metadata["duration_unavailable"], {"boolValue": False})
        self.assertEqual(metadata["retry_of"], {"stringValue": "a" * 64})
        self.assertEqual(
            json.loads(metadata["validation_checks"]["stringValue"]),
            [{"name": "unit-tests", "passed": True}],
        )
        self.assertNotIn("secret", json.dumps(span))
        self.assertEqual(
            int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]), 1
        )
        report["tasks"][0]["outcome"]["validation"]["duration_ms"] = float("nan")
        unknown = build_payload(report, "s")["resourceSpans"][0]["scopeSpans"][0][
            "spans"
        ][0]
        self.assertNotIn("validation_duration_ms", json.dumps(unknown))
        self.assertIn('"boolValue": true', json.dumps(unknown))
