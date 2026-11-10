"""Allowlisted metadata enrichment of the existing read-only routing audit."""

import hashlib
import json
from pathlib import Path

from internal_tools.model_router.audit import _read_session, _sessions, audit_routing

from .accounting import task_cost, validate_prices
from .outcomes import identifier, validate_outcomes


def _trace(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value, digest


def _safe_signals(value):
    if not isinstance(value, dict):
        return {}
    allowed = (
        "task_type_probability",
        "task_type_confidence",
        "needs_strong_model_probability",
        "capability_uncertain",
        "calibrated",
    )
    return {
        k: v
        for k, v in value.items()
        if k in allowed
        and (v is None or type(v) is bool or type(v) in (int, float) and 0 <= v <= 1)
    }


def _task(row):
    trace, digest = _trace(row["trace_file"])
    recommendation = trace["recommendation"]
    session_id = identifier(row["agent_id"])
    task_id = hashlib.sha256(f"{digest}:{session_id or ''}".encode()).hexdigest()
    return {
        "task_id": task_id,
        "recommended_model": identifier(row["recommended_model"]),
        "observed_models": [m for m in row["observed_models"] if identifier(m)],
        "reason_codes": [
            r for r in recommendation.get("reason_codes", []) if identifier(r)
        ],
        "uncertainty": _safe_signals(recommendation.get("uncertainty")),
        "policy_version": identifier(trace.get("policy", {}).get("version")),
        "routing_usage": row["routing_usage"],
        "worker_usage": row["worker_usage"],
        "usage_attribution": row["usage_attribution"],
        "outcome": {"status": "unknown"},
        "costs": {},
        "metadata": {
            "source": identifier(row["source"]),
            "recommended_tier": identifier(
                (recommendation.get("model") or {}).get("tier")
            ),
            "session_id": session_id,
            "model_match": row["model_match"],
            "reused_session": row["reused_agent"],
            "routing_usage_deduplicated": row["routing_usage_deduplicated"],
            "advisory_budget_tokens": row["advisory_budget_tokens"],
            "advisory_budget_status": row["advisory_budget_status"],
            "cache_usage": row["cache_usage"],
            "trace_mtime_unix_seconds": Path(row["trace_file"]).stat().st_mtime,
        },
    }


def _session_rows(audit, directory):
    states = _sessions(directory, [])
    workers = set(audit["worker_usage"]["agent_ids"])
    coordinators = set(audit["coordinator_usage"]["agent_ids"])
    mtimes = _session_mtimes(directory)
    rows = []
    for session_id in sorted(workers | coordinators):
        if not identifier(session_id):
            continue
        state = states.get(session_id, {})
        rows.append(
            {
                "session_id": session_id,
                "metadata": {"source_mtime_unix_seconds": mtimes.get(session_id)},
                "role": "worker" if session_id in workers else "coordinator",
                "models": sorted(m for m in state.get("models", []) if identifier(m)),
                "usage": state.get("usage"),
                "cache_usage": state.get("cache_usage"),
                "usage_attribution": "session_cumulative",
            }
        )
    return rows


def _alerts(tasks, audit):
    alerts = [{"code": identifier(i["code"]) or "audit_issue"} for i in audit["issues"]]
    strong = 0
    for task in tasks:
        codes = []
        if _fallback(task):
            codes.append("uncertainty_fallback")
        if task["routing_usage"] is None or task["worker_usage"] is None:
            codes.append("missing_accounting")
        if task["metadata"]["advisory_budget_status"] == "exceeded":
            codes.append("advisory_budget_overrun")
        strong += task["metadata"].get("recommended_tier") == "strong"
        alerts.extend({"code": code, "task_id": task["task_id"]} for code in codes)
    if strong >= 2 and strong == len(tasks):
        alerts.append({"code": "repeated_strong_only"})
    return alerts


def _summary(audit, tasks):
    keys = ("routing_usage", "routing_cache_usage", "worker_usage", "coordinator_usage")
    summary = {}
    for key in keys:
        summary[key] = {k: v for k, v in audit[key].items() if k != "agent_ids"}
    summary["task_count"] = len(tasks)
    summary["accepted_count"] = sum(t["outcome"]["status"] == "accepted" for t in tasks)
    summary["strong_count"] = sum(
        t["metadata"].get("recommended_tier") == "strong" for t in tasks
    )
    summary["uncertainty_fallback_count"] = sum(_fallback(t) for t in tasks)
    summary["uncertainty_fallback_rate"] = (
        summary["uncertainty_fallback_count"] / len(tasks) if tasks else None
    )
    summary["baseline_comparison"] = {
        "status": "unavailable",
        "savings": None,
        "reason": "explicit_equivalent_task_cohort_required",
    }
    comparisons = _comparisons(tasks)
    if comparisons:
        summary["baseline_comparison"] = {
            "status": "explicit_paired_cohorts",
            "comparisons": comparisons,
        }
    return summary


def collect_report(
    trace_dir: Path,
    receipts_dir: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    outcomes: list[dict] | None = None,
    prices: dict | None = None,
) -> dict:
    """Collect metadata; caller evidence alone may establish acceptance and costs."""
    audit = audit_routing(trace_dir, receipts_dir, sessions_dir)
    unique = {}
    for row in audit["traces"]:
        task = _task(row)
        existing = unique.setdefault(task["task_id"], task)
        key = "trace_mtime_unix_seconds"
        existing["metadata"][key] = min(
            existing["metadata"][key], task["metadata"][key]
        )
    tasks = [unique[key] for key in sorted(unique)]
    evidence, rates = validate_outcomes(outcomes), validate_prices(prices)
    known_tasks = {task["task_id"] for task in tasks}
    if evidence.keys() - known_tasks:
        raise ValueError("Outcome references unknown task")
    for task in tasks:
        task_id = task["task_id"]
        if task_id in evidence:
            task["outcome"] = evidence[task_id]
        task["costs"] = task_cost(task_id, evidence, rates, known_tasks)
    return {
        "schema_version": 1,
        "tasks": tasks,
        "sessions": _session_rows(audit, sessions_dir),
        "summary": _summary(audit, tasks),
        "alerts": _alerts(tasks, audit),
    }


def _fallback(task):
    return (
        task["uncertainty"].get("capability_uncertain") is True
        or "uncertain_task_type_conservative_strong" in task["reason_codes"]
    )


def _comparisons(tasks):
    indexed = {task["task_id"]: task for task in tasks}
    comparisons = []
    for task in tasks:
        baseline = task["outcome"].get("baseline")
        if not baseline or baseline["task_id"] == task["task_id"]:
            continue
        prior = indexed.get(baseline["task_id"], {})
        before, after = prior.get("costs", {}), task["costs"]
        if before.get("status") != "known" or after.get("status") != "known":
            continue
        if any(before[k] != after[k] for k in ("currency", "price_version")):
            continue
        comparisons.append(
            {
                "cohort_id": baseline["cohort_id"],
                "task_id": task["task_id"],
                "baseline_task_id": baseline["task_id"],
                "cost_difference": before["full_cost_per_accepted_task"]
                - after["full_cost_per_accepted_task"],
                "currency": after["currency"],
                "equivalence": "caller_asserted",
            }
        )
    return comparisons


def _session_mtimes(directory):
    if directory is None:
        return {}
    result = {}
    for path in sorted(directory.rglob("*.jsonl")):
        state = _read_session(path, [])
        session_id = state["id"]
        if session_id:
            result[session_id] = max(result.get(session_id, 0), path.stat().st_mtime)
    return result
