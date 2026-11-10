# Router observability

`model-router-monitor` reads local routing traces, actual dispatch receipts and Codex
session logs, writes an immutable SQLite snapshot, and prints one JSON object.
**Filter every dashboard/query to ONE `snapshot_id` before counting decisions or
summing usage.** Each export is an immutable audit revision, not a stream of new
tasks; use the latest successful CLI `snapshot_id` for current-state dashboards.

Collection performs no model calls or network requests. Only `--send` enables
Langfuse delivery, after local persistence succeeds.

```sh
uv sync --locked --no-editable
uv run --no-editable internal-tools model-router-monitor \
  --traces .local/routing --receipts .local/receipts \
  --sessions "$HOME/.codex/sessions" \
  --ledger .router-observability/ledger.sqlite3
```

`--outcomes outcomes.json` and `--prices prices.json` are optional explicit evidence.
Without them, outcomes and per-accepted-task costs remain unknown. Stdout contains
`snapshot_id`, `report` and `delivery`; exit 0 means successful local collection or
fully acknowledged export, and exit 1 means collection/persistence/delivery failure.
A failed delivery leaves its immutable snapshot pending. Errors omit credentials,
server bodies and local paths. Do not treat a successful export as task acceptance.

The [offline fixture](../../examples/router_observability/offline.py) creates a
simulated routing trace and a clearly labelled fixture receipt in a temporary
directory. It makes no model calls and deletes its fixture database on exit:

```sh
uv run --no-editable python examples/router_observability/offline.py
```

## Langfuse delivery and backfill

Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in the
environment, or supply `--env-file PATH`. Existing environment variables win;
env files are read only with `--send`. The default base URL is
`https://cloud.langfuse.com`. Use the appropriate region or self-hosted origin,
without a path, credentials, query or fragment. HTTPS is required except for
literal loopback IP addresses and `localhost`. Redirects are refused.

```sh
uv run --no-editable internal-tools model-router-monitor \
  --traces .local/routing --receipts .local/receipts \
  --sessions "$HOME/.codex/sessions" --send --env-file .env.langfuse
```

This sends **all pending snapshots in that ledger**, including historical snapshots,
oldest first. Delivery stops on the first failure. Rerun the same command to retry;
no automatic retries or background sending occur. Full HTTP 200 acknowledgement
is required; rejected spans, error messages, invalid JSON, HTTP failures and timeouts
leave the snapshot pending. The request timeout is 10 seconds, capped at 30 seconds
in the Python transport API. Avoid concurrent senders sharing the same ledger.
Acknowledgement is local to the ledger, so use separate ledgers for separate targets.
Use a new `--ledger` path to re-export to another Langfuse endpoint or project;
switching credentials alone will not resend acknowledged snapshots.

