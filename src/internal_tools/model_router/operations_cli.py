"""Local dispatch receipts and read-only usage audits; no worker execution."""

import json
from pathlib import Path

from .models import RoutingTrace


def configure_operations(commands) -> None:
    audit = commands.add_parser("model-router-audit", help="Audit actual routed usage")
    audit.add_argument("--traces", type=Path, required=True)
    audit.add_argument("--receipts", type=Path)
    audit.add_argument("--sessions", type=Path)
    audit.add_argument(
        "--require-model-match", action="store_true",
        help="Exit 2 for absent, mismatched, or unverified dispatches",
    )
    audit.set_defaults(handler=run_operation)
    receipt = commands.add_parser(
        "model-router-receipt", help="Record an actual spawn, not a recommendation"
    )
    receipt.add_argument("--trace-file", type=Path, required=True)
    receipt.add_argument("--agent-id", required=True)
    receipt.add_argument("--requested-model", required=True)
    receipt.add_argument("--parent-agent-id")
    receipt.add_argument("--out", type=Path, required=True)
    receipt.set_defaults(handler=run_operation)


def record_receipt(options) -> dict:
    path = options.trace_file.resolve(strict=True)
    trace = RoutingTrace.model_validate_json(path.read_text())
    recommendation = trace.recommendation
    if recommendation.status != "recommended" or trace.error:
        raise ValueError("cannot record a dispatch for unresolved routing advice")
    if options.requested_model != recommendation.model.model_id:
        raise ValueError("requested model differs from routing recommendation")
    if not options.agent_id.strip():
        raise ValueError("agent ID is required after an actual spawn")
    receipt = {
        "trace_file": str(path), "agent_id": options.agent_id,
        "requested_model": options.requested_model,
    }
    if options.parent_agent_id:
        receipt["parent_agent_id"] = options.parent_agent_id
    options.out.parent.mkdir(parents=True, exist_ok=True)
    with options.out.open("x") as output:
        output.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def run_operation(options) -> int:
    import sys

    try:
        if options.command == "model-router-receipt":
            result = record_receipt(options)
        else:
            from .audit import audit_routing

            result = audit_routing(options.traces, options.receipts, options.sessions)
        print(json.dumps(result, indent=2))
        if options.command == "model-router-audit" and options.require_model_match:
            return 0 if _models_verified(result) else 2
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"model-router: {error}", file=sys.stderr)
        return 1


def _models_verified(report: dict) -> bool:
    return bool(report["traces"]) and not report["issues"] and all(
        row["model_match"] == "match" and row["requested_model_match"] == "match"
        for row in report["traces"]
    )
