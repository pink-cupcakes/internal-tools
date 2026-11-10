"""Metadata-only router snapshots; offline unless explicitly sent."""

import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from internal_tools.model_router.audit import audit_routing


def _safe(value):
    if isinstance(value, str):
        return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else None
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value if value is None or type(value) in (bool, int, float) else None


def collect_report(traces: Path, receipts=None, sessions=None) -> dict:
    """Reuse audit accounting, including deduplicated cumulative session totals."""
    audit = audit_routing(traces, receipts, sessions)
    rows = []
    for original in audit.pop("traces"):
        row = dict(original)
        trace = json.loads(Path(row.pop("trace_file")).read_text(encoding="utf-8"))
        advice = trace["recommendation"]
        row.update(
            task_type=advice.get("task_type"),
            reason_codes=advice.get("reason_codes", []),
            uncertainty=advice.get("uncertainty"),
            policy=trace.get("policy"),
        )
        rows.append(_safe(row))
    audit["issues"] = [{"code": issue["code"]} for issue in audit["issues"]]
    report = _safe(audit)
    report.update(tasks=rows, task_count=len(rows), snapshot_id=uuid4().hex)
    report["task_type_fallback_count"] = sum(
        "uncertain_task_type_conservative_strong" in row["reason_codes"] for row in rows
    )
    return report


def export_report(report: dict, *, environment) -> None:
    public = environment.get("LANGFUSE_PUBLIC_KEY", "")
    secret = environment.get("LANGFUSE_SECRET_KEY", "")
    if not public.strip() or not secret.strip():
        raise ValueError("missing_credentials")
    from langfuse import Langfuse

    client = Langfuse(
        public_key=public,
        secret_key=secret,
        base_url=environment.get("LANGFUSE_BASE_URL", "http://localhost:3000"),
        timeout=10,
    )
    try:
        with client.start_as_current_observation(
            name="router.snapshot", as_type="span", metadata=report
        ):
            pass
    finally:
        client.shutdown()


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
    except Exception:
        print("model-router-monitor: collection or export failed", file=sys.stderr)
        return 1
