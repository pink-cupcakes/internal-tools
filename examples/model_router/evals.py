"""Live routing-label experiments; importing this module performs no I/O."""

import argparse
import json
import os
from pathlib import Path

from langfuse import Evaluation, Langfuse

from internal_tools.model_router import RoutingPolicy, Task, recommend, route

CASES = Path(__file__).with_name("evals.json")


def label(recommendation):
    model = recommendation.get("model")
    return {
        "status": recommendation["status"],
        "tier": model["tier"] if model else None,
    }


def scores(output, expected):
    """Rates use all cases; a stopped/error route never counts as small."""
    actual = label(output)
    valid = not output.get("error")
    strong = valid and actual == {"status": "recommended", "tier": "strong"}
    return {
        "routing_label_match": int(valid and actual == expected),
        "over_escalation": int(expected["tier"] == "small" and strong),
        "missed_escalation": int(expected["tier"] == "strong" and not strong),
    }


def evaluate(*, output, expected_output, **kwargs):
    return [
        Evaluation(name=name, value=value)
        for name, value in scores(output, expected_output).items()
    ]


def check_baseline(baseline, cases):
    ids = [item["metadata"]["id"] for item in cases["data"]]
    if (
        baseline.get("labels_version") != cases["version"]
        or baseline.get("case_ids") != ids
        or baseline.get("cases") != cases["data"]
    ):
        raise ValueError("Baseline requires identical version, case IDs and cases")


def collect(data):
    """One public live route per actionable case; declared gaps skip the API."""
    return {
        item["metadata"]["id"]: route(Task.from_dict(item["input"])) for item in data
    }


def outputs(data, traces, policy):
    result = {}
    for item in data:
        case_id = item["metadata"]["id"]
        task, trace = Task.from_dict(item["input"]), traces[case_id]
        if policy == "always-strong":
            result[case_id] = (
                {"status": "needs_clarification", "model": None}
                if task.missing_requirements
                else {"status": "recommended", "model": {"tier": "strong"}}
            )
        else:
            rec = (
                trace.recommendation
                if policy is None
                else recommend(task, trace.response, policy)
            )
            result[case_id] = rec.model_dump(mode="json") | {"error": trace.error}
    return result


def publish(client, cases, traces):
    policies = {
        "current": None,
        "always-strong": "always-strong",
        "candidate-0.5": RoutingPolicy(min_task_type_probability=0.5),
    }
    runs = {}
    for name, policy in policies.items():
        decisions = outputs(cases["data"], traces, policy)

        def task(*, item, **kwargs):
            return decisions[item["metadata"]["id"]]

        result = client.run_experiment(
            name=f"model-router-{cases['version']}",
            run_name=name,
            data=cases["data"],
            task=task,
            evaluators=[evaluate],
            max_concurrency=1,
        )
        metrics = {
            key: sum(
                scores(decisions[item["metadata"]["id"]], item["expected_output"])[key]
                for item in cases["data"]
            )
            / len(cases["data"])
            for key in scores(
                next(iter(decisions.values())), cases["data"][0]["expected_output"]
            )
        }
        runs[name] = {
            "experiment_url": result.experiment_url,
            "item_count": len(result.item_results),
            "metrics": metrics,
            "decisions": decisions,
        }
    return runs


def usage(traces):
    known = []
    unknown = 0
    for trace in traces.values():
        response = trace.response if isinstance(trace.response, dict) else {}
        value = response.get("usage")
        keys = ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(value, dict) and all(
            type(value.get(key)) is int and value[key] >= 0 for key in keys
        ):
            known.append(value)
        elif trace.request_sent:
            unknown += 1
    totals = {
        key: sum(item.get(key, 0) for item in known)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    return {
        "known_totals": totals,
        "known_calls": len(known),
        "calls_with_unknown_usage": unknown,
        "raw_usage": known,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    cases = json.loads(CASES.read_text())
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    if baseline is not None:
        check_baseline(baseline, cases)
    from dotenv import load_dotenv

    load_dotenv()
    if not all(
        os.getenv(key)
        for key in ("OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    ):
        parser.error("OpenAI and Langfuse credentials are required")
    client = Langfuse(base_url=os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000"))
    traces = collect(cases["data"])
    try:
        report = {
            "labels_version": cases["version"],
            "cases": cases["data"],
            "case_ids": [item["metadata"]["id"] for item in cases["data"]],
            "runs": publish(client, cases, traces),
            "router_usage": usage(traces),
            "traces": {key: value.to_dict() for key, value in traces.items()},
        }
        if baseline is not None:
            report["baseline_metric_deltas"] = {
                name: {
                    key: value - baseline["runs"][name]["metrics"][key]
                    for key, value in run["metrics"].items()
                }
                for name, run in report["runs"].items()
            }
        if args.out:
            args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        client.flush()


if __name__ == "__main__":
    main()
