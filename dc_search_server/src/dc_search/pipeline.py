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
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dc_search import extraction, retrieval, slot_binding
from dc_search import hooks as hooks_module
from dc_search import shape as shape_module
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
# Public orchestrators
# ---------------------------------------------------------------------------


async def run_simple(query: str) -> PipelineResult:
    """Run the single-variable pipeline (simple endpoint).

    One retrieval → one LLM slot-bind → materialize.  No extraction LLM call.
    """
    t0 = time.perf_counter()
    slot_bind_usages: list[Usage] = []
    vr = await _run_one_variable(None, query, slot_bind_usages=slot_bind_usages)
    elapsed = time.perf_counter() - t0

    llm_usage: list[TelemetryLLMUsage] = [
        TelemetryLLMUsage(
            step="slot_bind",
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            model=u.model,
            latency_s=u.latency_s,
        )
        for u in slot_bind_usages
    ]

    if isinstance(vr.outcome, AskClarification):
        terminated: Literal["answer", "ask", "no_candidates", "error"]
        terminated = "no_candidates" if vr.outcome.reason == "no_candidates" else "ask"
        return PipelineResult(
            query=query,
            answers=[],
            ask=vr.outcome,
            elapsed_s=elapsed,
            n_candidates=vr.n_candidates,
            n_shapes=vr.n_shapes,
            terminated_by=terminated,
            llm_usage=llm_usage,
        )

    return PipelineResult(
        query=query,
        answers=[vr.outcome],
        ask=None,
        elapsed_s=elapsed,
        n_candidates=vr.n_candidates,
        n_shapes=vr.n_shapes,
        terminated_by="answer",
        llm_usage=llm_usage,
    )


async def run_default(query: str) -> PipelineResult:
    """Run the multi-variable pipeline (default endpoint).

    Extraction LLM call → fan-out per variable → aggregate results.
    Falls back to ``run_simple`` semantics when extraction yields zero variables.
    """
    t0 = time.perf_counter()

    extraction_result, extract_usage = await extraction.extract(query)

    # Cap variables before fan-out to bound resource exhaustion.
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
        # Edge case: extraction returned nothing → fall back to simple semantics.
        simple_result = await run_simple(query)
        return simple_result.model_copy(
            update={
                "llm_usage": [extract_entry, *simple_result.llm_usage],
                "truncated": truncated,
            }
        )

    # Fan out — module-level semaphore bounds per-worker concurrency.
    slot_bind_usages: list[Usage] = []
    # Same entity list used for every variable's availability re-rank — entities
    # are place names from the query, shared across all extracted variables.
    extracted_entities: list[str] = extraction_result.entities

    extracted_dates: list[ExtractedDate] = extraction_result.dates

    async def per_variable(v: str) -> _VariableResult:
        async with _FANOUT_SEM:
            return await _run_one_variable(
                v,
                query,
                entities=extracted_entities,
                dates=extracted_dates,
                slot_bind_usages=slot_bind_usages,
            )

    raw_results = await asyncio.gather(
        *(per_variable(v) for v in variables), return_exceptions=True
    )
    var_results: list[_VariableResult] = []
    for item in raw_results:
        if isinstance(item, BaseException):
            logger.warning("per_variable raised an exception", exc_info=item)
            var_results.append(
                _VariableResult(
                    outcome=AskClarification(
                        reason="error",
                        message="One sub-query failed; partial results returned.",
                    )
                )
            )
        else:
            var_results.append(item)

    answers = [vr.outcome for vr in var_results if isinstance(vr.outcome, AnswerCollection)]
    ask: AskClarification | None = None
    if not answers:
        ask = next(
            (vr.outcome for vr in var_results if isinstance(vr.outcome, AskClarification)),
            None,
        )

    total_candidates = sum(vr.n_candidates for vr in var_results)
    total_shapes = sum(vr.n_shapes for vr in var_results)

    elapsed = time.perf_counter() - t0

    llm_usage: list[TelemetryLLMUsage] = [
        extract_entry,
        *[
            TelemetryLLMUsage(
                step="slot_bind",
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                model=u.model,
                latency_s=u.latency_s,
            )
            for u in slot_bind_usages
        ],
    ]

    if ask is not None and ask.reason == "no_candidates":
        terminated_by: Literal["answer", "ask", "no_candidates", "error"] = "no_candidates"
    elif ask is not None:
        terminated_by = "ask"
    else:
        terminated_by = "answer"

    return PipelineResult(
        query=query,
        answers=answers,
        ask=ask,
        elapsed_s=elapsed,
        n_candidates=total_candidates,
        n_shapes=total_shapes,
        terminated_by=terminated_by,
        llm_usage=llm_usage,
        truncated=truncated,
    )
