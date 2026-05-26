"""Pipeline orchestrators for the dc-search service.

Both endpoints share ``_run_one_variable`` as their single materialization
implementation.  ``run_simple`` calls it once with no variable hint;
``run_default`` fans out across ``extraction.extract`` results.

Data flow::

    run_simple(query)
        → _run_one_variable(None, query)

    run_default(query)
        → extraction.extract(query)        [LLM #1]
        → [per variable] _run_one_variable(v, query)   [LLM #2 each]

The generators ``stream_default`` / ``stream_simple`` are the canonical
implementations; ``run_default`` / ``run_simple`` are thin buffered drains.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from dc_search.predicate import AnswerCollection, AskClarification, Predicate
from dc_search.shape import ShapeContext, build_shape_context
from dc_search.telemetry import TelemetryLLMUsage, Usage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Per-worker bound — aggregates to 4 workers × 8 = 32 concurrent variable
# pipelines per container under full load.
_FANOUT_SEM: asyncio.Semaphore = asyncio.Semaphore(8)

# Maximum number of extracted variables before fan-out is capped.
MAX_VARIABLES: int = 6

# Soft deadline for both generators (matches app.py's route-level wait_for).
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

    A Topic is dominant when it is rank-1 overall AND its score exceeds the
    next-best non-Topic candidate by at least ``_TOPIC_DOMINANCE_ABS``
    (absolute) OR ``_TOPIC_DOMINANCE_RATIO`` (ratio).
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
# Availability re-rank helper
# ---------------------------------------------------------------------------


def _availability_sort_key(dcid: str, union_avail: frozenset[str]) -> int:
    """Return 0 for SVs in availability set (higher priority), 1 otherwise."""
    return 0 if dcid in union_avail else 1


def _resolve_union_availability(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> frozenset[str]:
    """Union the availability sets for already-resolved place DCIDs.

    Candidate path (known SVs): hybrid — custom-DC vars resolved via the
    coverage map's {E,V} presence, base-DC vars (map-absent) via a targeted
    live observation fetch, unioned. Map-absent + base-absent vars fail-open
    (not in either set, so _apply_availability_filter's empty-intersection
    fallback preserves them).

    Topic path (no candidate SVs): falls back to full-inventory batch.
    """
    if not place_dcids:
        return frozenset()

    if candidate_sv_dcids:
        # Fetch precomputed coverage map for the candidate set + resolved places.
        cov = retrieval.variable_date_coverage(
            variable_dcids=candidate_sv_dcids,
            entity_dcids=tuple(place_dcids),
        )

        # Custom vars: present in the coverage map at any of the resolved places.
        custom_present: set[str] = {
            v for v in candidate_sv_dcids if any((v, e) in cov.entity_ranges for e in place_dcids)
        }

        # Base-DC vars: map-absent (not in cov.envelopes) -> live obs check.
        base_candidates = tuple(v for v in candidate_sv_dcids if v not in cov.envelopes)
        base_present: frozenset[str] = (
            retrieval.presence_for_entities(
                variable_dcids=base_candidates,
                entity_dcids=tuple(place_dcids),
            )
            if base_candidates
            else frozenset()
        )

        return frozenset(custom_present | base_present)

    batch = retrieval.variables_for_entities_batch(entity_dcids=tuple(place_dcids))
    available: set[str] = set()
    for svs in batch.values():
        available.update(svs)
    return frozenset(available)


def _resolve_union_availability_checked(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> tuple[frozenset[str], bool]:
    """``_resolve_union_availability`` plus a fail-open degraded flag.

    Runs as the ``asyncio.to_thread`` target so it can read retrieval's per-context
    degraded flag in the SAME thread the coverage/presence helpers ran on (a
    ContextVar mutation inside a thread does not propagate back to the caller's
    context, so the flag must be captured here and returned).
    """
    retrieval.reset_dc_call_degraded()
    avail = _resolve_union_availability(place_dcids, candidate_sv_dcids)
    return avail, retrieval.dc_call_was_degraded()


def _resolve_union_availability_with_ranges(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> tuple[frozenset[str], dict[str, tuple[str | None, str | None]], bool]:
    """Like ``_resolve_union_availability_checked`` but also returns per-DCID date ranges.

    For custom-DC vars that appear in the coverage map's ``entity_ranges``, unions
    the ``(earliest, latest)`` strings across ``place_dcids`` via string-comparison
    min/max (ISO-style: lexicographic ordering preserves temporal ordering).
    For base-DC vars (absent from ``cov.envelopes``), issues a facet-select
    observation query to get the true place-specific span, and merges those
    ranges into the result.

    Returns:
        ``(availability_frozenset, dcid_to_range, degraded)`` — the range dict maps
        SV DCID to ``(earliest, latest)``; absent entries mean no range known.
    """
    retrieval.reset_dc_call_degraded()

    ranges: dict[str, tuple[str | None, str | None]] = {}

    if not place_dcids or not candidate_sv_dcids:
        avail = _resolve_union_availability(place_dcids, candidate_sv_dcids)
        return avail, ranges, retrieval.dc_call_was_degraded()

    cov = retrieval.variable_date_coverage(
        variable_dcids=candidate_sv_dcids,
        entity_dcids=tuple(place_dcids),
    )

    # Custom vars: union entity_ranges over all resolved places.
    custom_present: set[str] = set()
    for v in candidate_sv_dcids:
        if v not in cov.envelopes:
            continue  # base-DC var — skip range extraction
        lo: str | None = None
        hi: str | None = None
        present_at_any = False
        for e in place_dcids:
            er = cov.entity_ranges.get((v, e))
            if er is None:
                continue
            present_at_any = True
            er_lo, er_hi = er
            if er_lo is not None:
                lo = er_lo if (lo is None or er_lo < lo) else lo
            if er_hi is not None:
                hi = er_hi if (hi is None or er_hi > hi) else hi
        if present_at_any:
            custom_present.add(v)
            ranges[v] = (lo, hi)

    # Base-DC vars: facet-select observation query for presence + true date spans.
    base_candidates = tuple(v for v in candidate_sv_dcids if v not in cov.envelopes)
    base_present, base_ranges = (
        retrieval.observation_facet_ranges(
            variable_dcids=base_candidates,
            entity_dcids=tuple(place_dcids),
        )
        if base_candidates
        else (frozenset(), {})
    )
    ranges.update(base_ranges)

    avail = frozenset(custom_present | base_present)
    return avail, ranges, retrieval.dc_call_was_degraded()


# ---------------------------------------------------------------------------
# Place resolution helpers (query-global, run once per request)
# ---------------------------------------------------------------------------


async def _build_resolved_places(
    entities: list[str] | None,
    dcid_task: asyncio.Task[list[str]],
) -> list[ResolvedPlace]:
    """Await ``dcid_task`` then fetch canonical names; assemble ``ResolvedPlace`` objects.

    Fail-open: a name-fetch failure leaves ``name``/``type`` as ``None`` on the
    affected places.  Returns ``[]`` when no places resolved.

    The ``alternatives`` field is populated from the other ``resolve_places_batch``
    candidates (rank-1 is selected as primary; the rest become alternatives).
    """
    try:
        place_dcids = await dcid_task
    except Exception:
        return []

    if not place_dcids:
        return []

    # --- canonical names for resolved DCIDs ---
    try:
        names_map = await asyncio.to_thread(retrieval.place_names_batch, dcids=tuple(place_dcids))
    except Exception:
        names_map = {}

    # --- alternatives: re-use the resolve_places_batch result (already called) ---
    # We call it again here via asyncio.to_thread; caching in retrieval.py means
    # this hits the LRU cache and costs essentially nothing.
    # Skip when entities is None (simple endpoint) or empty — no alternatives available.
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

        # Derive input_name: for the default endpoint entities[i] is available.
        input_name: str = ent_list[i] if i < len(ent_list) else dcid

        # Build alternatives from the batch (all candidates other than the primary).
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
    """Step 1 — resolve indicator candidates and filter to SV/Topic DCIDs."""
    if variable is not None:
        retrieval_query = variable
    else:
        retrieval_query = query

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
    """Step 2 — topic-dominance short-circuit; returns answer or None to continue.

    ``place_dcids`` is pre-resolved by the caller (shared ``dcid_task``).
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
        # Fetch topic metadata here (distinct from _enrich_topic_metadata, which
        # runs on the non-short-circuit path). Fail-open: leave
        # topic_name/topic_description None on failure.
        try:
            topic_meta = await asyncio.to_thread(
                retrieval.topic_metadata_batch, dcids=(dominant_dcid,)
            )
            meta = topic_meta.get(dominant_dcid)
        except Exception:
            meta = None
        answer = answer.model_copy(
            update={
                "answer_kind": "topic",
                "topic_name": meta.name if meta else None,
                "topic_description": meta.description if meta else None,
            }
        )
        if variable is not None:
            answer = answer.model_copy(update={"variable_label": variable})
    return answer


