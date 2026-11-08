"""Enforce physical function length, including signatures and blank lines."""

import argparse
import ast
from pathlib import Path
import tokenize
import tomllib


def check_file(path: Path, max_lines: int) -> list[str]:
    try:
        with tokenize.open(path) as source:
            tree = ast.parse(source.read(), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as error:
        return [f"{path}: {error}"]

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = node.end_lineno - node.lineno + 1
            if length > max_lines:
                errors.append(
                    f"{path}:{node.lineno}: function '{node.name}' "
                    f"has {length} lines (maximum {max_lines})"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as config:
        settings = tomllib.load(config)["tool"]["function-length"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    options = parser.parse_args(argv)
    paths = options.paths or [root / path for path in settings["paths"]]

    files = set()
    for path in paths:
        files.update(path.rglob("*.py") if path.is_dir() else [path])
    errors = []
    for path in sorted(files):
        errors.extend(check_file(path, settings["max-lines"]))
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
