import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_tools.cli import main
from internal_tools import model_router as router

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/model_router"


class DispatchCliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.task = router.Task(
            request="Fix empty input", acceptance_criteria=("Pass",)
        )
        response = json.loads(
            (EXAMPLES / "parser_bug.response.simulated.json").read_text()
        )
        self.trace = router.route(
            self.task, send=lambda _: response, source="simulated"
        )
        self.path = self.root / "trace.json"
        self.path.write_text(self.trace.model_dump_json())

    def run_cli(self, args):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_replay_dispatch_uses_saved_task_and_explicit_model(self):
        code, out, _ = self.run_cli([
            "model-router", "--replay", str(self.path),
            "--dispatch-name", "fix_empty", "--workspace", str(self.root),
        ])
        self.assertEqual(code, 0)
        args = json.loads(out)
        self.assertEqual(args["model"], "gpt-6-luna")
        self.assertEqual(args["fork_turns"], "none")
        self.assertIn("Fix empty input", args["message"])
        self.assertIn("Pass", args["message"])

    def test_invalid_dispatch_fails_before_live_request(self):
        for extra in (["--dispatch-name", "fix"], [
            "--dispatch-name", "BAD", "--workspace", str(self.root),
        ]):
            with self.subTest(extra=extra), patch(
                "internal_tools.model_router.cli.route"
            ) as send:
                code, out, _ = self.run_cli([
                    "model-router", "--live", "--request", "Fix empty", *extra,
                ])
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                send.assert_not_called()

    def test_receipt_records_actual_agent_and_cannot_overwrite(self):
        output = self.root / "receipts" / "fix.json"
        args = [
            "model-router-receipt", "--trace-file", str(self.path),
            "--agent-id", "actual-agent", "--requested-model", "gpt-6-luna",
            "--out", str(output),
        ]
        self.assertEqual(self.run_cli(args)[0], 0)
        receipt = json.loads(output.read_text())
        self.assertEqual(receipt["agent_id"], "actual-agent")
        self.assertEqual(receipt["trace_file"], str(self.path.resolve()))
        self.assertEqual(self.run_cli(args)[0], 1)

    def test_receipt_rejects_silent_model_substitution(self):
        output = self.root / "receipt.json"
        code, _, _ = self.run_cli([
            "model-router-receipt", "--trace-file", str(self.path),
            "--agent-id", "actual-agent", "--requested-model", "gpt-6.1-sol",
            "--out", str(output),
        ])
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())


    def test_audit_model_verification_rejects_missing_receipt(self):
        # The saved simulation is not evidence that any worker executed.
        code, out, _ = self.run_cli([
            "model-router-audit", "--traces", str(self.root),
            "--require-model-match",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["traces"], [])

    def test_audit_verifies_receipted_worker_model(self):
        receipts, sessions = self.root / "receipts", self.root / "sessions"
        sessions.mkdir()
        self.run_cli([
            "model-router-receipt", "--trace-file", str(self.path),
            "--agent-id", "worker", "--requested-model", "gpt-6-luna",
            "--out", str(receipts / "worker.json"),
        ])
        events = [
            {"type": "session_meta", "payload": {"id": "worker"}},
            {"type": "turn_context", "payload": {"model": "gpt-6-luna"}},
        ]
        session = sessions / "worker.jsonl"
        session.write_text("\n".join(json.dumps(event) for event in events))
        args = [
            "model-router-audit", "--traces", str(self.root),
            "--receipts", str(receipts), "--sessions", str(sessions),
            "--require-model-match",
        ]
        self.assertEqual(self.run_cli(args)[0], 0)
        events[-1]["payload"]["model"] = "gpt-6.1-sol"
        session.write_text("\n".join(json.dumps(event) for event in events))
        self.assertEqual(self.run_cli(args)[0], 2)
