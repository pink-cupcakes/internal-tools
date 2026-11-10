"""Offline JSON by default; network access requires --send."""

import json
import os
from pathlib import Path
import sys

from .core import collect_report
from .exporter import export_report


def configure_parser(parser) -> None:
    parser.add_argument("--traces", type=Path, required=True)
    for name in ("receipts", "sessions"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--send", action="store_true")
    parser.set_defaults(handler=run_cli)


def run_cli(options) -> int:
    try:
        report = collect_report(options.traces, options.receipts, options.sessions)
        if options.send:
            from dotenv import dotenv_values

            environment = {
                k: v
                for k, v in dotenv_values(options.env_file).items()
                if v is not None
            }
            environment.update(os.environ)
            export_report(report, environment=environment)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError):
        print("model-router-monitor: collection or export failed", file=sys.stderr)
        return 1
