# Model router commands

Run from the repository root. [Options and schemas](../../README.md).

Default stdout: **Recommendation JSON only**. Add `--trace` for full request/response/recommendation JSON (`--json` is an alias). `--trace-out FILE` saves the full trace while stdout stays recommendation-only.

## Inline request

```sh
uv run --no-editable internal-tools model-router --live --request "Fix parse_numbers('') raising ValueError; empty input should return []."
```

## Preview request

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --request-only
```

## Simulate

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --simulate examples/model_router/parser_bug.response.simulated.json
```

## Live

Set `OPENAI_API_KEY` in `.env` or the environment.

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --live --trace-out docs/model-router/runs/parser-bug.live.json
```

## Replay saved run

```sh
uv run --no-editable internal-tools model-router --replay docs/model-router/runs/parser-bug.live.json
```

Uses the saved task and response. Optional `--task FILE` enforces an exact request match.

## Fixture

| File | Role |
| --- | --- |
| `parser_bug.task.json` | Self-contained task, code, tests, observed failure |
| `parser_bug.response.simulated.json` | Invented answers; no measured usage |
| `parser_bug/` | Intentionally broken parser; two empty-input errors expected |

The live trace is local and ignored by Git. The router returns resource advice; the fixture remains unchanged.

## Routing-label experiments

```sh
uv run --no-editable python examples/model_router/evals.py --out /tmp/router-evals.json
uv run --no-editable python examples/model_router/evals.py --baseline /tmp/router-evals.json
```

Uses `.env` credentials to compare current, always-strong and eval-only candidate
policies on eight versioned cases; publishes native Langfuse Experiments.
One paid router call per actionable case; comparisons reuse its response.
Scores: label match, over-escalation and missed escalation (all-case denominators).
Baseline comparison requires identical cases. Labels are hypotheses, not worker
quality or savings measurements. Production defaults stay unchanged.