async def _resolve_place_dcids(query: str, entities: list[str] | None) -> list[str]:
    """Resolve place names to DCIDs for the availability re-rank step.

    Uses LLM-extracted entity names (default endpoint); an empty list means the
    LLM found no place and is trusted as-is (no token fallback). Only the simple
    endpoint (``entities is None``, no LLM extraction) falls back to the
    deterministic extract_place_tokens path. LLM-extracted strings are
    Pydantic-validated list[str] before reaching this function; the mixer's
    /v2/resolve endpoint handles arbitrary name strings safely (name lookup,
    not query injection).
    """
    if entities is not None:
        # Default endpoint — the LLM authoritatively parsed place names. Trust it,
        # including an empty list (query named no place): do NOT fall back to the
        # token path, which brute-forces stopwords like "between" into a homograph
        # place (e.g. the town Between, GA) and silently breaks date filtering.
        if not entities:
            return []
        # Resolve LLM-extracted entity names to DCIDs via mixer in one batched call.
        resolved = await asyncio.to_thread(retrieval.resolve_places_batch, names=tuple(entities))
        # Iterate entities in order so result ordering is deterministic and
        # matches the input list (resolved.values() iteration order depends on
        # dict insertion order which can differ from entities order).
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
    # Simple endpoint (no LLM extraction ran) — deterministic token fallback.
    return await asyncio.to_thread(shape_module.extract_place_tokens, query)


