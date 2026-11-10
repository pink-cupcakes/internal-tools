import json
import unittest
from unittest.mock import Mock, patch

from internal_tools.model_router import route
from internal_tools.model_router.models import Task
from tests.test_router_telemetry import ENV


class TraceFailureTests(unittest.TestCase):
    def test_each_sdk_failure_is_fail_open_and_provider_runs_once(self):
        for operation in ("init", "start_observation", "update", "end", "flush"):
            with (
                self.subTest(operation=operation),
                patch.dict("os.environ", ENV, clear=True),
                patch("langfuse.Langfuse") as sdk,
            ):
                client = sdk.return_value
                span = client.start_observation.return_value
                target = (
                    sdk
                    if operation == "init"
                    else getattr(
                        span if operation in {"update", "end"} else client, operation
                    )
                )
                target.side_effect = RuntimeError("PRIVATE ERROR")
                sender = Mock(return_value={})
                result = route(Task(request="private"), send=sender)
                self.assertEqual(result.recommendation.status, "needs_review")
                sender.assert_called_once()

    def test_unexpected_provider_exception_is_preserved_without_content(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("langfuse.Langfuse") as sdk,
        ):
            sender = Mock(side_effect=RuntimeError("PRIVATE ERROR"))
            with self.assertRaisesRegex(RuntimeError, "PRIVATE ERROR"):
                route(Task(request="private"), send=sender)
            sender.assert_called_once()
            span = sdk.return_value.start_observation.return_value
            self.assertEqual(
                sdk.return_value.start_observation.call_args.kwargs["input"]["request"],
                "private",
            )
            update = span.update.call_args.kwargs
            self.assertEqual(update["status_message"], "RuntimeError")
            self.assertEqual(update["level"], "ERROR")
            self.assertNotIn("PRIVATE ERROR", json.dumps(update))
            span.end.assert_called_once()

    def test_real_sdk_repeated_calls_with_in_memory_exporter(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("httpx.Client.send", side_effect=AssertionError("network forbidden")),
            patch("requests.sessions.Session.request", side_effect=AssertionError),
            patch("langfuse.Langfuse") as sdk,
        ):
            sdk.side_effect = lambda **kw: Langfuse(
                **dict(kw, public_key="pk-lf-auto-memory"), span_exporter=exporter
            )
            for _ in range(2):
                route(Task(request="PRIVATE"), send=lambda _: {})
        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertTrue(all(span.name == "model_router.route" for span in spans))
        for span in spans:
            attrs = dict(span.attributes)
            self.assertIn("PRIVATE", json.dumps(attrs))
            self.assertEqual(attrs.get("langfuse.observation.model.name"), "gpt-6-luna")
            self.assertIn("langfuse.observation.input", attrs)
            self.assertIn("langfuse.observation.output", attrs)