Transport uses OTLP/HTTP JSON at `/api/public/otel/v1/traces`, Basic authentication,
and `x-langfuse-ingestion-version: 4`, following the official
[Langfuse OTEL integration](https://langfuse.com/integrations/native/opentelemetry)
and [OTLP specification](https://opentelemetry.io/docs/specs/otlp/).
There are no Langfuse SDK or collector dependencies. The repository's self-hosted
infrastructure instructions cover deployment; this command also works with Cloud.

## Interpreting the data

A report has `schema_version`, `tasks`, `sessions`, `summary` and `alerts`.
Task rows include recommendation and observed models, reason codes, uncertainty,
policy version, routing/worker usage, attribution, outcome, costs and metadata.
Session rows keep deduplicated cumulative worker/coordinator usage separate.
Reused sessions cannot establish per-task consumption. Router usage deduplication,
cache counts and advisory budget status remain explicit. An unknown token/cost
value differs from measured zero. No matched baseline means no savings claim.

Each task and each session becomes its own trace with one ordinary span. An
additional `kind=snapshot` observation carries `summary` counters and `alerts`
for that revision; filter to it for fallback/strong-model rates and alert review.
`task_count`, `accepted_count`, `strong_count`, `uncertainty_fallback_count` and
`uncertainty_fallback_rate` are also numeric metadata on that snapshot observation. Cumulative
sessions are never task generation spans. Models are metadata only: Langfuse model
pricing inference is deliberately not enabled. Local `costs.status` and
`costs.full_cost_per_accepted_task` are authoritative accounting fields, not a
provider billing claim. All spans carry common `schema_version`, `kind` and
`snapshot_id` metadata. No raw request, response, prompt, tool output, receipt path
or session body is exported.

Trace and span IDs are deterministic hashes of snapshot identity, row identity and
kind. Replaying an identical persisted snapshot produces identical IDs/timestamps.
Remote delivery is **not idempotent**: Langfuse v4 treats observations as immutable
and does not reliably deduplicate repeated IDs. A timeout after server ingestion
or a lost acknowledgement can cause duplicates on retry. Inspect uncertain
delivery before resending if duplicate-free remote counts are essential. The local
ledger prevents resending fully acknowledged snapshots; it cannot guarantee
exactly-once remote delivery. See [Langfuse data update semantics](https://langfuse.com/faq/all/tracing-data-updates).

New usage or outcomes produce a new snapshot and observations; do not sum multiple
snapshots as independent work. Filter a dashboard to one `snapshot_id` first.
Task timestamps use routing trace file mtime; session timestamps use source session
file mtime when available. `timestamp_source` identifies the source; these are
capture times, not model event times or execution durations. Explicit outcome evidence
is exported separately as `validation_duration_ms`, `validation_checks` (safe check
names and boolean results) and hashed `retry_of` lineage. `duration_unavailable`
is false only for a finite, nonnegative supplied duration, including measured zero;
the span itself remains a one-nanosecond capture marker. Truly unavailable
timestamps use the fixed 2000-01-01 sentinel and `timestamp_unavailable=true`;
include that date in the UI time window to inspect them.

In Langfuse's Observations view, filter metadata `kind=task`, `policy_version`,
`recommended_model`, `model_match`, `outcome_status`, `advisory_budget_status` and
`reused_session`. Use `kind=session` and `role` to inspect cumulative usage without
mixing it with task attribution. Reason codes and uncertainty values are uncalibrated
routing signals; monitor fallback rates by policy and outcome rather than treating
probabilities as measured correctness. Start alert review from the local `alerts`
array: missing/mismatched dispatches, unknown usage, uncertainty fallback, strong
model share and budget overruns require investigation. Alerts are advisory and
never enforce budgets or prove quality. Compare accepted-task costs only within
explicitly equivalent cohorts with complete attribution, including retries,
router overhead and coordinator work.

## Explicit outcomes and prices

`--outcomes` accepts a JSON list. Each record requires `task_id`, `status` (accepted,
failed or rejected), `validation` with nonempty `checks` (`name`, boolean `passed`)
and nonnegative `duration_ms`. Accepted records require all checks passed.
Optional `retry_of` points to the preceding task; `attribution` lists explicit
`task_id`, `role` (router, worker or coordinator), `model`, `usage` (input, output,
total tokens) and `cached_input_tokens`. For known full cost, attribution must
cover all three roles for every attempt exactly once. Cumulative session totals
are never substituted for this evidence. Duplicate outcomes and malformed evidence
are rejected.

`--prices` accepts `{ "version": "rates-v1", "currency": "USD", "models": {
"model-id": { "input": 1, "cached_input": 0.1, "output": 2 } } }` with explicit
nonnegative per-million-token rates. Missing model prices, incomplete retry lineage
or missing attribution leave costs unknown. Optional outcome `baseline` evidence
(`cohort_id`, `task_id`, `equivalence_asserted: true`) supports a comparison only
when both accepted tasks have full known costs under matching currency/prices;
caller-asserted equivalence is not independently verified. Do not assume current
pricing from the example numbers.

The Python API is `collect_report(trace_dir, receipts_dir=None, sessions_dir=None,
*, outcomes=None, prices=None)` and `write_snapshot(db_path, report)` from
`internal_tools.router_observability`. SQLite snapshots are content addressed and
immutable; delivery acknowledgements live in a separate table. Protect the local
database and credentials using your normal workstation access controls.

The current exporter sends one request per snapshot and buffers each report in
memory. It is intended for local router audits, not unbounded log streaming. No
live Langfuse integration is required for unit tests; verify a small explicit
`--send` against your deployment before relying on dashboards.
