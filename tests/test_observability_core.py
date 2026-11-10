import contextlib
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_tools.cli import main
from internal_tools.router_observability import collect_report


class SnapshotTests(unittest.TestCase):
    def test_privacy_fallback_and_audit_session_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces, receipts, sessions = [
                root / n for n in ("traces", "receipts", "logs")
            ]
            for path in (traces, receipts, sessions):
                path.mkdir()
            trace = {
                "source": "live",
                "request_sent": True,
                "request": {"prompt": "PRIVATE PROMPT"},
                "response": {
                    "output": "PRIVATE OUTPUT",
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                },
                "policy": {"version": "routing-2"},
                "recommendation": {
                    "model": {"model_id": "gpt-6-luna"},
                    "reason_codes": ["uncertain_task_type_conservative_strong"],
                    "uncertainty": {"capability_uncertain": False},
                },
            }
            self._write_audit_inputs(trace, traces, receipts, sessions)
            report = collect_report(traces, receipts, sessions)
        self.assertEqual(report["task_type_fallback_count"], 2)
        self.assertEqual(report["routing_usage"]["known_tokens"]["total_tokens"], 3)
        for role in ("worker_usage", "coordinator_usage"):
            self.assertEqual(report[role]["known_tokens"]["total_tokens"], 7)
            self.assertEqual(report[role]["attribution"], "session_cumulative")
        self.assertEqual(report["tasks"][0]["observed_models"], ["gpt-6-luna"])
        for private in ("PRIVATE", directory, "trace_file"):
            self.assertNotIn(private, json.dumps(report))

    def _write_audit_inputs(self, trace, traces, receipts, sessions):
        for index in range(2):
            path = traces / f"{index}.json"
            path.write_text(json.dumps(trace))
            (receipts / path.name).write_text(
                json.dumps(
                    {
                        "trace_file": str(path),
                        "agent_id": "worker",
                        "requested_model": "gpt-6-luna",
                        "parent_agent_id": "parent",
                    }
                )
            )
        for identity in ("worker", "parent"):
            events = [
                {"type": "session_meta", "payload": {"id": identity}},
                {"type": "turn_context", "payload": {"model": "gpt-6-luna"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 5,
                                "output_tokens": 2,
                                "total_tokens": 7,
                            }
                        },
                    },
                },
            ]
            (sessions / f"{identity}.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events)
            )

    def test_cli_offline_never_reads_env_or_sends(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("internal_tools.router_observability.export_report") as send,
            patch("dotenv.dotenv_values") as environment,
            patch.dict(sys.modules, {"langfuse": None}),
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    main(["model-router-monitor", "--traces", directory]), 0
                )
            self.assertEqual(json.loads(output.getvalue())["tasks"], [])
            send.assert_not_called()
            environment.assert_not_called()

    def test_cli_send_environment_precedence(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("internal_tools.router_observability.export_report") as send,
            patch(
                "dotenv.dotenv_values",
                return_value={
                    "LANGFUSE_PUBLIC_KEY": "file",
                    "LANGFUSE_SECRET_KEY": "secret",
                },
            ) as environment,
            patch.dict("os.environ", {"LANGFUSE_PUBLIC_KEY": "existing"}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                main(
                    [
                        "model-router-monitor",
                        "--traces",
                        directory,
                        "--send",
                        "--env-file",
                        "fake.env",
                    ]
                ),
                0,
            )
            environment.assert_called_once_with(Path("fake.env"))
            self.assertEqual(
                send.call_args.kwargs["environment"],
                {
                    "LANGFUSE_PUBLIC_KEY": "existing",
                    "LANGFUSE_SECRET_KEY": "secret",
                },
            )
