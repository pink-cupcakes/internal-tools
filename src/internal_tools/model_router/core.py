"""Bounded task classification, model recommendations, and advisory budgets."""

from copy import deepcopy
import math
from time import perf_counter
from typing import Any, Callable

from .models import (
    ModelRecommendation,
    Recommendation,
    RoutingPolicy,
    RoutingTrace,
    Task,
    Uncertainty,
)
from .questions import ROUTING_INSTRUCTIONS, ROUTING_QUESTIONS
from .telemetry import trace_live_route

ROUTER_MODEL = "gpt-6-luna"
ALLOWED_WORKFLOWS = ("explain", "fix", "implement", "clarify")


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


def _validate_workflow_answer(
    workflow_answer: dict[str, Any],
) -> tuple[str, float, float]:
    """Validate the complete distribution before trusting its selected choice."""
    selected_workflow = workflow_answer.get("choice")
    if (
        not isinstance(selected_workflow, str)
        or selected_workflow not in ALLOWED_WORKFLOWS
    ):
        raise ValueError("unknown_workflow")
    confidence = _validate_probability(workflow_answer.get("confidence"))
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
    return selected_workflow, probability_by_workflow[selected_workflow], confidence


def _validate_decision_response(
    decision_response: Any,
) -> tuple[str, float, float, float]:
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
    task_type, probability, confidence = _validate_workflow_answer(workflow_answer)
    return (
        task_type,
        probability,
        confidence,
        _validate_probability(capability_answer.get("probability")),
    )


def _stop(
    status: str,
    reason: str,
    *,
    task_type: str | None = None,
    uncertainty: Uncertainty | None = None,
    clarification_items: tuple[str, ...] = (),
) -> Recommendation:
    return Recommendation(
        status=status,
        task_type=task_type,
        model=None,
        budgets=None,
        uncertainty=uncertainty or Uncertainty(),
        reason_codes=(reason,),
        clarification_items=clarification_items,
    )


def _select_tier(
    strong_model_probability: float, policy: RoutingPolicy
) -> tuple[str, str]:
    if strong_model_probability <= policy.max_small_probability:
        return "small", "low_strong_model_probability"
    if strong_model_probability >= policy.min_strong_probability:
        return "strong", "high_strong_model_probability"
    return "strong", "uncertain_capability_conservative_strong"


def _uncertainty(
    probability: float,
    confidence: float,
    strong_probability: float,
    policy: RoutingPolicy,
) -> Uncertainty:
    return Uncertainty(
        task_type_probability=probability,
        task_type_confidence=confidence,
        needs_strong_model_probability=strong_probability,
        capability_uncertain=(
            policy.max_small_probability
            < strong_probability
            < policy.min_strong_probability
        ),
    )


def recommend(
    task: Task, response: Any, policy: RoutingPolicy | None = None
) -> Recommendation:
    """Return resource advice; execution and budget enforcement belong to caller."""
    policy = policy or RoutingPolicy()
    if task.missing_requirements:
        return _stop(
            "needs_clarification",
            "declared_missing_requirements",
            task_type="clarify",
            clarification_items=task.missing_requirements,
        )
    try:
        task_type, probability, confidence, strong_probability = (
            _validate_decision_response(response)
        )
    except ValueError as error:
        return _stop("needs_review", str(error))
    uncertainty = _uncertainty(probability, confidence, strong_probability, policy)
    if probability < policy.min_task_type_probability:
        return _uncertain_workflow(task_type, uncertainty, policy)
    if task_type == "clarify":
        return _stop(
            "needs_clarification",
            "task_requires_clarification",
            task_type=task_type,
            uncertainty=uncertainty,
            clarification_items=(
                "Specify the intended outcome or missing requirements.",
            ),
        )
    tier, reason = _select_tier(strong_probability, policy)
    return Recommendation(
        status="recommended",
        task_type=task_type,
        model=ModelRecommendation(
            tier=tier,
            model_id=policy.small_model if tier == "small" else policy.strong_model,
        ),
        budgets=policy.small_budgets if tier == "small" else policy.strong_budgets,
        uncertainty=uncertainty,
        reason_codes=("task_type_probability_passed", reason),
    )


def _uncertain_workflow(
    task_type: str, uncertainty: Uncertainty, policy: RoutingPolicy
) -> Recommendation:
    """Escalate valid but ambiguous classification to a worker, not a human."""
    if policy.uncertain_task_type_action == "review":
        return _stop(
            "needs_review",
            "uncertain_task_type",
            task_type=task_type,
            uncertainty=uncertainty,
        )
    return Recommendation(
        status="recommended",
        task_type=task_type,
        model=ModelRecommendation(tier="strong", model_id=policy.strong_model),
        budgets=policy.strong_budgets,
        uncertainty=uncertainty,
        reason_codes=("uncertain_task_type_conservative_strong",),
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


@trace_live_route
def route(
    task: Task,
    *,
    send: Callable[[dict[str, Any]], Any] | None = None,
    policy: RoutingPolicy | None = None,
    source: str = "live",
) -> RoutingTrace:
    """Route once and retain the raw response, policy, and recommendation."""
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
            recommendation=recommend(task, None, policy),
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
            recommendation=_stop("needs_review", "decision_call_failed"),
            policy=policy,
            elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
            error=str(error),
        )
    return RoutingTrace(
        source=source,
        request_sent=source == "live",
        request=request_body,
        response=decision_response,
        recommendation=recommend(task, decision_response, policy),
        policy=policy,
        elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
    )
