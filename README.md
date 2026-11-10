# Internal Tools

Python 3.11+. Independent public modules:

| Module | Command | Purpose |
| --- | --- | --- |
| `internal_tools.model_router` | `internal-tools model-router` | Recommend models and budgets |
| `internal_tools.linear_next` | `internal-tools linear-next` | Build a reviewed Linear queue grouped by project |
| `internal_tools.router_observability` | `internal-tools model-router-monitor` | Audit routing outcomes and export metadata to Langfuse |

The [Linear queue guide](docs/linear-next/README.md) covers collection, local data,
the Python API, and the [offline demo](examples/linear_next/README.md).
Each module owns its interface and performs no work on import. The router details
below apply to `model_router`.

The [router monitoring guide](docs/router-observability/README.md) covers the
durable local ledger, usage and acceptance reports, and opt-in Langfuse export.

## Boundaries

| Component | Responsibility | Side effects |
| --- | --- | --- |
| `internal_tools` | Exports independent tool modules | None on import |
| `build_request` | Task → Decisions request body | None |
| `route` | Task → model recommendation, budgets, uncertainty + trace | At most one Decisions API call |
| `recommend` | Recorded answers + policy → recommendation | None |
| `replay` | Saved request + response → current recommendation | No API call |
| `build_dispatch` | Task + recommendation → explicit-model spawn arguments | None |
| `audit_routing` | Traces + receipts + local sessions → observed usage | Read-only file I/O |
| CLI | Read files, call module, print/save output | File I/O; API only in live mode |

**Router owns:** classification, model recommendation, advisory budgets, uncertainty, dispatch handoff, and usage audit.

**Calling session owns:** context gathering, planning, model dispatch, tools, edits, tests, and budget enforcement.

## Python API

```text
build_request(task: Task) -> dict
recommend(task: Task, response, policy=None) -> Recommendation
route(task: Task, *, send=None, policy=None, source="live") -> RoutingTrace
replay(saved_trace, *, task=None, policy=None) -> RoutingTrace
build_dispatch(task, recommendation, *, task_name, workspace) -> dict
audit_routing(trace_dir, receipts_dir=None, sessions_dir=None) -> dict
```

Data models: `Task`, `Evidence`, `Budgets`, `ModelRecommendation`, `Uncertainty`, `Recommendation`, `RoutingPolicy`, `RoutingTrace`. All are frozen Pydantic models. `send` injects a response callable. No executor is exported.

```python
from internal_tools import model_router

task = model_router.Task(request="Fix empty input raising ValueError.")
advice = model_router.route(task).recommendation
print(advice.model_dump_json(indent=2))
```

## CLI options

```sh
uv sync --locked --no-editable
uv run --no-editable internal-tools model-router MODE [OPTIONS]
```

Choose **one** mode:

| Mode | Task required? | API calls | Output |
| --- | --- | --- | --- |
| `--request-only` | `--request TEXT` or `--task FILE` | 0 | Request body JSON |
| `--live` | `--request TEXT` or `--task FILE` | 0–1 | Recommendation JSON |
| `--simulate RESPONSE.json` | `--request TEXT` or `--task FILE` | 0 | Recommendation JSON |
| `--replay TRACE.json` | Optional | 0 | Recommendation JSON |

| Option | Behavior |
| --- | --- |
| `--request TEXT` | Inline request. Mutually exclusive with `--task`. |
| `--task FILE` | Read Task JSON. With replay, require a match to the saved request. |
| `--trace` | Print full RoutingTrace JSON instead of Recommendation JSON. |
| `--json` | Compatibility alias for `--trace`. |
| `--trace-out FILE` | Save RoutingTrace JSON; ignored in request-only mode. |
| `--env-file FILE` | Live only; default `.env`. Existing environment values win. |
| `--dispatch-name NAME` | Print exact `collaboration.spawn_agent` kwargs; requires `--workspace`. Mutually exclusive with `--trace`. |
| `--workspace PATH` | Absolute existing worker directory; dispatch only. |
| `--help` | List arguments. |

Live uses `OPENAI_API_KEY`. Replay uses saved evidence and questions, with current policy. Stdout contains one JSON object; errors go to stderr.

```sh
uv run --no-editable internal-tools model-router --live --request "Fix parse_numbers('') raising ValueError; empty input should return []."
```

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

## Output schema: Recommendation

```text
Recommendation {
  status: "recommended" | "needs_clarification" | "needs_review"
  task_type: "explain" | "fix" | "implement" | "clarify" | null
  model: { tier: "small" | "strong", model_id: string } | null
  budgets: Budgets | null
  uncertainty: {
    task_type_probability: number | null
    task_type_confidence: number | null
    needs_strong_model_probability: number | null
    capability_uncertain: boolean | null
    calibrated: false
  }
  reason_codes: string[]
  clarification_items: string[]
}

Budgets {
  max_calls: positive integer
  max_total_tokens: positive integer
  max_input_tokens_per_call: positive integer
  max_output_tokens_per_call: positive integer
}
```

| Status | Model / budgets | Caller action |
| --- | --- | --- |
| `recommended` | Present | Decide whether to dispatch using this advice |
| `needs_clarification` | Null | Resolve missing requirements |
| `needs_review` | Null | Inspect uncertainty or invalid response |

Probabilities range from 0 to 1. Null means unavailable. The middle capability region selects strong conservatively with `capability_uncertain: true`. These signals do not verify task correctness.

## Output schema: RoutingTrace

