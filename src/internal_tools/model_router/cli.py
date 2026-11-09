"""Thin CLI over the exported model_router functions."""

import argparse
import json
from pathlib import Path
import sys

from .core import build_request, route
from .dispatch import build_dispatch
from .models import RoutingTrace, Task
from .replay import _saved_task, replay


def configure_parser(parser: argparse.ArgumentParser) -> None:
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument(
        "--task",
        type=Path,
        help="Task JSON; optional for replay, which uses its saved task",
    )
    inputs.add_argument("--request", help="Inline coding request instead of Task JSON")
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
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument(
        "--trace",
        "--json",
        dest="full_trace",
        action="store_true",
        help="Print full RoutingTrace JSON (default: recommendation JSON)",
    )

    outputs.add_argument(
        "--dispatch-name", help="Print explicit-model spawn_agent arguments"
    )
    parser.add_argument("--workspace", help="Absolute worker workspace for dispatch")


def _print_result(routing_trace: RoutingTrace, *, full_trace: bool) -> None:
    """Keep stdout as one JSON object and send call diagnostics to stderr."""
    result = routing_trace if full_trace else routing_trace.recommendation
    print(result.model_dump_json(indent=2))
    if routing_trace.error:
        print(f"model-router: {routing_trace.error}", file=sys.stderr)


def _load_task(options: argparse.Namespace) -> Task | None:
    if options.request is not None:
        return Task(request=options.request)
    if options.task:
        return Task.model_validate_json(options.task.read_text())
    return None


def _route_from_options(task: Task | None, options: argparse.Namespace) -> RoutingTrace:
    if task is None:
        raise ValueError("--task is required unless --request is supplied")
    if options.simulate:
        response = json.loads(options.simulate.read_text())
        return route(task, send=lambda _request: response, source="simulated")
    from dotenv import load_dotenv

    load_dotenv(options.env_file, override=False)
    return route(task)


def run_cli(options: argparse.Namespace) -> int:
    request_only = options.request_only
    replay_path = options.replay
    trace_path = options.trace_out

    try:
        _validate_dispatch_options(options)
        coding_task = _load_task(options)
        if request_only:
            if coding_task is None:
                raise ValueError("--task is required unless --request is supplied")
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
        _output_result(routing_trace, options)
        return 2 if routing_trace.recommendation.status == "needs_review" else 0
    except (OSError, ValueError, TypeError) as error:
        print(f"model-router: {error}", file=sys.stderr)
        return 1


def _validate_dispatch_options(options: argparse.Namespace) -> None:
    if bool(options.dispatch_name) != bool(options.workspace):
        raise ValueError("--dispatch-name and --workspace must be supplied together")
    if options.request_only and options.dispatch_name:
        raise ValueError("dispatch requires a recommendation, not --request-only")
    if options.dispatch_name:
        from .dispatch import validate_dispatch_target

        validate_dispatch_target(options.dispatch_name, options.workspace)


def _output_result(trace: RoutingTrace, options: argparse.Namespace) -> None:
    if options.dispatch_name:
        args = build_dispatch(
            _saved_task(trace.request), trace.recommendation,
            task_name=options.dispatch_name, workspace=options.workspace,
        )
        print(json.dumps(args, indent=2))
    else:
        _print_result(trace, full_trace=options.full_trace)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m internal_tools.model_router")
    configure_parser(parser)
    return run_cli(parser.parse_args(argv))
