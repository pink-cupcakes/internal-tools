"""Strict validation of caller-supplied outcome and attribution evidence."""

import re


_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_KEYS = {"input_tokens", "output_tokens", "total_tokens"}


def identifier(value):
    """Retain bounded identifiers, never free text or local paths."""
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def usage(value):
    if not isinstance(value, dict) or not _KEYS <= value.keys():
        return None
    if any(type(value[k]) is not int or value[k] < 0 for k in _KEYS):
        return None
    if value["total_tokens"] != value["input_tokens"] + value["output_tokens"]:
        return None
    return {key: value[key] for key in sorted(_KEYS)}


def _attribution(record):
    if not isinstance(record, dict):
        raise ValueError("Invalid attribution record")
    result = {key: identifier(record.get(key)) for key in ("task_id", "model")}
    role = record.get("role")
    tokens = usage(record.get("usage"))
    cached = record.get("cached_input_tokens")
    if (
        not all(result.values())
        or role not in {"router", "worker", "coordinator"}
        or tokens is None
        or type(cached) is not int
        or not 0 <= cached <= tokens["input_tokens"]
    ):
        raise ValueError("Invalid explicit attribution")
    return dict(result, role=role, usage=tokens, cached_input_tokens=cached)


def _validation(value):
    if not isinstance(value, dict):
        raise ValueError("Outcome requires validation evidence")
    checks, duration = value.get("checks"), value.get("duration_ms")
    if type(duration) not in (int, float) or not 0 <= duration < float("inf"):
        raise ValueError("Invalid duration_ms")
    if not isinstance(checks, list) or not checks:
        raise ValueError("Outcome requires nonempty checks")
    clean = []
    for check in checks:
        if (
            not isinstance(check, dict)
            or not identifier(check.get("name"))
            or type(check.get("passed")) is not bool
        ):
            raise ValueError("Invalid validation check")
        clean.append({"name": check["name"], "passed": check["passed"]})
    return {"checks": clean, "duration_ms": duration}


def validate_outcomes(records):
    """Reject ambiguous, duplicate or unvalidated acceptance evidence."""
    result = {}
    for record in records or []:
        if not isinstance(record, dict) or not identifier(record.get("task_id")):
            raise ValueError("Outcome requires task_id")
        task_id, status = record["task_id"], record.get("status")
        if task_id in result or status not in {"accepted", "failed", "rejected"}:
            raise ValueError("Duplicate outcome or invalid status")
        validation = _validation(record.get("validation"))
        if status == "accepted" and not all(c["passed"] for c in validation["checks"]):
            raise ValueError("Accepted outcome has failed checks")
        retry = record.get("retry_of")
        if retry is not None and (not identifier(retry) or retry == task_id):
            raise ValueError("Invalid retry lineage")
        result[task_id] = {
            "status": status,
            "validation": validation,
            "retry_of": retry,
            "attribution": [_attribution(r) for r in record.get("attribution", [])],
            "baseline": _baseline(record.get("baseline")),
        }
    _validate_lineages(result)
    return result


def _baseline(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not identifier(value.get("cohort_id"))
        or not identifier(value.get("task_id"))
        or value.get("equivalence_asserted") is not True
    ):
        raise ValueError("Baseline requires explicit equivalent-task cohort")
    return {
        "cohort_id": value["cohort_id"],
        "task_id": value["task_id"],
        "equivalence_asserted": True,
    }


def _validate_lineages(outcomes):
    for task_id, outcome in outcomes.items():
        seen = {task_id}
        predecessor = outcome["retry_of"]
        while predecessor is not None:
            if predecessor in seen or predecessor not in outcomes:
                raise ValueError("Missing or cyclic retry lineage")
            seen.add(predecessor)
            prior = outcomes[predecessor]
            if prior["status"] == "accepted":
                raise ValueError("Retry predecessor already accepted")
            predecessor = prior["retry_of"]
