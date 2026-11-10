# Internal tools

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --no-editable
cp .env.example .env
```

Fill `.env`, then run:

```sh
uv run --no-editable internal-tools model-router --live --request "Describe a task"
uv run --no-editable internal-tools linear-next --help
```

The router recommends models; callers dispatch them. Use `--help` for options.
Live calls trace automatically with `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
`LANGFUSE_BASE_URL` defaults to `http://localhost:3000`; tracing is metadata only.

[Self-host Langfuse](deploy/langfuse/README.md) ·
[Linear queue](docs/linear-next/README.md)
