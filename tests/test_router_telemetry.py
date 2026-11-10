import contextlib
import inspect
import io
import json
import unittest
from unittest.mock import Mock, patch

from internal_tools.cli import main
from internal_tools.model_router import route
from internal_tools.model_router.core import DecisionCallError
from internal_tools.model_router.models import Task

ENV = {
    "LANGFUSE_PUBLIC_KEY": "public",
    "LANGFUSE_SECRET_KEY": "secret",
    "LANGFUSE_TRACING_ENABLED": "true",
}


class AutomaticTracingTests(unittest.TestCase):
    def test_two_live_calls_cover_execution_and_private_metadata(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("langfuse.Langfuse") as sdk,
        ):
            span = sdk.return_value.start_observation.return_value
            sender = Mock(
                return_value={
                    "private": "PRIVATE RESPONSE",
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 1,
                        "total_tokens": 4,
                        "input_tokens_details": {"cached_tokens": 2},
                        "secret": "PRIVATE",
                    },
                }
            )
            sender.side_effect = lambda _: self._during_span(
                span, sender.call_count, sender.return_value
            )
            for _ in range(2):
                route(
                    Task(
                        request="PRIVATE REQUEST",
                        evidence=[
                            {"ref": "PRIVATE REF", "content": "PRIVATE EVIDENCE"}
                        ],
                    ),
                    send=sender,
                )
            self.assertEqual(sender.call_count, 2)
            self.assertEqual(span.end.call_count, 2)
            self.assertEqual(sdk.return_value.flush.call_count, 2)
            sdk.return_value.shutdown.assert_not_called()
            self.assertEqual(sdk.call_args.kwargs["base_url"], "http://localhost:3000")
            metadata = span.update.call_args.kwargs["metadata"]
            self.assertEqual(metadata["routing_usage"]["total_tokens"], 4)
            self.assertEqual(metadata["routing_cache_usage"]["cached_input_tokens"], 2)
            self.assertNotIn("PRIVATE", json.dumps(metadata))
            self.assertNotIn("clarification_items", metadata["recommendation"])

    def _during_span(self, span, calls, response):
        self.assertEqual(span.end.call_count, calls - 1)
        return response

    def test_failure_and_skipped_results_are_recorded(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("langfuse.Langfuse") as sdk,
        ):
            send = Mock(
                side_effect=DecisionCallError("PRIVATE ERROR", request_sent=True)
            )
            route(Task(request="private"), send=send)
            span = sdk.return_value.start_observation.return_value
            self.assertNotIn("PRIVATE", json.dumps(span.update.call_args.kwargs))
            result = route(Task(request="private", missing_requirements=["PRIVATE"]))
            self.assertEqual(result.source, "skipped")
            self.assertEqual(span.update.call_count, 2)
            self.assertEqual(send.call_count, 1)

    def test_offline_missing_keys_and_opt_out_never_initialize_sdk(self):
        for environment in (
            {},
            {"LANGFUSE_PUBLIC_KEY": "public"},
            dict(ENV, LANGFUSE_TRACING_ENABLED="false"),
        ):
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("langfuse.Langfuse") as sdk,
            ):
                route(Task(request="private"), send=lambda _: {})
                sdk.assert_not_called()
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("langfuse.Langfuse") as sdk,
        ):
            for source in ("simulated", "replay"):
                route(Task(request="private"), source=source, send=lambda _: {})
            with (
                patch("dotenv.load_dotenv") as load,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(["model-router", "--request-only", "--request", "private"]), 0
                )
                load.assert_not_called()
            sdk.assert_not_called()

    def test_live_cli_uses_public_hook(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch("langfuse.Langfuse") as sdk,
            patch("dotenv.load_dotenv"),
            patch(
                "internal_tools.model_router.provider.send_decision", return_value={}
            ) as send,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                main(["model-router", "--live", "--request", "private"]), 2
            )
            send.assert_called_once()
            sdk.return_value.start_observation.assert_called_once()
            sdk.return_value.flush.assert_called_once()

    def test_monitor_removed_and_signature_preserved(self):
        self.assertIn("source", inspect.signature(route).parameters)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["model-router-monitor"])
