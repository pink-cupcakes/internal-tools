# Offline Linear queue demo

All issues, projects, workspace IDs, ownership keys, and evidence in this
directory are synthetic. No Linear or model API calls are made.

From the repository root:

```sh
uv sync --locked --no-editable
uv run --no-editable python examples/linear_next/demo.py
```

Open `.linear-next/demo/widget.html`. It contains two example projects with
independent ready tasks, an active producer, and a blocked consumer. The generator
sets a fresh demo timestamp and matching review digest. The committed fixed-date
fixtures intentionally age out if used directly; the planner fails closed.

Inspect or rebuild the generated demo:

```sh
uv run --no-editable internal-tools linear-next status --directory .linear-next/demo
uv run --no-editable internal-tools linear-next build --directory .linear-next/demo
```

For real collection, follow the packaged `REFRESH.md` and the
[module guide](../../docs/linear-next/README.md). Do not reuse the demo workspace
ID or synthetic evidence as live configuration.
