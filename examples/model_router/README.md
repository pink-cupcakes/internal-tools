# Model router commands

Run from the repository root. [Options and schemas](../../README.md).

## Preview request

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --request-only
```

## Simulate

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --simulate examples/model_router/parser_bug.response.simulated.json --json
```

## Live

Set `OPENAI_API_KEY` in `.env` or the environment.

```sh
uv run --no-editable internal-tools model-router --task examples/model_router/parser_bug.task.json --live --trace-out docs/model-router/runs/parser-bug.live.json --json
```

## Replay saved run

```sh
uv run --no-editable internal-tools model-router --replay docs/model-router/runs/parser-bug.live.json --json
```

Uses the saved task and response. Optional `--task FILE` enforces an exact request match.

## Fixture

| File | Role |
| --- | --- |
| `parser_bug.task.json` | Self-contained task, code, tests, observed failure |
| `parser_bug.response.simulated.json` | Invented answers; no measured usage |
| `parser_bug/` | Intentionally broken parser; two empty-input errors expected |

The live trace is local and ignored by Git. The router returns a plan; the fixture remains unchanged.
