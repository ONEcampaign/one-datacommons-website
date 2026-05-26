"""Runtime configuration resolved from environment + .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache

from dotenv import load_dotenv

_DEFAULT_API_URL = "http://localhost:8081/v2"

# Localhost-only allowlist: this service only talks to the in-container mixer.
# Raising ValueError at startup surfaces SSRF-risky misconfigurations early.
_ALLOWED_API_URL_PREFIXES = ("http://localhost:",)

_ALLOWED_RESOLVE_TARGETS = frozenset({"base_and_custom", "custom_only", "base_only"})


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved endpoints + auth for the dc-search server.

    Standardized on ``DC_API_URL`` only (no ``DC_INSTANCE``).
    ``api_url`` must start with one of ``_ALLOWED_API_URL_PREFIXES``.
    ``resolve_target`` is passed to the DataCommons indicator resolver.
    """

    api_url: str
    api_key: str | None
    model: str
    resolve_target: str
    initial_k: int = 80
    max_shapes: int | None = 10


@cache
def load_config() -> Config:
    """Load config from environment. Cached — call once per process."""
    load_dotenv()

    api_url = (os.getenv("DC_API_URL") or "").strip() or _DEFAULT_API_URL
    api_url = api_url.rstrip("/")

    if not any(api_url.startswith(prefix) for prefix in _ALLOWED_API_URL_PREFIXES):
        allowed = ", ".join(f'"{p}"' for p in _ALLOWED_API_URL_PREFIXES)
        raise ValueError(
            f"DC_API_URL must start with one of: {allowed}. Got: {api_url!r}. "
            "This service only communicates with the in-container mixer."
        )

    api_key = (os.getenv("DC_API_KEY") or "").strip() or None
    model = (os.getenv("DC_SEARCH_MODEL") or "").strip() or "gemini-flash-lite-latest"
    resolve_target = (os.getenv("DC_RESOLVE_TARGET") or "").strip() or "base_and_custom"

    if resolve_target not in _ALLOWED_RESOLVE_TARGETS:
        allowed_rt = ", ".join(sorted(_ALLOWED_RESOLVE_TARGETS))
        raise ValueError(
            f"DC_RESOLVE_TARGET must be one of: {allowed_rt}. Got: {resolve_target!r}."
        )

    # Retrieval candidate feed: top-k SV/Topic candidates fetched and grouped
    # into shapes. Wider than the shape cap so a constraint value (e.g. a
    # CRS_DAC recipient) that ranks past the cap still reaches materialization.
    # Default 80; override with DC_SEARCH_INITIAL_K (e.g. 30 = legacy feed).
    initial_k = int((os.getenv("DC_SEARCH_INITIAL_K") or "80").strip() or "80")
    if initial_k < 1:
        raise ValueError(f"DC_SEARCH_INITIAL_K must be a positive integer. Got: {initial_k}.")

    # Cap on the number of shapes shown to the slot-binding LLM, applied after
    # the retrieval-score sort. Bounds LLM prompt noise without shrinking the
    # candidate pool materialization binds against. Default 10; override with
    # DC_SEARCH_MAX_SHAPES, where 0 disables the cap (no limit).
    max_shapes_val = int((os.getenv("DC_SEARCH_MAX_SHAPES") or "10").strip() or "10")
    if max_shapes_val < 0:
        raise ValueError(
            f"DC_SEARCH_MAX_SHAPES must be >= 0 (0 disables the cap). Got: {max_shapes_val}."
        )
    max_shapes = max_shapes_val or None

    return Config(
        api_url=api_url,
        api_key=api_key,
        model=model,
        resolve_target=resolve_target,
        initial_k=initial_k,
        max_shapes=max_shapes,
    )
