"""Explicit evidence only: session cumulative tokens never imply task cost."""

import math

from .outcomes import identifier


def validate_prices(prices):
    if prices is None:
        return None
    if not isinstance(prices, dict) or not identifier(prices.get("version")):
        raise ValueError("Prices require a version")
    if not identifier(prices.get("currency")):
        raise ValueError("Prices require currency")
    models = prices.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("Prices require model rates")
    clean = {}
    for model, rates in models.items():
        if not identifier(model) or not isinstance(rates, dict):
            raise ValueError("Invalid price model")
        keys = ("input", "cached_input", "output")
        if any(
            type(rates.get(k)) not in (int, float)
            or not math.isfinite(rates[k])
            or rates[k] < 0
            for k in keys
        ):
            raise ValueError("Invalid per-million rate")
        clean[model] = {k: rates[k] for k in keys}
    return {
        "version": prices["version"],
        "currency": prices["currency"],
        "models": clean,
    }


def _lineage(task_id, outcomes):
    seen = set()
    while task_id is not None:
        if task_id in seen or task_id not in outcomes:
            return None
        seen.add(task_id)
        task_id = outcomes[task_id]["retry_of"]
    return seen


def _charge(record, prices):
    rates = prices["models"].get(record["model"])
    if rates is None:
        return None
    tokens, cached = record["usage"], record["cached_input_tokens"]
    return (
        (tokens["input_tokens"] - cached) * rates["input"]
        + cached * rates["cached_input"]
        + tokens["output_tokens"] * rates["output"]
    ) / 1_000_000


def task_cost(task_id, outcomes, prices, known_tasks):
    unknown = {"status": "unknown", "full_cost_per_accepted_task": None}
    outcome = outcomes.get(task_id)
    if not prices or not outcome or outcome["status"] != "accepted":
        return unknown
    lineage = _lineage(task_id, outcomes)
    if lineage is None or not lineage <= known_tasks:
        return unknown
    records = outcome["attribution"]
    required = {
        (task, role) for task in lineage for role in ("router", "worker", "coordinator")
    }
    pairs = [(r["task_id"], r["role"]) for r in records]
    if len(pairs) != len(set(pairs)) or set(pairs) != required:
        return unknown
    charges = [_charge(record, prices) for record in records]
    if any(charge is None for charge in charges):
        return unknown
    return {
        "status": "known",
        "full_cost_per_accepted_task": sum(charges),
        "currency": prices["currency"],
        "price_version": prices["version"],
        "attempt_count": len(lineage),
        "attribution": "explicit_task_evidence",
    }
