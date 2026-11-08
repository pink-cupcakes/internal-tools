"""Thin CLI over the exported model_router functions."""

import argparse
import json
from pathlib import Path
import sys

from .core import build_request, route
from .models import RoutingTrace, Task
from .replay import replay


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--task",
        type=Path,
        help="Task JSON; optional for replay, which uses its saved task",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--live", action="store_true", help="Call Decisions once using OPENAI_API_KEY"
    )
    modes.add_argument(
        "--simulate",
        type=Path,
        help="Use an explicitly simulated response JSON; no API call",
    )
    modes.add_argument(
        "--replay",
        type=Path,
        help="Reassemble the original saved request and response; no API call",
    )
    modes.add_argument(
        "--request-only",
        action="store_true",
        help="Print the exact request body without sending it",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Explicit local dotenv file; loaded only in live mode",
    )
    parser.add_argument(
        "--trace-out", type=Path, help="Save the credential-free trace locally"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print one machine-readable JSON object"
    )


def _print_trace(routing_trace: RoutingTrace, *, as_json: bool) -> None:
    """Render a trace without mixing presentation with routing and file I/O."""
    data = routing_trace.to_dict()
    if as_json:
        print(json.dumps(data, indent=2))
        return
    print(
        f"SOURCE: {routing_trace.source.upper()} "
        f"| live request sent: {routing_trace.request_sent}"
    )
    if routing_trace.source == "simulated":
        print(
            "SIMULATED EXAMPLE: probabilities are invented; "
            "no API usage or savings measured."
        )
    for title, content in (
        ("REQUEST BODY", routing_trace.request),
        ("RAW RESPONSE", routing_trace.response),
        ("PLAN ASSEMBLED BY CODE", data["plan"]),
    ):
        print(f"\n{title}\n{json.dumps(content, indent=2)}")
    print(
        f"\nPolicy: {routing_trace.policy.version} (uncalibrated starting thresholds)"
    )
    if routing_trace.error:
        print(f"Call error: {routing_trace.error}")


def _route_from_options(task: Task | None, options: argparse.Namespace) -> RoutingTrace:
    if task is None:
        raise ValueError("--task is required for --live and --simulate")
    if options.simulate:
        response = json.loads(options.simulate.read_text())
        return route(task, send=lambda _request: response, source="simulated")
    from dotenv import load_dotenv

    load_dotenv(options.env_file, override=False)
    return route(task)


def execute(options: argparse.Namespace) -> int:
    task_path = options.task
    request_only = options.request_only
    replay_path = options.replay
    trace_path = options.trace_out
    as_json = options.json

    try:
        coding_task = (
            Task.model_validate_json(task_path.read_text()) if task_path else None
        )
        if request_only:
            if coding_task is None:
                raise ValueError("--task is required for --request-only")
            print(json.dumps(build_request(coding_task), indent=2))
            return 0
        if replay_path:
            routing_trace = replay(
                json.loads(replay_path.read_text()), task=coding_task
            )
        else:
            routing_trace = _route_from_options(coding_task, options)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(json.dumps(routing_trace.to_dict(), indent=2) + "\n")
        _print_trace(routing_trace, as_json=as_json)
        return 2 if routing_trace.plan.status == "needs_review" else 0
    except (OSError, ValueError, TypeError) as error:
        print(f"model-router: {error}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m internal_tools.model_router")
    configure_parser(parser)
    return execute(parser.parse_args(argv))
