# Langfuse on one host, ClickHouse Cloud analytics

This is a single-host Compose installation for CHA-147. PostgreSQL, authenticated
Redis and S3-compatible MinIO stay on the host; **ClickHouse is external only**.
It is suitable for a small internal pilot, not a highly available installation.
Use a Linux Docker host with durable storage; upstream recommends at least 4 CPU
cores and 16 GiB RAM. Compose v2 is required. `compose.yaml` uses JSON syntax,
which is valid YAML, so the offline checker needs only Python 3.11 stdlib.

## Source and image verification

Checked on 2026-11-09 against the [official Compose guide](https://langfuse.com/self-hosting/deployment/docker-compose),
[upstream Compose](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml),
[ClickHouse requirements](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse)
and [release v4.77.0](https://github.com/langfuse/langfuse/releases/tag/v4.77.0).
Web and worker use that same exact release. `images.lock.json` records all image
references and registries; registry manifest responses supplied every SHA256 pin.
Postgres 17.9 and Redis 7.4.8 use exact patch tags plus immutable digest pins.
MinIO uses the upstream-selected Chainguard distribution at an immutable digest
(the public image has no release version tag). Registry existence was verified;
the dependency images also passed a local Podman smoke test. These pins are a reproducible snapshot,
not a claim that these are the newest patches. Review security updates regularly.

## Prepare ClickHouse Cloud

Provision a dedicated service using the account with credits; never paste service
or cloud-console credentials into this repository. Confirm available credits,
expiry, billing owner and spend alerts in the Cloud console before provisioning.
Use the smallest suitable service, cap autoscaling, and consider idle suspension
where available. Credits are not a hard spending limit. Review daily usage during
the pilot, then weekly; agree a monetary ceiling and stop ingestion before funds
expire. This repository does not provision resources or enforce a cloud budget.

For Langfuse v4 use ClickHouse **25.12 or newer**, server/session timezone **UTC**.
Confirm `SELECT version(), timezone()` in the Cloud SQL console. Create a dedicated
`langfuse` database and service user. As the administrator, apply database-scoped
DDL/DML privileges and system-table reads listed in the current
[official user permissions section](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse#user-permissions).
That includes SELECT/INSERT, updates/deletes, schema and index migrations, and
system parts/mutations/tables/processes/query-log reads. Grant ALTER SETTINGS on
`langfuse.observations_pid_tid_sorting` for the v4 backfill. Do not grant SYSTEM
MERGES on Cloud. Follow upstream settings-profile guidance if enabling lightweight
updates. Use the administrator only for setup; retain the dedicated user's
credentials in the private configuration. Recheck grants at each version upgrade.

Set `CLICKHOUSE_HOST` to the service DNS name only; `CLICKHOUSE_DB=langfuse`.
Both containers use HTTPS 8443 and native migrations on TLS 9440 with
`CLICKHOUSE_MIGRATION_SSL=true`. `CLICKHOUSE_CLUSTER_ENABLED=false` intentionally
avoids ON CLUSTER for Cloud replicated databases; Cloud handles replication.
Allowlist the Docker host's fixed public **egress** IP (or use private networking).
Allow outbound DNS and TLS 8443/9440; never allow all source addresses as a shortcut.
Test both ports from the deployment host, not merely the operator's laptop.

## Configure and start

From the repository root:

```sh
cp deploy/langfuse/configuration.example deploy/langfuse/.env
chmod 600 deploy/langfuse/.env
# Edit .env locally; generate each hex secret separately: openssl rand -hex 32
python3 deploy/langfuse/check.py --env-file deploy/langfuse/.env
python3 deploy/langfuse/check.py --env-file deploy/langfuse/.env --docker-config
docker compose --env-file deploy/langfuse/.env -f deploy/langfuse/compose.yaml up -d
python3 deploy/langfuse/check.py --smoke
```

All empty secrets use Compose `${VAR:?message}` interpolation and fail before
startup. The offline checker also rejects missing values and invalid formats.
The supplied template intentionally fails. Use a simple alphanumeric MinIO user
and hex secrets (including PostgreSQL's URL-safe password); ENCRYPTION_KEY is
exactly 64 hex characters. Use an unquoted `KEY=value` file without shell syntax.
ClickHouse passwords must be Compose dotenv-safe: use an issued/generated value
without `$`, quotes or newlines. Keep credentials in a secret manager and back up
ENCRYPTION_KEY and SALT securely: changing/loss can make existing data unusable.
Do not paste expanded `docker compose config` output or `.env` into logs/tickets.
Environment exported in the shell overrides dotenv values during Compose runs;
clear conflicting variables first. The offline checker validates the file only.

`bucket-init` authenticates to healthy MinIO and idempotently creates the bucket
using `mc mb --ignore-existing`. Both app services wait for bootstrap completion and
healthy Postgres/Redis/MinIO. MinIO credentials are shared with both app services;
for a larger deployment use scoped S3 credentials and managed object storage.
Postgres, Redis and MinIO retain named volumes; Redis uses AOF and noeviction.
No database/cache ports are published. All published ports bind to loopback.

For a remote host, tunnel both UI and S3, then visit localhost:3000:

```sh
ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 operator@host
```

Create the first administrator over the private tunnel and create a project/API
key. Keep the deployment private during enrollment; configure signup restrictions
before allowing more users. The exporter uses project keys, not NEXTAUTH_SECRET.
Follow the [router exporter workflow](../../docs/router-observability/README.md)
for an explicit trace export. Verify the trace appears, worker queues drain, and
there are no authentication/migration errors. The HTTP health probe alone cannot
prove ingestion or query correctness. MinIO browser-presigned media URLs require
the same S3 endpoint to be reachable from the browser.

For a shared TLS hostname, put a TLS reverse proxy on the same host forwarding to
127.0.0.1:3000. Set NEXTAUTH_URL to the HTTPS UI origin and S3_PUBLIC_ENDPOINT to a
separate HTTPS origin forwarding to 127.0.0.1:9090; restart web/worker. Preserve
Host/forwarded headers, follow upstream proxy configuration, and configure OAuth
callbacks if used. Keep Compose ports on loopback, restrict ingress to the proxy,
and arrange certificate renewal. Do not expose MinIO console 9091 publicly.

## Retention, recovery and upgrades

Start with a short trace retention policy (for example 7 days if supported by the
installed edition); verify deletion actually completes. Otherwise schedule and
verify project trace deletion using supported Langfuse APIs. Do not assume Cloud
credits impose retention. Align S3 lifecycle rules with ingestion/retry windows
and exports; do not remove queued event objects before processing. Monitor host
disk, Redis memory/queue lag, ClickHouse storage/compute and failed migrations.
Pause the exporter when budgets or capacity limits approach.

Back up Postgres with `pg_dump` using the deployment database credentials; keep
encrypted off-host copies, the compose/configuration versions and secret-manager
references. Preserve MinIO objects with a tested S3 backup tool or a stopped-volume
snapshot. Cloud backups are separate: confirm service backup coverage, retention,
restore permissions and costs in ClickHouse Cloud. Redis AOF helps restart but is
not a substitute for replayable source traces; record the exporter checkpoint.
Never use `docker compose down -v` on retained data.

For a consistent recovery point pause exporters, drain worker queues, then stop
web/worker before coordinated Postgres/S3/Cloud snapshots. Test restore into an
isolated stack and a separate ClickHouse database/service: restore Postgres,
objects and ClickHouse to the matched recovery point, restore original secrets,
start dependencies then matched web/worker, and validate login plus a known trace.
Only resume exporter checkpoints after confirming duplicates/replay behavior.

Before upgrades read release/migration notes, verify new registry digests, update
both app images together and update the lock. Back up first; test migrations on
an isolated restore with the same Cloud version/grants. Run checks below and a
real export smoke test. Database migrations may prevent simple image rollback;
roll back by restoring the coordinated backup with the prior image/config pins.

## Validation limits

```sh
python3 deploy/langfuse/check.py
uv run python -m unittest discover -s tests -p test_langfuse_deployment.py
```

Offline checks verify pins, required configuration, Cloud endpoints and loopback
bindings. `--docker-config` additionally asks Compose to parse/interpolate without
starting services; `--smoke` probes only local web health. The Docker CLI was
unavailable, but a checksum-verified official standalone Compose v5.7.0 passed
`config --quiet` with synthetic credentials and a fake Cloud hostname. A local
Podman runtime test confirmed healthy Postgres, authenticated Redis (`PONG`),
healthy MinIO, successful authenticated bucket creation, and S3 bucket access.
The isolated test containers and their temporary volumes were removed afterward.
All six offline deployment tests passed. No Cloud account credentials
were supplied. Cloud connectivity/grants, backup restore, and end-to-end trace
ingestion remain unvalidated. Operators must complete those checks before
treating the deployment as ready.
