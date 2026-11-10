# Router snapshots

```sh
uv run --no-editable internal-tools model-router-monitor --traces .local/routing --receipts .local/receipts --sessions /path/to/sessions
```

Default: metadata-only JSON on stdout, no network or local database.
Add `--send` to post an OTLP snapshot to `LANGFUSE_BASE_URL` (default
`http://localhost:3000`). Requires `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
`--env-file FILE` defaults to `.env`; existing environment values win.
HTTPS is required except for loopback. Redirects are refused.

Uses `model_router.audit.audit_routing`: routing usage is deduplicated;
worker/coordinator usage is deduplicated, cumulative session usage, not task cost.
Includes recommended/observed models, policy, reasons, uncertainty and missing data.
Prompts, messages, outputs and absolute paths are excluded.
Each view has one immutable `snapshot_id`; IDs are stable within that snapshot.
Timestamps label snapshot import time, not execution duration.
Repeated sends may duplicate ingestion; delivery is not idempotent or retried.
