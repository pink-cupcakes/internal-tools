"""Read-only accounting from routing traces, caller receipts, and session metadata.

Session usage is cumulative and cannot establish per-task costs or correctness.
Only selected accounting fields leave this module; message bodies are never emitted.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")
_CACHE_KEYS = ("cached_input_tokens", "uncached_input_tokens")


def _issue(issues: list, path: Path, code: str) -> None:
    issues.append({"file": str(path.resolve()), "code": code})


def _json(path: Path, issues: list) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        _issue(issues, path, "unreadable_json")
        return None


def _usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    if not all(type(value.get(key)) is int and value[key] >= 0 for key in _USAGE_KEYS):
        return None
    return {key: value[key] for key in _USAGE_KEYS}


def _cache_usage(value: Any, *, routing: bool = False) -> dict | None:
    if _usage(value) is None:
        return None
    details = value.get("input_tokens_details")
    cached = value.get("cached_input_tokens")
    if routing:
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if type(cached) is not int or not 0 <= cached <= value["input_tokens"]:
        return None
    return {
        "cached_input_tokens": cached,
        "uncached_input_tokens": value["input_tokens"] - cached,
    }


def _summary(items: list, keys: tuple = _USAGE_KEYS) -> dict:
    known = [item for item in items if item is not None]
    return {
        "status": "known" if len(known) == len(items) else "unknown",
        "known_tokens": {key: sum(item[key] for item in known) for key in keys},
        "known_count": len(known),
        "unknown_count": len(items) - len(known),
    }


def _receipt_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = ("trace_file", "agent_id", "requested_model")
    if not all(isinstance(value.get(k), str) and value[k].strip() for k in required):
        return False
    if not Path(value["trace_file"]).is_absolute():
        return False
    parent = value.get("parent_agent_id")
    return parent is None or (isinstance(parent, str) and bool(parent.strip()))


def _receipts(directory: Path | None, issues: list) -> dict:
    result = {}
    if directory is None:
        return result
    for path in sorted(directory.rglob("*.json")):
        value = _json(path, issues)
        if not _receipt_valid(value):
            _issue(issues, path, "malformed_receipt")
            continue
        key = str(Path(value["trace_file"]).resolve())
        if key in result and result[key] != value:
            _issue(issues, path, "conflicting_receipts")
            raise ValueError(f"Conflicting receipts for {key}")
        result[key] = value
    return result


def _is_trace(value: Any, receipted: bool = False) -> bool:
    return (
        isinstance(value, dict)
        and (
            (value.get("source") == "live" and value.get("request_sent") is True)
            or (receipted and value.get("source") in {"replay", "simulated"})
        )
        and isinstance(value.get("request"), dict)
        and isinstance(value.get("recommendation"), dict)
        and "response" in value
    )


def _traces(directory: Path, receipts: dict, issues: list) -> list:
    groups: dict[str, list] = {}
    for path in sorted(directory.rglob("*.json")):
        value = _json(path, issues)
        if not _is_trace(value, str(path.resolve()) in receipts):
            continue
        fingerprint = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        groups.setdefault(fingerprint, []).append((str(path.resolve()), value))
    result = []
    for entries in groups.values():
        dispatched = [entry for entry in entries if entry[0] in receipts]
        for index, (path, value) in enumerate(dispatched or entries[:1]):
            result.append((path, value, index > 0))
    included = {entry[0] for entry in result}
    for path in sorted(receipts.keys() - included):
        _issue(issues, Path(path), "receipt_trace_not_found")
    return sorted(result, key=lambda item: item[0])


def _session_event(event: dict, state: dict) -> None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return
    kind = event.get("type")
    if kind == "session_meta" and isinstance(payload.get("id"), str):
        state["id"] = payload["id"]
    if kind == "turn_context" and isinstance(payload.get("model"), str):
        state["models"].add(payload["model"])
    if kind == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        if isinstance(info, dict) and "total_token_usage" in info:
            state["usage"] = _usage(info["total_token_usage"])
            state["cache_usage"] = _cache_usage(info["total_token_usage"])


def _read_session(path: Path, issues: list) -> dict:
    state = {"id": None, "models": set(), "usage": None, "cache_usage": None}
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    _issue(issues, path, "malformed_session_event")
                    continue
                if isinstance(event, dict):
                    _session_event(event, state)
    except (OSError, UnicodeError):
        _issue(issues, path, "unreadable_session")
    return state


def _sessions(directory: Path | None, issues: list) -> dict:
    result = {}
    if directory is None:
        return result
    for path in sorted(directory.rglob("*.jsonl")):
        state = _read_session(path, issues)
        agent_id = state["id"]
        if agent_id is None:
            continue
        if agent_id not in result:
            result[agent_id] = state
            continue
        previous = result[agent_id]
        previous["models"].update(state["models"])
        old, new = previous["usage"], state["usage"]
        if new is not None and (
            old is None or new["total_tokens"] >= old["total_tokens"]
        ):
            previous["usage"] = new
            previous["cache_usage"] = state["cache_usage"]
    return result


def _match(expected: str | None, observed: list) -> str:
    if not expected or not observed:
        return "unverified"
    if len(observed) > 1:
        return "multiple_models"
    return "match" if observed == [expected] else "mismatch"


def _trace_row(path: str, trace: dict, receipt: dict, session: dict) -> dict:
    model = trace["recommendation"].get("model")
    recommended = model.get("model_id") if isinstance(model, dict) else None
    observed = sorted(session.get("models", []))
    response = trace.get("response")
    usage = response.get("usage") if isinstance(response, dict) else None
    if trace["source"] != "live":
        usage = dict.fromkeys(_USAGE_KEYS, 0)
        usage["input_tokens_details"] = {"cached_tokens": 0}
    return {
        "source": trace["source"],
        "trace_file": path,
        "agent_id": receipt.get("agent_id"),
        "recommended_model": recommended,
        "requested_model": receipt.get("requested_model"),
        "observed_models": observed,
        "model_match": _match(recommended, observed),
        "requested_model_match": _match(receipt.get("requested_model"), observed),
        "routing_usage": _usage(usage),
        "worker_usage": session.get("usage"),
        "cache_usage": {
            "routing": _cache_usage(usage, routing=True),
            "worker": session.get("cache_usage"),
        },
        "advisory_budget_tokens": _budget(trace),
        "usage_attribution": "session_cumulative" if session else "unknown",
        "success": "unknown",
    }


def _budget(trace: dict) -> int | None:
    budgets = trace["recommendation"].get("budgets")
    limit = budgets.get("max_total_tokens") if isinstance(budgets, dict) else None
    return limit if type(limit) is int and limit > 0 else None


def _budget_status(row: dict) -> str:
    limit, usage = row["advisory_budget_tokens"], row["worker_usage"]
    if row["reused_agent"] or limit is None or usage is None:
        return "unknown"
    return "exceeded" if usage["total_tokens"] > limit else "within"


def _rows(traces: list, receipts: dict, sessions: dict) -> list:
    rows = []
    for path, trace, deduplicated in traces:
        receipt = receipts.get(path, {})
        session = sessions.get(receipt.get("agent_id"), {})
        row = _trace_row(path, trace, receipt, session)
        row["routing_usage_deduplicated"] = deduplicated
        if deduplicated:
            row["routing_usage"] = dict.fromkeys(_USAGE_KEYS, 0)
            row["cache_usage"]["routing"] = dict.fromkeys(_CACHE_KEYS, 0)
        rows.append(row)
    counts = Counter(row["agent_id"] for row in rows if row["agent_id"])
    for row in rows:
        row["reused_agent"] = counts[row["agent_id"]] > 1
        row["advisory_budget_status"] = _budget_status(row)
    return rows


def _aggregate_agents(ids: set, sessions: dict) -> dict:
    items = [sessions.get(agent_id, {}).get("usage") for agent_id in sorted(ids)]
    result = _summary(items)
    caches = [sessions.get(agent_id, {}).get("cache_usage") for agent_id in sorted(ids)]
    result["cache_usage"] = _summary(caches, _CACHE_KEYS)
    result["agent_ids"] = sorted(ids)
    result["attribution"] = "session_cumulative"
    return result


def audit_routing(
    trace_dir: Path,
    receipts_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> dict:
    """Audit observed accounting without inferring task success or token savings.

    Receipt errors appear in ``issues``; conflicting receipts raise ValueError.
    Known token subtotals exclude unknown usage. Worker and coordinator accounting
    represent whole sessions, possibly including work outside these routing traces.
    """
    for directory in (trace_dir, receipts_dir, sessions_dir):
        if directory is not None and not directory.is_dir():
            raise ValueError(f"Audit directory does not exist: {directory}")
    issues: list[dict] = []
    receipts = _receipts(receipts_dir, issues)
    traces = _traces(trace_dir, receipts, issues)
    sessions = _sessions(sessions_dir, issues)
    rows = _rows(traces, receipts, sessions)
    worker_ids = {row["agent_id"] for row in rows if row["agent_id"]}
    parents = {
        receipts[row["trace_file"]]["parent_agent_id"]
        for row in rows
        if receipts.get(row["trace_file"], {}).get("parent_agent_id")
    }
    workers = _aggregate_agents(worker_ids, sessions)
    workers["unassigned_trace_count"] = sum(row["agent_id"] is None for row in rows)
    if workers["unassigned_trace_count"]:
        workers["status"] = "unknown"
        workers["cache_usage"]["status"] = "unknown"
    return {
        "traces": rows,
        "routing_usage": _summary([row["routing_usage"] for row in rows]),
        "routing_cache_usage": _summary(
            [row["cache_usage"]["routing"] for row in rows], _CACHE_KEYS
        ),
        "worker_usage": workers,
        "coordinator_usage": _aggregate_agents(parents - worker_ids, sessions),
        "coordinator_worker_overlap": sorted(parents & worker_ids),
        "issues": issues,
    }
