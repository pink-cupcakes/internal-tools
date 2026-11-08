"""Public inputs, configurable policy, and inspectable routing outputs."""

from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    InstanceOf,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
PositiveInt = Annotated[int, Field(gt=0)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class RouterModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", validate_default=True
    )


class Evidence(RouterModel):
    ref: NonEmptyText
    content: NonEmptyText


class Task(RouterModel):
    request: NonEmptyText
    evidence: tuple[Evidence, ...] = ()
    acceptance_criteria: tuple[NonEmptyText, ...] = ()
    missing_requirements: tuple[NonEmptyText, ...] = ()

    @field_validator(
        "evidence", "acceptance_criteria", "missing_requirements", mode="before"
    )
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("evidence")
    @classmethod
    def validate_unique_refs(
        cls, evidence: tuple[Evidence, ...]
    ) -> tuple[Evidence, ...]:
        if len({item.ref for item in evidence}) != len(evidence):
            raise ValueError("evidence references must be unique")
        return evidence

    @classmethod
    def from_dict(cls, task_data: dict[str, Any]) -> "Task":
        return cls.model_validate(task_data)


class Limits(RouterModel):
    """Proposed worker limits; a future executor must enforce them."""

    max_worker_calls: PositiveInt = 2
    max_worker_total_tokens: PositiveInt = 8000
    max_context_tokens: PositiveInt = 6000
    max_output_tokens: PositiveInt = 2000

    @model_validator(mode="after")
    def validate_token_budget(self) -> Self:
        if (
            self.max_context_tokens + self.max_output_tokens
            > self.max_worker_total_tokens
        ):
            raise ValueError(
                "one full worker call must fit within the total token limit"
            )
        return self


class RoutingPolicy(RouterModel):
    """Starting thresholds are uncalibrated; version changes with policy changes."""

    version: NonEmptyText = "initial-1"
    min_workflow_probability: Probability = 0.75
    max_small_capability_probability: Probability = 0.25
    min_strong_capability_probability: Probability = 0.75
    small_limits: InstanceOf[Limits] = Field(default_factory=Limits)
    strong_limits: InstanceOf[Limits] = Field(
        default_factory=lambda: Limits(
            max_worker_calls=4,
            max_worker_total_tokens=24000,
            max_context_tokens=12000,
            max_output_tokens=4000,
        )
    )

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Self:
        if (
            self.max_small_capability_probability
            >= self.min_strong_capability_probability
        ):
            raise ValueError("small and strong capability thresholds must be ordered")
        return self


class Plan(RouterModel):
    status: str
    workflow: str | None
    worker_tier: str | None
    steps: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    permitted_tools: tuple[str, ...]
    limits: Limits | None
    reason_codes: tuple[str, ...]
    checks: tuple[str, ...]
    clarification_items: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    execution_enabled: bool = False


class RoutingTrace(RouterModel):
    source: str
    request_sent: bool
    request: dict[str, Any]
    response: Any
    plan: Plan
    policy: RoutingPolicy
    elapsed_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
