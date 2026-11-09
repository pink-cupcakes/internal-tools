"""Model routing advice; callers own tools, execution, and budget enforcement."""

from .core import DecisionCallError, build_request, recommend, route
from .dispatch import build_dispatch
from .audit import audit_routing
from .models import (
    Budgets,
    Evidence,
    ModelRecommendation,
    Recommendation,
    RoutingPolicy,
    RoutingTrace,
    Task,
    Uncertainty,
)
from .replay import replay

__all__ = [
    "DecisionCallError",
    "Evidence",
    "Budgets",
    "ModelRecommendation",
    "Recommendation",
    "RoutingPolicy",
    "RoutingTrace",
    "Task",
    "Uncertainty",
    "build_request",
    "build_dispatch",
    "audit_routing",
    "recommend",
    "replay",
    "route",
]
