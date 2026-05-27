"""Indicator / stat-var feature fetching.

Resolves natural-language queries to StatVar / Topic candidates and fetches
structured graph features (populationType, constraints, etc.). get_client() is
looked up as a package attribute so that monkeypatched get_client is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from dc_search import retrieval as graph

from ._cache import (
    _cache_lock,
    _features_cache,
    _inverse_arcs_cache_lru,
    _resolve_indicator_cache_lru,
    _stat_var_features_cache_lru,
    _vgroups_cache,
)

logger = logging.getLogger(__name__)

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
class IndicatorCandidate:
    """One StatVar / Topic candidate returned by /v2/resolve."""

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
    # Constraint properties (gender, age, race, ...) — the fields that
    # distinguish similar SVs. Populated from unused ->* arcs.
    constraints: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class VariableGroupInfo:
    """A StatVarGroup node with its parent, child groups, and child SVs."""

    dcid: str
    name: str
    parents: list[dict[str, str]]
    child_groups: list[dict[str, str]]
    child_vars: list[dict[str, str]]


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

    client = graph.get_client()
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


def stat_var_features_batch(
    *,
    sv_dcids: list[str],
) -> dict[str, StatVarFeatures]:
    """Fetch structured features for a batch of SV DCIDs.

    Hits module-level cache first; cache misses go to the network.
    Cold path: ``/v2/node`` for named fields, then a second call for
    constraint arcs (gender, age, etc.) of SVs that declare constraintProperties.

    Returns a dict keyed by sv_dcid. SVs absent from the response are skipped
    and not cached.
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

    client = graph.get_client()
    expr = "->[" + ",".join(_BATCH_PROPS) + "]"
    raw = client.node.fetch(node_dcids=misses, expression=expr).to_dict()

    # Extract named fields and the constraint properties each SV declares.
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

    # Collect the union of constraint properties across all SVs;
    # fetch those arcs in one additional call.
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
            partial[dcid] = replace(feats, constraints=constraints)

    # Cache and merge into result. Iterate misses to preserve input order.
    with _cache_lock:
        for dcid in misses:
            feats = partial.get(dcid)
            if feats is not None:
                _features_cache[dcid] = feats
                result[dcid] = feats

    return result


def stat_var_features(*, sv_dcid: str) -> StatVarFeatures:
    """Fetch structured features for a single SV.

    Single-SV wrapper around ``stat_var_features_batch``, cached at module level.
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


def _short_nodes(arcs: dict[str, Any], prop: str) -> list[dict[str, str]]:
    """Return ``[{dcid, name}, ...]`` for nodes on the given arc."""
    out: list[dict[str, str]] = []
    for n in arcs.get(prop, {}).get("nodes", []):
        if "dcid" in n:
            out.append({"dcid": n["dcid"], "name": n.get("name", "")})
    return out


def variable_groups_batch(
    *,
    dcids: tuple[str, ...],
) -> dict[str, VariableGroupInfo]:
    """Fetch StatVarGroup info for a batch of group DCIDs.

    Hits module-level cache first; cache misses use the network.
    Cold path: exactly two ``/v2/node`` calls — one for outgoing arcs
    (name, parent groups), one for incoming arcs (child groups, child SVs).
    Fails open: if either call raises, returns cached results without raising.

    Args:
        dcids: Tuple of StatVarGroup DCIDs to look up (duplicates deduplicated).

    Returns:
        Mapping from DCID to ``VariableGroupInfo``. DCIDs absent from both
        cache and API are silently omitted.
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
        client = graph.get_client()
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
    """Fetch a StatVarGroup with its parent, child groups, and child SVs.

    Single-DCID wrapper around ``variable_groups_batch``.

    Raises:
        KeyError: If the DCID is absent from both cache and API response.
    """
    return variable_groups_batch(dcids=(dcid,))[dcid]


def svs_by_inverse_arcs(
    *,
    value_dcids: tuple[str, ...],
    properties: tuple[str, ...],
) -> dict[str, frozenset[str]]:
    """For each value DCID, return SVs that declare it on the given properties.

    Uses a single ``<-[prop1,prop2,...]`` ``/v2/node`` call. Cached at module
    level. Fails open: returns ``{}`` on any API error.

    Args:
        value_dcids: Constraint-value DCIDs to query for inbound arcs.
        properties: Property names to form the combined inbound arc expression.

    Returns:
        Mapping from each value DCID to frozenset of SVs declaring it via
        one of the requested properties. Absent value DCIDs map to empty frozensets.
    """
    if not value_dcids or not properties:
        return {}

    cache_key = (tuple(sorted(value_dcids)), tuple(sorted(properties)))
    with _cache_lock:
        cached = _inverse_arcs_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    try:
        expression = "<-[" + ",".join(sorted(properties)) + "]"
        client = graph.get_client()
        raw = client.node.fetch(
            node_dcids=list(value_dcids), expression=expression
        ).to_dict()
    except Exception:
        logger.warning(
            "svs_by_inverse_arcs failed for value_dcids=%s properties=%s",
            value_dcids,
            properties,
        )
        return {}

    result: dict[str, frozenset[str]] = {}
    for dcid in value_dcids:
        arcs = _node_arcs(raw, dcid)
        sv_dcids: set[str] = set()
        for prop in properties:
            for node in arcs.get(prop, {}).get("nodes", []):
                if "dcid" in node:
                    sv_dcids.add(node["dcid"])
        result[dcid] = frozenset(sv_dcids)

    with _cache_lock:
        _inverse_arcs_cache_lru[cache_key] = result
    return result
