"""Command entry point shared by the internal tools modules."""

import argparse
from .model_router.cli import configure_parser, run_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internal-tools")
    commands = parser.add_subparsers(dest="command", required=True)
    configure_parser(
        commands.add_parser("model-router", help="Recommend a model and budgets")
    )
    options = parser.parse_args(argv)
    return run_cli(options)
