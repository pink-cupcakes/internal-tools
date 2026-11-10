"""Best-effort, metadata-only Langfuse spans for live public routing calls."""

from functools import wraps
import os
from typing import Callable

from .audit import _cache_usage, _usage
from .models import RoutingTrace


def _start():
    """Leave offline/unconfigured processes free of SDK initialization."""
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "true").lower() == "false":
        return None, None
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        return None, None
    client = None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=public,
            secret_key=secret,
            base_url=os.environ.get("LANGFUSE_BASE_URL") or "http://localhost:3000",
            timeout=10,
        )
        return client, client.start_observation(
            name="model_router.route", as_type="span"
        )
    except Exception:
        return client, None


def _metadata(trace: RoutingTrace) -> dict:
    response = trace.response
    usage = response.get("usage") if isinstance(response, dict) else None
    return {
        "recommendation": trace.recommendation.model_dump(
            mode="json", exclude={"clarification_items"}
        ),
        "source": trace.source,
        "request_sent": trace.request_sent,
        "policy_version": trace.policy.version,
        "elapsed_ms": trace.elapsed_ms,
        "routing_usage": _usage(usage),
        "routing_cache_usage": _cache_usage(usage, routing=True),
    }


def _finish(client, span, trace: RoutingTrace | None) -> None:
    """Isolate each SDK operation so update/end failures cannot prevent flush."""
    if span is not None:
        try:
            if trace is not None:
                span.update(metadata=_metadata(trace))
        except Exception:
            pass
        try:
            span.end()
        except Exception:
            pass
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass


def trace_live_route(function: Callable) -> Callable:
    """Wrap execution once; never give the SDK task input or exception content."""

    @wraps(function)
    def traced(*args, **kwargs):
        if kwargs.get("source", "live") != "live":
            return function(*args, **kwargs)
        client, span = _start()
        result = None
        try:
            result = function(*args, **kwargs)
            return result
        finally:
            _finish(client, span, result)

    return traced
