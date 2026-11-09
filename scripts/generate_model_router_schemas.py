"""Generate routing-tool JSON Schemas, or check them with --check."""

import argparse
import json
from pathlib import Path

from openai.types.decision import Decision

from internal_tools import model_router as router

MODELS = {
    "task": (router.Task, "validation"),
    "budgets": (router.Budgets, "serialization"),
    "model-recommendation": (router.ModelRecommendation, "serialization"),
    "uncertainty": (router.Uncertainty, "serialization"),
    "recommendation": (router.Recommendation, "serialization"),
    "routing-trace": (router.RoutingTrace, "serialization"),
    "routing-policy": (router.RoutingPolicy, "validation"),
    "decision-response": (Decision, "serialization"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args(argv)
    folder = Path(__file__).resolve().parents[1] / "schemas/model_router"
    if not options.check:
        folder.mkdir(parents=True, exist_ok=True)
    mismatches = []
    for slug, (model, mode) in MODELS.items():
        schema = model.model_json_schema(mode=mode)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        content = json.dumps(schema, indent=2) + "\n"
        path = folder / f"{slug}.schema.json"
        if options.check:
            if not path.is_file() or path.read_text() != content:
                mismatches.append(path.name)
        else:
            path.write_text(content)
    if mismatches:
        print("Schemas out of date: " + ", ".join(mismatches))
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
