# Linear next-work queue

An independent module that plans from collected Linear evidence and an independent
review, then builds a local HTML report grouped by project. It does not call
Linear, launch work, or change tickets. The calling session uses the connected
Linear plugin to collect evidence; no additional API key is required.

Python 3.11+ on macOS or Linux; publishing uses a POSIX file lock. No additional
dependencies beyond the existing package. Importing the module performs no I/O.

## Commands

Install from the repository with `uv sync --locked --no-editable`. Commands use
`.linear-next` under the current working directory unless `--directory` is set.
Keep live evidence outside version control; `.linear-next/` is ignored.

```sh
uv run --no-editable internal-tools linear-next digest --snapshot PATH/snapshot.json
uv run --no-editable internal-tools linear-next build --directory PATH
uv run --no-editable internal-tools linear-next status --directory PATH
```

`build` reads `snapshot.json` and `review.json` from that directory by default.
Override inputs with `--snapshot FILE --review FILE`. Set `--limit 1..10` for a
batch size (default 3). `--inline FILE` also writes an HTML fragment. The module
entry point is `python -m internal_tools.linear_next` with the same arguments.
Successful commands exit 0, input/publication errors 1, and argument errors 2.

Open `PATH/widget.html` to view the report. Summary counts cover the full
workspace; Next batch, In flight, and Held show separate project sections.
Dependency review retains cross-project relationships. The file reloads every
minute, but only the calling session can refresh Linear evidence. Buttons request
that session's review when the host supports follow-up messages; other browsers
explain how to request a refresh.

## Python API

```python
from internal_tools import linear_next

review["snapshotDigest"] = linear_next.digest(snapshot)
report = linear_next.plan(snapshot, review, limit=3)
html_fragment = linear_next.render(report)
linear_next.publish(report, ".linear-next")
```

`plan` and `render` are pure with respect to external services. `build` reads
input files, plans, and publishes. `publish` uses an exclusive lock, atomic file
replacement for each output, and a monotonic snapshot-time guard. The outputs
are not a multi-file transaction; the lock only coordinates report publishers.

Review digests must match the full snapshot. Missing required coverage, stale
evidence, unknown prerequisites, possible duplicates, ownership conflicts, and
overlapping active work hold recommendations. Parent tracking tickets, backlog,
owned tickets, and active work are excluded from the next batch. Canceled
prerequisites do not count as delivered. Reviewed resource keys establish
separate ownership in a batch; title similarity only holds work for review.
Every start still requires a fresh check: this module is not a work-claim service.

## Collection and scheduling

The packaged [REFRESH.md](../../src/internal_tools/linear_next/REFRESH.md) defines
the collection and review protocol. Configure the expected workspace ID and
project/repository mappings locally before collecting. The Python CLI builds
from already collected evidence; it never treats a file reload as a network scan.

Scheduling is separate from the module. An existing caller's hourly automation
can continue using its current data directory. Installing this module does not
create, migrate, or modify that automation. See the
[synthetic example](../../examples/linear_next/README.md) for an offline demo.
