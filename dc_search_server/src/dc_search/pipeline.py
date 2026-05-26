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
from dc_search.events import Done, DoneTelemetry, Event, Interpretation, Result, Stage, Start
from dc_search.extraction import ExtractedDate
from dc_search.hooks import HookContext
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
    query: str,
    retrieval_scores: dict[str, float],
    variable: str | None,
    entities: list[str] | None = None,
    dates: list[ExtractedDate] | None = None,
) -> AnswerCollection | AskClarification | None:
    """Step 2 — topic-dominance short-circuit; returns answer or None to continue."""
    dominant_dcid = _dominant_topic_dcid(candidates)
    if dominant_dcid is None:
        return None

    place_dcids: list[str] = await _resolve_place_dcids(query, entities)
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
    if variable is not None and isinstance(answer, AnswerCollection):
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
    query: str,
    entities: list[str] | None = None,
) -> tuple[list[str], list[str], frozenset[str], bool]:
    """Step 3 — availability re-rank.

    Returns (reranked_sv_dcids, place_dcids, union_avail, availability_degraded).
    """
    place_dcids: list[str] = await _resolve_place_dcids(query, entities)
    union_avail: frozenset[str] = frozenset()
    avail_degraded = False
    if place_dcids:
        union_avail, avail_degraded = await asyncio.to_thread(
            _resolve_union_availability_checked, place_dcids, tuple(sv_dcids)
        )
        if union_avail:
            sv_dcids = sorted(
                sv_dcids,
                key=lambda d: _availability_sort_key(d, union_avail),
            )
    return sv_dcids, place_dcids, union_avail, avail_degraded


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
) -> AnswerCollection | AskClarification:
    """Step 8 — materialize via hooks (to_thread offloads blocking mixer HTTP calls)."""
    hook_ctx = HookContext(
        place_dcids=tuple(place_dcids),
        place_availability=union_avail if union_avail else None,
        retrieval_scores=retrieval_scores,
        raw_candidates=tuple(feature_list),
        dates=dates or [],
        availability_degraded=availability_degraded,
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
    entities: list[str] | None = None,
    dates: list[ExtractedDate] | None = None,
    slot_bind_usages: list[Usage],
) -> _VariableResult:
    """Shared pipeline core: retrieve → shape → bind → materialize.

    Called by ``run_simple`` (variable=None, entities=None) and by each fan-out
    branch in ``run_default`` (variable=measure string, entities=extraction
    result entities).

    Args:
        variable: Extracted variable phrase for retrieval scoping.  None for the
            simple endpoint (uses original query verbatim).
        query: Original user query, forwarded to the LLM for context.
        entities: LLM-extracted place names from ``extraction.extract`` (default
            endpoint).  ``None`` triggers the deterministic ``extract_place_tokens``
            fallback (simple endpoint or extraction returned no entities).
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

    # Step 2 — topic-dominance short-circuit.
    topic_answer = await _short_circuit_topic(
        candidates, query, retrieval_scores, variable, entities=entities, dates=dates
    )
    if topic_answer is not None:
        return _VariableResult(outcome=topic_answer, n_candidates=n_candidates)

    # Step 3 — availability re-rank.
    sv_dcids, place_dcids, union_avail, avail_degraded = await _rerank_by_availability(
        sv_dcids, query, entities
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
    )
    return _VariableResult(outcome=answer, n_candidates=n_candidates, n_shapes=n_shapes)


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


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

    Order: start → interpretation → result* (fastest-first) → done.
    Falls back to simple semantics (still default-mode events) on zero variables.
    Soft-deadline: yields a terminal Done(timed_out=True) with partials if the
    25s budget is exhausted. Never yields Error (see pipeline.py error note).
    """
    t0 = time.perf_counter()
    yield Start(query=query, mode="default")

    extraction_result, extract_usage = await extraction.extract(query)  # may raise → propagates

    truncated = len(extraction_result.variables) > MAX_VARIABLES
    variables = extraction_result.variables[:MAX_VARIABLES]

    extract_entry = TelemetryLLMUsage(
        step="extract",
        input_tokens=extract_usage.input_tokens,
        output_tokens=extract_usage.output_tokens,
        model=extract_usage.model,
        latency_s=extract_usage.latency_s,
    )

    if not variables:
        # Zero-variable fallback: emit coherent default-mode stream mirroring simple semantics.
        yield Interpretation(
            variables=[],
            entities=extraction_result.entities,
            dates=extraction_result.dates,
            expected_results=1,
            truncated=truncated,
        )
        slot_bind_usages: list[Usage] = []
        vr = await _run_one_variable(None, query, slot_bind_usages=slot_bind_usages)
        vr.index = 0
        yield _result_event(0, None, vr.outcome)
        yield _build_done(
            t0,
            [vr],
            [extract_entry, *_slot_usages(slot_bind_usages)],
            truncated=truncated,
        )
        return

    yield Interpretation(
        variables=variables,
        entities=extraction_result.entities,
        dates=extraction_result.dates,
        expected_results=len(variables),
        truncated=truncated,
    )

    slot_bind_usages: list[Usage] = []
    extracted_entities: list[str] = extraction_result.entities
    extracted_dates: list[ExtractedDate] = extraction_result.dates
    deadline = t0 + _ROUTE_TIMEOUT_S

    async def per_variable(index: int, v: str) -> _VariableResult:
        async with _FANOUT_SEM:
            try:
                vr = await _run_one_variable(
                    v,
                    query,
                    entities=extracted_entities,
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

    tasks = [asyncio.create_task(per_variable(i, v)) for i, v in enumerate(variables)]

    collected: list[_VariableResult] = []
    try:
        for fut in asyncio.as_completed(tasks):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break  # budget already spent → timeout path
            try:
                # wait_for raises TimeoutError to bound the await, but because
                # as_completed yields wrapper awaitables (not the original tasks),
                # wait_for does NOT cancel the underlying per_variable task on timeout.
                # The finally block below is the single cancellation source for BOTH
                # the timeout break and the client-disconnect CancelledError paths.
                vr = await asyncio.wait_for(fut, timeout=remaining)
            except asyncio.TimeoutError:
                break  # a branch is hung past the budget
            collected.append(vr)
            yield _result_event(vr.index, vr.variable_label, vr.outcome)
    finally:
        # Cancel + await any branch still pending (timeout OR client-disconnect
        # CancelledError arriving here). No orphaned tasks, no leaked in-flight work.
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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

    Order: start → stage("retrieving") → result → done.
    """
    t0 = time.perf_counter()
    yield Start(query=query, mode="simple")
    yield Stage(stage="retrieving")

    slot_bind_usages: list[Usage] = []
    task = asyncio.create_task(_run_one_variable(None, query, slot_bind_usages=slot_bind_usages))
    try:
        vr = await asyncio.wait_for(task, timeout=_ROUTE_TIMEOUT_S)  # may raise → propagates
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        yield _build_done(t0, [], _slot_usages(slot_bind_usages), truncated=False, timed_out=True)
        return
    vr.index = 0

    yield _result_event(0, None, vr.outcome)
    yield _build_done(t0, [vr], _slot_usages(slot_bind_usages), truncated=False)


# ---------------------------------------------------------------------------
# Buffered drain
# ---------------------------------------------------------------------------


async def _drain(stream: AsyncIterator[Event], query: str) -> PipelineResult:
    """Collect a stream into a PipelineResult, re-sorting results by index.

    Ignores start/interpretation/stage; gathers Result events; reads the terminal
    Done for telemetry. The generator never yields Error (errors propagate as
    exceptions so the route's handlers map them to 504/503), so a Done is always
    the terminal event. A Done(timed_out=True) is treated like any other Done.
    """
    results: list[Result] = []
    done: Done | None = None
    async for event in stream:  # exceptions propagate (preserve 504/503)
        if isinstance(event, Result):
            results.append(event)
        elif isinstance(event, Done):
            done = event
    assert done is not None  # generators always end with Done (no Error from generator)
    results.sort(key=lambda r: r.index)  # undo as_completed reordering
    answers = [r.answer for r in results if isinstance(r.answer, AnswerCollection)]
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
