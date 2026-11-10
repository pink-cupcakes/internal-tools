import json
import unittest
from unittest.mock import MagicMock, patch

from internal_tools.router_observability.exporter import (
    ExportError,
    _acknowledged,
    _NoRedirect,
    build_payload,
    endpoint_url,
    export_report,
)


class ExportTests(unittest.TestCase):
    report = {
        "snapshot_id": "a" * 64,
        "import_time_unix_nano": "123",
        "timestamp_source": "snapshot_import_time",
        "tasks": [],
    }
    environment = {"LANGFUSE_PUBLIC_KEY": "public", "LANGFUSE_SECRET_KEY": "secret"}

    def test_stable_identity_and_import_timestamp(self):
        self.assertEqual(build_payload(self.report), build_payload(dict(self.report)))
        span = build_payload(self.report)["resourceSpans"][0]["scopeSpans"][0]["spans"][
            0
        ]
        self.assertEqual(span["startTimeUnixNano"], span["endTimeUnixNano"])
        changed = dict(self.report, snapshot_id="b" * 64)
        self.assertNotEqual(
            span["traceId"],
            build_payload(changed)["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "traceId"
            ],
        )

    def test_endpoints_and_redirects(self):
        for base in (
            "http://localhost:3000",
            "http://127.0.0.1",
            "https://example.com",
        ):
            self.assertTrue(endpoint_url(base).endswith("/api/public/otel/v1/traces"))
        for base in (
            "http://example.com",
            "https://u:p@example.com",
            "https://example.com/path",
            "https://example.com?secret=x",
        ):
            with self.assertRaises(ExportError):
                endpoint_url(base)
        with self.assertRaises(ExportError):
            _NoRedirect().redirect_request(None, None, 302, None, None, "https://x")

    def test_acknowledgements_and_partial_failure(self):
        for ack in (
            {},
            {"partialSuccess": {}},
            {
                "name": "otel-ingestion-job",
                "data": {"payload": {"authCheck": {"validKey": True}}},
            },
        ):
            _acknowledged(json.dumps(ack).encode())
        for ack in (
            {"partialSuccess": {"rejectedSpans": 1}},
            {"partialSuccess": {"errorMessage": "SECRET"}},
            [],
            {"name": "otel-ingestion-job", "data": None},
        ):
            with self.assertRaises(ExportError) as error:
                _acknowledged(json.dumps(ack).encode())
            self.assertNotIn("SECRET", str(error.exception))

    def test_send_headers_timeout_and_sanitized_failure(self):
        response = MagicMock(status=200)
        response.read.return_value = b"{}"
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch("urllib.request.build_opener", return_value=opener):
            export_report(self.report, environment=self.environment)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header("X-langfuse-ingestion-version"), "4")
            self.assertTrue(request.get_header("Authorization").startswith("Basic "))
            self.assertTrue(request.full_url.startswith("http://localhost:3000/"))
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
            opener.open.side_effect = OSError("SECRET BODY")
            with self.assertRaisesRegex(ExportError, "^transport_error$"):
                export_report(self.report, environment=self.environment)
