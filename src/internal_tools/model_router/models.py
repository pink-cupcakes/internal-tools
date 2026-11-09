"""Public inputs, configurable policy, and inspectable routing outputs."""

from typing import Annotated, Any, Literal, Self

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


class Budgets(RouterModel):
    """Advisory allowances for the calling session, excluding routing usage."""

    max_calls: PositiveInt = 2
    max_total_tokens: PositiveInt = 8000
    max_input_tokens_per_call: PositiveInt = 6000
    max_output_tokens_per_call: PositiveInt = 2000

    @model_validator(mode="after")
    def validate_token_budget(self) -> Self:
        if (
            self.max_input_tokens_per_call + self.max_output_tokens_per_call
            > self.max_total_tokens
        ):
            raise ValueError(
                "one full model call must fit within the total token budget"
            )
        return self


class RoutingPolicy(RouterModel):
    """Starting thresholds are uncalibrated; version changes with policy changes."""

    version: NonEmptyText = "routing-2"
    min_task_type_probability: Probability = 0.75
    uncertain_task_type_action: Literal["strong", "review"] = "strong"
    max_small_probability: Probability = 0.25
    min_strong_probability: Probability = 0.75
    small_model: NonEmptyText = "gpt-6-luna"
    strong_model: NonEmptyText = "gpt-6.1-sol"
    small_budgets: InstanceOf[Budgets] = Field(default_factory=Budgets)
    strong_budgets: InstanceOf[Budgets] = Field(
        default_factory=lambda: Budgets(
            max_calls=4,
            max_total_tokens=24000,
            max_input_tokens_per_call=12000,
            max_output_tokens_per_call=4000,
        )
    )

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Self:
        if self.max_small_probability >= self.min_strong_probability:
            raise ValueError("small and strong capability thresholds must be ordered")
        return self


class ModelRecommendation(RouterModel):
    tier: Literal["small", "strong"]
    model_id: NonEmptyText


class Uncertainty(RouterModel):
    task_type_probability: Probability | None = None
    task_type_confidence: Probability | None = None
    needs_strong_model_probability: Probability | None = None
    capability_uncertain: bool | None = None
    calibrated: Literal[False] = False


class Recommendation(RouterModel):
    status: Literal["recommended", "needs_clarification", "needs_review"]
    task_type: Literal["explain", "fix", "implement", "clarify"] | None
    model: ModelRecommendation | None
    budgets: Budgets | None
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    reason_codes: tuple[str, ...]
    clarification_items: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        if self.status == "recommended":
            if self.model is None or self.budgets is None:
                raise ValueError("recommended status requires model and budgets")
            if self.task_type not in ("explain", "fix", "implement", "clarify"):
                raise ValueError("recommended status requires an actionable task type")
            if self.task_type == "clarify" and self.model.tier != "strong":
                raise ValueError("uncertain clarification requires a strong model")
        elif self.model is not None or self.budgets is not None:
            raise ValueError("stopped recommendations cannot select model or budgets")
        return self


class RoutingTrace(RouterModel):
    source: Literal["live", "simulated", "replay", "skipped"]
    request_sent: bool
    request: dict[str, Any]
    response: Any
    recommendation: Recommendation
    policy: RoutingPolicy
    elapsed_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
