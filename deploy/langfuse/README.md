# Local Langfuse

From the repository root:

```sh
cp deploy/langfuse/configuration.example deploy/langfuse/.env
chmod 600 deploy/langfuse/.env
# Set the blank secrets locally; generate secrets with: openssl rand -hex 32
docker compose --env-file deploy/langfuse/.env -f deploy/langfuse/compose.yaml config --quiet
docker compose --env-file deploy/langfuse/.env -f deploy/langfuse/compose.yaml up -d
```

Open http://localhost:3000. Create a project and put its API keys in the root
`.env` (`LANGFUSE_BASE_URL=http://localhost:3000`). Langfuse stores data in
persistent Docker volumes; keep the `.env` secrets and avoid `down -v`.
