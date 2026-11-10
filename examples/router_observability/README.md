# Offline router monitoring example

From the repository root, run:

```sh
uv run --no-editable python examples/router_observability/offline.py
```

This creates a simulated trace and fixture receipt in a temporary directory, prints
a durable-before-output monitoring report, and cleans up. It makes no network or
model calls. Missing real session usage and acceptance evidence remain unknown.
See the [monitoring guide](../../docs/router-observability/README.md) for persistent
ledgers, explicit outcome attribution and opt-in Langfuse export.
