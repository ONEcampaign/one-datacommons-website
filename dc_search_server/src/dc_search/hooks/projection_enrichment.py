"""ProjectionEnrichmentHook — terminal patch-up for projection-driven queries.

Two responsibilities, both gated on the materialized ``AnswerCollection``:

1. **Enrichment** — when the donor place set differs from the full resolved
   place set (because a recipient was bound), or when the hook chain added SVs
   that weren't in the original retrieval pool (e.g. CRS_DAC per-country
   variants from inverse arcs):

   - Backup feature fetch for SVs absent from ``ctx.raw_candidates`` so display
     names are populated.
   - Recompute availability + date ranges against the *donor* set, not the
     full place set (which would include the bound recipient as a phantom
     observation entity).
   - Rebuild ``variables`` with the merged features, fresh availability, and
     refreshed date ranges.

   The two enrichment steps are independent: when an upstream hook (e.g.
   ``CrsDacRetrievalRecoveryHook``) has already produced complete variables
   inline, the backup feature fetch is skipped, but the donor-set availability
   recompute still runs and patches the existing variables — the upstream
   hook ran before donor narrowing so its availability values reflect the
   full place set.

2. **Caveat stamping** — when ``ctx.defaulted_recipient`` is True, add the
   ``interpreted_place_as_recipient`` caveat. This is informational only; the
   user benefits from knowing the role assignment came from an unqualified-
   place default rather than explicit "to X" grammar.

Runs last in the hook chain (after ``EmptyResultHook``) so it only fires
when the chain produced a real answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dc_search.predicate import (
    AnswerCollection,
    DateRange,
    Predicate,
    ResolvedVariable,
)

from ._helpers import _caveats, _project_variables
from .context import HookContext, HookResult

logger = logging.getLogger(__name__)

HOOK_NAME = "projection_enrichment"


@dataclass(frozen=True, slots=True)
class ProjectionEnrichmentHook:
    """Terminal hook: enrichment + ``interpreted_place_as_recipient`` caveat.

    See module docstring for the responsibilities.  Always applies — the work
    is conditional on the ``ctx`` flags, so a non-projection query pays
    nothing.
    """

    name: str = HOOK_NAME

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple,
        ctx: HookContext,
    ) -> bool:
        del predicate, candidates, ctx
        return True

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate

        donor_dcids = ctx.place_dcids
        all_resolved = ctx.all_resolved_dcids

        # Two independent enrichment triggers:
        #  - donor_narrowed: a recipient was bound, so availability and date
        #    ranges must be recomputed against the donor set (not the full
        #    resolved set that pre-bind availability used).
        #  - added_via_hook: hooks added SVs not in the retrieval pool, so
        #    features for those SVs need a backup fetch to populate names.
        retrieved_dcids: set[str] = {f.dcid for f in ctx.raw_candidates}
        added_via_hook = bool(set(result.sv_set) - retrieved_dcids)
        donor_narrowed = bool(all_resolved) and tuple(all_resolved) != tuple(donor_dcids)

        updated = result
        if donor_narrowed or added_via_hook:
            updated = self._enrich(
                updated,
                ctx,
                donor_narrowed=donor_narrowed,
                added_via_hook=added_via_hook,
            )

        if ctx.defaulted_recipient and "interpreted_place_as_recipient" not in updated.caveats:
            updated = updated.model_copy(
                update={
                    "caveats": _caveats(
                        "interpreted_place_as_recipient", base=list(updated.caveats)
                    ),
                }
            )

        return updated

    @staticmethod
    def _variables_complete(result: AnswerCollection) -> bool:
        """True when ``result.variables`` already covers every SV with a name.

        An upstream hook (e.g. ``CrsDacRetrievalRecoveryHook``) may build the
        full variable list inline when it has the features in hand.  Detect
        that so the backup feature fetch can skip — names are already there.
        Does NOT imply the availability values are correct; an upstream hook
        runs before donor narrowing, so its ``available_at_place`` reflects
        the full place set rather than the donor subset.
        """
        if not result.variables or len(result.variables) != len(result.sv_set):
            return False
        var_dcids = {v.dcid for v in result.variables}
        if var_dcids != set(result.sv_set):
            return False
        return all(v.name is not None for v in result.variables)

    def _enrich(
        self,
        result: AnswerCollection,
        ctx: HookContext,
        *,
        donor_narrowed: bool,
        added_via_hook: bool,
    ) -> AnswerCollection:
        # Deferred imports: ``pipeline._availability`` re-exports ``HookContext``
        # from this package, so a top-level import here would cycle.
        # ``_hooks_pkg`` indirection on ``stat_var_features_batch`` mirrors
        # registry.py — tests patch ``dc_search.hooks.<name>`` and the runtime
        # lookup honours the patch.
        from dc_search import hooks as _hooks_pkg
        from dc_search.pipeline._availability import (
            _resolve_union_availability_with_ranges,
        )

        final_sv_set = list(result.sv_set)
        if not final_sv_set:
            return result

        # Backup feature fetch — only when an upstream hook hasn't already
        # populated complete variables AND we have SVs that weren't in the
        # original retrieval pool.
        upstream_built_variables = self._variables_complete(result)
        merged_features = {f.dcid: f for f in ctx.raw_candidates}
        if added_via_hook and not upstream_built_variables:
            missing_dcids = [d for d in final_sv_set if d not in merged_features]
            if missing_dcids:
                try:
                    merged_features.update(
                        _hooks_pkg.stat_var_features_batch(sv_dcids=missing_dcids)
                    )
                except Exception:
                    # Fail-open: names remain None for missing DCIDs rather
                    # than tanking the whole answer over a transient blip.
                    logger.warning(
                        "projection enrichment backup feature fetch failed; "
                        "names may be missing for %d DCIDs",
                        len(missing_dcids),
                    )

        # Recompute availability + date_range against the *donor* set over the
        # final sv_set. Omit availability (None, not False) when donor_dcids is
        # empty — every place was bound as a recipient, no observation entity
        # remained, and "no availability information" is the truthful answer.
        donor_dcids = ctx.place_dcids
        if donor_narrowed:
            if donor_dcids:
                try:
                    new_avail, new_ranges, _degraded = _resolve_union_availability_with_ranges(
                        list(donor_dcids),
                        tuple(final_sv_set),
                    )
                except Exception:
                    new_avail = frozenset()
                    new_ranges = {}
            else:
                new_avail = None
                new_ranges = {}
        else:
            # Donor matches the full resolved set — keep the pre-bind values
            # (the upstream hook used them, and they were correct then).
            new_avail = ctx.place_availability
            new_ranges = dict(ctx.dcid_to_date_range)

        # Two rebuild paths:
        #  - Upstream-built variables: patch availability/date_range fields in
        #    place. Preserves names/descriptions the upstream hook fetched.
        #  - Otherwise: full projection from merged_features.
        if upstream_built_variables:
            new_variables = _patch_availability(
                result.variables, donor_dcids, new_avail, new_ranges
            )
        else:
            enrich_ctx = HookContext(
                place_dcids=donor_dcids,
                place_availability=new_avail,
                retrieval_scores=ctx.retrieval_scores,
                raw_candidates=tuple(merged_features.values()),
                dates=ctx.dates,
                availability_degraded=ctx.availability_degraded,
                dcid_to_sentence=ctx.dcid_to_sentence,
                dcid_to_date_range=new_ranges,
                hook_timings=ctx.hook_timings,
                all_resolved_dcids=ctx.all_resolved_dcids,
                defaulted_recipient=ctx.defaulted_recipient,
            )
            new_variables = _project_variables(final_sv_set, merged_features, enrich_ctx)

        return result.model_copy(update={"variables": new_variables})


def _patch_availability(
    variables: list[ResolvedVariable],
    donor_dcids: tuple[str, ...],
    new_avail: frozenset[str] | None,
    new_ranges: dict[str, tuple[str | None, str | None]],
) -> list[ResolvedVariable]:
    """Replace ``available_at_place`` and ``date_range`` on each variable.

    Used when an upstream hook produced full variables but its availability
    was computed against the pre-donor-narrow place set.  Names and other
    feature fields are preserved.
    """
    patched: list[ResolvedVariable] = []
    for var in variables:
        if not donor_dcids or new_avail is None:
            avail: bool | None = None
        else:
            avail = var.dcid in new_avail
        rng = new_ranges.get(var.dcid)
        date_range = DateRange(earliest=rng[0], latest=rng[1]) if rng else None
        patched.append(
            var.model_copy(
                update={"available_at_place": avail, "date_range": date_range}
            )
        )
    return patched
