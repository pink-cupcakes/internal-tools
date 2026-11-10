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
uv run --no-editable internal-tools model-router-monitor --traces PATH
```

The router recommends models; callers dispatch them. Use `--help` for options.

[Export traces](docs/router-observability/README.md) ·
[Linear queue](docs/linear-next/README.md)
