"""Metadata snapshots; accounting remains owned by model_router.audit."""

import hashlib
import json
from pathlib import Path
import re
import time

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
    """Return one immutable view; session totals include work outside this view."""
    audit = audit_routing(traces, receipts, sessions)
    rows = []
    for original in audit.pop("traces"):
        row = dict(original)
        path = Path(row.pop("trace_file"))
        trace = json.loads(path.read_text(encoding="utf-8"))
        advice = trace["recommendation"]
        row.update(
            task_id=hashlib.sha256(str(path).encode()).hexdigest(),
            task_type=advice.get("task_type"),
            reason_codes=advice.get("reason_codes", []),
            uncertainty=advice.get("uncertainty"),
            policy=trace.get("policy"),
        )
        rows.append(_safe(row))
    audit["issues"] = [{"code": issue["code"]} for issue in audit["issues"]]
    report = _safe(audit)
    report.update(
        tasks=rows, task_count=len(rows), timestamp_source="snapshot_import_time"
    )
    report["import_time_unix_nano"] = str(time.time_ns())
    report["task_type_fallback_count"] = sum(
        "uncertain_task_type_conservative_strong" in row["reason_codes"] for row in rows
    )
    report["snapshot_id"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    return report
