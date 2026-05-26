"""Observation, date-coverage, and variable-entity discovery helpers.

Wraps /v2/observation, /v2/variable/coverage, and /v2/bulk/info/variable.
get_client() is looked up as a package attribute so that monkeypatched get_client is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dc_search import retrieval as graph

from ._cache import (
    _cache_lock,
    _coverage_cache,
    _entity_svs_cache,
    _observation_dates_cache,
    _observation_facet_ranges_cache,
    _presence_cache,
    _variable_info_dates_cache,
    _variables_for_entity_cache_lru,
)
from ._degraded import _dc_call_degraded

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DateCoverage:
    """Coverage returned by the mixer /v2/variable/coverage endpoint.

    envelopes: var dcid -> (earliest, latest); each bound may be None.
    entity_ranges: (var dcid, entity dcid) -> (earliest, latest).
    """

    envelopes: dict[str, tuple[str | None, str | None]]
    entity_ranges: dict[tuple[str, str], tuple[str | None, str | None]]


# Bound the base-DC batch sent to variable/info (placeless path can exceed this
# after topic expansion); the overflow is fail-open kept.
_VARIABLE_INFO_DATE_CAP = 25


def _parse_observation(
    raw: dict[str, Any],
    variable_dcids: tuple[str, ...],
) -> tuple[frozenset[str], dict[tuple[str, str], tuple[str | None, str | None]]]:
    """Parse /v2/observation into (present_vars, per-(var,entity) date ranges).

    Takes the union of earliest/latest dates across all orderedFacets per pair,
    not just the first (preferred) facet.
    """
    by_variable = raw.get("byVariable", {})
    present: set[str] = set()
    ranges: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for sv in variable_dcids:
        by_entity = by_variable.get(sv, {}).get("byEntity", {})
        for ent, ent_data in by_entity.items():
            facets = ent_data.get("orderedFacets") or []
            if not facets:
                continue
            present.add(sv)
            earliest_dates = [f.get("earliestDate") for f in facets if f.get("earliestDate")]
            latest_dates = [f.get("latestDate") for f in facets if f.get("latestDate")]
            ranges[(sv, ent)] = (
                min(earliest_dates) if earliest_dates else None,
                max(latest_dates) if latest_dates else None,
            )
    return frozenset(present), ranges


def presence_for_entities(
    *,
    variable_dcids: tuple[str, ...],
    entity_dcids: tuple[str, ...],
) -> frozenset[str]:
    """Return the subset of ``variable_dcids`` that have data for any of ``entity_dcids``.

    Issues one ``/v2/observation`` fetch for all (variable, entity) pairs in a
    single HTTP round-trip and returns the frozenset of variable DCIDs that
    have at least one observation against any of the requested entities.

    The result is cached by ``(sorted(variable_dcids), sorted(entity_dcids))``
    — repeated queries for the same SVxentity combination are free.

    Args:
        variable_dcids: Candidate SV DCIDs to check (e.g. the 30 retrieved).
        entity_dcids: Place DCIDs to check against.

    Returns:
        Frozenset of ``variable_dcids`` that have at least one observation.
        Empty frozenset if neither list has entries or no data is found.
    """
    if not variable_dcids or not entity_dcids:
        return frozenset()

    cache_key = (tuple(sorted(variable_dcids)), tuple(sorted(entity_dcids)))
    with _cache_lock:
        cached = _presence_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
        raw = client.observation.fetch(
            variable_dcids=list(variable_dcids),
            entity_dcids=list(entity_dcids),
            date="LATEST",
        ).to_dict()
    except Exception:
        logger.warning(
            "presence_for_entities: transient error; fail-open",
            exc_info=True,
        )
        _dc_call_degraded.set(True)
        return frozenset()

    result = _parse_observation(raw, variable_dcids)[0]
    with _cache_lock:
        _presence_cache[cache_key] = result
    return result


def variables_for_entities_batch(
    *,
    entity_dcids: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """Return the SV inventories for a batch of entities in one HTTP call.

    Issues a single ``fetch_available_statistical_variables`` call for all
    cache-miss entities. Populates ``_entity_svs_cache`` so repeated queries
    for the same entity (across batch and single-entity call sites) are free.

    Args:
        entity_dcids: Tuple of entity DCIDs to look up. Duplicates are
            deduplicated before the network call.

    Returns:
        ``{entity_dcid: tuple_of_sv_dcids}`` for every requested entity.
        Entities absent from the DC response are included with ``()``.
    """
    if not entity_dcids:
        return {}

    result: dict[str, tuple[str, ...]] = {}
    misses: list[str] = []
    seen: set[str] = set()
    with _cache_lock:
        for dcid in entity_dcids:
            if dcid in seen:
                continue
            seen.add(dcid)
            cached = _entity_svs_cache.get(dcid)
            if cached is not None:
                result[dcid] = cached
            else:
                misses.append(dcid)

    if misses:
        client = graph.get_client()
        by_entity = client.observation.fetch_available_statistical_variables(entity_dcids=misses)
        with _cache_lock:
            for dcid in misses:
                svs = tuple(by_entity.get(dcid, []))
                _entity_svs_cache[dcid] = svs
                result[dcid] = svs

    return result


def variables_for_entity(*, entity_dcid: str) -> tuple[str, ...]:
    """Return the SV DCIDs that have observation data for one entity.

    Single-entity wrapper around ``variables_for_entities_batch``, cached.
    """
    with _cache_lock:
        cached = _variables_for_entity_cache_lru.get(entity_dcid)
    if cached is not None:
        return cached
    result = variables_for_entities_batch(entity_dcids=(entity_dcid,)).get(entity_dcid, ())
    with _cache_lock:
        _variables_for_entity_cache_lru[entity_dcid] = result
    return result


def variable_date_coverage(
    *,
    variable_dcids: tuple[str, ...],
    entity_dcids: tuple[str, ...] = (),
) -> DateCoverage:
    """Fetch precomputed custom-DC date coverage from the mixer.

    One POST to /v2/variable/coverage via the SDK's low-level client.api.post.
    Cached by (sorted(variable_dcids), sorted(entity_dcids)). Fail-open: returns
    an empty DateCoverage on HTTP/parse error so callers never drop vars on
    transient failure.
    """
    if not variable_dcids:
        return DateCoverage({}, {})

    cache_key = (tuple(sorted(variable_dcids)), tuple(sorted(entity_dcids)))
    with _cache_lock:
        cached = _coverage_cache.get(cache_key)
    if cached is not None:
        return cached

    payload: dict[str, Any] = {
        "variables": list(variable_dcids),
        "entities": list(entity_dcids),
    }
    try:
        client = graph.get_client()
        raw: dict[str, Any] = client.api.post(payload, endpoint="variable/coverage")
    except Exception:
        logger.warning(
            "variable_date_coverage: transient error fetching coverage; fail-open",
            exc_info=True,
        )
        _dc_call_degraded.set(True)
        return DateCoverage({}, {})

    # Parse both camelCase and snake_case (Envoy transcoder may emit either).
    vc = raw.get("variableCoverage") or raw.get("variable_coverage") or {}
    ec = raw.get("entityCoverage") or raw.get("entity_coverage") or {}

    envelopes: dict[str, tuple[str | None, str | None]] = {
        v: (r.get("earliest"), r.get("latest")) for v, r in vc.items()
    }
    entity_ranges: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for v, ent_map in ec.items():
        # EntityRanges.entity wraps the per-entity dict in {"entity": {...}}.
        inner = ent_map.get("entity") or ent_map
        for e, r in inner.items():
            entity_ranges[(v, e)] = (r.get("earliest"), r.get("latest"))

    result = DateCoverage(envelopes=envelopes, entity_ranges=entity_ranges)
    with _cache_lock:
        _coverage_cache[cache_key] = result
    return result


def variable_info_date_ranges(
    *,
    variable_dcids: tuple[str, ...],
) -> dict[str, tuple[str | None, str | None]]:
    """Base-DC placeless date envelopes from /v2/bulk/info/variable.

    Folds provenanceSummary[].seriesSummary[].{earliestDate,latestDate} to one
    (min earliest, max latest) per variable. Capped at _VARIABLE_INFO_DATE_CAP,
    cached by sorted tuple, fail-open → empty dict.
    """
    if not variable_dcids:
        return {}

    # Sort then cap so the checked subset is deterministic regardless of caller
    # argument order (avoids order-dependent cache keys for >cap-var sets).
    variable_dcids = tuple(sorted(variable_dcids))[:_VARIABLE_INFO_DATE_CAP]

    cache_key = variable_dcids
    with _cache_lock:
        cached = _variable_info_dates_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
        raw: dict[str, Any] = client.api.post(
            {"nodes": list(variable_dcids)},
            endpoint="bulk/info/variable",
        )
    except Exception:
        logger.warning(
            "variable_info_date_ranges: transient error; fail-open",
            exc_info=True,
        )
        _dc_call_degraded.set(True)
        return {}

    result: dict[str, tuple[str | None, str | None]] = {}
    for entry in raw.get("data") or []:
        node = entry.get("node")
        info = entry.get("info") or {}
        if not node:
            continue
        prov_summary = info.get("provenanceSummary") or {}
        if isinstance(prov_summary, dict):
            if "seriesSummary" in prov_summary:
                prov_items = [prov_summary]
            else:
                prov_items = prov_summary.values()
        else:
            prov_items = prov_summary

        earliest_dates: list[str] = []
        latest_dates: list[str] = []
        for prov in prov_items:
            for series in prov.get("seriesSummary") or []:
                ed = series.get("earliestDate")
                ld = series.get("latestDate")
                if ed:
                    earliest_dates.append(ed)
                if ld:
                    latest_dates.append(ld)

        if earliest_dates or latest_dates:
            result[node] = (
                min(earliest_dates) if earliest_dates else None,
                max(latest_dates) if latest_dates else None,
            )

    with _cache_lock:
        _variable_info_dates_cache[cache_key] = result
    return result


def observation_facet_ranges(
    *,
    variable_dcids: tuple[str, ...],
    entity_dcids: tuple[str, ...],
) -> tuple[frozenset[str], dict[str, tuple[str | None, str | None]]]:
    """Presence + true date span per variable via a facet-select observation query.

    Returns (present_vars, {var_dcid: (earliest, latest)}) where ranges are unioned
    across entities (min earliest / max latest, ISO-lexicographic). Lightweight:
    no observation values are returned. Cached, fail-open → (frozenset(), {}).
    """
    if not variable_dcids or not entity_dcids:
        return frozenset(), {}

    cache_key = (tuple(sorted(variable_dcids)), tuple(sorted(entity_dcids)))
    with _cache_lock:
        cached = _observation_facet_ranges_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
        raw: dict[str, Any] = client.api.post(
            {
                "select": ["variable", "entity", "facet"],
                "variable": {"dcids": list(variable_dcids)},
                "entity": {"dcids": list(entity_dcids)},
            },
            endpoint="observation",
        )
    except Exception:
        logger.warning(
            "observation_facet_ranges: transient error; fail-open",
            exc_info=True,
        )
        _dc_call_degraded.set(True)
        return frozenset(), {}

    present, pair_ranges = _parse_observation(raw, variable_dcids)

    # Union (var, entity) pair ranges into per-var ranges across all entities.
    per_var: dict[str, tuple[str | None, str | None]] = {}
    for sv in present:
        lo: str | None = None
        hi: str | None = None
        for ent in entity_dcids:
            er = pair_ranges.get((sv, ent))
            if er is None:
                continue
            er_lo, er_hi = er
            if er_lo is not None:
                lo = er_lo if (lo is None or er_lo < lo) else lo
            if er_hi is not None:
                hi = er_hi if (hi is None or er_hi > hi) else hi
        per_var[sv] = (lo, hi)

    result = (present, per_var)
    with _cache_lock:
        _observation_facet_ranges_cache[cache_key] = result
    return result


def observation_date_ranges(
    *,
    variable_dcids: tuple[str, ...],
    entity_dcids: tuple[str, ...],
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """Base-DC placed date ranges via /v2/observation (LATEST).

    Returns a (var, entity) -> (earliestDate, latestDate) dict from the first
    orderedFacet per (var, entity) pair. Cached by (sorted vars, sorted entities).
    Fail-open → empty dict on any error.
    """
    if not variable_dcids or not entity_dcids:
        return {}

    cache_key = (tuple(sorted(variable_dcids)), tuple(sorted(entity_dcids)))
    with _cache_lock:
        cached = _observation_dates_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
        raw = client.observation.fetch(
            variable_dcids=list(variable_dcids),
            entity_dcids=list(entity_dcids),
            date="LATEST",
        ).to_dict()
    except Exception:
        logger.warning(
            "observation_date_ranges: transient error; fail-open",
            exc_info=True,
        )
        _dc_call_degraded.set(True)
        return {}

    _present, result = _parse_observation(raw, variable_dcids)

    with _cache_lock:
        _observation_dates_cache[cache_key] = result
    return result
