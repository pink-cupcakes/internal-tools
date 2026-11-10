import json
import unittest
from unittest.mock import patch

from internal_tools.router_observability import export_report


class ExportTests(unittest.TestCase):
    report = {"snapshot_id": "snapshot", "tasks": [], "task_count": 0}
    environment = {"LANGFUSE_PUBLIC_KEY": "public", "LANGFUSE_SECRET_KEY": "secret"}

    def test_sdk_configuration_metadata_and_shutdown(self):
        for base in (None, "https://langfuse.example"):
            environment = dict(self.environment)
            if base:
                environment["LANGFUSE_BASE_URL"] = base
            with patch("langfuse.Langfuse") as sdk:
                export_report(self.report, environment=environment)
                sdk.assert_called_once_with(
                    public_key="public",
                    secret_key="secret",
                    base_url=base or "http://localhost:3000",
                    timeout=10,
                )
                client = sdk.return_value
                client.start_as_current_observation.assert_called_once_with(
                    name="router.snapshot", as_type="span", metadata=self.report
                )
                client.shutdown.assert_called_once_with()

    def test_shutdown_after_observation_failure(self):
        with patch("langfuse.Langfuse") as sdk:
            client = sdk.return_value
            client.start_as_current_observation.side_effect = RuntimeError("failed")
            with self.assertRaises(RuntimeError):
                export_report(self.report, environment=self.environment)
            client.shutdown.assert_called_once_with()

    def test_missing_keys_fail_before_sdk_initialization(self):
        with patch("langfuse.Langfuse") as sdk:
            for environment in ({}, {"LANGFUSE_PUBLIC_KEY": "public"}):
                with self.assertRaisesRegex(ValueError, "missing_credentials"):
                    export_report(self.report, environment=environment)
            sdk.assert_not_called()

    def test_actual_sdk_with_in_memory_exporter(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        actual_sdk = Langfuse
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("requests.sessions.Session.request", side_effect=AssertionError),
            patch("httpx.Client.send", side_effect=AssertionError),
            patch("langfuse.Langfuse") as sdk,
        ):
            sdk.side_effect = lambda **kwargs: actual_sdk(
                **dict(kwargs, public_key="pk-lf-memory"), span_exporter=exporter
            )
            export_report(self.report, environment=self.environment)
        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "router.snapshot")
        self.assertEqual(
            {
                key.removeprefix("langfuse.observation.metadata."): json.loads(value)
                for key, value in spans[0].attributes.items()
                if key.startswith("langfuse.observation.metadata.")
            },
            self.report,
        )
