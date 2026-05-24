"""google-genai 2.6.0 wrapper — singleton client, async structured-output helper."""

from __future__ import annotations

import os
import time
from typing import TypeVar, cast

from google import genai
from google.genai.types import GenerateContentConfig, ThinkingConfig, ThinkingLevel
from pydantic import BaseModel

from dc_search.telemetry import Usage

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_CLIENT: genai.Client | None = None

# Single model for both extraction and slot binding.
# Configurable via DC_SEARCH_MODEL; defaults to flash-lite.
MODEL: str = os.getenv("DC_SEARCH_MODEL", "gemini-flash-lite-latest")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_client() -> genai.Client:
    """Return (or construct) the singleton genai.Client.

    Must be called from FastAPI lifespan startup or from within a running
    event loop — never at module import time, because the internal
    httpx.AsyncClient may otherwise bind to the wrong event loop.
    """
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT


def _thinking_config_for_model(model: str, *, thinking: bool) -> ThinkingConfig | None:
    """Return the right ThinkingConfig for the given model, or None if not applicable.

    Gemini 3 family (including the -latest aliases that currently point at Gemini 3)
    uses ``thinking_level="minimal"`` — combining this with ``thinking_budget`` in the
    same request is not supported and may cause unexpected behaviour.
    Gemini 2.5 uses ``thinking_budget=0`` to disable thinking.
    Gemma and unknown models get no ThinkingConfig.
    """
    if thinking:
        return None  # let the model think freely
    if "gemma" in model:
        return None  # gemma doesn't support ThinkingConfig
    # Gemini 3 — includes the -latest aliases (gemini-flash-latest, gemini-flash-lite-latest)
    # which currently resolve to Gemini 3 models.
    if "gemini-3" in model or model in ("gemini-flash-latest", "gemini-flash-lite-latest"):
        return ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)
    # Gemini 2.5
    if "gemini-2.5" in model:
        return ThinkingConfig(thinking_budget=0, include_thoughts=False)
    # Unknown gemini-* — use the forward-compatible knob as a safe default.
    if "gemini" in model:
        return ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)
    return None


async def generate_structured(
    *,
    prompt: str,
    system: str | None,
    schema: type[_SchemaT],
    model: str | None = None,
    thinking: bool = False,
) -> tuple[_SchemaT, Usage]:
    """Async wrapper around client.aio.models.generate_content with structured output.

    Returns a (parsed, Usage) tuple where parsed is a typed instance of schema.
    Raises google.genai.errors.APIError, httpx.HTTPError, or asyncio.TimeoutError
    on transport failures — callers map these to HTTP statuses.
    """
    client = get_client()
    model_label = model or MODEL

    thinking_config = _thinking_config_for_model(model_label, thinking=thinking)

    config = GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        # Pass the Pydantic class, not model_json_schema() — this is what makes
        # response.parsed a typed instance rather than a plain dict.
        response_schema=schema,
        temperature=0.0,
        thinking_config=thinking_config,
    )

    t0 = time.monotonic()
    response = await client.aio.models.generate_content(
        model=model_label,
        contents=prompt,
        config=config,
    )
    elapsed = time.monotonic() - t0

    parsed = response.parsed
    if parsed is None:
        raise ValueError("LLM output failed schema validation; response.parsed is None")
    usage = to_usage(response, model_label, latency_s=elapsed)
    # response_schema is a BaseModel subclass, so response.parsed is an instance
    # of it; the genai stub widens the type to BaseModel | dict | Enum.
    return cast("_SchemaT", parsed), usage


def to_usage(response: object, model: str, *, latency_s: float | None) -> Usage:
    """Map response.usage_metadata into a Usage dataclass."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return Usage(model=model, latency_s=latency_s, model_requests=1)
    return Usage(
        input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        cached_input_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
        model=model,
        latency_s=latency_s,
        model_requests=1,
    )