```text
RoutingTrace {
  source: "live" | "simulated" | "replay" | "skipped"
  request_sent: boolean
  request: object              // exact Decisions request body
  response: any               // raw response; null when unavailable
  recommendation: Recommendation
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

Configure with `RoutingPolicy(...)` in Python. No CLI policy-file option. The API question named `workflow` supplies the output's `task_type`.

| Field | Default / rule |
| --- | --- |
| `version` | `"routing-2"` |
| `min_task_type_probability` | `0.75`; below → strong worker by default |
| `uncertain_task_type_action` | `"strong"`; `"review"` restores the strict gate |
| `max_small_probability` | `0.25`; at or below → small |
| `min_strong_probability` | `0.75`; at or above → strong |
| `small_model` | `"gpt-6-luna"` |
| `strong_model` | `"gpt-6.1-sol"` |
| Between capability thresholds | Strong, conservatively |
| Choice `confidence` | Retained; does not drive thresholds |
| `small_budgets`, `strong_budgets` | `Budgets` instances; defaults below |

| Tier | Calls | Total caller tokens | Input / call | Output / call |
| --- | ---: | ---: | ---: | ---: |
| Small | 2 | 8,000 | 6,000 | 2,000 |
| Strong | 4 | 24,000 | 12,000 | 4,000 |

Budgets are advisory, enforced by the caller, and exclude routing tokens. Model IDs are configurable; the calling session handles availability and dispatch.

## JSON Schema files

Generated from current models and the installed SDK:

[Task](schemas/model_router/task.schema.json) · [Recommendation](schemas/model_router/recommendation.schema.json) · [Budgets](schemas/model_router/budgets.schema.json) · [Uncertainty](schemas/model_router/uncertainty.schema.json) · [RoutingTrace](schemas/model_router/routing-trace.schema.json) · [RoutingPolicy](schemas/model_router/routing-policy.schema.json) · [Live Decisions response](schemas/model_router/decision-response.schema.json)

Regenerate: `uv run --no-editable python scripts/generate_model_router_schemas.py`. Add `--check` to detect drift. `RoutingTrace.response` preserves raw data; routing code validates the required answers.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Request printed, recommendation returned, or clarification required |
| `1` | File, task, or replay validation error; message on stderr |
| `2` | Routing needs review, or argparse rejected the command |

## Commands

[Examples](examples/model_router/README.md) · [Code checks](docs/code-quality.md) · [Diagram](docs/model-router/router-flow.html)

```sh
uv run --no-editable python -m unittest discover -s tests -v
uv run --no-editable ruff check .
```

After source edits: `uv sync --no-editable --reinstall-package internal-tools`.

Valid but uncertain workflow classifications now select the strong worker with an explicit reason code, including tentative `clarify` results. The worker should inspect context and resolve routine choices before asking the user. Confident clarification and caller-declared missing requirements still stop. Malformed responses, refusals, and transport failures remain review results; they are not silently converted to successful routing. Raw probabilities remain available and are uncalibrated.

## Dispatch and verify

The primary chat's model stays selected by its session. A recommendation only
changes execution when the caller dispatches a worker with the chosen model.
Full-history forks inherit the parent model. Dispatch output always supplies
`model` explicitly and `fork_turns: "none"` with a self-contained task handoff.

```sh
uv run --no-editable internal-tools model-router --live --task /repo/.local/routing/fix.task.json --trace-out /repo/.local/routing/fix.trace.json --dispatch-name fix_empty --workspace /repo
```

Pass the output object directly to `collaboration.spawn_agent`. This CLI prints
arguments; it cannot invoke the chat's collaboration tool itself. Unresolved
recommendations cannot produce dispatch arguments. Invalid targets fail before
the live routing call. Replay can generate a handoff without a new routing call.

After spawning, record the actual session UUID returned/observed by the harness
(not the task name or canonical agent path):

```sh
uv run --no-editable internal-tools model-router-receipt --trace-file /repo/.local/routing/fix.trace.json --agent-id ACTUAL_SESSION_UUID --requested-model gpt-6-luna --parent-agent-id PRIMARY_SESSION_UUID --out /repo/.local/receipts/fix.json
uv run --no-editable internal-tools model-router-audit --traces /repo/.local/routing --receipts /repo/.local/receipts --sessions /Users/USER/.codex/sessions --require-model-match
```

Receipts refuse model substitutions and overwrites. They record the requested
model; session logs independently verify observed models and cumulative usage.
Audit reports missing receipts/usage and mismatches explicitly, deduplicates
worker sessions, and separates coordinator usage. `--require-model-match` exits
2 when dispatches are missing, unverified, or mismatched. Each dispatch needs its
own trace path/receipt; rerouted retries should save a new trace. Replays do not incur fresh
routing usage. Session totals can include earlier work; reused sessions cannot
prove per-task consumption. Cached and uncached inputs are shown separately;
cached input remains part of the input total. Advisory budget overruns are
reported, with per-task status unknown for reused workers. No baseline means no
measured savings.

The coordinator decomposes work into small useful tasks with separate file
ownership and acceptance checks, routes each task, and delegates implementation.
Keep handoffs compact, batch trivial related edits, and avoid duplicating worker
work in the primary chat. Escalate failures with concrete evidence. Unknown model
availability is a dispatch failure, never permission to silently inherit.

Budgets remain advisory: collaboration tools cannot enforce hard token caps.
Count router overhead, workers, retries, and coordinator tokens; compare verified
outcomes against matched baselines before claiming savings. See AGENTS.md for the
working protocol. Bug: [CHA-64](https://linear.app/chaochaoqi/issue/CHA-64/model-router-recommendations-are-ignored-by-inherited-model-subagents).
