"""Official SDK integration, imported only when a live call is requested."""

from typing import Any
from .core import DecisionCallError


def send_decision(request_body: dict[str, Any]) -> dict[str, Any]:
    import os
    import openai

    if not os.environ.get("OPENAI_API_KEY"):
        raise DecisionCallError("OPENAI_API_KEY is not configured")
    try:
        # Disable implicit retries: one routing attempt has one visible API call.
        with openai.OpenAI(timeout=20.0, max_retries=0) as client:
            return client.decisions.create(**request_body).model_dump(mode="json")
    except openai.APIError as error:
        # Avoid dumping request headers, credentials, or provider response bodies.
        raise DecisionCallError(type(error).__name__, request_sent=True) from None
