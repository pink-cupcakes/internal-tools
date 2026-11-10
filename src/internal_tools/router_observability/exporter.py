"""Explicit opt-in Langfuse transport with no redirects or response leakage."""

import base64
import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .otlp import build_payload


class ExportError(ValueError):
    """A sanitized delivery failure; the local snapshot remains retryable."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ExportError("redirect_refused")


def endpoint_url(base_url: str) -> str:
    if any(character.isspace() for character in base_url):
        raise ExportError("invalid_base_url")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise ExportError("invalid_base_url") from None
    host = parsed.hostname
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.scheme not in ("https", "http")
        or port == 0
    ):
        raise ExportError("invalid_base_url")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme == "http" and not loopback:
        raise ExportError("insecure_base_url")
    return base_url.rstrip("/") + "/api/public/otel/v1/traces"


def _request(payload: dict, environment: dict) -> urllib.request.Request:
    url = endpoint_url(
        environment.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    )
    public = environment.get("LANGFUSE_PUBLIC_KEY", "")
    secret = environment.get("LANGFUSE_SECRET_KEY", "")
    if not public.strip() or not secret.strip() or ":" in public:
        raise ExportError("missing_or_invalid_credentials")
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return urllib.request.Request(
        url,
        data=json.dumps(payload, allow_nan=False).encode(),
        method="POST",
        headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json",
            "x-langfuse-ingestion-version": "4",
        },
    )


def _acknowledged(raw: bytes) -> None:
    try:
        response = json.loads(raw)
        if not isinstance(response, dict) or set(response) - {"partialSuccess"}:
            raise ValueError
        partial = response.get("partialSuccess", {})
        if not isinstance(partial, dict):
            raise ValueError
        rejected = partial.get("rejectedSpans", 0)
        if not isinstance(rejected, (str, int)) or str(rejected) != "0":
            raise ExportError("partial_success")
        if partial.get("errorMessage"):
            raise ExportError("partial_success")
    except ExportError:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise ExportError("invalid_acknowledgement") from None


def export_report(
    report: dict, snapshot_id: str, *, environment=None, timeout: float = 10.0
) -> None:
    """Return only on complete acknowledgement; never retry implicitly."""
    if not 0 < timeout <= 30:
        raise ExportError("invalid_timeout")
    request = _request(
        build_payload(report, snapshot_id),
        os.environ if environment is None else environment,
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ExportError("http_error")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ExportError("oversized_acknowledgement")
            _acknowledged(raw)
    except ExportError:
        raise
    except (OSError, urllib.error.URLError, ValueError):
        raise ExportError("transport_error") from None
