"""Bounded question construction and deterministic plan assembly."""

from copy import deepcopy
import math
from time import perf_counter
from typing import Any, Callable

from .models import Plan, RoutingPolicy, RoutingTrace, Task
from .questions import ROUTING_INSTRUCTIONS, ROUTING_QUESTIONS

ROUTER_MODEL = "gpt-6-luna"
ALLOWED_WORKFLOWS = ("explain", "fix", "implement", "clarify")
WORKFLOW_STEPS = {
    "explain": ("inspect", "explain", "check_claims"),
    "fix": ("inspect", "reproduce", "patch", "verify"),
    "implement": ("inspect", "confirm_acceptance_criteria", "implement", "verify"),
}


class DecisionCallError(Exception):
    """A transport failure with a sanitized, credential-free reason."""

    def __init__(self, message: str, *, request_sent: bool = False):
        super().__init__(message)
        self.request_sent = request_sent


def build_request(task: Task) -> dict[str, Any]:
    """Return the exact body sent to POST /v1/decisions, with no credentials."""
    return {
        "model": ROUTER_MODEL,
        "input": [
            {"role": "developer", "content": ROUTING_INSTRUCTIONS},
            {
                "role": "user",
                "content": [{"type": "input_json", "data": task.model_dump()}],
            },
        ],
        "questions": deepcopy(ROUTING_QUESTIONS),
    }


def _validate_probability(value: Any) -> float:
    if (
        type(value) not in (int, float)
        or not 0 <= value <= 1
        or not math.isfinite(value)
    ):
        raise ValueError("invalid_probability")
    return float(value)


def _validate_named_answers(decision_response: Any) -> dict[str, dict[str, Any]]:
    """Require exactly one answer for each question, independent of order."""
    if not isinstance(decision_response, dict) or not isinstance(
        decision_response.get("answers"), list
    ):
        raise ValueError("invalid_response")
    answers_by_question: dict[str, dict[str, Any]] = {}
    for answer in decision_response["answers"]:
        if not isinstance(answer, dict):
            raise ValueError("invalid_answer")
        name = answer.get("name")
        if (
            not isinstance(name, str)
            or name not in ("workflow", "needs_strong_model")
            or name in answers_by_question
        ):
            raise ValueError("unexpected_or_duplicate_answer")
        answers_by_question[name] = answer
    if set(answers_by_question) != {"workflow", "needs_strong_model"}:
        raise ValueError("missing_answer")
    if any(answer.get("type") == "refusal" for answer in answers_by_question.values()):
        raise ValueError("decision_refused")
    return answers_by_question


def _validate_workflow_answer(workflow_answer: dict[str, Any]) -> tuple[str, float]:
    """Validate the complete distribution before trusting its selected choice."""
    selected_workflow = workflow_answer.get("choice")
    if (
        not isinstance(selected_workflow, str)
        or selected_workflow not in ALLOWED_WORKFLOWS
    ):
        raise ValueError("unknown_workflow")
    _validate_probability(
        workflow_answer.get("confidence")
    )  # Distinct from selected-option probability.
    options = workflow_answer.get("probabilities")
    if not isinstance(options, list):
        raise ValueError("invalid_distribution")
    probability_by_workflow: dict[str, float] = {}
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("invalid_distribution")
        name = option.get("value")
        if (
            not isinstance(name, str)
            or name not in ALLOWED_WORKFLOWS
            or name in probability_by_workflow
        ):
            raise ValueError("invalid_distribution")
        probability_by_workflow[name] = _validate_probability(option.get("probability"))
    if set(probability_by_workflow) != set(ALLOWED_WORKFLOWS) or not math.isclose(
        sum(probability_by_workflow.values()), 1, abs_tol=0.001
    ):
        raise ValueError("invalid_distribution")
    if (
        probability_by_workflow[selected_workflow]
        < max(probability_by_workflow.values()) - 1e-9
    ):
        raise ValueError("choice_distribution_mismatch")
    return selected_workflow, probability_by_workflow[selected_workflow]


def _validate_decision_response(decision_response: Any) -> tuple[str, float, float]:
    answers_by_question = _validate_named_answers(decision_response)
    workflow_answer, capability_answer = (
        answers_by_question["workflow"],
        answers_by_question["needs_strong_model"],
    )
    if (
        workflow_answer.get("type") != "choice"
        or capability_answer.get("type") != "predicate"
    ):
        raise ValueError("unexpected_answer_type")
    selected_workflow, selected_workflow_probability = _validate_workflow_answer(
        workflow_answer
    )
    return (
        selected_workflow,
        selected_workflow_probability,
        _validate_probability(capability_answer.get("probability")),
    )


