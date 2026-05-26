"""Topic and group expansion helpers.

BFS walk of topic DCID hierarchies to leaf StatisticalVariable DCIDs, plus
batch metadata fetch. get_client() is looked up as a package attribute so that
monkeypatched get_client is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dc_search import retrieval as graph

from ._cache import (
    _cache_lock,
    _child_vars_of_groups_cache_lru,
    _expand_topic_cache_lru,
    _topic_arc_cache,
    _topic_metadata_batch_cache_lru,
)
from .indicator import _arc_values, _node_arcs

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TopicMetadata:
    """Name and description for a Topic DCID, fetched from the graph."""

    dcid: str
    name: str | None
    description: str | None


def _fetch_arc_nodes_batch(
    dcids: list[str],
    expression: str,
) -> dict[str, list[dict[str, object]]]:
    """Fetch a single-arc ``->`` expression for a batch of DCIDs.

    Returns ``{dcid: arc_nodes}`` keyed by the arc named in ``expression``.
    Results are cached by ``(dcid, expression)`` so subsequent BFS levels skip
    re-fetching seen DCIDs. Fails open (caches empty lists on any error).
    """
    arc_name = expression.removeprefix("->")
    with _cache_lock:
        uncached = [d for d in dcids if (d, expression) not in _topic_arc_cache]
    if uncached:
        try:
            client = graph.get_client()
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
                    .get(arc_name, {})
                    .get("nodes", [])
                )
                _topic_arc_cache[(d, expression)] = nodes
    with _cache_lock:
        return {d: _topic_arc_cache[(d, expression)] for d in dcids}


def _fetch_relevant_variables_batch(
    dcids: list[str],
) -> dict[str, list[dict[str, object]]]:
    """Fetch ``->relevantVariable`` for a batch of topic DCIDs."""
    return _fetch_arc_nodes_batch(dcids, "->relevantVariable")


def _fetch_svpg_members_batch(
    svpg_dcids: list[str],
) -> dict[str, list[str]]:
    """Fetch ``->member`` for a batch of SVPG DCIDs as child DCID lists.

    Results are cached; fails open (returns empty lists on any error).
    """
    nodes_by_dcid = _fetch_arc_nodes_batch(svpg_dcids, "->member")
    result: dict[str, list[str]] = {}
    for d, nodes in nodes_by_dcid.items():
        result[d] = [n["dcid"] for n in nodes if isinstance(n.get("dcid"), str)]
    return result


def _classify_child(node: dict[str, object]) -> tuple[str, str] | None:
    """Return ``(dcid, kind)`` for a relevantVariable child node, or None.

    ``kind`` is one of ``"sv"``, ``"topic"``, or ``"svpg"``.
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
        to_expand: dict[str, int] = {
            d: depth for d, depth in frontier.items() if d not in seen and depth > 0
        }
        if not to_expand:
            break

        seen.update(to_expand)
        topics_this_level = list(to_expand)

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


def topic_metadata_batch(*, dcids: tuple[str, ...]) -> dict[str, TopicMetadata]:
    """Batch-fetch ``name`` + ``description`` for Topic DCIDs.

    Args:
        dcids: Tuple of Topic DCIDs (e.g. ``"dc/topic/HealthcareExpenditure"``).

    Returns:
        Mapping from DCID to ``TopicMetadata``. All requested DCIDs are included
        (missing ones have ``name=None`` and ``description=None``).
    """
    result: dict[str, TopicMetadata] = {
        d: TopicMetadata(dcid=d, name=None, description=None) for d in dcids
    }
    if not dcids:
        return result

    cache_key = tuple(sorted(dcids))
    with _cache_lock:
        cached = _topic_metadata_batch_cache_lru.get(cache_key)
    if cached is not None:
        return cached

    try:
        client = graph.get_client()
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
        client = graph.get_client()
        raw = client.node.fetch(
            node_dcids=list(svg_group_dcids),
            expression="<-memberOf",
        ).to_dict()
    except Exception:
        return {}

    result: dict[str, list[str]] = {dcid: [] for dcid in svg_group_dcids}
    for group_dcid in svg_group_dcids:
        sv_nodes = _node_arcs(raw, group_dcid).get("memberOf", {}).get("nodes", [])
        result[group_dcid] = [n["dcid"] for n in sv_nodes if "dcid" in n]

    with _cache_lock:
        _child_vars_of_groups_cache_lru[cache_key] = result
    return result
