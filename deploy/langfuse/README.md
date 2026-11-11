# Langfuse with ClickHouse Cloud

Use ClickHouse Cloud **Basic Mini 8 GiB**, **25.12+**, UTC, and a dedicated database/user with the
[required grants](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse#user-permissions),
and allowlist the Docker host's egress IP. Allow outbound TLS on 8443 and 9440.

From the repository root:

```sh
cp deploy/langfuse/configuration.example deploy/langfuse/.env
chmod 600 deploy/langfuse/.env
# Fill .env locally; generate each secret separately: openssl rand -hex 32
# ENCRYPTION_KEY needs 64 hex characters; use a hex PostgreSQL password.
docker compose --env-file deploy/langfuse/.env -f deploy/langfuse/compose.yaml config --quiet
docker compose --env-file deploy/langfuse/.env -f deploy/langfuse/compose.yaml up -d
```

Open http://localhost:3000 and create a project and API keys. In the root
`.env`, set `LANGFUSE_BASE_URL=http://localhost:3000`, `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` to those project keys.
For a remote host, use `ssh -N -L 3000:127.0.0.1:3000 operator@host`.
Data uses persistent volumes; preserve the configured secrets and avoid `down -v`.