async def _rerank_by_availability(
    sv_dcids: list[str],
    place_dcids: list[str],
) -> tuple[list[str], frozenset[str], dict[str, tuple[str | None, str | None]], bool]:
    """Step 3 — availability re-rank.

    ``place_dcids`` is pre-resolved by the caller (shared ``dcid_task``).

    Returns ``(reranked_sv_dcids, union_avail, dcid_to_date_range, availability_degraded)``.
    """
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
    """Step 4 — batch-fetch StatVarFeatures for the filtered SV DCIDs."""
    features_dict = await asyncio.to_thread(retrieval.stat_var_features_batch, sv_dcids=sv_dcids)
    return list(features_dict.values())


def _build_shape(
    query: str,
    feature_list: list,
    retrieval_scores: dict[str, float],
) -> ShapeContext | AskClarification:
    """Step 5 — build shape context (pure, no I/O)."""
    shape_ctx = build_shape_context(query, feature_list, retrieval_scores=retrieval_scores)
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
    """Step 6 — enrich ShapeContext with human-readable topic metadata."""
    topic_dcids: tuple[str, ...] = tuple(
        sorted({s.member_dcids[0] for s in shape_ctx.shapes if s.is_topic and s.member_dcids})
    )
    if not topic_dcids:
        return shape_ctx
    topic_meta = await asyncio.to_thread(retrieval.topic_metadata_batch, dcids=topic_dcids)
    return ShapeContext(
        query=shape_ctx.query,
        shapes=shape_ctx.shapes,
        keyword_cues=shape_ctx.keyword_cues,
        topic_metadata=topic_meta,
    )


async def _bind_slot(
    shape_ctx: ShapeContext,
    slot_bind_usages: list[Usage],
) -> tuple | AskClarification:
    """Step 7 — LLM slot-binding call; appends usage to slot_bind_usages."""
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
    """Step 8 — materialize via hooks (to_thread offloads blocking mixer HTTP calls)."""
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


