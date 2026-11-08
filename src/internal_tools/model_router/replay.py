"""Replay the original saved request without rebuilding or sending it."""

from copy import deepcopy
from typing import Any

from .core import assemble_plan, build_request
from .models import RoutingPolicy, RoutingTrace, Task


def _saved_task(request: dict[str, Any]) -> Task:
    inputs = request.get("input")
    if not isinstance(inputs, list):
        raise ValueError("saved request must contain task input")
    task_inputs = []
    for message in inputs:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            task_inputs.extend(
                part["data"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "input_json"
                and "data" in part
            )
    if len(task_inputs) != 1:
        raise ValueError("saved request must contain exactly one task input_json")
    return Task.model_validate(task_inputs[0])


def replay(
    saved_trace: Any,
    *,
    task: Task | None = None,
    policy: RoutingPolicy | None = None,
) -> RoutingTrace:
    """Apply current policy to saved evidence and answers, without an API call.

    An optional task checks that its current request matches the snapshot.
    Omitting it replays the snapshot even when local examples have changed.
    """
    if (
        not isinstance(saved_trace, dict)
        or not isinstance(saved_trace.get("request"), dict)
        or saved_trace.get("response") is None
    ):
        raise ValueError("replay trace must contain a request and a response")
    request = saved_trace["request"]
    original_task = _saved_task(request)
    if task is not None and request != _json_request(task):
        raise ValueError(
            "saved request differs from --task or current routing questions. "
            "Omit --task to replay the original saved request and response. "
            "No API call was made."
        )
    policy = policy or RoutingPolicy()
    return RoutingTrace(
        source="replay",
        request_sent=False,
        request=deepcopy(request),
        response=deepcopy(saved_trace["response"]),
        plan=assemble_plan(original_task, saved_trace["response"], policy),
        policy=policy,
        elapsed_ms=0,
    )


def _json_request(task: Task) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(build_request(task)))
