"""google-genai 2.6.0 wrapper — singleton client, async structured-output helper."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import TypeVar, cast

from google import genai
from google.genai import errors as genai_errors
from google.genai.types import (
    CreateCachedContentConfig,
    GenerateContentConfig,
    ThinkingConfig,
    ThinkingLevel,
)
from pydantic import BaseModel

from dc_search.telemetry import Usage

logger = logging.getLogger(__name__)

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_CLIENT: genai.Client | None = None

# Single model for both extraction and slot binding.
# Configurable via DC_SEARCH_MODEL; defaults to flash-lite.
MODEL: str = os.getenv("DC_SEARCH_MODEL", "gemini-flash-lite-latest")

# ---------------------------------------------------------------------------
# Explicit context caching
# ---------------------------------------------------------------------------
# Large, stable system prompts (e.g. slot-binding's ~1.4k-token instruction) can
# be cached server-side via the Gemini explicit-cache API and referenced by name,
# so their tokens bill at the cached rate. This is a best-effort optimization:
# every helper degrades to passing the prompt inline, so a disabled flag, a
# below-minimum prompt (the API floor is 1024 tokens), or any API error never
# breaks a request. Explicit caching is also our workaround for Gemini 3's
# implicit cache not engaging for structured-output calls.
#
# OFF by default: at current query volume the per-hour cache-storage charge
# (one resource per gunicorn worker) outweighs the per-call token savings, and
# the latency benefit for a ~1.4k-token prefix is negligible. Opt in per
# deployment with DC_SEARCH_LLM_CACHE=1 once sustained volume justifies it.

_CACHE_ENABLED: bool = os.getenv("DC_SEARCH_LLM_CACHE", "0").lower() in ("1", "true", "yes")
_CACHE_TTL_S: int = int(os.getenv("DC_SEARCH_LLM_CACHE_TTL_S", "3600"))


@dataclass
class _CacheEntry:
    name: str
    expires_at: float  # time.monotonic() deadline


# Keyed by f"{model}:{sha256(system)}" so a prompt or model change rotates the entry.
_system_caches: dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


def _cache_key(system: str, model: str) -> str:
    return f"{model}:{hashlib.sha256(system.encode()).hexdigest()}"


async def get_system_cache(*, system: str, model: str | None = None) -> str | None:
    """Get-or-create an explicit cache holding ``system`` as the system instruction.

    Returns the cache's resource name, or ``None`` when caching is disabled, the
    prompt is below the model's minimum, or creation fails — callers then pass
    ``system`` inline. Concurrency-safe: one creation per (model, system) is
    shared across concurrent callers, so fan-out doesn't create duplicate caches.
    """
    if not _CACHE_ENABLED:
        return None
    model_label = model or MODEL
    key = _cache_key(system, model_label)
    now = time.monotonic()
    async with _cache_lock:
        entry = _system_caches.get(key)
        # 60s safety margin: don't hand back a cache about to expire mid-request.
        if entry is not None and entry.expires_at - now > 60:
            return entry.name
        try:
            # get_client() inside the try so a missing API key (tests) degrades to None.
            cache = await get_client().aio.caches.create(
                model=model_label,
                config=CreateCachedContentConfig(
                    system_instruction=system,
                    display_name="dc_search-system",
                    ttl=f"{_CACHE_TTL_S}s",
                ),
            )
        except Exception:
            logger.warning("llm system-cache create failed; using inline system", exc_info=True)
            return None
        _system_caches[key] = _CacheEntry(name=cache.name, expires_at=now + _CACHE_TTL_S)
        return cache.name


def invalidate_system_cache(*, system: str, model: str | None = None) -> None:
    """Drop the in-process cache entry so the next call recreates it."""
    _system_caches.pop(_cache_key(system, model or MODEL), None)


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


def _build_config(
    *,
    system: str | None,
    schema: type[_SchemaT],
    thinking_config: ThinkingConfig | None,
    cached_content: str | None,
) -> GenerateContentConfig:
    return GenerateContentConfig(
        # When the system instruction is served from an explicit cache it must NOT
        # also be passed inline (the API rejects setting both); None is omitted
        # from the wire payload, so the cache supplies it.
        system_instruction=None if cached_content else system,
        cached_content=cached_content,
        response_mime_type="application/json",
        # Pass the Pydantic class, not model_json_schema() — this is what makes
        # response.parsed a typed instance rather than a plain dict.
        response_schema=schema,
        temperature=0.0,
        thinking_config=thinking_config,
    )


async def generate_structured(
    *,
    prompt: str,
    system: str | None,
    schema: type[_SchemaT],
    model: str | None = None,
    thinking: bool = False,
    cached_content: str | None = None,
) -> tuple[_SchemaT, Usage]:
    """Async wrapper around client.aio.models.generate_content with structured output.

    When ``cached_content`` (an explicit-cache resource name) is given, the system
    instruction is served from the cache instead of ``system``. If that cache has
    expired or been deleted (HTTP 404), the call is retried once inline with
    ``system`` and the stale entry is invalidated, so an expired cache never fails
    a request.

    Returns a (parsed, Usage) tuple where parsed is a typed instance of schema.
    Raises google.genai.errors.APIError, httpx.HTTPError, or asyncio.TimeoutError
    on transport failures — callers map these to HTTP statuses.
    """
    client = get_client()
    model_label = model or MODEL

    thinking_config = _thinking_config_for_model(model_label, thinking=thinking)

    config = _build_config(
        system=system, schema=schema, thinking_config=thinking_config, cached_content=cached_content
    )

    t0 = time.monotonic()
    try:
        response = await client.aio.models.generate_content(
            model=model_label, contents=prompt, config=config
        )
    except genai_errors.ClientError as e:
        # Only a missing/expired cache (404) is recoverable here; surface 400/429/etc.
        if not cached_content or e.code != 404:
            raise
        invalidate_system_cache(system=system or "", model=model_label)
        inline_config = _build_config(
            system=system, schema=schema, thinking_config=thinking_config, cached_content=None
        )
        response = await client.aio.models.generate_content(
            model=model_label, contents=prompt, config=inline_config
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
