# Accounting evidence

`collect_report(trace_dir, receipts_dir=None, sessions_dir=None, *, outcomes=None,
prices=None)` returns schema version 1 with `tasks`, `sessions`, `summary` and
`alerts`. `write_snapshot(db_path, report)` returns a SHA-256 content ID. Repeated
imports of identical content produce one immutable SQLite snapshot. Delivery
acknowledgments live separately; `pending_snapshots` and `mark_exported` support
retries without modifying accounting history.

The collector builds on `audit_routing`. Only allowlisted numeric accounting,
model identifiers, policy versions, reason codes, receipt/session IDs and
filesystem modification timestamps leave the collector. Request, response,
message, output and path bodies are excluded. Identifiers allow bounded ASCII
letters, digits, `_`, `.`, `:` and `-`; unsupported identifiers become unknown.
Task IDs hash canonical trace contents plus worker session ID. Trace copies and
session copies are deduplicated by the underlying audit. Shared sessions appear
once in session summaries. Worker and coordinator token totals are cumulative
session observations, never task consumption. Cache inputs remain part of total
input tokens. Unknown usage stays null, never zero. `source_mtime_unix_seconds`
means observation-file modification time, not execution start or duration.

## Outcome input

First collect a report to obtain task IDs. Supply a JSON array such as:

```json
[{
  "task_id": "TASK_ID_FROM_REPORT",
  "status": "accepted",
  "retry_of": null,
  "validation": {
    "duration_ms": 1200,
    "checks": [{"name": "unit_tests", "passed": true}]
  },
  "attribution": [{
    "task_id": "TASK_ID_FROM_REPORT",
    "role": "worker",
    "model": "model-id",
    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    "cached_input_tokens": 30
  }]
}]
```

Statuses are `accepted`, `failed`, `rejected`. Every record needs nonempty named
boolean checks and finite nonnegative duration. Acceptance requires all checks
passed. Duplicate task IDs, unknown tasks, missing retry predecessors, cycles,
and retries of already accepted tasks are rejected. Set `retry_of` to the failed
or rejected predecessor's task ID and retain every attempt's validation record.
Unknown outcomes remain `{"status":"unknown"}`.

Attribution is explicit caller evidence, not independently proven from session
logs. Full cost remains unknown unless the accepted record supplies exactly one
attribution record for each role (`router`, `worker`, `coordinator`) for every
attempt in its retry chain. Each needs model, consistent token counts and known
cached-input count. This intentionally conservative schema cannot currently
price multiple models within a single role/attempt. Incomplete or ambiguous
attribution cannot become full accepted-task cost. The example's lone worker
record is valid partial evidence but insufficient for full cost.

## Price input

No current prices are guessed. Supply versioned rates per million tokens:

```json
{
  "version": "contract-2026-11",
  "currency": "USD",
  "models": {
    "model-id": {"input": 2.0, "cached_input": 0.5, "output": 8.0}
  }
}
```

All rates must be finite and nonnegative. Every attributed model must have all
three rates. Cost is `(uncached_input * input + cached_input * cached_input +
output_tokens * output) / 1000000`. Accepted-task cost includes failed attempts,
routing overhead and explicitly attributed coordinator work. Session cumulative
usage does not satisfy this requirement.

## Baseline safeguards

An outcome may add `baseline: {"cohort_id":"equivalent_work_v1",
"task_id":"BASELINE_TASK_ID", "equivalence_asserted":true}`. Both referenced
and current tasks must be present, accepted and fully priced using the same
currency and price version. The caller explicitly asserts equivalent work;
the collector cannot establish semantic equivalence itself. Only then does
`summary.baseline_comparison.comparisons` contain the paired `cost_difference`
(baseline minus current). Negative differences mean increased cost. No automatic
cross-task savings estimate or aggregate percentage is emitted. Without an
eligible pair, savings remains null and status unavailable.

Alerts include repeated strong-only recommendations, explicit uncertainty
fallbacks, missing accounting and advisory budget overruns. Budgets remain
advisory, and reused session totals cannot prove task budget compliance.