async def _run_one_variable(
    variable: str | None,
    query: str,
    *,
    place_dcids: list[str],
    dates: list[ExtractedDate] | None = None,
    slot_bind_usages: list[Usage],
) -> _VariableResult:
    """Shared pipeline core: retrieve → shape → bind → materialize.

    Called by ``run_simple`` (variable=None) and by each fan-out branch in
    ``run_default`` (variable=measure string).

    Args:
        variable: Extracted variable phrase for retrieval scoping.  None for the
            simple endpoint (uses original query verbatim).
        query: Original user query, forwarded to the LLM for context.
        place_dcids: Already-resolved place DCIDs from the shared ``dcid_task``
            (resolution happens once per query, not once per variable).
        dates: Extracted date references from the default endpoint's extraction
            step.  None (simple endpoint) becomes an empty list at the
            HookContext layer.
        slot_bind_usages: Mutable list; appended to for each slot-binding LLM
            call so the caller can aggregate usage across fan-out branches.

    Returns:
        ``_VariableResult`` with the answer (or AskClarification) and telemetry
        counts (n_candidates, n_shapes).
    """
    # Step 1 — retrieval.
    retrieve_out = await _retrieve(variable, query)
    if isinstance(retrieve_out, AskClarification):
        return _VariableResult(outcome=retrieve_out)

    candidates, sv_dcids, retrieval_scores = retrieve_out
    n_candidates = len(sv_dcids)

    # Build dcid_to_sentence map from retrieval candidates.
    dcid_to_sentence: dict[str, str] = {
        c.dcid: c.sentence for c in candidates if getattr(c, "sentence", None)
    }

    # Step 2 — topic-dominance short-circuit (receives pre-resolved place_dcids).
    topic_answer = await _short_circuit_topic(
        candidates, retrieval_scores, variable, place_dcids=place_dcids, dates=dates
    )
    if topic_answer is not None:
        return _VariableResult(outcome=topic_answer, n_candidates=n_candidates)

    # Step 3 — availability re-rank (returns date ranges alongside the rerank).
    sv_dcids, union_avail, dcid_to_date_range, avail_degraded = await _rerank_by_availability(
        sv_dcids, place_dcids
    )

    # Step 4 — feature fetch.
    feature_list = await _fetch_features(sv_dcids)

    # Step 5 — shape context.
    shape_or_ask = _build_shape(query, feature_list, retrieval_scores)
    if isinstance(shape_or_ask, AskClarification):
        return _VariableResult(outcome=shape_or_ask, n_candidates=n_candidates)

    shape_ctx = shape_or_ask
    n_shapes = len(shape_ctx.shapes)

    # Step 6 — topic metadata enrichment.
    shape_ctx = await _enrich_topic_metadata(shape_ctx)

    # Step 7 — slot binding LLM call.
    bound = await _bind_slot(shape_ctx, slot_bind_usages)
    if isinstance(bound, AskClarification):
        return _VariableResult(outcome=bound, n_candidates=n_candidates, n_shapes=n_shapes)

    _shape, predicates, _ = bound

    # Step 8 — materialize via hooks.
    answer = await _materialize(
        predicates,
        feature_list,
        place_dcids,
        union_avail,
        retrieval_scores,
        variable,
        dates=dates,
        availability_degraded=avail_degraded,
        dcid_to_sentence=dcid_to_sentence,
        dcid_to_date_range=dcid_to_date_range,
    )
    return _VariableResult(outcome=answer, n_candidates=n_candidates, n_shapes=n_shapes)


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


