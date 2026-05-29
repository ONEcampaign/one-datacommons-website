"""Pipeline orchestration core — streaming generators and their helpers.

All patchable internal names (``_run_one_variable``, ``_ROUTE_TIMEOUT_S``,
``_resolve_place_dcids``) are defined here so patches intercept module-global lookups.
``materialize_many`` is invoked as ``hooks_module.materialize_many`` (a module-attribute
lookup, not a direct import) so a monkeypatched ``dc_search.hooks.materialize_many`` is honored.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dc_search import extraction, place_hierarchy, retrieval, slot_binding
from dc_search import hooks as hooks_module
from dc_search import shape as shape_module
from dc_search.events import (
    Done,
    DoneTelemetry,
    Event,
    Interpretation,
    Places,
    Result,
    Stage,
    Start,
)
from dc_search.extraction import ExtractedDate
from dc_search.hooks import HookContext
from dc_search.interpretation import (
    ChildPlace,
    PlaceAlternative,
    QueryInterpretation,
    ResolvedPlace,
)
from dc_search.place_role import classify_place_roles, place_directional_role
from dc_search.predicate import AnswerCollection, AskClarification, Predicate
from dc_search.retrieval import StatVarFeatures
from dc_search.shape import ShapeContext, build_shape_context
from dc_search.telemetry import TelemetryLLMUsage, Usage

from ._availability import (
    _availability_sort_key,
    _resolve_union_availability_checked,
    _resolve_union_availability_with_ranges,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Per-worker semaphore: bounds fan-out to 8 concurrent variable pipelines per worker.
_FANOUT_SEM: asyncio.Semaphore = asyncio.Semaphore(8)

# Maximum number of extracted variables before fan-out is capped.
MAX_VARIABLES: int = 6

# Soft deadline: matches app.py's route-level timeout.
_ROUTE_TIMEOUT_S: float = 25.0

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


class PipelineResult(BaseModel):
    """End-to-end result of a dc-search pipeline run."""

    model_config = ConfigDict(frozen=True)

    query: str
    answers: list[AnswerCollection]
    ask: AskClarification | None = None
    elapsed_s: float
    n_candidates: int
    n_shapes: int
    terminated_by: Literal["answer", "ask", "no_candidates", "error"]
    llm_usage: list[TelemetryLLMUsage]
    truncated: bool = False
    interpretation: QueryInterpretation | None = None
    """Buffered query interpretation assembled from Interpretation + Places events.

    ``None`` when neither event was emitted (simple-endpoint degenerate case).
    """


# ---------------------------------------------------------------------------
# Internal: per-variable step result carrying counts alongside the answer
# ---------------------------------------------------------------------------


@dataclass
class _VariableResult:
    """Carries the answer and telemetry counts from one variable pipeline run."""

    outcome: AnswerCollection | AskClarification
    n_candidates: int = 0
    n_shapes: int = 0
    # Set by the fan-out wrapper after the await; used by _build_done + _result_event.
    index: int = 0
    variable_label: str | None = None


@dataclass(frozen=True, slots=True)
class PlaceResolution:
    """Resolved place DCIDs plus child-expansion metadata for one query.

    ``dcids`` is the full ordered, de-duplicated resolved set (parents then
    children) consumed by availability re-rank and the role tuples. The two
    maps drive the interpretation echo only; empty when contained_in is false.

    The two maps are treated as read-only and are never hashed.
    """

    dcids: tuple[str, ...]
    parent_to_children: dict[
        str, tuple[tuple[str, str | None], ...]
    ]  # parent dcid -> (child_dcid, child_name)
    parent_to_child_type: dict[str, str]  # parent dcid -> derived child type


# ---------------------------------------------------------------------------
# Topic-dominance helpers
# ---------------------------------------------------------------------------

_TOPIC_DOMINANCE_ABS: float = 0.4
_TOPIC_DOMINANCE_RATIO: float = 2.0


def _dominant_topic_dcid(
    indicator_candidates: tuple,
) -> str | None:
    """Return the DCID of the dominant Topic candidate, or None.

    Dominant when rank-1 overall and score exceeds next-best non-Topic by
    ``_TOPIC_DOMINANCE_ABS`` (absolute) or ``_TOPIC_DOMINANCE_RATIO`` (ratio).
    """
    if not indicator_candidates:
        return None

    topic_cands = [c for c in indicator_candidates if any("Topic" in t for t in c.type_of)]
    if not topic_cands:
        return None

    top_overall = max(indicator_candidates, key=lambda c: c.score or 0.0)
    top_topic = max(topic_cands, key=lambda c: c.score or 0.0)
    if top_overall.dcid != top_topic.dcid:
        return None
    if top_topic.score is None:
        return None

    non_topic_cands = [c for c in indicator_candidates if not any("Topic" in t for t in c.type_of)]
    if not non_topic_cands:
        return top_topic.dcid

    next_best_score = max(c.score or 0.0 for c in non_topic_cands)
    delta = top_topic.score - next_best_score
    ratio = top_topic.score / max(next_best_score, 1e-9)
    if delta >= _TOPIC_DOMINANCE_ABS or ratio >= _TOPIC_DOMINANCE_RATIO:
        return top_topic.dcid

    return None


# ---------------------------------------------------------------------------
# Place resolution helpers (query-global, run once per request)
# ---------------------------------------------------------------------------


async def _build_resolved_places(
    entities: list[str] | None,
    resolution_task: asyncio.Task[PlaceResolution],
) -> list[ResolvedPlace]:
    """Await resolution_task, fetch canonical names, assemble ResolvedPlace objects.

    Fail-open: name-fetch failure leaves name/type as None. Returns [] when no
    places resolved. Alternatives come from resolve_places_batch candidates;
    rank-1 is primary, the rest become alternatives.

    For parents with contained-in expansion, emits one top-level ResolvedPlace
    per parent (input entity) with ``expanded=True``, ``child_type``, and
    ``children`` populated. Children are NOT emitted as their own top-level entries.
    Non-expanded parents render exactly as today.
    """
    try:
        resolution = await resolution_task
    except Exception:
        return []

    place_dcids = list(resolution.dcids)
    if not place_dcids:
        return []

    # Fetch canonical names for the full resolved DCID set (parents + children).
    try:
        names_map = await asyncio.to_thread(retrieval.place_names_batch, dcids=tuple(place_dcids))
    except Exception:
        names_map = {}

    # Fetch alternatives from resolve_places_batch (cached, no-op if already called).
    # Skip for simple endpoint (entities=None) or empty entity list.
    ent_list: list[str] = entities if entities is not None else []
    resolved_all: dict = {}
    if ent_list:
        try:
            resolved_all = await asyncio.to_thread(
                retrieval.resolve_places_batch, names=tuple(ent_list)
            )
        except Exception:
            resolved_all = {}

    from dc_search.config import load_config

    cap = load_config().child_place_cap

    out: list[ResolvedPlace] = []
    for entity in ent_list:
        # Look up this entity's rank-1 DCID from resolve_places_batch.
        candidates = resolved_all.get(entity)
        if candidates:
            parent_dcid: str | None = candidates[0].dcid
        else:
            parent_dcid = None

        # Alternatives: all candidates except the primary (rank-1).
        alt_candidates = resolved_all.get(entity, ())
        alternatives: list[PlaceAlternative] = [
            PlaceAlternative(dcid=c.dcid, name=None, type=c.dominant_type)
            for c in alt_candidates
            if c.dcid != parent_dcid
        ]

        if parent_dcid is None:
            # Entity failed to resolve — emit a stub with expanded=False.
            out.append(
                ResolvedPlace(
                    input_name=entity,
                    dcid=None,
                    expanded=False,
                )
            )
            continue

        name_entry = names_map.get(parent_dcid, (None, None))
        canonical_name, place_type = name_entry

        # Expansion echo: emit children if this parent was expanded.
        children_raw = resolution.parent_to_children.get(parent_dcid)
        if children_raw is not None:
            child_type = resolution.parent_to_child_type.get(parent_dcid)
            children: list[ChildPlace] = [
                ChildPlace(dcid=d, name=n, type=child_type) for d, n in children_raw[:cap]
            ]
            out.append(
                ResolvedPlace(
                    input_name=entity,
                    dcid=parent_dcid,
                    name=canonical_name,
                    type=place_type,
                    alternatives=alternatives,
                    expanded=True,
                    child_type=child_type,
                    children=children,
                )
            )
        else:
            out.append(
                ResolvedPlace(
                    input_name=entity,
                    dcid=parent_dcid,
                    name=canonical_name,
                    type=place_type,
                    alternatives=alternatives,
                )
            )

    # Simple endpoint (entities=None) — fall back to the flat DCID list as today.
    if not ent_list:
        for dcid in place_dcids:
            name_entry = names_map.get(dcid, (None, None))
            canonical_name, place_type = name_entry
            out.append(
                ResolvedPlace(
                    input_name=dcid,
                    dcid=dcid,
                    name=canonical_name,
                    type=place_type,
                )
            )

    return out


# ---------------------------------------------------------------------------
# Per-step private helpers
# ---------------------------------------------------------------------------


async def _retrieve(
    variable: str | None,
    query: str,
) -> tuple[tuple, list[str], dict[str, float]] | AskClarification:
    """Resolve indicator candidates and filter to SV/Topic DCIDs."""
    retrieval_query = variable if variable is not None else query

    from dc_search.config import load_config

    initial_k = load_config().initial_k
    candidates = await asyncio.to_thread(
        retrieval.resolve_indicator, query=retrieval_query, k=initial_k
    )

    sv_dcids = [
        c.dcid for c in candidates if any(t in ("StatisticalVariable", "Topic") for t in c.type_of)
    ][:initial_k]

    if not sv_dcids:
        return AskClarification(
            reason="no_candidates",
            message="No StatisticalVariable candidates surfaced for this query.",
        )

    retrieval_scores: dict[str, float] = {
        c.dcid: (c.score or 0.0) for c in candidates if c.score is not None
    }
    return candidates, sv_dcids, retrieval_scores


async def _short_circuit_topic(
    candidates: tuple,
    retrieval_scores: dict[str, float],
    variable: str | None,
    place_dcids: list[str],
    dates: list[ExtractedDate] | None = None,
) -> AnswerCollection | AskClarification | None:
    """Topic-dominance short-circuit; returns answer or None to continue.

    place_dcids is pre-resolved by the caller.
    """
    dominant_dcid = _dominant_topic_dcid(candidates)
    if dominant_dcid is None:
        return None

    union_avail: frozenset[str] = frozenset()
    avail_degraded = False
    if place_dcids:
        union_avail, avail_degraded = await asyncio.to_thread(
            _resolve_union_availability_checked, place_dcids
        )
    topic_predicate = Predicate(
        population_type=None,
        measured_property=None,
        constraints={"relevantTopic": dominant_dcid},
    )
    topic_ctx = HookContext(
        place_dcids=tuple(place_dcids),
        place_availability=union_avail if union_avail else None,
        retrieval_scores=retrieval_scores,
        raw_candidates=(),
        dates=dates or [],
        availability_degraded=avail_degraded,
    )
    answer = await asyncio.to_thread(
        hooks_module.materialize_many, (topic_predicate,), [], ctx=topic_ctx
    )
    if isinstance(answer, AnswerCollection):
        # The short-circuit skips the normal feature-fetch step, so the topic's
        # expanded members would otherwise carry bare DCIDs (no name/description/
        # unit). Fetch their features and the topic metadata concurrently
        # (fail-open), then re-project enriched variables. score/matched_sentence
        # stay None — members weren't retrieved individually — which is correct.
        async def _fetch_member_features():
            if not answer.sv_set:
                return {}
            try:
                return await asyncio.to_thread(
                    retrieval.stat_var_features_batch, sv_dcids=list(answer.sv_set)
                )
            except Exception:
                return {}

        async def _fetch_topic_meta():
            try:
                topic_meta = await asyncio.to_thread(
                    retrieval.topic_metadata_batch, dcids=(dominant_dcid,)
                )
                return topic_meta.get(dominant_dcid)
            except Exception:
                return None

        feats, meta = await asyncio.gather(_fetch_member_features(), _fetch_topic_meta())

        update: dict = {
            "answer_kind": "topic",
            "topic_name": meta.name if meta else None,
            "topic_description": meta.description if meta else None,
        }
        if feats:
            enriched_ctx = replace(topic_ctx, raw_candidates=tuple(feats.values()))
            update["variables"] = hooks_module._build_variables(answer.sv_set, enriched_ctx)
        if variable is not None:
            update["variable_label"] = variable
        answer = answer.model_copy(update=update)
    return answer


async def _expand_children(
    parent_dcids: list[str],
    expand_dcids: frozenset[str] | None = None,
) -> tuple[list[str], dict[str, tuple[tuple[str, str | None], ...]], dict[str, str]]:
    """Resolve each parent to its immediate children.

    ``expand_dcids`` restricts expansion to that subset of ``parent_dcids`` (the
    entities the query flagged as contained-in parents); parents outside it are
    kept whole — present in the combined result but contributing no children. When
    None, every parent is expanded (legacy whole-query behavior).

    Returns ``(combined_dcids, parent_to_children, parent_to_child_type)``.
    Fully fail-open — any network step failure leaves parents intact and the
    affected parent contributes no children. Never raises.
    """
    if not parent_dcids:
        return ([], {}, {})

    # place_names_batch is warm-cached after _resolve_place_dcids ran.
    try:
        names_map = await asyncio.to_thread(retrieval.place_names_batch, dcids=tuple(parent_dcids))
    except Exception:
        names_map = {}
    parent_type: dict[str, str | None] = {
        p: names_map.get(p, (None, None))[1] for p in parent_dcids
    }

    # Only this subset is expanded; the rest are kept whole (no child fetch).
    to_expand = [p for p in parent_dcids if expand_dcids is None or p in expand_dcids]

    # Admin-area parents need a country lookup for the per-country remap.
    admin_parents = [
        p for p in to_expand if place_hierarchy.needs_parent_country(parent_type[p])
    ]
    countries: dict[str, str | None] = {}
    if admin_parents:
        try:
            countries = await asyncio.to_thread(
                retrieval.parent_countries_batch, parent_dcids=tuple(admin_parents)
            )
        except Exception:
            countries = {p: None for p in admin_parents}

    # Derive each parent's country arg and immediate child type, grouping parents
    # by child type for one fetch per type.
    parent_to_child_type: dict[str, str] = {}
    groups: dict[str, list[str]] = {}  # child_type -> [parent_dcids]
    for p in to_expand:
        if parent_type[p] == "Country":
            # Country parents use their own DCID as the country arg.
            country = p
        elif p in admin_parents:
            country = countries.get(p)
        else:
            country = None
        ctype = place_hierarchy.default_child_type(
            parent_dcid=p,
            parent_type=parent_type[p],
            parent_country=country,
        )
        if ctype is None:
            continue
        parent_to_child_type[p] = ctype
        groups.setdefault(ctype, []).append(p)

    from dc_search.config import load_config

    cap = load_config().child_place_cap

    # One child_places_batch call per distinct child type.
    parent_to_children: dict[str, tuple[tuple[str, str | None], ...]] = {}
    for ctype, parents_of_type in groups.items():
        try:
            batch = await asyncio.to_thread(
                retrieval.child_places_batch,
                parent_dcids=tuple(parents_of_type),
                child_type=ctype,
                cap=cap,
            )
        except Exception:
            batch = {p: () for p in parents_of_type}
        for p, kids in batch.items():
            # Record child_type for every parent for which we derived it, even if
            # zero children returned (so the UI can surface "expanded to County (0 found)").
            parent_to_children[p] = kids

    # Build the combined DCID list: parents first (input order), then children
    # (sorted-by-dcid per parent, in parent input order), deduplicating.
    seen: set[str] = set()
    combined: list[str] = []
    for p in parent_dcids:
        if p not in seen:
            combined.append(p)
            seen.add(p)
    for p in parent_dcids:
        for child_dcid, _child_name in parent_to_children.get(p, ()):
            if child_dcid not in seen:
                combined.append(child_dcid)
                seen.add(child_dcid)

    # Pre-warm place_names_batch for the combined parent+children set so that
    # _build_resolved_places and _build_resolved_places_triples (which both call
    # place_names_batch(combined) concurrently after this task resolves) get cache
    # hits instead of cold round-trips (~300-500 ms each on contained-in queries).
    # We already have all the data: parent names/types from names_map (fetched
    # above), and child names from child_places_batch (typeOf not available for
    # children, but downstream consumers only use typeOf for parent DCIDs).
    if combined:
        combined_names: dict[str, tuple[str | None, str | None]] = {}
        # Seed parents from the names_map already fetched at the top of this function.
        for dcid in parent_dcids:
            combined_names[dcid] = names_map.get(dcid, (None, None))
        # Seed children from the (dcid, name) pairs returned by child_places_batch.
        for p in parent_dcids:
            for child_dcid, child_name in parent_to_children.get(p, ()):
                if child_dcid not in combined_names:
                    combined_names[child_dcid] = (child_name, None)
        combined_key = tuple(sorted(combined))
        with retrieval._cache_lock:
            # Only seed if not already present (e.g. a second call in the same session).
            if combined_key not in retrieval._place_names_cache:
                retrieval._place_names_cache[combined_key] = combined_names

    return (combined, parent_to_children, parent_to_child_type)


async def _resolve_place_dcids(
    query: str,
    entities: list[str] | None,
    *,
    contained_in_parents: tuple[str, ...] = (),
) -> PlaceResolution:
    """Resolve place names to DCIDs for availability re-rank.

    Default endpoint (entities non-None): trust LLM extraction, including empty
    list (no place found). Never falls back to token path.

    Simple endpoint (entities=None): use deterministic extract_place_tokens.
    ``contained_in_parents`` is always empty for the simple endpoint.

    ``contained_in_parents`` names the subset of ``entities`` to expand into their
    immediate child places (e.g. "Africa" in "grants France to African countries",
    leaving "France" whole). Entities not in this list resolve to a single DCID and
    contribute no children. Empty list -> no expansion, byte-identical to the old
    list[str] behavior (short-circuits before any child/country call).
    """
    if entities is not None:
        # Default endpoint: trust LLM-extracted names authoritatively.
        # Empty list means no place found; do not fall back to token path.
        if not entities:
            return PlaceResolution(dcids=(), parent_to_children={}, parent_to_child_type={})
        # Resolve to DCIDs via mixer in one batched call.
        resolved = await asyncio.to_thread(retrieval.resolve_places_batch, names=tuple(entities))
        # Iterate in order to match input entity ordering, tracking which resolved
        # DCIDs came from contained-in parent entities (matched by surface name).
        expand_names = {n.strip().casefold() for n in contained_in_parents}
        n_unresolved = 0
        parent_dcids: list[str] = []
        expand_dcids: set[str] = set()
        for name in entities:
            candidates = resolved.get(name)
            if candidates:
                dcid = candidates[0].dcid
                parent_dcids.append(dcid)
                if name.strip().casefold() in expand_names:
                    expand_dcids.add(dcid)
            else:
                n_unresolved += 1
        if n_unresolved:
            logger.debug("resolve_place_dcids: %d entity/entities unresolved", n_unresolved)

        # Short-circuit when nothing to expand — byte-identical resolved set to today.
        if not expand_dcids:
            return PlaceResolution(
                dcids=tuple(parent_dcids), parent_to_children={}, parent_to_child_type={}
            )

        # Expansion path: expand only the flagged parents; keep the rest whole.
        combined, parent_to_children, parent_to_child_type = await _expand_children(
            parent_dcids, frozenset(expand_dcids)
        )
        return PlaceResolution(
            dcids=tuple(combined),
            parent_to_children=parent_to_children,
            parent_to_child_type=parent_to_child_type,
        )

    # Simple endpoint: deterministic token fallback; contained_in is always False here.
    dcids = await asyncio.to_thread(shape_module.extract_place_tokens, query)
    return PlaceResolution(dcids=tuple(dcids), parent_to_children={}, parent_to_child_type={})


async def _rerank_by_availability(
    sv_dcids: list[str],
    place_dcids: list[str],
) -> tuple[list[str], frozenset[str], dict[str, tuple[str | None, str | None]], bool]:
    """Re-rank by availability; returns (reranked_sv_dcids, union_avail, ranges, degraded)."""
    union_avail: frozenset[str] = frozenset()
    dcid_to_date_range: dict[str, tuple[str | None, str | None]] = {}
    avail_degraded = False
    if place_dcids:
        union_avail, dcid_to_date_range, avail_degraded = await asyncio.to_thread(
            _resolve_union_availability_with_ranges, place_dcids, tuple(sv_dcids)
        )
        if union_avail:
            sv_dcids = sorted(
                sv_dcids,
                key=lambda d: _availability_sort_key(d, union_avail),
            )
    return sv_dcids, union_avail, dcid_to_date_range, avail_degraded


async def _fetch_features(sv_dcids: list[str]) -> list[StatVarFeatures]:
    """Batch-fetch StatVarFeatures for the filtered SV DCIDs."""
    features_dict = await asyncio.to_thread(retrieval.stat_var_features_batch, sv_dcids=sv_dcids)
    return list(features_dict.values())


def _build_shape(
    query: str,
    feature_list: list,
    retrieval_scores: dict[str, float],
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...] = (),
    contained_in: bool = False,
    parent_to_children: dict | None = None,
) -> ShapeContext | AskClarification:
    """Build shape context (pure, no I/O)."""
    from dc_search.config import load_config

    shape_ctx = build_shape_context(
        query,
        feature_list,
        retrieval_scores=retrieval_scores,
        resolved_places=resolved_places,
        contained_in=contained_in,
        parent_to_children=parent_to_children,
        max_shapes=load_config().max_shapes,
    )
    if not shape_ctx.shapes:
        return AskClarification(
            reason="no_shapes",
            message=(
                f"Retrieval returned {len(feature_list)} candidates, but "
                "shape grouping produced no usable shapes. Try a more specific query."
            ),
        )
    return shape_ctx


async def _enrich_topic_metadata(shape_ctx: ShapeContext) -> ShapeContext:
    """Enrich ShapeContext with human-readable topic metadata."""
    topic_dcids: tuple[str, ...] = tuple(
        sorted({s.member_dcids[0] for s in shape_ctx.shapes if s.is_topic and s.member_dcids})
    )
    if not topic_dcids:
        return shape_ctx
    topic_meta = await asyncio.to_thread(retrieval.topic_metadata_batch, dcids=topic_dcids)
    # Use dataclasses.replace so ALL fields (including contained_in /
    # parent_to_children) are forwarded automatically.
    return replace(shape_ctx, topic_metadata=topic_meta)


async def _bind_slot(
    shape_ctx: ShapeContext,
    slot_bind_usages: list[Usage],
) -> slot_binding.BindResult | AskClarification:
    """LLM slot-binding call; appends usage to slot_bind_usages."""
    bound = await slot_binding.bind(shape_ctx)
    last_usage = slot_binding.get_last_usage()
    if last_usage is not None:
        slot_bind_usages.append(last_usage)
    return bound


async def _materialize(
    predicates: tuple,
    feature_list: list,
    place_dcids: list[str],
    union_avail: frozenset[str],
    retrieval_scores: dict[str, float],
    variable: str | None,
    dates: list[ExtractedDate] | None = None,
    availability_degraded: bool = False,
    dcid_to_sentence: dict[str, str] | None = None,
    dcid_to_date_range: dict[str, tuple[str | None, str | None]] | None = None,
    all_resolved_dcids: tuple[str, ...] = (),
    defaulted_recipient: bool = False,
) -> AnswerCollection | AskClarification:
    """Materialize via hooks (via to_thread for blocking mixer HTTP calls).

    ``place_dcids`` is the *donor* subset; ``all_resolved_dcids`` is the union
    before donor-narrowing. The terminal ``ProjectionEnrichmentHook`` reads
    both: when they differ, it recomputes availability/date-ranges against
    the donor set (the bound recipient would otherwise show up as a phantom
    observation entity). ``defaulted_recipient`` drives the
    ``interpreted_place_as_recipient`` caveat in the same hook.
    """
    hook_ctx = HookContext(
        place_dcids=tuple(place_dcids),
        place_availability=union_avail if union_avail else None,
        retrieval_scores=retrieval_scores,
        raw_candidates=tuple(feature_list),
        dates=dates or [],
        availability_degraded=availability_degraded,
        dcid_to_sentence=dcid_to_sentence or {},
        dcid_to_date_range=dcid_to_date_range or {},
        all_resolved_dcids=all_resolved_dcids,
        defaulted_recipient=defaulted_recipient,
    )
    answer = await asyncio.to_thread(
        hooks_module.materialize_many, predicates, feature_list, ctx=hook_ctx
    )
    if variable is not None and isinstance(answer, AnswerCollection):
        answer = answer.model_copy(update={"variable_label": variable})
    return answer


# ---------------------------------------------------------------------------
# Shared core
# ---------------------------------------------------------------------------


async def _build_resolved_places_triples(
    place_dcids: list[str],
    entities: list[str] | None,
    *,
    query: str = "",
    parent_to_children: dict[str, tuple[tuple[str, str | None], ...]] | None = None,
) -> tuple[tuple[str, str | None, str | None, str], ...]:
    """Build (dcid, canonical_name, input_surface, role) 4-tuples for the default endpoint.

    ``role`` is computed once per query from the ORIGINAL full query string via
    ``place_directional_role`` — NOT from any per-variable scoped shape query
    (fan-out scoping must not corrupt directional detection).

    Calls resolve_places_batch (LRU-cached, warm after _resolve_place_dcids ran)
    to re-derive each entity's own DCID, so entities that failed to resolve are
    naturally skipped and the surface string is always paired with the DCID that
    came from that specific entity.  Calls place_names_batch over the FULL
    resolved set (cold on first request; warm on subsequent calls within the same
    process — racing with the Places-event task, so the first request may be cold)
    so the name cache is primed for every resolved place.

    When ``parent_to_children`` is provided (contained-in expansion), appends one
    4-tuple per child place with ``input_surface=None`` and role ``"ambiguous"``
    (Decision 1 — children were never typed by the user so the directional scan
    returns "ambiguous" for them; they are donors by default).

    Returns empty tuple for the simple endpoint (entities=None) — the simple
    endpoint does not use place-role binding.
    """
    if entities is None or not place_dcids:
        return ()

    # Re-resolve to get per-entity DCID mapping; the cache makes this a
    # near-zero-cost repeat call after _resolve_place_dcids already ran.
    try:
        resolved = await asyncio.to_thread(retrieval.resolve_places_batch, names=tuple(entities))
    except Exception:
        resolved = {}

    try:
        names_map = await asyncio.to_thread(retrieval.place_names_batch, dcids=tuple(place_dcids))
    except Exception:
        names_map = {}

    tuples: list[tuple[str, str | None, str | None, str]] = []
    for entity in entities:
        candidates = resolved.get(entity)
        if not candidates:
            # Entity did not resolve; skip to keep surface↔DCID aligned.
            continue
        dcid = candidates[0].dcid
        name_entry = names_map.get(dcid, (None, None))
        canonical_name = name_entry[0] if name_entry else None
        # Compute directional role from the ORIGINAL full query (not the per-variable
        # scoped shape_query) so "from X to Y" grammar is preserved across fan-out.
        role = place_directional_role(
            query=query,
            input_surface=entity,
            canonical_name=canonical_name,
            place_dcid=dcid,
        )
        tuples.append((dcid, canonical_name, entity, role))

    # Append child 4-tuples when contained-in expansion is active.
    # Children carry input_surface=None; the directional scan naturally returns
    # "ambiguous" (they were never typed by the user — Decision 1).
    if parent_to_children:
        for _parent, children in parent_to_children.items():
            for child_dcid, child_name in children:
                child_role = place_directional_role(
                    query=query,
                    input_surface=None,
                    canonical_name=child_name,
                    place_dcid=child_dcid,
                )
                tuples.append((child_dcid, child_name, None, child_role))

    return tuple(tuples)


async def _run_one_variable(
    variable: str | None,
    query: str,
    *,
    resolution_task: asyncio.Task[PlaceResolution],
    dates: list[ExtractedDate] | None = None,
    entities: list[str] | None = None,
    contained_in: bool = False,
    slot_bind_usages: list[Usage],
) -> _VariableResult:
    """Shared pipeline core: retrieve → shape → bind → materialize.

    Args:
        variable: Extracted variable for scoping; None for simple endpoint.
        query: Original user query (forwarded to LLM for context).
        resolution_task: Shared place-resolution task (resolve + contained-in expansion).
            Awaited once inside the body (just before the topic short-circuit); yields the
            full resolved DCID set and the parent->children expansion map. Started concurrently
            with retrieval so resolution latency is hidden behind the retrieval round-trip.
        dates: Extracted date references (None on simple endpoint).
        entities: Extracted place names (as written in the query). Folded into
            the shape-building query alongside ``variable`` so slot-binding sees
            the place without the sibling variables. Ignored when ``variable`` is
            None (simple endpoint). None on the simple endpoint.
        slot_bind_usages: Mutable list appended to for LLM usage aggregation.

    Returns:
        _VariableResult with answer/clarification and telemetry counts.
    """
    # Start retrieval concurrently; it is place-independent, so it overlaps resolution.
    retrieve_task = asyncio.create_task(_retrieve(variable, query))
    try:
        # Join place resolution (shared dcid_task) — the first real consumer of resolved
        # places is the topic short-circuit below.
        resolution = await resolution_task
        place_dcids = list(resolution.dcids)
        parent_to_children = resolution.parent_to_children or None

        retrieve_out = await retrieve_task
        if isinstance(retrieve_out, AskClarification):
            return _VariableResult(outcome=retrieve_out)

        candidates, sv_dcids, retrieval_scores = retrieve_out
        n_candidates = len(sv_dcids)

        # Build dcid_to_sentence map from candidates.
        dcid_to_sentence: dict[str, str] = {
            c.dcid: c.sentence for c in candidates if getattr(c, "sentence", None)
        }

        # Topic-dominance short-circuit.
        topic_answer = await _short_circuit_topic(
            candidates, retrieval_scores, variable, place_dcids=place_dcids, dates=dates
        )
        if topic_answer is not None:
            return _VariableResult(outcome=topic_answer, n_candidates=n_candidates)

        # Re-rank and feature fetch are order-independent (features keyed by DCID),
        # so overlap them. Post-gather sort restores availability-priority order
        # for downstream consumers (_build_shape, materialize_many).
        (
            (sv_dcids, union_avail, dcid_to_date_range, avail_degraded),
            features_unordered,
        ) = await asyncio.gather(
            _rerank_by_availability(sv_dcids, place_dcids),
            _fetch_features(sv_dcids),
        )
        rerank_pos = {d: i for i, d in enumerate(sv_dcids)}
        feature_list = sorted(
            features_unordered, key=lambda f: rerank_pos.get(f.dcid, len(sv_dcids))
        )

        # Build (dcid, canonical_name, input_surface, role) 4-tuples for the default endpoint.
        # Role is computed from the ORIGINAL full `query` — NOT from the per-variable
        # scoped `shape_query` built below — so directional grammar ("from X to Y") is
        # preserved across fan-out.  Simple endpoint passes resolved_places=()
        # — it already works on the full query and does not use place-role binding.
        resolved_places = await _build_resolved_places_triples(
            place_dcids, entities, query=query, parent_to_children=parent_to_children
        )

        # Shape context. In multi-variable fan-out, scope the shape-building query
        # to the per-variable phrase (plus any extracted places) rather than the full
        # query, so the slot-binding LLM's shape election isn't biased by sibling
        # variables (e.g. "gdp" dragging "unemployment" into the broad Economy topic).
        # Mirrors _retrieve, which already scopes retrieval to `variable`. Entities are
        # kept so the place survives for place-as-constraint binding (e.g. CRS_DAC
        # recipient). The simple endpoint (variable is None) keeps the full query.
        if variable is not None:
            shape_query = f"{variable} in {', '.join(entities)}" if entities else variable
        else:
            shape_query = query
        shape_or_ask = _build_shape(
            shape_query,
            feature_list,
            retrieval_scores,
            resolved_places,
            contained_in=contained_in,
            parent_to_children=parent_to_children,
        )
        if isinstance(shape_or_ask, AskClarification):
            return _VariableResult(outcome=shape_or_ask, n_candidates=n_candidates)

        shape_ctx = shape_or_ask
        n_shapes = len(shape_ctx.shapes)

        # Topic metadata enrichment (carries resolved_places through the rebuild).
        shape_ctx = await _enrich_topic_metadata(shape_ctx)

        # Slot binding LLM call — returns BindResult with attribute access.
        bound = await _bind_slot(shape_ctx, slot_bind_usages)
        if isinstance(bound, AskClarification):
            return _VariableResult(outcome=bound, n_candidates=n_candidates, n_shapes=n_shapes)

        # Unpack BindResult via attribute access.
        predicates = bound.predicates
        defaulted_recipient = bound.defaulted_recipient

        # Donor set = resolved places NOT bound as a constraint value in any predicate.
        # Pass donor_dcids as the HookContext.place_dcids so materialize_many treats
        # only donors as observation entities.
        donor_dcids: tuple[str, ...] = classify_place_roles(
            resolved_places=resolved_places, predicates=predicates
        )

        # Materialize via hooks using the donor set as the entity set.
        # Enrichment (donor-set availability recompute, backup feature fetch,
        # interpreted_place_as_recipient caveat) runs as the terminal
        # ProjectionEnrichmentHook inside the chain — the orchestrator just
        # threads the inputs it needs (all_resolved_dcids, defaulted_recipient)
        # through HookContext.
        answer = await _materialize(
            predicates,
            feature_list,
            list(donor_dcids),
            # Pre-bind availability/ranges were computed against the full place_dcids;
            # ProjectionEnrichmentHook supersedes them when the donor set differs.
            union_avail,
            retrieval_scores,
            variable,
            dates=dates,
            availability_degraded=avail_degraded,
            dcid_to_sentence=dcid_to_sentence,
            dcid_to_date_range=dcid_to_date_range,
            all_resolved_dcids=tuple(place_dcids),
            defaulted_recipient=defaulted_recipient,
        )

        return _VariableResult(outcome=answer, n_candidates=n_candidates, n_shapes=n_shapes)
    finally:
        retrieve_task.cancel()


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


async def _cancel_and_drain(*tasks: asyncio.Task) -> None:
    """Cancel every task then await them, swallowing results/exceptions.

    Ensures no task outlives the generator response when the consumer cancels.
    """
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _slot_usages(usages: list[Usage]) -> list[TelemetryLLMUsage]:
    """Map raw Usage records to TelemetryLLMUsage entries with step="slot_bind"."""
    return [
        TelemetryLLMUsage(
            step="slot_bind",
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cached_input_tokens=u.cached_input_tokens,
            model=u.model,
            latency_s=u.latency_s,
        )
        for u in usages
    ]


def _result_event(
    index: int,
    label: str | None,
    outcome: AnswerCollection | AskClarification,
) -> Result:
    """Build a Result event, deriving outcome_kind from the outcome type.

    All three call sites (fallback, fan-out, simple) go through here so
    outcome_kind and answer are always in lockstep.
    """
    outcome_kind: Literal["answer", "clarification"] = (
        "answer" if isinstance(outcome, AnswerCollection) else "clarification"
    )
    return Result(index=index, variable_label=label, outcome_kind=outcome_kind, answer=outcome)


def _log_llm_usage(llm_usage: list[TelemetryLLMUsage]) -> None:
    """Emit one greppable line of per-request LLM token usage + cache-hit ratio.

    Grep Cloud Run logs for ``dc_search llm_usage`` to read it; ``cache_hit_pct``
    is the fraction of input tokens served from Gemini's context cache. Emitted
    at INFO via the ``dc_search`` logger (configured at app startup), once per
    request from the single terminal Done builder.
    """
    if not llm_usage:
        return
    total_in = sum(u.input_tokens for u in llm_usage)
    total_cached = sum(u.cached_input_tokens for u in llm_usage)
    per_step = " ".join(
        f"{u.step}(in={u.input_tokens},cached={u.cached_input_tokens},out={u.output_tokens})"
        for u in llm_usage
    )
    pct = (100.0 * total_cached / total_in) if total_in else 0.0
    logger.info(
        "dc_search llm_usage: %s total_in=%d total_cached=%d cache_hit_pct=%.1f",
        per_step,
        total_in,
        total_cached,
        pct,
    )


def _build_done(
    t0: float,
    var_results: list[_VariableResult],
    llm_usage: list[TelemetryLLMUsage],
    *,
    truncated: bool,
    timed_out: bool = False,
) -> Done:
    """Assemble the terminal Done event from collected branch results.

    Mirrors run_default and run_simple terminated_by logic for both single and
    multi-element var_results so the buffered drain reproduces PipelineResult.
    """
    sorted_results = sorted(var_results, key=lambda vr: vr.index)

    answers = [vr.outcome for vr in sorted_results if isinstance(vr.outcome, AnswerCollection)]
    ask: AskClarification | None = None
    if not answers:
        ask = next(
            (vr.outcome for vr in sorted_results if isinstance(vr.outcome, AskClarification)),
            None,
        )

    # terminated_by ladder: no_candidates, ask, or answer.
    terminated_by: Literal["answer", "ask", "no_candidates", "error"]
    if ask is not None and ask.reason == "no_candidates":
        terminated_by = "no_candidates"
    elif ask is not None:
        terminated_by = "ask"
    else:
        terminated_by = "answer"

    n_candidates = sum(vr.n_candidates for vr in var_results)
    n_shapes = sum(vr.n_shapes for vr in var_results)
    elapsed = time.perf_counter() - t0

    _log_llm_usage(llm_usage)

    telemetry = DoneTelemetry(
        llm_usage=llm_usage,
        n_candidates=n_candidates,
        n_shapes=n_shapes,
        terminated_by=terminated_by,
        truncated=truncated,
    )
    return Done(
        telemetry=telemetry,
        elapsed_s=elapsed,
        terminated_by=terminated_by,
        truncated=truncated,
        timed_out=timed_out,
        ask=ask,
    )


# ---------------------------------------------------------------------------
# Streaming generators
# ---------------------------------------------------------------------------


async def stream_default(query: str) -> AsyncIterator[Event]:
    """Yield typed events for the default (multi-variable) pipeline.

    Order: start → interpretation → {places, result* interleaved} → done.
    Guarantees: interpretation precedes places; places never blocks result emission.
    Falls back to simple semantics on zero variables.

    Soft-deadline: yields Done(timed_out=True) with partials if 25s budget exhausted.
    """
    t0 = time.perf_counter()
    yield Start(query=query, mode="default")

    extraction_result, extract_usage = await extraction.extract(query)  # may raise → propagates

    truncated = len(extraction_result.variables) > MAX_VARIABLES
    variables = extraction_result.variables[:MAX_VARIABLES]
    extracted_entities: list[str] = extraction_result.entities
    extracted_dates: list[ExtractedDate] = extraction_result.dates

    extract_entry = TelemetryLLMUsage(
        step="extract",
        input_tokens=extract_usage.input_tokens,
        output_tokens=extract_usage.output_tokens,
        cached_input_tokens=extract_usage.cached_input_tokens,
        model=extract_usage.model,
        latency_s=extract_usage.latency_s,
    )

    if not variables:
        # Zero-variable fallback.
        yield Interpretation(
            variables=[],
            entities=extracted_entities,
            dates=extracted_dates,
            expected_results=1,
            truncated=truncated,
            contained_in=extraction_result.contained_in,
        )
        # Start tasks after Interpretation is yielded.
        dcid_task: asyncio.Task[PlaceResolution] = asyncio.create_task(
            _resolve_place_dcids(
                query,
                extracted_entities,
                contained_in_parents=tuple(extraction_result.contained_in_parents),
            )
        )
        place_event_task: asyncio.Task[list[ResolvedPlace]] = asyncio.create_task(
            _build_resolved_places(extracted_entities, dcid_task)
        )
        slot_bind_usages: list[Usage] = []

        async def _run_zero_variable() -> _VariableResult:
            # Resolution is joined inside _run_one_variable (overlapped with retrieval).
            return await _run_one_variable(
                None,
                query,
                resolution_task=dcid_task,
                slot_bind_usages=slot_bind_usages,
            )

        run_task: asyncio.Task[_VariableResult] = asyncio.create_task(_run_zero_variable())

        vr: _VariableResult | None = None
        # Interleave Places with result via FIRST_COMPLETED (same as normal path).
        pending: set[asyncio.Task] = {run_task, place_event_task}
        run_done = False
        try:
            while pending:
                done_set, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for d in done_set:
                    if d is place_event_task:
                        yield Places(places=d.result())
                    else:
                        vr = d.result()
                        vr.index = 0
                        yield _result_event(0, None, vr.outcome)
                        run_done = True
        finally:
            await _cancel_and_drain(run_task, place_event_task, dcid_task)

        collected_zero = [vr] if vr is not None else []
        yield _build_done(
            t0,
            collected_zero,
            [extract_entry, *_slot_usages(slot_bind_usages)],
            truncated=truncated,
            timed_out=not run_done,
        )
        return

    # Normal path: variables present.
    yield Interpretation(
        variables=variables,
        entities=extracted_entities,
        dates=extracted_dates,
        expected_results=len(variables),
        truncated=truncated,
        contained_in=extraction_result.contained_in,
    )

    slot_bind_usages = []
    deadline = t0 + _ROUTE_TIMEOUT_S

    # DCID-only resolution task; fan-out branches await this.
    dcid_task = asyncio.create_task(
        _resolve_place_dcids(
            query,
            extracted_entities,
            contained_in_parents=tuple(extraction_result.contained_in_parents),
        )
    )

    # Places-event task; awaits dcid_task then does name fetch + assembly.
    # Only Places depends on this; fan-out never awaits it. Fail-open.
    place_event_task = asyncio.create_task(_build_resolved_places(extracted_entities, dcid_task))

    async def per_variable(index: int, v: str) -> _VariableResult:
        async with _FANOUT_SEM:
            try:
                # Resolution is joined inside _run_one_variable (overlapped with retrieval).
                vr = await _run_one_variable(
                    v,
                    query,
                    resolution_task=dcid_task,
                    dates=extracted_dates,
                    entities=extracted_entities,
                    contained_in=extraction_result.contained_in,
                    slot_bind_usages=slot_bind_usages,
                )
            except Exception as exc:
                # Per-branch failure.
                logger.warning("per_variable raised", exc_info=exc)
                vr = _VariableResult(
                    outcome=AskClarification(
                        reason="error",
                        message="One sub-query failed; partial results returned.",
                    )
                )
            vr.index = index
            vr.variable_label = v
            return vr

    result_tasks = [asyncio.create_task(per_variable(i, v)) for i, v in enumerate(variables)]

    # Interleave Places with results via FIRST_COMPLETED.
    pending: set[asyncio.Task] = set(result_tasks) | {place_event_task}
    collected: list[_VariableResult] = []
    try:
        while pending:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:  # timed out this slice
                break
            for d in done:
                if d is place_event_task:
                    yield Places(places=d.result())
                else:
                    vr = d.result()
                    collected.append(vr)
                    yield _result_event(vr.index, vr.variable_label, vr.outcome)
    finally:
        await _cancel_and_drain(*result_tasks, place_event_task, dcid_task)

    timed_out = len(collected) < len(variables)
    yield _build_done(
        t0,
        collected,
        [extract_entry, *_slot_usages(slot_bind_usages)],
        truncated=truncated,
        timed_out=timed_out,
    )


async def stream_simple(query: str) -> AsyncIterator[Event]:
    """Yield typed events for the simple (single-variable) pipeline.

    Order: start → stage("retrieving") → {places, result interleaved} → done.
    """
    t0 = time.perf_counter()
    yield Start(query=query, mode="simple")
    yield Stage(stage="retrieving")

    slot_bind_usages: list[Usage] = []
    deadline = t0 + _ROUTE_TIMEOUT_S

    # entities=None triggers token fallback in _resolve_place_dcids; contained_in is
    # always False on the simple endpoint (no extraction LLM → never set).
    dcid_task: asyncio.Task[PlaceResolution] = asyncio.create_task(
        _resolve_place_dcids(query, None)
    )
    place_event_task: asyncio.Task[list[ResolvedPlace]] = asyncio.create_task(
        _build_resolved_places(None, dcid_task)
    )

    async def _run_simple_variable() -> _VariableResult:
        # Resolution is joined inside _run_one_variable (overlapped with retrieval).
        return await _run_one_variable(
            None, query, resolution_task=dcid_task, slot_bind_usages=slot_bind_usages
        )

    run_task: asyncio.Task[_VariableResult] = asyncio.create_task(_run_simple_variable())

    pending: set[asyncio.Task] = {run_task, place_event_task}
    collected: list[_VariableResult] = []
    run_done = False
    try:
        while pending:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:  # timed out this slice
                break
            for d in done:
                if d is place_event_task:
                    yield Places(places=d.result())
                else:
                    vr = d.result()
                    vr.index = 0
                    collected.append(vr)
                    yield _result_event(0, None, vr.outcome)
                    run_done = True
    finally:
        await _cancel_and_drain(run_task, place_event_task, dcid_task)

    if not run_done:
        # Timed out before the run task completed.
        yield _build_done(t0, [], _slot_usages(slot_bind_usages), truncated=False, timed_out=True)
        return

    yield _build_done(t0, collected, _slot_usages(slot_bind_usages), truncated=False)


# ---------------------------------------------------------------------------
# Buffered drain
# ---------------------------------------------------------------------------


async def _drain(stream: AsyncIterator[Event], query: str) -> PipelineResult:
    """Collect a stream into a PipelineResult, re-sorting results by index.

    Captures Interpretation and Places to assemble QueryInterpretation.
    Gathers Result events; reads terminal Done for telemetry.
    """
    results: list[Result] = []
    done: Done | None = None
    interp_evt: Interpretation | None = None
    places_evt: Places | None = None
    async for event in stream:  # exceptions propagate (preserve 504/503)
        if isinstance(event, Result):
            results.append(event)
        elif isinstance(event, Interpretation):
            interp_evt = event
        elif isinstance(event, Places):
            places_evt = event
        elif isinstance(event, Done):
            done = event
    assert done is not None  # generators always end with Done (no Error from generator)
    results.sort(key=lambda r: r.index)  # undo FIRST_COMPLETED reordering
    answers = [r.answer for r in results if isinstance(r.answer, AnswerCollection)]

    # Assemble QueryInterpretation from the two signal events.
    interpretation: QueryInterpretation | None = None
    if interp_evt is not None or places_evt is not None:
        interpretation = QueryInterpretation(
            variables=interp_evt.variables if interp_evt else [],
            places=places_evt.places if places_evt else [],
            dates=interp_evt.dates if interp_evt else [],
            contained_in=interp_evt.contained_in if interp_evt else False,
        )

    return PipelineResult(
        query=query,
        answers=answers,
        ask=done.ask,
        elapsed_s=done.elapsed_s,
        n_candidates=done.telemetry.n_candidates,
        n_shapes=done.telemetry.n_shapes,
        terminated_by=done.telemetry.terminated_by,
        llm_usage=done.telemetry.llm_usage,
        truncated=done.telemetry.truncated,
        interpretation=interpretation,
    )


# ---------------------------------------------------------------------------
# Public orchestrators
# ---------------------------------------------------------------------------


async def run_simple(query: str) -> PipelineResult:
    """Buffered simple pipeline — a drain over stream_simple (unchanged contract)."""
    return await _drain(stream_simple(query), query)


async def run_default(query: str) -> PipelineResult:
    """Buffered default pipeline — a drain over stream_default (unchanged contract)."""
    return await _drain(stream_default(query), query)
