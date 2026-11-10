#!/usr/bin/env python3
"""Offline contract checks and optional live HTTP smoke check; no dependencies."""

import argparse
import json
import re
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
REQUIRED = re.compile(r"\$\{([A-Z0-9_]+):\?[^}]+\}")


def load_configuration(path: Path) -> dict[str, str]:
    """Read the deliberately restricted KEY=value template without evaluation."""
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z0-9_]+", key):
            raise ValueError("configuration must use unquoted KEY=value lines")
        if key in values:
            raise ValueError(f"duplicate configuration key: {key}")
        values[key] = value
    return values


def validate_values(values: dict[str, str], text: str) -> None:
    missing = sorted(key for key in set(REQUIRED.findall(text)) if not values.get(key))
    if missing:
        raise ValueError("missing required configuration: " + ", ".join(missing))
    for key in (
        "NEXTAUTH_SECRET",
        "SALT",
        "POSTGRES_PASSWORD",
        "REDIS_AUTH",
        "MINIO_ROOT_PASSWORD",
    ):
        if not re.fullmatch(r"[a-fA-F0-9]{64,}", values[key]):
            raise ValueError(f"{key} must be at least 32 random bytes in hex")
    if re.search(r"[\s\x24\x22\x27]", values["CLICKHOUSE_PASSWORD"]):
        raise ValueError("CLICKHOUSE_PASSWORD must be unquoted dotenv-safe text")
    validate_public_urls(values)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", values["ENCRYPTION_KEY"]):
        raise ValueError("ENCRYPTION_KEY must be exactly 32 bytes in hex")
    if not re.fullmatch(
        r"[a-zA-Z0-9.-]+\.clickhouse\.cloud", values["CLICKHOUSE_HOST"]
    ):
        raise ValueError("CLICKHOUSE_HOST must be a ClickHouse Cloud hostname")
    for key in ("CLICKHOUSE_DB", "CLICKHOUSE_USER", "MINIO_ROOT_USER"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", values[key]):
            raise ValueError(f"{key} must be a simple identifier")


def validate_public_urls(values: dict[str, str]) -> None:
    for key in ("NEXTAUTH_URL", "S3_PUBLIC_ENDPOINT"):
        url = urlsplit(values[key])
        local = url.hostname in {"localhost", "127.0.0.1"}
        if url.username or url.password or not url.hostname:
            raise ValueError(f"{key} requires a hostname without credentials")
        if url.scheme != "https" and not (local and url.scheme == "http"):
            raise ValueError(f"{key} requires HTTPS outside localhost")


def validate_contract(document: dict) -> None:
    services = document["services"]
    if set(services) != {"web", "worker", "postgres", "redis", "minio", "bucket-init"}:
        raise ValueError("unexpected services; ClickHouse must remain external")
    lock = json.loads((ROOT / "images.lock.json").read_text())["images"]
    for name, service in services.items():
        expected = lock["minio" if name == "bucket-init" else name]
        if service["image"] != expected or not re.search(
            r"@sha256:[a-f0-9]{64}$", expected
        ):
            raise ValueError(f"image pin differs from verified lock: {name}")
        if any(not port.startswith("127.0.0.1:") for port in service.get("ports", [])):
            raise ValueError(f"non-loopback published port: {name}")
    web, worker = (services[name]["environment"] for name in ("web", "worker"))
    overrides = {
        "LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT",
        "LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT",
        "LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT",
    }
    if {k: v for k, v in web.items() if k not in overrides} != {
        k: v for k, v in worker.items() if k not in overrides
    }:
        raise ValueError("web/worker shared environment differs")
    expected = {
        "CLICKHOUSE_MIGRATION_SSL": "true",
        "CLICKHOUSE_CLUSTER_ENABLED": "false",
    }
    if any(worker[key] != value for key, value in expected.items()):
        raise ValueError("ClickHouse Cloud TLS/cluster settings changed")
    if not worker["CLICKHOUSE_URL"].endswith(":8443"):
        raise ValueError("Cloud HTTP endpoint must use TLS port 8443")
    if not worker["CLICKHOUSE_MIGRATION_URL"].endswith(":9440"):
        raise ValueError("Cloud migration endpoint must use native TLS port 9440")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--docker-config", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    text = (ROOT / "compose.yaml").read_text()
    try:
        validate_contract(json.loads(text))
        if args.env_file:
            validate_values(load_configuration(args.env_file), text)
        if args.docker_config:
            if not args.env_file:
                raise ValueError("--docker-config requires --env-file")
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(args.env_file),
                    "-f",
                    str(ROOT / "compose.yaml"),
                    "config",
                    "--quiet",
                ],
                check=True,
            )
        if args.smoke:
            with urllib.request.urlopen(
                "http://127.0.0.1:3000/api/public/health", timeout=10
            ) as response:
                if response.status != 200:
                    raise ValueError("Langfuse health endpoint is unhealthy")
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Never include file contents or expanded compose config in output.
        print(f"Validation failed: {type(exc).__name__}: {exc}")
        return 1
    print("Passed requested checks; offline checks do not prove live ingestion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
