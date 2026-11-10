"""Run from the repository root: no API requests or model calls."""

import json
from pathlib import Path
import tempfile

from internal_tools.cli import main
from internal_tools.model_router import Task, recommend, RoutingTrace, RoutingPolicy


def fixture_trace() -> RoutingTrace:
    task = Task(request="Fix empty input returning the wrong count.")
    policy = RoutingPolicy()
    response = {
        "answers": [
            {
                "type": "choice",
                "name": "workflow",
                "choice": "fix",
                "probabilities": [
                    {"value": "fix", "probability": 0.95},
                    {"value": "explain", "probability": 0.02},
                    {"value": "implement", "probability": 0.02},
                    {"value": "clarify", "probability": 0.01},
                ],
                "confidence": 0.95,
            },
            {"type": "predicate", "name": "needs_strong_model", "probability": 0.1},
        ]
    }
    return RoutingTrace(
        source="simulated",
        request_sent=False,
        request={},
        response=response,
        recommendation=recommend(task, response, policy),
        policy=policy,
        elapsed_ms=0,
        error=None,
    )


def main_example() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        traces = root / "traces"
        traces.mkdir()
        trace = fixture_trace()
        trace_path = traces / "example.json"
        trace_path.write_text(trace.model_dump_json())
        receipts = root / "receipts"
        receipts.mkdir()
        (receipts / "example.json").write_text(
            json.dumps(
                {
                    "trace_file": str(trace_path),
                    "agent_id": "offline-fixture-worker",
                    "requested_model": trace.recommendation.model.model_id,
                }
            )
        )
        result = main(
            [
                "model-router-monitor",
                "--traces",
                str(traces),
                "--receipts",
                str(receipts),
                "--ledger",
                str(root / "ledger.sqlite3"),
            ]
        )
        if result:
            raise RuntimeError(json.dumps({"offline_example_exit": result}))
        return result


if __name__ == "__main__":
    raise SystemExit(main_example())
