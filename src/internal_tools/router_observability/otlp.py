"""Deterministic, allowlisted OTLP/HTTP JSON without task content."""

import hashlib
import json
import math
import re

_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_FIELDS = (
    "recommended_model",
    "observed_models",
    "reason_codes",
    "uncertainty",
    "policy_version",
    "routing_usage",
    "worker_usage",
    "usage_attribution",
    "role",
    "models",
    "usage",
    "cache_usage",
    "alerts",
    "summary",
)
_METADATA = (
    "source",
    "model_match",
    "reused_session",
    "advisory_budget_tokens",
    "advisory_budget_status",
    "routing_usage_deduplicated",
    "cache_usage",
)


def _hash(value: str, size: int = 32) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:size]


def _safe(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if _SAFE.fullmatch(value) else None
    if isinstance(value, list):
        return [_safe(item) for item in value if _safe(item) is not None]
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if _SAFE.fullmatch(key) and _safe(item) is not None
        }
    return None


def _attribute(key: str, value) -> dict:
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    elif isinstance(value, float):
        encoded = {"doubleValue": value}
    else:
        encoded = {
            "stringValue": value
            if isinstance(value, str)
            else json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        }
    return {"key": key, "value": encoded}


def _summary_counters(row: dict) -> dict:
    summary = row.get("summary", {})
    fields = (
        "task_count",
        "accepted_count",
        "strong_count",
        "uncertainty_fallback_count",
        "uncertainty_fallback_rate",
    )
    return {
        key: summary[key] for key in fields if type(summary.get(key)) in (int, float)
    }


def _outcome_metadata(row: dict) -> dict:
    outcome = row.get("outcome") or {}
    validation = outcome.get("validation") or {}
    duration = validation.get("duration_ms")
    known_duration = (
        type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
    )
    metadata = {"duration_unavailable": not known_duration}
    if known_duration:
        metadata["validation_duration_ms"] = duration
    checks = validation.get("checks", [])
    if isinstance(checks, list):
        metadata["validation_checks"] = [
            {"name": check["name"], "passed": check["passed"]}
            for check in checks
            if isinstance(check, dict)
            and isinstance(check.get("name"), str)
            and _SAFE.fullmatch(check["name"])
            and type(check.get("passed")) is bool
        ]
    retry = outcome.get("retry_of")
    if isinstance(retry, str) and retry:
        metadata["retry_of"] = (
            retry if re.fullmatch(r"[0-9a-f]{64}", retry) else _hash(retry, 64)
        )
    return metadata


def _attributes(row: dict, snapshot_id: str, kind: str) -> list[dict]:
    metadata = {key: _safe(row[key]) for key in _FIELDS if key in row}
    metadata.update({key: _safe(row.get("metadata", {}).get(key)) for key in _METADATA})
    metadata.update(_summary_counters(row))
    metadata.update(_outcome_metadata(row))
    costs = row.get("costs", {})
    metadata["costs"] = {
        key: _safe(costs[key])
        for key in (
            "status",
            "full_cost_per_accepted_task",
            "currency",
            "price_version",
            "attempt_count",
            "attribution",
        )
        if key in costs
    }
    metadata["task_id"] = _safe(row.get("task_id"))
    if row.get("session_id"):
        metadata["session_key"] = _hash(str(row["session_id"]))
    metadata["outcome_status"] = _safe((row.get("outcome") or {}).get("status"))
    metadata.update(
        {
            "snapshot_id": snapshot_id,
            "kind": kind,
            "schema_version": 1,
            "timestamp_unavailable": True,
        }
    )
    # No model attribute is emitted: Langfuse automatic pricing must not turn
    # unknown local cost into an inferred known price.
    attributes = [_attribute("langfuse.observation.type", "span")]
    attributes.extend(
        _attribute("langfuse.observation.metadata." + key, value)
        for key, value in metadata.items()
        if value is not None
    )
    return attributes


def _span(row: dict, snapshot_id: str, kind: str) -> dict:
    identity = str(row.get("task_id", row.get("session_id", "unknown")))
    seed = snapshot_id + ":" + kind + ":" + identity
    source = row.get("metadata", {})
    field = (
        "trace_mtime_unix_seconds" if kind == "task" else "source_mtime_unix_seconds"
    )
    timestamp = source.get(field)
    start = int(timestamp * 1_000_000_000) if timestamp else 946684800000000000
    attributes = _attributes(row, snapshot_id, kind)
    if timestamp:
        attributes.append(
            _attribute("langfuse.observation.metadata.timestamp_source", field)
        )
        attributes = [
            item
            for item in attributes
            if not item["key"].endswith("timestamp_unavailable")
        ]
    return {
        "traceId": _hash(seed),
        "spanId": _hash(seed + ":root", 16),
        "name": "router." + kind,
        "kind": 1,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 1),
        "attributes": attributes,
        "status": {"code": 1},
    }


def build_payload(report: dict, snapshot_id: str) -> dict:
    """Each cumulative session gets a separate trace, never a generation."""
    spans = [_span(row, snapshot_id, "task") for row in report.get("tasks", [])]
    spans.extend(
        _span(row, snapshot_id, "session") for row in report.get("sessions", [])
    )
    summary = _safe(report.get("summary", {}))
    snapshot = {
        "task_id": snapshot_id,
        "summary": summary,
        "alerts": [
            {"code": _safe(alert.get("code")), "task_id": _safe(alert.get("task_id"))}
            for alert in report.get("alerts", [])
        ],
    }
    timestamps = [
        row.get("metadata", {}).get(field)
        for rows, field in (
            ("tasks", "trace_mtime_unix_seconds"),
            ("sessions", "source_mtime_unix_seconds"),
        )
        for row in report.get(rows, [])
        if row.get("metadata", {}).get(field) is not None
    ]
    snapshot["metadata"] = {"source_mtime_unix_seconds": max(timestamps, default=None)}
    spans.append(_span(snapshot, snapshot_id, "snapshot"))
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [_attribute("service.name", "model-router")]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "internal-tools.router-observability"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }
