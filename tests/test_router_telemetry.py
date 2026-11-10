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
    "LANGFUSE_PUBLIC_KEY": "auth-public",
    "LANGFUSE_SECRET_KEY": "auth-secret",
    "LANGFUSE_TRACING_ENABLED": "true",
}


class AutomaticTracingTests(unittest.TestCase):
    def test_two_live_calls_capture_task_decision_and_private_metadata(self):
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
            self.assertEqual(sdk.call_args.kwargs["base_url"], "http://localhost:3000")
            update = span.update.call_args.kwargs
            metadata = update["metadata"]
            self.assertEqual(metadata["routing_usage"]["total_tokens"], 4)
            self.assertEqual(metadata["routing_cache_usage"]["cached_input_tokens"], 2)
            self.assertIn("PRIVATE REQUEST", json.dumps(update["input"]))
            self.assertIn("PRIVATE EVIDENCE", json.dumps(update["input"]))
            self.assertEqual(
                update["usage_details"],
                {"input": 1, "output": 1, "total": 4, "input_cached_tokens": 2},
            )
            self.assertEqual(update["model"], "gpt-6-luna")
            self.assertIn("PRIVATE RESPONSE", json.dumps(update["output"]))
            self.assertNotIn("PRIVATE", json.dumps(metadata))
            self.assertIn("clarification_items", update["output"]["recommendation"])
            self.assertNotIn("auth-public", json.dumps(update))
            self.assertNotIn("auth-secret", json.dumps(update))

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
            failure_update = span.update.call_args.kwargs
            self.assertIn("private", json.dumps(failure_update["input"]))
            self.assertEqual(failure_update["level"], "ERROR")
            self.assertEqual(failure_update["status_message"], "PRIVATE ERROR")
            result = route(Task(request="private", missing_requirements=["PRIVATE"]))
            self.assertEqual(result.source, "skipped")
            self.assertEqual(span.update.call_count, 2)
            skipped = span.update.call_args.kwargs
            self.assertEqual(skipped["model"], None)
            self.assertIn("PRIVATE", json.dumps(skipped["output"]))
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
