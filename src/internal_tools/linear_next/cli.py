"""Local Linear queue CLI; network collection is owned by the calling session."""

import argparse
import json
from pathlib import Path
import sys

from .common import digest
from .storage import build


def configure_parser(parser):
    parser.add_argument("action", choices=["digest", "build", "status"])
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(".linear-next"),
        help="Local data directory (default: .linear-next in the working directory)",
    )
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--inline", type=Path, help="Also write an HTML fragment")
    parser.set_defaults(handler=run_cli)


def execute(options):
    directory = options.directory
    snapshot = options.snapshot or directory / "snapshot.json"
    review = options.review or directory / "review.json"
    if options.action == "digest":
        return digest(json.loads(snapshot.read_text(encoding="utf-8")))
    if options.action == "status":
        report = json.loads((directory / "queue.json").read_text(encoding="utf-8"))
        return {key: report[key] for key in ("capturedAt", "gaps", "recommendations")}
    report = build(
        snapshot, review, directory, limit=options.limit, inline=options.inline
    )
    return dict(
        recommendations=[row["id"] for row in report["recommendations"]],
        gaps=report["gaps"],
        reviewValid=report["reviewValid"],
    )


def run_cli(options):
    try:
        result = execute(options)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"linear-next: {error}", file=sys.stderr)
        return 1
    print(result if isinstance(result, str) else json.dumps(result, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    configure_parser(parser)
    return run_cli(parser.parse_args(argv))
