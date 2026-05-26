"""Place-name resolution helpers.

Wraps /v2/resolve (by name) into PlaceCandidate objects. get_client() is
looked up as a package attribute so that monkeypatched get_client is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from dc_search import retrieval as graph

from ._cache import _cache_lock, _place_names_cache, _resolve_cache

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PlaceCandidate:
    dcid: str
    dominant_type: str | None = None


def _parse_resolve_entities(
    raw: dict[str, Any],
) -> dict[str, tuple[PlaceCandidate, ...]]:
    """Parse a /v2/resolve response into a ``{surface: candidates}`` map.

    Used by both ``resolve_place`` and ``resolve_places_batch``. The endpoint
    returns one entity per requested node, each with its own candidates list.
    Missing entities (no row for a requested surface) map to ``()``; callers
    seed the result dict with empty tuples first to make this concrete.
    """
    out: dict[str, tuple[PlaceCandidate, ...]] = {}
    for entity in raw.get("entities", []):
        node = entity.get("node")
        if not node:
            continue
        out[node] = tuple(
            PlaceCandidate(dcid=cand["dcid"], dominant_type=cand.get("dominantType"))
            for cand in entity.get("candidates", [])
        )
    return out


def resolve_places_batch(
    *,
    names: tuple[str, ...],
) -> dict[str, tuple[PlaceCandidate, ...]]:
    """Resolve many place names in ONE HTTP call to /v2/resolve.

    The V2 resolve endpoint natively accepts a list of node IDs and returns
    one entity per request. Batching collapses what would be N sequential
    round-trips (each ~1s on a custom DC instance) into a single ~0.5s
    round-trip — a ~20x speedup measured against a 13-name workload.

    Results (including empty resolutions) populate the module-level
    ``_resolve_cache``, so future calls to either this function or
    ``resolve_place`` short-circuit. When every requested name is already
    cached, no HTTP request is made.

    Args:
        names: Surface strings to resolve. Duplicates are deduplicated before
            the network call. Empty input returns ``{}``.

    Returns:
        Mapping ``surface -> candidates`` for every input name. Names with no
        match map to an empty tuple.
    """
    if not names:
        return {}

    out: dict[str, tuple[PlaceCandidate, ...]] = {}
    misses: list[str] = []
    seen: set[str] = set()
    with _cache_lock:
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            cached = _resolve_cache.get(name)
            if cached is not None:
                out[name] = cached
            else:
                misses.append(name)

    if not misses:
        return out

    try:
        client = graph.get_client()
        raw = client.resolve.fetch_dcids_by_name(names=misses).to_dict()
        parsed_result = _parse_resolve_entities(raw)
    except (httpx.HTTPError, ValueError):
        logger.warning(
            "resolve_places_batch: transient error; returning empty results",
            exc_info=True,
        )
        return out
    # Seed every miss with () so negatives also get cached.
    with _cache_lock:
        for name in misses:
            value = parsed_result.get(name, ())
            _resolve_cache[name] = value
            out[name] = value

    return out


def resolve_place(*, name: str) -> tuple[PlaceCandidate, ...]:
    """Return ranked DCID candidates for a place name.

    Thin single-surface wrapper around ``resolve_places_batch``. Shares the
    ``_resolve_cache`` so warm calls from either entry point hit the same
    in-memory store. Repeated names within a session cost zero network
    round-trips.
    """
    return resolve_places_batch(names=(name,))[name]


def place_names_batch(*, dcids: tuple[str, ...]) -> dict[str, tuple[str | None, str | None]]:
    """Batch-fetch (name, primary type) for place DCIDs; cached, fail-open.

    Issues one ``client.node.fetch`` call with ``expression="->[name,typeOf]"``.
    Caching is by sorted tuple to maximise reuse across calls. Falls back to
    ``(None, None)`` for any DCID where properties are missing or the call fails.

    Args:
        dcids: Tuple of place DCIDs to look up.

    Returns:
        Mapping from DCID to ``(name, typeOf)`` for every requested DCID.
        Missing or errored entries are included as ``(None, None)`` so callers
        can safely look up any requested DCID without a ``KeyError``.
    """
    from .indicator import _arc_values, _node_arcs

    result: dict[str, tuple[str | None, str | None]] = {d: (None, None) for d in dcids}
    if not dcids:
        return result

    # Sort for stable cache key so different orderings of the same set hit the cache.
    cache_key = tuple(sorted(dcids))
    with _cache_lock:
        cached = _place_names_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
        raw = client.node.fetch(
            node_dcids=list(dcids),
            expression="->[name,typeOf]",
        ).to_dict()
    except Exception:
        return result

    for dcid in dcids:
        arcs = _node_arcs(raw, dcid)
        if not arcs:
            continue
        name_vals = _arc_values(arcs, "name")
        type_vals = _arc_values(arcs, "typeOf")
        result[dcid] = (
            name_vals[0] if name_vals else None,
            type_vals[0] if type_vals else None,
        )

    with _cache_lock:
        _place_names_cache[cache_key] = result
    return result
