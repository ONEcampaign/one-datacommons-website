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

    return Config(api_url=api_url, api_key=api_key, model=model, resolve_target=resolve_target)
