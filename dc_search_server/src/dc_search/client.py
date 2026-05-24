"""Factory for the official datacommons-client.

Wraps construction so callers don't repeat the base-DC-vs-custom-DC branch.
"""

from __future__ import annotations

from functools import cache

from datacommons_client.client import DataCommonsClient

from dc_search.config import load_config


@cache
def get_client() -> DataCommonsClient:
    """Return a process-lifetime singleton DataCommonsClient configured from env.

    Intentional process-lifetime singleton, not a bounded cache — one client
    instance per process is correct and expected. Use dependency injection
    (pass a mock DataCommonsClient) in tests instead of clearing this cache.
    """
    cfg = load_config()
    api_key = cfg.api_key
    api_url = cfg.api_url
    if api_url and "datacommons.org" not in api_url:
        # Local/custom URL explicitly provided (in-container mixer path).
        return DataCommonsClient(url=f"{api_url}/")
    if not api_key:
        raise RuntimeError(
            "DC_API_KEY is required when DC_API_URL points to datacommons.org. "
            "Get one at https://apikeys.datacommons.org or copy .env.example."
        )
    return DataCommonsClient(api_key=api_key)