async def _cancel_and_drain(*tasks: asyncio.Task) -> None:
    """Cancel every task then await them, swallowing results/exceptions.

    Used in the generators' ``finally`` blocks so no fan-out / place task
    outlives the response (the consumer's cancellation unwinds the generator,
    whose ``finally`` lands here).  Cancelling is idempotent — a task already
    owned by another (e.g. ``dcid_task`` awaited inside ``place_event_task``)
    is cancelled defensively here too.
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


def _build_done(
    t0: float,
    var_results: list[_VariableResult],
    llm_usage: list[TelemetryLLMUsage],
    *,
    truncated: bool,
    timed_out: bool = False,
) -> Done:
    """Assemble the terminal Done event from collected branch results.

    Mirrors the original run_default and run_simple terminated_by ladders for
    BOTH single-element (simple/fallback) and multi-element (default)
    var_results, so the buffered drain reproduces PipelineResult exactly.

    Steps (spec):
    1. Sort var_results by index so first-AskClarification selection is deterministic.
    2. Collect AnswerCollection outcomes.
    3. ask = None if answers, else first AskClarification in sorted order.
    4. terminated_by ladder: no reason=="error" special-case (all non-no_candidates → "ask").
    5. Sum n_candidates / n_shapes.
    6. Compute elapsed.
    7. Build DoneTelemetry.
    8. Return Done with top-level terminated_by/truncated from same locals.
    """
    sorted_results = sorted(var_results, key=lambda vr: vr.index)

    answers = [vr.outcome for vr in sorted_results if isinstance(vr.outcome, AnswerCollection)]
    ask: AskClarification | None = None
    if not answers:
        ask = next(
            (vr.outcome for vr in sorted_results if isinstance(vr.outcome, AskClarification)),
            None,
        )

    # Ladder copied verbatim from today's run_default (no reason=="error" branch).
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

    Order: start → interpretation → {places, result*  interleaved} → done.
    The hard guarantees are: interpretation precedes places; places never blocks
    result emission.  Falls back to simple semantics (still default-mode events)
    on zero variables.

    Soft-deadline: yields a terminal Done(timed_out=True) with partials if the
    25s budget is exhausted. Never yields Error (see pipeline.py error note).
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
        model=extract_usage.model,
        latency_s=extract_usage.latency_s,
    )

    if not variables:
        # Zero-variable fallback: emit a coherent default-mode stream.
        yield Interpretation(
            variables=[],
            entities=extracted_entities,
            dates=extracted_dates,
            expected_results=1,
            truncated=truncated,
        )
        # Start both tasks after Interpretation is yielded (perf-neutrality).
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

        # vr is initialized before the try so _build_done never hits a NameError
        # if run_task fails before producing a result.
        vr: _VariableResult | None = None
        # Interleave Places with the single result via FIRST_COMPLETED — same
        # pattern as the normal path, so the result is not serialized behind the
        # name fetch.
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
    # Yield Interpretation FIRST, unconditionally, for perf-neutrality: nothing
    # downstream (place resolution, fan-out) can delay it.
    yield Interpretation(
        variables=variables,
        entities=extracted_entities,
        dates=extracted_dates,
        expected_results=len(variables),
        truncated=truncated,
    )

    slot_bind_usages = []
    deadline = t0 + _ROUTE_TIMEOUT_S

    # DCID-only resolution task — the fan-out branches await this. No name fetch.
    dcid_task = asyncio.create_task(_resolve_place_dcids(query, extracted_entities))

    # Places-event task — awaits dcid_task, then does the name fetch + assembly.
    # Only the Places event depends on this; fan-out never awaits it. Fail-open → [].
    place_event_task = asyncio.create_task(_build_resolved_places(extracted_entities, dcid_task))

    async def per_variable(index: int, v: str) -> _VariableResult:
        async with _FANOUT_SEM:
            try:
                # Await the SHARED dcid_task — free once resolved, resolves once.
                place_dcids_resolved = await dcid_task
                vr = await _run_one_variable(
                    v,
                    query,
                    place_dcids=place_dcids_resolved,
                    dates=extracted_dates,
                    slot_bind_usages=slot_bind_usages,
                )
            except Exception as exc:
                # Per-branch failure — mirror gather(return_exceptions=True) semantics.
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

    # Interleave Places with results via FIRST_COMPLETED so neither blocks the other.
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
                    # build is fail-open → never raises; safe to call .result()
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

    Order: start → stage("retrieving") → {places, result  interleaved} → done.
    """
    t0 = time.perf_counter()
    yield Start(query=query, mode="simple")
    yield Stage(stage="retrieving")

    slot_bind_usages: list[Usage] = []
    deadline = t0 + _ROUTE_TIMEOUT_S

    # entities=None → simple endpoint token fallback inside _resolve_place_dcids.
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

    Captures Interpretation and Places events to assemble QueryInterpretation.
    Gathers Result events; reads the terminal Done for telemetry. The generator
    never yields Error (errors propagate as exceptions so the route's handlers
    map them to 504/503), so a Done is always the terminal event. A
    Done(timed_out=True) is treated like any other Done.
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
