"""High-level helpers for the four discrimination primitives.

Each wraps the official ``datacommons-client`` and reshapes responses into
compact Python objects easy to put in front of an LLM. All four are
implemented over V2 — verified working against the custom DC where V1 paths
are not exposed.

The four primitives:

1. ``resolve_place`` — name → candidate DCIDs (with type).
2. ``stat_var_features`` — SV DCID → structured graph features (populationType,
   measuredProperty, statType, measurementQualifier, constraint properties).
   This is the LLM-friendly disambiguation view of a stat var.
3. ``variables_for_entity`` — entity DCID → set of SV DCIDs that actually have
   observations (discovery mode of /v2/observation). Critical filter.
4. ``variable_group`` — V2 traversal of a StatVarGroup node into its parent
   group, child groups, and child SVs.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import cachetools
import httpx

from dc_search.client import get_client

logger = logging.getLogger(__name__)

# Single RLock guards all module-level LRUCache instances.  cachetools LRUCache
# is not thread-safe (OrderedDict.move_to_end races under concurrent __setitem__).
# asyncio.to_thread dispatches sync functions onto the default ThreadPoolExecutor,
# so concurrent callers on different threads can collide on the same cache.
_cache_lock = threading.RLock()

# Structured properties that together identify what an SV measures.
SV_DEFINING_PROPS = [
    "populationType",
    "measuredProperty",
    "statType",
    "measurementQualifier",
    "measurementDenominator",
    "name",
    "description",
    "memberOf",
]

# Explicit property list for batched feature fetch — avoids the overhead of
# fetching all outgoing arcs (->*) when we only care about these fields.
# constraintProperties is included so we know which additional arcs to fetch.
_BATCH_PROPS = [
    "populationType",
    "measuredProperty",
    "statType",
    "measurementQualifier",
    "measurementDenominator",
    "memberOf",
    "name",
    "description",
    "constraintProperties",
]


@dataclass(slots=True)
class PlaceCandidate:
    dcid: str
    dominant_type: str | None = None


@dataclass(slots=True)
class IndicatorCandidate:
    """One candidate returned by /v2/resolve with resolver='indicator'."""

    dcid: str
    type_of: list[str]
    score: float | None = None
    sentence: str | None = None


@dataclass(slots=True)
class StatVarFeatures:
    """Compact structured view of one StatisticalVariable."""

    dcid: str
    name: str | None = None
    description: str | None = None
    population_type: list[str] = field(default_factory=list)
    measured_property: list[str] = field(default_factory=list)
    stat_type: list[str] = field(default_factory=list)
    measurement_qualifier: list[str] = field(default_factory=list)
    measurement_denominator: list[str] = field(default_factory=list)
    measurement_method: list[str] = field(default_factory=list)
    observation_period: list[str] = field(default_factory=list)
    unit: list[str] = field(default_factory=list)
    member_of: list[str] = field(default_factory=list)
    # Anything else surfaced by ->* that isn't in the named list above —
    # these are the constraint properties (gender, age, race, ...) that
    # actually distinguish similar SVs.
    constraints: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class VariableGroupInfo:
    """A StatVarGroup node with its parent, child groups, and child SVs."""

    dcid: str
    name: str
    parents: list[dict[str, str]]
    child_groups: list[dict[str, str]]
    child_vars: list[dict[str, str]]


# Module-level cache for place resolutions. Both ``resolve_place`` (single)
# and ``resolve_places_batch`` (many) consult and populate this dict, so the
# two functions share one source of truth — calling either warms the cache
# for the other. Empty results (negative resolutions) are cached so cold-cache
# re-queries are free; place names don't churn at process-lifetime granularity.
_resolve_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


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
        cands: list[PlaceCandidate] = []
        for cand in entity.get("candidates", []):
            cands.append(
                PlaceCandidate(
                    dcid=cand["dcid"],
                    dominant_type=cand.get("dominantType"),
                )
            )
        out[node] = tuple(cands)
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
        client = get_client()
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


# Module-level LRU cache for resolve_indicator results.
_resolve_indicator_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def resolve_indicator(*, query: str, k: int = 30) -> tuple[IndicatorCandidate, ...]:
    """Resolve a query to ranked StatVar / Topic candidates.

    Wraps ``/v2/resolve`` with ``resolver="indicator"``. Returns candidates
    with their match score and the sentence each one matched against — the
    same signal ``/api/search_vars/`` produces.

    Explicitly passes ``target`` (from ``Config.resolve_target``, default
    ``"base_and_custom"``) so a future server-side default change can't
    silently drop base-DC indicators from a custom-instance query.

    Results are cached in a module-level LRU cache (maxsize=2048) — identical
    queries (eval re-runs, repeated user queries in a session) reuse the same
    response. The ``k`` parameter is included in the cache key so different
    top-K requests for the same query are stored separately.

    Args:
        query: Natural-language query string.
        k: Number of top candidates to return (passed to caller; the full
            result is cached and the caller trims to k).
    """
    cache_key = (query, k)
    with _cache_lock:
        cached = _resolve_indicator_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    from dc_search.config import load_config

    client = get_client()
    cfg = load_config()
    raw = client.resolve.fetch(
        node_ids=query, resolver="indicator", target=cfg.resolve_target
    ).to_dict()
    out: list[IndicatorCandidate] = []
    for entity in raw.get("entities", []):
        for cand in entity.get("candidates", []):
            md = cand.get("metadata") or {}
            score_raw = md.get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            out.append(
                IndicatorCandidate(
                    dcid=cand["dcid"],
                    type_of=list(cand.get("typeOf", []) or []),
                    score=score,
                    sentence=md.get("sentence"),
                )
            )
    result = tuple(out)
    with _cache_lock:
        _resolve_indicator_cache_lru[cache_key] = result
    return result


def _node_arcs(raw: dict[str, Any], dcid: str) -> dict[str, dict[str, Any]]:
    """Extract the ``arcs`` map for a node from a v2/node response."""
    return raw.get("data", {}).get(dcid, {}).get("arcs", {})


def _arc_values(arcs: dict[str, Any], prop: str) -> list[str]:
    """Pull display values from an arc — DCID if present, else literal value."""
    out: list[str] = []
    for node in arcs.get(prop, {}).get("nodes", []):
        if "dcid" in node:
            out.append(node["dcid"])
        elif "value" in node:
            out.append(str(node["value"]))
    return out


# Module-level cache for per-SV features. SV metadata (populationType,
# constraints, etc.) is essentially immutable over a process lifetime, so we
# memoize at SV granularity to share results across overlapping batches.
# Missing SVs are NOT cached — a re-request will retry the network fetch
# (handles transient DC API hiccups). Cleared by tests via `_features_cache.clear()`.
_features_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=4096)


def stat_var_features_batch(
    *,
    sv_dcids: list[str],
) -> dict[str, StatVarFeatures]:
    """Fetch structured features for a batch of SV DCIDs.

    Hits the module-level ``_features_cache`` first; only cache-miss DCIDs go
    to the network. With a warm cache the function returns without any API
    calls. Cold path: one ``/v2/node`` request for the named fields + a second
    ``/v2/node`` request for constraint arcs of SVs that declare
    ``constraintProperties``.

    Returns a dict keyed by sv_dcid, with one entry per input that resolved.
    SVs absent from the response are silently skipped and not cached.
    """
    if not sv_dcids:
        return {}

    # Split into hits / misses, preserving input order and deduplicating.
    result: dict[str, StatVarFeatures] = {}
    misses: list[str] = []
    seen: set[str] = set()
    with _cache_lock:
        for dcid in sv_dcids:
            if dcid in seen:
                continue
            seen.add(dcid)
            cached = _features_cache.get(dcid)
            if cached is not None:
                result[dcid] = cached
            else:
                misses.append(dcid)

    if not misses:
        return result

    client = get_client()
    expr = "->[" + ",".join(_BATCH_PROPS) + "]"
    raw = client.node.fetch(node_dcids=misses, expression=expr).to_dict()

    # Parse the first-pass response — extract named fields and the set of
    # constraintProperties each SV declares.
    partial: dict[str, StatVarFeatures] = {}
    sv_constraint_prop_names: dict[str, list[str]] = {}

    for dcid in misses:
        arcs = _node_arcs(raw, dcid)
        if not arcs:
            continue

        constraint_prop_names = _arc_values(arcs, "constraintProperties")
        sv_constraint_prop_names[dcid] = constraint_prop_names

        partial[dcid] = StatVarFeatures(
            dcid=dcid,
            name=(_arc_values(arcs, "name") or [None])[0],
            description=(_arc_values(arcs, "description") or [None])[0],
            population_type=_arc_values(arcs, "populationType"),
            measured_property=_arc_values(arcs, "measuredProperty"),
            stat_type=_arc_values(arcs, "statType"),
            measurement_qualifier=_arc_values(arcs, "measurementQualifier"),
            measurement_denominator=_arc_values(arcs, "measurementDenominator"),
            measurement_method=_arc_values(arcs, "measurementMethod"),
            observation_period=_arc_values(arcs, "observationPeriod"),
            unit=_arc_values(arcs, "unit"),
            member_of=_arc_values(arcs, "memberOf"),
            constraints={},
        )

    # Collect the union of all constraint property names across the candidate
    # set; make one additional batched call to fetch those arcs.
    union_constraint_props: list[str] = sorted(
        {p for names in sv_constraint_prop_names.values() for p in names}
    )

    if union_constraint_props:
        constraint_expr = "->[" + ",".join(union_constraint_props) + "]"
        present_dcids = list(partial.keys())
        c_raw = client.node.fetch(node_dcids=present_dcids, expression=constraint_expr).to_dict()

        for dcid, feats in partial.items():
            c_arcs = _node_arcs(c_raw, dcid)
            declared = sv_constraint_prop_names.get(dcid, [])
            constraints: dict[str, list[str]] = {}
            for prop in declared:
                vals = _arc_values(c_arcs, prop)
                if vals:
                    constraints[prop] = vals
            # Replace the dataclass (frozen=False, so direct attribute write works)
            partial[dcid] = StatVarFeatures(
                dcid=feats.dcid,
                name=feats.name,
                description=feats.description,
                population_type=feats.population_type,
                measured_property=feats.measured_property,
                stat_type=feats.stat_type,
                measurement_qualifier=feats.measurement_qualifier,
                measurement_denominator=feats.measurement_denominator,
                measurement_method=feats.measurement_method,
                observation_period=feats.observation_period,
                unit=feats.unit,
                member_of=feats.member_of,
                constraints=constraints,
            )

    # Populate cache and merge into the result. Iterate `misses` rather than
    # `partial` so the result preserves the (deduplicated) input order.
    with _cache_lock:
        for dcid in misses:
            feats = partial.get(dcid)
            if feats is not None:
                _features_cache[dcid] = feats
                result[dcid] = feats

    return result


# Module-level LRU cache for stat_var_features single-SV wrapper.
_stat_var_features_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def stat_var_features(*, sv_dcid: str) -> StatVarFeatures:
    """Project an SV node into its structured features.

    Thin wrapper around ``stat_var_features_batch`` for the single-SV case.
    Cached at module level — the same SV recurs across many queries.
    """
    with _cache_lock:
        cached = _stat_var_features_cache_lru.get(sv_dcid)
    if cached is not None:
        return cached
    batch = stat_var_features_batch(sv_dcids=[sv_dcid])
    if sv_dcid not in batch:
        # Return an empty feature record rather than raising, so the CLI still
        # works for unknown DCIDs (mirrors the old ->* behaviour).
        result = StatVarFeatures(dcid=sv_dcid)
    else:
        result = batch[sv_dcid]
    with _cache_lock:
        _stat_var_features_cache_lru[sv_dcid] = result
    return result


# Module-level cache for per-entity SV inventories. Shared between
# ``variables_for_entity`` and ``variables_for_entities_batch`` so that
# a prior single-entity call warms the cache for future batch calls.
_entity_svs_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=512)


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
        client = get_client()
        by_entity = client.observation.fetch_available_statistical_variables(entity_dcids=misses)
        with _cache_lock:
            for dcid in misses:
                svs = tuple(by_entity.get(dcid, []))
                _entity_svs_cache[dcid] = svs
                result[dcid] = svs

    return result


# Module-level LRU cache for variables_for_entity single-entity wrapper.
_variables_for_entity_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def variables_for_entity(*, entity_dcid: str) -> tuple[str, ...]:
    """Return the SV DCIDs that have observation data for one entity.

    Thin wrapper around ``variables_for_entities_batch`` for the single-entity
    case. Cached at module level — an entity's SV inventory is stable within a
    session and the same place is queried repeatedly.
    """
    with _cache_lock:
        cached = _variables_for_entity_cache_lru.get(entity_dcid)
    if cached is not None:
        return cached
    result = variables_for_entities_batch(entity_dcids=(entity_dcid,)).get(entity_dcid, ())
    with _cache_lock:
        _variables_for_entity_cache_lru[entity_dcid] = result
    return result


# Module-level cache for targeted presence checks. Keyed by
# (sorted_variable_dcids, sorted_entity_dcids) so the result is reused when
# the same SVxentity combination is queried again (e.g. on a second pipeline
# run within the same session). Cleared by tests via ``_presence_cache.clear()``.
_presence_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)


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

    client = get_client()
    raw = client.observation.fetch(
        variable_dcids=list(variable_dcids),
        entity_dcids=list(entity_dcids),
        date="LATEST",
    ).to_dict()

    # Response shape: {"byVariable": {sv_dcid: {"byEntity": {entity_dcid: ...}}}}
    by_variable: dict[str, Any] = raw.get("byVariable", {})
    present: set[str] = set()
    for sv_dcid in variable_dcids:
        sv_data = by_variable.get(sv_dcid, {})
        by_entity: dict[str, Any] = sv_data.get("byEntity", {})
        for entity_data in by_entity.values():
            if entity_data.get("orderedFacets"):
                present.add(sv_dcid)
                break

    result = frozenset(present)
    with _cache_lock:
        _presence_cache[cache_key] = result
    return result


def _short_nodes(arcs: dict[str, Any], prop: str) -> list[dict[str, str]]:
    """Return ``[{dcid, name}, ...]`` for nodes on the given arc."""
    out: list[dict[str, str]] = []
    for n in arcs.get(prop, {}).get("nodes", []):
        if "dcid" in n:
            out.append({"dcid": n["dcid"], "name": n.get("name", "")})
    return out


# Module-level LRU cache for child_vars_of_groups.
_child_vars_of_groups_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def child_vars_of_groups(*, svg_group_dcids: tuple[str, ...]) -> dict[str, list[str]]:
    """Fetch child SV DCIDs for a batch of StatVarGroup DCIDs via ``<-memberOf``.

    Uses a single ``/v2/node`` call to retrieve all SVs that declare
    ``memberOf`` pointing to one of the given group DCIDs.

    Returns a dict mapping each group DCID to its list of child SV DCIDs.
    Missing groups (no members, or not present in the response) are included
    with an empty list.  Returns an empty dict on any API error so callers
    can degrade gracefully.

    Cached at module level — the same SVG hierarchy is stable within a session.

    Args:
        svg_group_dcids: Tuple (not list — cache requires hashable args) of
            StatVarGroup DCIDs whose child SVs are needed.

    Returns:
        ``{group_dcid: [child_sv_dcid, ...]}`` for every requested group.
    """
    if not svg_group_dcids:
        return {}

    cache_key = svg_group_dcids
    with _cache_lock:
        cached = _child_vars_of_groups_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = get_client()
        raw = client.node.fetch(
            node_dcids=list(svg_group_dcids),
            expression="<-memberOf",
        ).to_dict()
    except Exception:
        return {}

    result: dict[str, list[str]] = {dcid: [] for dcid in svg_group_dcids}
    for group_dcid in svg_group_dcids:
        arcs = raw.get("data", {}).get(group_dcid, {}).get("arcs", {})
        sv_nodes = arcs.get("memberOf", {}).get("nodes", [])
        result[group_dcid] = [n["dcid"] for n in sv_nodes if "dcid" in n]

    with _cache_lock:
        _child_vars_of_groups_cache_lru[cache_key] = result
    return result


# Module-level arc cache for expand_topic's BFS walk.
# Maps (dcid, expression) → list of child node dicts parsed from the response.
# Consulted before each BFS level fetch; populated as responses arrive.
_topic_arc_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def _fetch_relevant_variables_batch(
    dcids: list[str],
) -> dict[str, list[dict[str, object]]]:
    """Fetch ``->relevantVariable`` for a batch of topic DCIDs in one call.

    Returns a mapping from each DCID to its list of child node dicts (may be
    empty for DCIDs absent from the response).  Results are written into
    ``_topic_arc_cache`` so subsequent BFS levels can skip re-fetching any DCID
    that was already seen.
    """
    expression = "->relevantVariable"
    with _cache_lock:
        uncached = [d for d in dcids if (d, expression) not in _topic_arc_cache]
    if uncached:
        try:
            client = get_client()
            raw = client.node.fetch(
                node_dcids=uncached,
                expression=expression,
            ).to_dict()
        except Exception:
            raw = {}
        with _cache_lock:
            for d in uncached:
                nodes = (
                    raw.get("data", {})
                    .get(d, {})
                    .get("arcs", {})
                    .get("relevantVariable", {})
                    .get("nodes", [])
                )
                _topic_arc_cache[(d, expression)] = nodes
    with _cache_lock:
        return {d: _topic_arc_cache[(d, expression)] for d in dcids}


def _fetch_svpg_members_batch(
    svpg_dcids: list[str],
) -> dict[str, list[str]]:
    """Fetch ``->member`` for a batch of SVPG DCIDs in one call.

    Returns a mapping from SVPG DCID to its list of member SV DCIDs.  Results
    are written into ``_topic_arc_cache`` so overlapping subtrees skip
    re-fetching.  Fails open (returns empty lists on any error).
    """
    expression = "->member"
    with _cache_lock:
        uncached = [d for d in svpg_dcids if (d, expression) not in _topic_arc_cache]
    if uncached:
        try:
            client = get_client()
            raw = client.node.fetch(
                node_dcids=uncached,
                expression=expression,
            ).to_dict()
        except Exception:
            raw = {}
        with _cache_lock:
            for d in uncached:
                nodes = (
                    raw.get("data", {})
                    .get(d, {})
                    .get("arcs", {})
                    .get("member", {})
                    .get("nodes", [])
                )
                _topic_arc_cache[(d, expression)] = nodes
    result: dict[str, list[str]] = {}
    with _cache_lock:
        for d in svpg_dcids:
            member_dcids: list[str] = []
            for n in _topic_arc_cache[(d, expression)]:
                raw_dcid = n.get("dcid")
                if isinstance(raw_dcid, str):
                    member_dcids.append(raw_dcid)
            result[d] = member_dcids
    return result


def _classify_child(node: dict[str, object]) -> tuple[str, str] | None:
    """Return ``(dcid, kind)`` for a relevantVariable child node, or None.

    ``kind`` is one of ``"sv"``, ``"topic"``, or ``"svpg"``.  Type dispatch
    follows the precedence in the old recursive implementation: explicit
    ``types``/``typeOf`` first, then DCID-prefix fallback.
    """
    child_dcid = node.get("dcid")
    if not isinstance(child_dcid, str) or not child_dcid:
        return None

    raw_types = node.get("types") or node.get("typeOf") or []
    child_types: list[str] = (
        [t for t in raw_types if isinstance(t, str)] if isinstance(raw_types, list) else []
    )

    if "StatisticalVariable" in child_types:
        return child_dcid, "sv"
    if "Topic" in child_types:
        return child_dcid, "topic"
    if "StatVarPeerGroup" in child_types:
        return child_dcid, "svpg"

    # Fallback: DCID-prefix dispatch.
    if child_dcid.startswith("dc/topic/") or child_dcid.startswith("ONE/topic/"):
        return child_dcid, "topic"
    if child_dcid.startswith("dc/svpg/"):
        return child_dcid, "svpg"
    return child_dcid, "sv"


# Module-level LRU cache for expand_topic results.
_expand_topic_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def expand_topic(*, dcid: str, max_depth: int = 3) -> tuple[str, ...]:
    """Walk a topic to its descendant StatisticalVariable DCIDs.

    Supports both ``dc/topic/*`` and ``ONE/topic/*``. For each child reached
    via ``->relevantVariable``, types it and dispatches:
      - StatisticalVariable → emit
      - Topic → recurse (depth-limited; visited set prevents cycles)
      - StatVarPeerGroup → fetch ``->member`` and emit children
      - other types → skip

    Uses a BFS that batches all DCIDs at each frontier into a single
    ``node.fetch`` call — at most ``2 * max_depth`` HTTP round-trips instead of
    one per node.

    Returns a tuple of unique SV DCIDs, capped at 200 entries.
    Fails open on any network error (returns whatever was collected so far).
    """
    cache_key = (dcid, max_depth)
    with _cache_lock:
        cached = _expand_topic_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    _TOPIC_EXPAND_CAP = 200

    # BFS: frontier maps topic DCID → remaining depth budget for that node.
    frontier: dict[str, int] = {dcid: max_depth}
    seen: set[str] = set()
    sv_dcids: list[str] = []
    seen_svs: set[str] = set()

    while frontier:
        # Only expand topics that still have depth budget and haven't been seen.
        to_expand: dict[str, int] = {
            d: depth for d, depth in frontier.items() if d not in seen and depth > 0
        }
        if not to_expand:
            break

        seen.update(to_expand)
        topics_this_level = list(to_expand)

        # One batched fetch for all topic DCIDs at this BFS level.
        children_by_dcid = _fetch_relevant_variables_batch(topics_this_level)

        next_frontier: dict[str, int] = {}
        svpg_this_level: list[str] = []

        for topic_d, depth in to_expand.items():
            for node in children_by_dcid.get(topic_d, []):
                classified = _classify_child(node)
                if classified is None:
                    continue
                child_dcid, kind = classified
                if kind == "sv":
                    if child_dcid not in seen_svs:
                        seen_svs.add(child_dcid)
                        sv_dcids.append(child_dcid)
                elif kind == "topic":
                    if child_dcid not in seen and child_dcid not in next_frontier:
                        next_frontier[child_dcid] = depth - 1
                elif kind == "svpg":
                    if child_dcid not in svpg_this_level:
                        svpg_this_level.append(child_dcid)

        # One batched fetch for all SVPGs discovered at this level.
        if svpg_this_level:
            members_by_svpg = _fetch_svpg_members_batch(svpg_this_level)
            for member_dcids in members_by_svpg.values():
                for member_dcid in member_dcids:
                    if member_dcid not in seen_svs:
                        seen_svs.add(member_dcid)
                        sv_dcids.append(member_dcid)

        frontier = next_frontier

    result = tuple(sv_dcids[:_TOPIC_EXPAND_CAP])
    with _cache_lock:
        _expand_topic_cache_lru[cache_key] = result
    return result


@dataclass(frozen=True, slots=True)
class TopicMetadata:
    """Name and description for a Topic DCID, fetched from the graph."""

    dcid: str
    name: str | None
    description: str | None


# Module-level LRU cache for topic_metadata_batch results.
_topic_metadata_batch_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)


def topic_metadata_batch(*, dcids: tuple[str, ...]) -> dict[str, TopicMetadata]:
    """Batch-fetch ``name`` + ``description`` for Topic DCIDs.

    Uses the existing ``client.node.fetch`` primitive with the same
    ``_BATCH_PROPS`` pattern as ``stat_var_features_batch``. Falls back to
    ``None`` when properties are missing. Caching is by sorted tuple to
    maximise reuse across calls.

    Args:
        dcids: Tuple of Topic DCIDs (e.g. ``"dc/topic/HealthcareExpenditure"``).

    Returns:
        Mapping from DCID to ``TopicMetadata``. DCIDs absent from the response
        are still included with ``name=None`` and ``description=None`` so
        callers can safely look up any requested DCID without a ``KeyError``.
    """
    result: dict[str, TopicMetadata] = {
        d: TopicMetadata(dcid=d, name=None, description=None) for d in dcids
    }
    if not dcids:
        return result

    # Sort for stable cache key so different orderings of the same set hit the cache.
    cache_key = tuple(sorted(dcids))
    with _cache_lock:
        cached = _topic_metadata_batch_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = get_client()
        raw = client.node.fetch(
            node_dcids=list(dcids),
            expression="->[name,description]",
        ).to_dict()
    except Exception:
        return result

    for dcid in dcids:
        arcs = _node_arcs(raw, dcid)
        if not arcs:
            continue
        name_vals = _arc_values(arcs, "name")
        desc_vals = _arc_values(arcs, "description")
        result[dcid] = TopicMetadata(
            dcid=dcid,
            name=name_vals[0] if name_vals else None,
            description=desc_vals[0] if desc_vals else None,
        )

    with _cache_lock:
        _topic_metadata_batch_cache_lru[cache_key] = result
    return result


# Module-level cache for per-group info. Both ``variable_group`` (single) and
# ``variable_groups_batch`` (many) consult and populate this dict so the two
# functions share one source of truth — calling either warms the cache for the
# other. Cleared by tests via ``_vgroups_cache.clear()``.
_vgroups_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)


def variable_groups_batch(
    *,
    dcids: tuple[str, ...],
) -> dict[str, VariableGroupInfo]:
    """Fetch StatVarGroup info for a batch of group DCIDs.

    Hits the module-level ``_vgroups_cache`` first; only cache-miss DCIDs go
    to the network. With a warm cache the function returns without any API
    calls. Cold path: exactly two ``/v2/node`` calls regardless of N — one for
    ``->[name,specializationOf]`` (outgoing arcs) and one for
    ``<-[specializationOf,memberOf]`` (incoming arcs). Fails open: if either
    call raises, returns whatever was already cached without raising.

    Args:
        dcids: Tuple of StatVarGroup DCIDs to look up. Duplicates are
            deduplicated before the network call.

    Returns:
        Mapping from DCID to ``VariableGroupInfo`` for every input that
        resolved. DCIDs absent from both cache and API response are silently
        omitted.
    """
    if not dcids:
        return {}

    result: dict[str, VariableGroupInfo] = {}
    misses: list[str] = []
    seen: set[str] = set()
    with _cache_lock:
        for dcid in dcids:
            if dcid in seen:
                continue
            seen.add(dcid)
            cached = _vgroups_cache.get(dcid)
            if cached is not None:
                result[dcid] = cached
            else:
                misses.append(dcid)

    if not misses:
        return result

    try:
        client = get_client()
        out_raw = client.node.fetch(
            node_dcids=misses,
            expression="->[name,specializationOf]",
        ).to_dict()
        in_raw = client.node.fetch(
            node_dcids=misses,
            expression="<-[specializationOf,memberOf]",
        ).to_dict()
    except Exception:
        return result

    for dcid in misses:
        out_arcs = _node_arcs(out_raw, dcid)
        if not out_arcs and dcid not in out_raw.get("data", {}):
            # Node absent from both responses — skip; don't cache a negative.
            continue
        name_vals = _arc_values(out_arcs, "name")
        name = name_vals[0] if name_vals else ""
        parents = _short_nodes(out_arcs, "specializationOf")

        in_arcs = _node_arcs(in_raw, dcid)
        info = VariableGroupInfo(
            dcid=dcid,
            name=name,
            parents=parents,
            child_groups=_short_nodes(in_arcs, "specializationOf"),
            child_vars=_short_nodes(in_arcs, "memberOf"),
        )
        with _cache_lock:
            _vgroups_cache[dcid] = info
        result[dcid] = info

    return result


def variable_group(*, dcid: str) -> VariableGroupInfo:
    """Fetch a StatVarGroup with its parent, child groups, and child SVs via V2.

    Thin shim over ``variable_groups_batch`` for the single-DCID case. Shares
    the module-level ``_vgroups_cache`` so warm calls from either entry point
    hit the same in-memory store. Repeated DCIDs within a session cost zero
    network round-trips.

    Raises:
        KeyError: If the DCID is absent from both cache and API response.
    """
    return variable_groups_batch(dcids=(dcid,))[dcid]
