"""Universal materializer and multi-predicate fan-out entry point.

The six retrieval names patched via ``patch("dc_search.hooks.<name>")`` are
accessed through the hooks package namespace at call time via _hooks_pkg so
monkeypatching intercepts the runtime lookup.
"""

from __future__ import annotations

import time

from dc_search import hooks as _hooks_pkg
from dc_search.predicate import (
    CONFIDENCE_LEVELS,
    AnswerCollection,
    AskClarification,
    Caveat,
    Predicate,
    ResolvedVariable,
    _build_crs_svg_dcid,
    _filter_by_predicate,
)
from dc_search.retrieval import StatVarFeatures, reset_dc_call_degraded

from ._helpers import _caveats, _ordered_union, _project_variables
from .context import HookContext, HookResult
from .registry import HOOKS, SetCapHook

_CRS_DAC_POPULATION_TYPE = "DevelopmentFinance"
_CRS_DAC_RECIPIENT_SLOT = "DevelopmentFinanceRecipient"


def _universal_materialize(
    predicate: Predicate,
    candidates: list[StatVarFeatures],
) -> AnswerCollection:
    """Filter candidates by predicate and build an initial AnswerCollection."""
    sv_set = _filter_by_predicate(predicate, candidates)
    return AnswerCollection(
        predicate=predicate,
        sv_set=sv_set,
        svg_dcids=(),
        collection_dcid=None,
        confidence="medium",
        caveats=[],
    )


def materialize_via_hooks(
    predicate: Predicate,
    candidates: list[StatVarFeatures],
    *,
    ctx: HookContext,
) -> AnswerCollection | AskClarification:
    """Run the universal materializer then the hook chain."""
    cand_tuple = tuple(candidates)
    sink = ctx.hook_timings

    reset_dc_call_degraded()
    _t = time.perf_counter()
    result: HookResult = _universal_materialize(predicate, candidates)
    if sink is not None:
        sink["universal_filter"] = time.perf_counter() - _t

    for hook in HOOKS:
        if not hook.applies(predicate, cand_tuple, ctx):
            continue
        if not isinstance(result, AnswerCollection):
            break
        _t = time.perf_counter()
        result = hook.run(predicate, result, ctx)
        if sink is not None:
            sink[hook.name] = time.perf_counter() - _t

    if isinstance(result, AnswerCollection) and (
        ctx.availability_degraded or _hooks_pkg.dc_call_was_degraded()
    ):
        if "filtering_degraded" not in result.caveats:
            result = result.model_copy(
                update={"caveats": _caveats("filtering_degraded", base=list(result.caveats))}
            )

    return result


def _build_variables(sv_set: list[str], ctx: HookContext) -> list[ResolvedVariable]:
    """Project each surviving DCID to an enriched ResolvedVariable.

    Indexes features by ``ctx.raw_candidates`` (the materializer's candidate
    pool), then delegates the per-DCID projection to ``_project_variables``.
    """
    feat_by_dcid = {f.dcid: f for f in ctx.raw_candidates}
    return _project_variables(sv_set, feat_by_dcid, ctx)


def materialize_many(
    predicates: tuple[Predicate, ...],
    candidates: list[StatVarFeatures],
    *,
    ctx: HookContext,
) -> AnswerCollection | AskClarification:
    """Materialise a tuple of predicates and union the results."""
    if len(predicates) == 1:
        result = materialize_via_hooks(predicates[0], candidates, ctx=ctx)
        if isinstance(result, AnswerCollection) and not result.variables:
            # Hook chain didn't populate variables (the common case for
            # non-projection queries) — fill them from raw_candidates.
            # When ProjectionEnrichmentHook or CrsDacRetrievalRecoveryHook
            # already produced variables with enriched availability/names,
            # leave them alone.
            result = result.model_copy(update={"variables": _build_variables(result.sv_set, ctx)})
        return result

    prewarm_dcids = _ordered_union(
        [_build_crs_svg_dcid(p)]
        for p in predicates
        if p.population_type == _CRS_DAC_POPULATION_TYPE
        and _CRS_DAC_RECIPIENT_SLOT in p.constraints
    )
    if prewarm_dcids:
        _hooks_pkg.variable_groups_batch(dcids=tuple(prewarm_dcids))

    accumulated: list[AnswerCollection] = []
    add_partial_result = False

    for sub_pred in predicates:
        # ctx.place_dcids is the authoritative observation-entity (donor) set;
        # the pipeline pre-narrows it to donors before calling materialize_many.
        sub_result = materialize_via_hooks(sub_pred, candidates, ctx=ctx)

        if isinstance(sub_result, AnswerCollection):
            accumulated.append(sub_result)
        elif sub_result.reason == "retrieval_weak":
            add_partial_result = True
        else:
            return sub_result

    if not accumulated:
        return AskClarification(
            reason="retrieval_weak",
            message=(
                "No matching statistical variables were found for any of the "
                "sub-predicates. The query may be under-specified or the retrieval "
                "step did not surface relevant candidates."
            ),
            proposed_clarifications=[],
        )

    unioned_svs = _ordered_union(ac.sv_set for ac in accumulated)
    unioned_svs.sort(key=lambda d: ctx.retrieval_scores.get(d, 0.0), reverse=True)
    unioned_svgs = _ordered_union(ac.svg_dcids for ac in accumulated)

    unioned_caveats: list[Caveat] = _caveats(*(c for ac in accumulated for c in ac.caveats))
    if add_partial_result:
        unioned_caveats = _caveats("partial_result", base=unioned_caveats)

    merged_confidence = CONFIDENCE_LEVELS[
        min(CONFIDENCE_LEVELS.index(ac.confidence) for ac in accumulated)
    ]

    if len(unioned_svs) >= SetCapHook().threshold:
        unioned_caveats = _caveats("set_valued_answer", base=unioned_caveats)

    # Preserve per-sub-result variable enrichment (backup-fetched names for
    # hook-added SVs, donor-narrowed availability/date_range from each
    # sub-pred's ProjectionEnrichmentHook). Rebuilding from ctx.raw_candidates
    # would lose both: raw_candidates excludes hook-added SVs and
    # ctx.place_availability is the pre-donor-narrow value. Fall back to
    # _build_variables only for SVs no sub-result enriched (sub-preds whose
    # hook chain produced an AnswerCollection with empty variables).
    var_by_dcid: dict[str, ResolvedVariable] = {}
    for ac in accumulated:
        for v in ac.variables:
            var_by_dcid.setdefault(v.dcid, v)
    missing = [d for d in unioned_svs if d not in var_by_dcid]
    if missing:
        for v in _build_variables(missing, ctx):
            var_by_dcid[v.dcid] = v
    unioned_variables = [var_by_dcid[d] for d in unioned_svs]

    return AnswerCollection(
        predicate=accumulated[0].predicate,
        sv_set=unioned_svs,
        svg_dcids=tuple(unioned_svgs),
        collection_dcid=accumulated[0].collection_dcid,
        confidence=merged_confidence,
        caveats=unioned_caveats,
        variables=unioned_variables,
    )
