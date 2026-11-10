"""Offline collection first, durable snapshots second, optional delivery last."""

import json
import os
from pathlib import Path
import sys

from .exporter import ExportError, export_report


def configure_parser(parser) -> None:
    parser.add_argument("--traces", type=Path, required=True)
    for name in ("receipts", "sessions", "outcomes", "prices", "env-file"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument(
        "--ledger", type=Path, default=Path(".router-observability/ledger.sqlite3")
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Explicitly send pending snapshots to Langfuse",
    )
    parser.set_defaults(handler=run_cli)


def _read_json(path: Path | None, expected: type):
    if path is None:
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError("invalid input shape")
    return value


def _environment(path: Path | None) -> dict:
    environment = dict(os.environ)
    if path is not None:
        from dotenv import dotenv_values

        for key, value in dotenv_values(path).items():
            if value is not None:
                environment.setdefault(key, value)
    return environment


def _deliver(options) -> dict:
    from .ledger import mark_exported, pending_snapshots

    sent = 0
    environment = _environment(options.env_file)
    for snapshot_id, report in pending_snapshots(options.ledger):
        try:
            export_report(report, snapshot_id, environment=environment)
        except ExportError as error:
            return {"status": "pending", "sent": sent, "error": str(error)}
        mark_exported(options.ledger, snapshot_id)
        sent += 1
    return {"status": "sent", "sent": sent}


def run_cli(options) -> int:
    from . import collect_report, write_snapshot

    try:
        report = collect_report(
            options.traces,
            options.receipts,
            options.sessions,
            outcomes=_read_json(options.outcomes, list),
            prices=_read_json(options.prices, dict),
        )
        snapshot_id = write_snapshot(options.ledger, report)
        delivery = _deliver(options) if options.send else {"status": "offline"}
        print(
            json.dumps(
                {"snapshot_id": snapshot_id, "report": report, "delivery": delivery},
                indent=2,
                allow_nan=False,
            )
        )
        return 1 if delivery["status"] == "pending" else 0
    except (OSError, ValueError, TypeError):
        print(
            "model-router-monitor: local collection or persistence failed",
            file=sys.stderr,
        )
        return 1
