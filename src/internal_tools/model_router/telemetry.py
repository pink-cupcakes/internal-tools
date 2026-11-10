"""Best-effort Langfuse generations for live public routing calls."""

from functools import wraps
import os
from typing import Callable

from .audit import _cache_usage, _usage
from .models import RoutingTrace


def _start(task_input):
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
            name="model_router.route", as_type="generation", input=task_input
        )
    except Exception:
        return client, None


def _usage_details(metadata: dict) -> dict | None:
    validated = metadata["routing_usage"]
    if validated is None:
        return None
    details = metadata["routing_cache_usage"]
    result = {
        "input": validated["input_tokens"],
        "output": validated["output_tokens"],
        "total": validated["total_tokens"],
    }
    if details is not None:
        result["input"] = details["uncached_input_tokens"]
        result["input_cached_tokens"] = details["cached_input_tokens"]
    return result


def _metadata(trace: RoutingTrace) -> dict:
    response = trace.response
    usage = response.get("usage") if isinstance(response, dict) else None
    return {
        "source": trace.source,
        "request_sent": trace.request_sent,
        "policy_version": trace.policy.version,
        "elapsed_ms": trace.elapsed_ms,
        "routing_usage": _usage(usage),
        "routing_cache_usage": _cache_usage(usage, routing=True),
    }


def _finish(client, span, trace: RoutingTrace | None, failure_class=None) -> None:
    """Isolate each SDK operation so update/end failures cannot prevent flush."""
    if span is not None:
        try:
            if trace is not None:
                request = trace.request
                metadata = _metadata(trace)
                span.update(
                    input=request,
                    output={
                        "recommendation": trace.recommendation.model_dump(mode="json"),
                        "decision": trace.response,
                    },
                    model=request.get("model") if trace.request_sent else None,
                    usage_details=_usage_details(metadata),
                    level="ERROR" if trace.error else "DEFAULT",
                    status_message=trace.error,
                    metadata=metadata,
                )
            elif failure_class:
                span.update(
                    level="ERROR",
                    status_message=failure_class,
                    metadata={"source": "live"},
                )
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
    """Wrap live routing once and keep SDK failures isolated."""

    @wraps(function)
    def traced(*args, **kwargs):
        if kwargs.get("source", "live") != "live":
            return function(*args, **kwargs)
        task = kwargs.get("task", args[0] if args else None)
        try:
            task_input = task.model_dump(mode="json")
        except Exception:
            task_input = None
        client, span = _start(task_input)
        result = None
        try:
            result = function(*args, **kwargs)
            return result
        except Exception as error:
            failure_class = type(error).__name__
            raise
        finally:
            _finish(client, span, result, locals().get("failure_class"))

    return traced
