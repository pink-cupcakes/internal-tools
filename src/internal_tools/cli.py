"""Command entry point shared by the internal tools modules."""

import argparse
from .model_router.cli import configure_parser, run_cli
from .model_router.operations_cli import configure_operations
from .linear_next.cli import configure_parser as configure_linear_next
from .router_observability import configure_parser as configure_monitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internal-tools")
    commands = parser.add_subparsers(dest="command", required=True)
    configure_parser(
        commands.add_parser("model-router", help="Recommend a model and budgets")
    )
    configure_linear_next(
        commands.add_parser("linear-next", help="Build a reviewed Linear work queue")
    )
    configure_monitor(
        commands.add_parser(
            "model-router-monitor", help="Collect offline router metrics"
        )
    )
    configure_operations(commands)
    options = parser.parse_args(argv)
    return options.handler(options) if hasattr(options, "handler") else run_cli(options)
