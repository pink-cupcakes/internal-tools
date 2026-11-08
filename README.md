# Internal Tools

Python 3.11+. Public import: `from internal_tools import model_router`.

## Boundaries

| Component | Responsibility | Side effects |
| --- | --- | --- |
| `internal_tools` | Exports independent tool modules | None on import |
| `build_request` | Task → Decisions request body | None |
| `route` | Request → answers → plan + trace | At most one live API call |
| `assemble_plan` | Answers + policy → fixed plan template | None |
| `replay` | Saved request + response → plan under current policy | No API call |
| CLI | Read files, call module, print/save output | File I/O; API only in live mode |

**Implemented:** routing and plan assembly. **Outside scope:** repository discovery, worker execution, code edits, test execution, budget enforcement.

## Python API

```text
build_request(task: Task) -> dict
assemble_plan(task: Task, response, policy=None) -> Plan
route(task: Task, *, send=None, policy=None, source="live") -> RoutingTrace
replay(saved_trace, *, task=None, policy=None) -> RoutingTrace
```

Exports: `Task`, `Evidence`, `Limits`, `RoutingPolicy`, `Plan`, `RoutingTrace`, `DecisionCallError`, and the functions above. Data models are frozen Pydantic models; construct with named arguments. `send` injects a response callable.

## CLI options

```sh
uv sync --locked --no-editable
uv run --no-editable internal-tools model-router MODE [OPTIONS]
```

Choose **one** mode:

| Mode | Task required? | API calls | Output |
| --- | --- | --- | --- |
| `--request-only` | `--task FILE` | 0 | Request body JSON |
| `--live` | `--task FILE` | 0–1 | RoutingTrace |
| `--simulate RESPONSE.json` | `--task FILE` | 0 | RoutingTrace, source `simulated` |
| `--replay TRACE.json` | Optional | 0 | RoutingTrace, source `replay` |

| Option | Behavior |
| --- | --- |
| `--task FILE` | Read Task JSON. With replay, require a match to the saved request. |
| `--json` | Print RoutingTrace JSON instead of labeled text sections. |
| `--trace-out FILE` | Save RoutingTrace JSON; ignored in request-only mode. |
| `--env-file FILE` | Live only; default `.env`. Existing environment values win. |
| `--help` | List arguments. |

Live uses `OPENAI_API_KEY`. Replay uses saved evidence and questions, with current policy.

## Input schema: Task

```text
Task {
  request: string
  evidence?: { ref: string, content: string }[] = []
  acceptance_criteria?: string[] = []
  missing_requirements?: string[] = []
}
```

Non-whitespace strings; unique evidence refs; unknown fields rejected.

## Output schema: Plan

Values below describe output emitted by application rules.

```text
Workflow = "explain" | "fix" | "implement" | "clarify"

Plan {
  status: "ready_for_review" | "needs_clarification" | "needs_review"
  workflow: Workflow | null
  worker_tier: "small" | "strong" | null
  steps: string[]
  evidence_refs: string[]
  acceptance_criteria: string[]
  permitted_tools: string[]
  limits: Limits | null
  reason_codes: string[]
  checks: string[]
  clarification_items: string[]
  warnings: string[]
  execution_enabled: false
}

Limits {
  max_worker_calls: positive integer
  max_worker_total_tokens: positive integer
  max_context_tokens: positive integer
  max_output_tokens: positive integer
}
```

| Status | Worker | Result |
| --- | --- | --- |
| `ready_for_review` | Small or strong tier | Proposed steps, tools, limits |
| `needs_clarification` | None | Clarification items |
| `needs_review` | None | Reason codes; empty execution steps |

## Output schema: RoutingTrace

```text
RoutingTrace {
  source: "live" | "simulated" | "replay" | "skipped"
  request_sent: boolean
  request: object              // exact Decisions request body
  response: any               // raw response; null when unavailable
  plan: Plan
  policy: RoutingPolicy
  elapsed_ms: number
  error: string | null
}
```

| Decisions answer | Fields consumed |
| --- | --- |
| Choice, name `workflow` | `choice`, `probabilities: {value, probability}[]`, `confidence` |
| Predicate, name `needs_strong_model` | `probability` |
| Refusal | `type: "refusal"`, `name` → review |

Live `response.usage` contains input/output/total tokens and cache/reasoning breakdowns. Simulations can omit usage. Replay retains original accounting; no new API usage.

## Policy options

Configure with `RoutingPolicy(...)` in Python. No CLI policy-file option.

| Field | Default / rule |
| --- | --- |
| `version` | `"initial-1"` |
| `min_workflow_probability` | `0.75`; below → review |
| `max_small_capability_probability` | `0.25`; at or below → small |
| `min_strong_capability_probability` | `0.75`; at or above → strong |
| Between capability thresholds | Strong, conservatively |
| Choice `confidence` | Retained; does not drive thresholds |
| `small_limits`, `strong_limits` | `Limits` instances; defaults below |

| Tier | Calls | Total worker tokens | Context / call | Output / call |
| --- | ---: | ---: | ---: | ---: |
| Small | 2 | 8,000 | 6,000 | 2,000 |
| Strong | 4 | 24,000 | 12,000 | 4,000 |

Thresholds are uncalibrated. Limits are proposed worker allowances; routing tokens are separate.

## JSON Schema files

Generated from current models and the installed SDK:

[Task](docs/model-router/schemas/task.schema.json) · [Plan](docs/model-router/schemas/plan.schema.json) · [RoutingTrace](docs/model-router/schemas/routing-trace.schema.json) · [RoutingPolicy](docs/model-router/schemas/routing-policy.schema.json) · [Live Decisions response](docs/model-router/schemas/decision-response.schema.json)

`RoutingTrace.response` is stored as `Any`; routing code validates answers. Status/workflow/tier are declared as strings in the models; their emitted values come from application rules.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Request printed, plan ready, or clarification required |
| `1` | File, task, or replay validation error; message on stderr |
| `2` | Plan needs review, or argparse rejected the command |

## Commands

[Examples](examples/model_router/README.md) · [Code checks](docs/code-quality.md) · [Diagram](docs/model-router/router-flow.html)

```sh
uv run --no-editable python -m unittest discover -s tests -v
uv run --no-editable ruff check .
```

After source edits: `uv sync --no-editable --reinstall-package internal-tools`.