def _build_stopped_plan(task: Task, plan_status: str, reason_code: str) -> Plan:
    needs_clarification = plan_status == "needs_clarification"
    return Plan(
        status=plan_status,
        workflow="clarify" if needs_clarification else None,
        worker_tier=None,
        steps=("clarify_requirements",) if needs_clarification else (),
        evidence_refs=tuple(item.ref for item in task.evidence),
        acceptance_criteria=task.acceptance_criteria,
        permitted_tools=(),
        limits=None,
        reason_codes=(reason_code,),
        checks=("execution_disabled",),
        clarification_items=task.missing_requirements
        or (
            ("Specify the intended outcome or missing requirements.",)
            if needs_clarification
            else ()
        ),
    )


def _select_worker_tier(
    strong_model_probability: float, policy: RoutingPolicy
) -> tuple[str, str]:
    if strong_model_probability <= policy.max_small_capability_probability:
        return "small", "low_strong_model_probability"
    if strong_model_probability >= policy.min_strong_capability_probability:
        return "strong", "high_strong_model_probability"
    return "strong", "uncertain_capability_conservative_strong"


def _build_ready_plan(
    task: Task,
    selected_workflow: str,
    worker_tier: str,
    worker_tier_reason: str,
    policy: RoutingPolicy,
) -> Plan:
    plan_warnings = ["thresholds_uncalibrated", "worker_limits_not_yet_enforced"]
    if not task.evidence:
        plan_warnings.append("no_code_evidence_supplied_inspect_before_work")
    return Plan(
        status="ready_for_review",
        workflow=selected_workflow,
        worker_tier=worker_tier,
        steps=WORKFLOW_STEPS[selected_workflow],
        evidence_refs=tuple(item.ref for item in task.evidence),
        acceptance_criteria=task.acceptance_criteria,
        permitted_tools=("read_file", "search")
        + (() if selected_workflow == "explain" else ("run_tests", "apply_patch")),
        limits=policy.small_limits if worker_tier == "small" else policy.strong_limits,
        reason_codes=(
            "workflow_probability_passed",
            worker_tier_reason,
            "template_selected_in_code",
        ),
        checks=(
            "named_answers_valid",
            "answer_types_valid",
            "workflow_allowed",
            "no_declared_missing_requirements",
            "workflow_probability_passed",
            "execution_disabled",
        ),
        warnings=tuple(plan_warnings),
    )


def assemble_plan(
    task: Task, response: Any, policy: RoutingPolicy | None = None
) -> Plan:
    """Validate named answers and apply local rules; never generate extra steps."""
    policy = policy or RoutingPolicy()
    if task.missing_requirements:
        return _build_stopped_plan(
            task, "needs_clarification", "declared_missing_requirements"
        )
    try:
        selected_workflow, selected_workflow_probability, strong_model_probability = (
            _validate_decision_response(response)
        )
    except ValueError as error:
        return _build_stopped_plan(task, "needs_review", str(error))
    if selected_workflow_probability < policy.min_workflow_probability:
        return _build_stopped_plan(task, "needs_review", "uncertain_workflow")
    if selected_workflow == "clarify":
        return _build_stopped_plan(
            task, "needs_clarification", "workflow_requires_clarification"
        )
    worker_tier, worker_tier_reason = _select_worker_tier(
        strong_model_probability, policy
    )
    return _build_ready_plan(
        task, selected_workflow, worker_tier, worker_tier_reason, policy
    )


def _resolve_sender(
    send: Callable[[dict[str, Any]], Any] | None, source: str
) -> Callable[[dict[str, Any]], Any]:
    if send is not None:
        return send
    if source != "live":
        raise ValueError(
            "simulated or replay routing requires an explicit response sender"
        )
    from .provider import send_decision

    return send_decision


def route(
    task: Task,
    *,
    send: Callable[[dict[str, Any]], Any] | None = None,
    policy: RoutingPolicy | None = None,
    source: str = "live",
) -> RoutingTrace:
    """Route once and retain the body, raw response, policy, and assembled plan."""
    policy = policy or RoutingPolicy()
    if source not in {"live", "simulated", "replay"}:
        raise ValueError("source must be live, simulated, or replay")
    request_body = build_request(task)
    started_at = perf_counter()
    if task.missing_requirements:
        return RoutingTrace(
            source="skipped",
            request_sent=False,
            request=request_body,
            response=None,
            plan=assemble_plan(task, None, policy),
            policy=policy,
            elapsed_ms=0,
        )
    send = _resolve_sender(send, source)
    try:
        decision_response = send(request_body)
    except DecisionCallError as error:
        return RoutingTrace(
            source=source,
            request_sent=error.request_sent,
            request=request_body,
            response=None,
            plan=_build_stopped_plan(task, "needs_review", "decision_call_failed"),
            policy=policy,
            elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
            error=str(error),
        )
    return RoutingTrace(
        source=source,
        request_sent=source == "live",
        request=request_body,
        response=decision_response,
        plan=assemble_plan(task, decision_response, policy),
        policy=policy,
        elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
    )
