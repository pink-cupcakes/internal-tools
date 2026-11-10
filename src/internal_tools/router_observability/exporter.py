"""Explicit, metadata-only OTLP/HTTP export using the standard library."""

import base64
import hashlib
import ipaddress
import json
import os
import urllib.parse
import urllib.request


class ExportError(ValueError):
    """Sanitized transport failure."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ExportError("redirect_refused")


def endpoint_url(base_url: str) -> str:
    try:
        url = urllib.parse.urlsplit(base_url)
        port = url.port
        host = url.hostname
        loopback = host == "localhost"
        if host and not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        valid = (
            host
            and not any(c.isspace() for c in base_url)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and url.path in ("", "/")
            and port != 0
            and (url.scheme == "https" or (url.scheme == "http" and loopback))
        )
        if not valid:
            raise ValueError
    except ValueError:
        raise ExportError("invalid_base_url") from None
    return base_url.rstrip("/") + "/api/public/otel/v1/traces"


def build_payload(report: dict) -> dict:
    identity = report["snapshot_id"]
    timestamp = report["import_time_unix_nano"]
    metadata = {
        "task_count": str(report.get("task_count", 0)),
        "task_type_fallback_count": str(report.get("task_type_fallback_count", 0)),
        "snapshot_id": identity,
        "snapshot": json.dumps(report, allow_nan=False),
    }
    attributes = [
        {"key": "langfuse.observation.metadata." + key, "value": {"stringValue": value}}
        for key, value in metadata.items()
    ]
    span = {
        "traceId": identity[:32],
        "spanId": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "name": "router.snapshot",
        "kind": 1,
        "startTimeUnixNano": timestamp,
        "endTimeUnixNano": timestamp,
        "attributes": attributes,
    }
    return {"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}


def _acknowledged(raw: bytes) -> None:
    try:
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError
        partial = response.get("partialSuccess", {})
        if not isinstance(partial, dict):
            raise ValueError
        if str(partial.get("rejectedSpans", 0)) != "0" or partial.get("errorMessage"):
            raise ExportError("partial_success")
        if response == {} or set(response) == {"partialSuccess"}:
            return
        payload = response.get("data", {}).get("payload", {})
        if (
            response.get("name") == "otel-ingestion-job"
            and payload.get("authCheck", {}).get("validKey") is True
        ):
            return
        raise ValueError
    except ExportError:
        raise
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ExportError("invalid_acknowledgement") from None


def export_report(report: dict, *, environment=None, timeout: float = 10) -> None:
    environment = os.environ if environment is None else environment
    url = endpoint_url(environment.get("LANGFUSE_BASE_URL", "http://localhost:3000"))
    public = environment.get("LANGFUSE_PUBLIC_KEY", "")
    secret = environment.get("LANGFUSE_SECRET_KEY", "")
    if not public.strip() or not secret.strip() or ":" in public:
        raise ExportError("missing_or_invalid_credentials")
    if not 0 < timeout <= 30:
        raise ExportError("invalid_timeout")
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    request = urllib.request.Request(
        url,
        data=json.dumps(build_payload(report), allow_nan=False).encode(),
        headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": "4",
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=timeout
        ) as response:
            if response.status != 200:
                raise ExportError("http_error")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ExportError("oversized_acknowledgement")
            _acknowledged(raw)
    except ExportError:
        raise
    except (OSError, ValueError):
        raise ExportError("transport_error") from None
