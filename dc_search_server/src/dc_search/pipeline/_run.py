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

from dc_search import extraction, retrieval, slot_binding
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
from dc_search.interpretation import PlaceAlternative, QueryInterpretation, ResolvedPlace
from dc_search.place_role import classify_place_roles, place_directional_role
from dc_search.predicate import AnswerCollection, AskClarification, Predicate
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
    dcid_task: asyncio.Task[list[str]],
) -> list[ResolvedPlace]:
    """Await dcid_task, fetch canonical names, assemble ResolvedPlace objects.

    Fail-open: name-fetch failure leaves name/type as None. Returns [] when no
    places resolved. Alternatives come from resolve_places_batch candidates;
    rank-1 is primary, the rest become alternatives.
    """
    try:
        place_dcids = await dcid_task
    except Exception:
        return []

    if not place_dcids:
        return []

    # Fetch canonical names for resolved DCIDs.
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

    out: list[ResolvedPlace] = []
    for i, dcid in enumerate(place_dcids):
        name_entry = names_map.get(dcid, (None, None))
        canonical_name, place_type = name_entry

        # Use entities[i] if available; fallback to dcid.
        input_name: str = ent_list[i] if i < len(ent_list) else dcid

        # Alternatives: all candidates except the primary (rank-1).
        alt_candidates = resolved_all.get(input_name, ())
        alternatives: list[PlaceAlternative] = [
            PlaceAlternative(dcid=c.dcid, name=None, type=c.dominant_type)
            for c in alt_candidates
            if c.dcid != dcid
        ]

        out.append(
            ResolvedPlace(
                input_name=input_name,
                dcid=dcid,
                name=canonical_name,
                type=place_type,
                alternatives=alternatives,
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

    candidates = await asyncio.to_thread(retrieval.resolve_indicator, query=retrieval_query, k=30)

    sv_dcids = [
        c.dcid for c in candidates if any(t in ("StatisticalVariable", "Topic") for t in c.type_of)
    ][:30]

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


async def _resolve_place_dcids(query: str, entities: list[str] | None) -> list[str]:
    """Resolve place names to DCIDs for availability re-rank.

    Default endpoint (entities non-None): trust LLM extraction, including empty
    list (no place found). Never falls back to token path.

    Simple endpoint (entities=None): use deterministic extract_place_tokens.
    """
    if entities is not None:
        # Default endpoint: trust LLM-extracted names authoritatively.
        # Empty list means no place found; do not fall back to token path.
        if not entities:
            return []
        # Resolve to DCIDs via mixer in one batched call.
        resolved = await asyncio.to_thread(retrieval.resolve_places_batch, names=tuple(entities))
        # Iterate in order to match input entity ordering.
        n_unresolved = 0
        place_dcids: list[str] = []
        for name in entities:
            candidates = resolved.get(name)
            if candidates:
                place_dcids.append(candidates[0].dcid)
            else:
                n_unresolved += 1
        if n_unresolved:
            logger.debug("resolve_place_dcids: %d entity/entities unresolved", n_unresolved)
        return place_dcids
    # Simple endpoint: deterministic token fallback.
    return await asyncio.to_thread(shape_module.extract_place_tokens, query)


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


async def _fetch_features(sv_dcids: list[str]) -> list:
    """Batch-fetch StatVarFeatures for the filtered SV DCIDs."""
    features_dict = await asyncio.to_thread(retrieval.stat_var_features_batch, sv_dcids=sv_dcids)
    return list(features_dict.values())


def _build_shape(
    query: str,
    feature_list: list,
    retrieval_scores: dict[str, float],
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...] = (),
) -> ShapeContext | AskClarification:
    """Build shape context (pure, no I/O)."""
    shape_ctx = build_shape_context(
        query, feature_list, retrieval_scores=retrieval_scores, resolved_places=resolved_places
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
    # Carry resolved_places forward (G1/R3): without this the field is silently
    # dropped before bind and the place-offer feature becomes a no-op.
    return ShapeContext(
        query=shape_ctx.query,
        shapes=shape_ctx.shapes,
        keyword_cues=shape_ctx.keyword_cues,
        topic_metadata=topic_meta,
        resolved_places=shape_ctx.resolved_places,
    )


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
) -> AnswerCollection | AskClarification:
    """Materialize via hooks (via to_thread for blocking mixer HTTP calls)."""
    hook_ctx = HookContext(
        place_dcids=tuple(place_dcids),
        place_availability=union_avail if union_avail else None,
        retrieval_scores=retrieval_scores,
        raw_candidates=tuple(feature_list),
        dates=dates or [],
        availability_degraded=availability_degraded,
        dcid_to_sentence=dcid_to_sentence or {},
        dcid_to_date_range=dcid_to_date_range or {},
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
) -> tuple[tuple[str, str | None, str | None, str], ...]:
    """Build (dcid, canonical_name, input_surface, role) 4-tuples for the default endpoint.

    ``role`` is computed once per query from the ORIGINAL full query string via
    ``place_directional_role`` — NOT from any per-variable scoped shape query
    (Amendment 2: fan-out scoping must not corrupt directional detection).

    Calls resolve_places_batch (LRU-cached, warm after _resolve_place_dcids ran)
    to re-derive each entity's own DCID, so entities that failed to resolve are
    naturally skipped and the surface string is always paired with the DCID that
    came from that specific entity.  Calls place_names_batch over the FULL
    resolved set (cold on first request; warm on subsequent calls within the same
    process — racing with the Places-event task, so the first request may be cold)
    so the name cache is primed for every resolved place (review P3).

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

    return tuple(tuples)


async def _run_one_variable(
    variable: str | None,
    query: str,
    *,
    place_dcids: list[str],
    dates: list[ExtractedDate] | None = None,
    entities: list[str] | None = None,
    slot_bind_usages: list[Usage],
) -> _VariableResult:
    """Shared pipeline core: retrieve → shape → bind → materialize.

    Args:
        variable: Extracted variable for scoping; None for simple endpoint.
        query: Original user query (forwarded to LLM for context).
        place_dcids: Pre-resolved place DCIDs (shared across all variables).
        dates: Extracted date references (None on simple endpoint).
        entities: Extracted place names (as written in the query). Folded into
            the shape-building query alongside ``variable`` so slot-binding sees
            the place without the sibling variables. Ignored when ``variable`` is
            None (simple endpoint). None on the simple endpoint.
        slot_bind_usages: Mutable list appended to for LLM usage aggregation.

    Returns:
        _VariableResult with answer/clarification and telemetry counts.
    """
    # Retrieval.
    retrieve_out = await _retrieve(variable, query)
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

    # Availability re-rank (against the full resolved place set, before donor narrowing).
    sv_dcids, union_avail, dcid_to_date_range, avail_degraded = await _rerank_by_availability(
        sv_dcids, place_dcids
    )

    # Feature fetch.
    feature_list = await _fetch_features(sv_dcids)

    # Build (dcid, canonical_name, input_surface, role) 4-tuples for the default endpoint.
    # Role is computed from the ORIGINAL full `query` — NOT from the per-variable
    # scoped `shape_query` built below — so directional grammar ("from X to Y") is
    # preserved across fan-out (Amendment 2).  Simple endpoint passes resolved_places=()
    # — it already works on the full query and does not use place-role binding.
    resolved_places = await _build_resolved_places_triples(place_dcids, entities, query=query)

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
    shape_or_ask = _build_shape(shape_query, feature_list, retrieval_scores, resolved_places)
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

    # Unpack BindResult via attribute access (review G2/A1).
    predicates = bound.predicates
    defaulted_recipient = bound.defaulted_recipient

    # Donor set = resolved places NOT bound as a constraint value in any predicate.
    # Pass donor_dcids as the HookContext.place_dcids so materialize_many treats
    # only donors as observation entities (R1).
    donor_dcids: tuple[str, ...] = classify_place_roles(
        resolved_places=resolved_places, predicates=predicates
    )

    # Materialize via hooks using the donor set as the entity set.
    answer = await _materialize(
        predicates,
        feature_list,
        list(donor_dcids),
        # Pre-bind availability/ranges were computed against the full place_dcids;
        # they are superseded by the post-materialize enrichment when needed.
        union_avail,
        retrieval_scores,
        variable,
        dates=dates,
        availability_degraded=avail_degraded,
        dcid_to_sentence=dcid_to_sentence,
        dcid_to_date_range=dcid_to_date_range,
    )

    # ------------------------------------------------------------------
    # Post-materialize enrichment (conditional — CRS recipient-bound path only).
    # Fires when:
    #   • the donor set differs from the full place set (a recipient was bound), OR
    #   • the final sv_set has DCIDs absent from the retrieved feature pool
    #     (Piece D recovered them — their availability/names need a recompute).
    # The common non-CRS path skips this block entirely (zero added cost).
    # ------------------------------------------------------------------
    if isinstance(answer, AnswerCollection):
        retrieved_dcids: set[str] = {f.dcid for f in feature_list}
        needs_enrichment = tuple(place_dcids) != donor_dcids or bool(
            set(answer.sv_set) - retrieved_dcids
        )
        if needs_enrichment:
            final_sv_set = list(answer.sv_set)

            # Backup feature fetch: collect any DCIDs still missing from raw_candidates
            # (S5 hook is the primary owner; this catches any gaps).
            missing_dcids = [d for d in final_sv_set if d not in retrieved_dcids]
            merged_features: dict[str, object] = {f.dcid: f for f in feature_list}
            if missing_dcids:
                try:
                    extra = await asyncio.to_thread(
                        retrieval.stat_var_features_batch, sv_dcids=missing_dcids
                    )
                    merged_features.update(extra)
                except Exception:
                    pass  # fail-open: names remain None for missing DCIDs

            # Recompute availability + date_range against the donor set over the
            # final sv_set. When donor_dcids is empty (every place was a recipient)
            # we OMIT availability — None, not False (review G4).
            if donor_dcids:
                try:
                    new_avail, new_ranges, new_degraded = await asyncio.to_thread(
                        _resolve_union_availability_with_ranges,
                        list(donor_dcids),
                        tuple(final_sv_set),
                    )
                except Exception:
                    new_avail = frozenset()
                    new_ranges = {}
                    new_degraded = False
            else:
                new_avail = None
                new_ranges = {}
                new_degraded = False

            # Rebuild variables with updated features, availability, and ranges.
            enrich_ctx = HookContext(
                place_dcids=donor_dcids,
                place_availability=new_avail,
                retrieval_scores=retrieval_scores,
                raw_candidates=tuple(merged_features.values()),
                dates=dates or [],
                availability_degraded=new_degraded,
                dcid_to_sentence=dcid_to_sentence,
                dcid_to_date_range=new_ranges,
            )
            answer = answer.model_copy(
                update={"variables": hooks_module._build_variables(final_sv_set, enrich_ctx)}
            )

        # Stamp interpreted_place_as_recipient caveat when the recipient role was
        # assigned by the unqualified-place default (not by explicit "to X" cue).
        if defaulted_recipient and "interpreted_place_as_recipient" not in answer.caveats:
            answer = answer.model_copy(
                update={
                    "caveats": [*answer.caveats, "interpreted_place_as_recipient"],
                }
            )

    return _VariableResult(outcome=answer, n_candidates=n_candidates, n_shapes=n_shapes)


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
        )
        # Start tasks after Interpretation is yielded.
        dcid_task: asyncio.Task[list[str]] = asyncio.create_task(
            _resolve_place_dcids(query, extracted_entities)
        )
        place_event_task: asyncio.Task[list[ResolvedPlace]] = asyncio.create_task(
            _build_resolved_places(extracted_entities, dcid_task)
        )
        slot_bind_usages: list[Usage] = []

        async def _run_zero_variable() -> _VariableResult:
            place_dcids_resolved = await dcid_task
            return await _run_one_variable(
                None, query, place_dcids=place_dcids_resolved, slot_bind_usages=slot_bind_usages
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
    )

    slot_bind_usages = []
    deadline = t0 + _ROUTE_TIMEOUT_S

    # DCID-only resolution task; fan-out branches await this.
    dcid_task = asyncio.create_task(_resolve_place_dcids(query, extracted_entities))

    # Places-event task; awaits dcid_task then does name fetch + assembly.
    # Only Places depends on this; fan-out never awaits it. Fail-open.
    place_event_task = asyncio.create_task(_build_resolved_places(extracted_entities, dcid_task))

    async def per_variable(index: int, v: str) -> _VariableResult:
        async with _FANOUT_SEM:
            try:
                # Await shared dcid_task (resolved once per query).
                place_dcids_resolved = await dcid_task
                vr = await _run_one_variable(
                    v,
                    query,
                    place_dcids=place_dcids_resolved,
                    dates=extracted_dates,
                    entities=extracted_entities,
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

    # entities=None triggers token fallback in _resolve_place_dcids.
    dcid_task: asyncio.Task[list[str]] = asyncio.create_task(_resolve_place_dcids(query, None))
    place_event_task: asyncio.Task[list[ResolvedPlace]] = asyncio.create_task(
        _build_resolved_places(None, dcid_task)
    )

    async def _run_simple_variable() -> _VariableResult:
        place_dcids_resolved = await dcid_task
        return await _run_one_variable(
            None, query, place_dcids=place_dcids_resolved, slot_bind_usages=slot_bind_usages
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
