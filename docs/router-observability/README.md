# Router snapshots
```sh
uv run --no-editable internal-tools model-router-monitor --traces .local/routing --receipts .local/receipts --sessions /path/to/sessions
```
Default: metadata JSON on stdout; no SDK initialization, environment-file reads or network.
Add `--send` for one `router.snapshot` observation through the official Langfuse SDK.
Requires `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`; `LANGFUSE_BASE_URL` defaults to `http://localhost:3000`.
`--env-file FILE` defaults to `.env`; existing environment values win.
Reuses routing audit deduplication; worker/coordinator totals are cumulative session usage, not task cost.
Includes models, reasons, policy and uncertainty; excludes prompts, messages, outputs and absolute paths.
Each collection gets a snapshot ID; resending may duplicate observations.
SDK handles auth, OTLP and retries; shutdown drains pending spans best-effort, without delivery acknowledgement or exactly-once guarantees.
