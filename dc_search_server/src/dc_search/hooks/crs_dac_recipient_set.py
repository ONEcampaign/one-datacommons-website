"""CrsDacRecipientSetHook — per-country CRS_DAC SV materialization.

This is a CRS_DAC-specific materializer. The name is deliberately not generic:
the trigger conditions (purpose∩scheme intersection, ISO3 suffix filter,
``ONE/CRS_DAC/`` namespace) all hard-code the CRS_DAC import recipe.  When a
second dataset needs an analogous capability, a separate hook should be added
rather than parameterizing this one — at that point the right abstraction will
be visible.

What it does: materializes per-recipient SVs for contained-in DevelopmentFinance
queries by intersecting inverse constraint arcs (purpose∩scheme family) and
filtering to child ISO3 suffixes.  The geographic aggregate is NOT produced
here — it already sits in ``result.sv_set`` from the universal materializer
(scalar recipient path).  Fails open on every error path so the aggregate-only
scalar answer stands.

Coordination with ``CrsDacSvgExpansionHook`` happens via the typed
``AnswerCollection.handled_by`` channel (not via user-facing caveats).  On
every successful run this hook adds ``HOOK_NAME`` to ``handled_by`` so the
SVG hook can short-circuit; on fail-open paths ``handled_by`` is untouched so
the SVG hook still runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dc_search import retrieval
from dc_search.predicate import AnswerCollection, Predicate

from ._helpers import (
    _CRS_DAC_SV_CAP,
    _caveats,
    _ordered_union,
    _project_variables,
    _resolve_confidence,
)
from .context import HookContext, HookResult

logger = logging.getLogger(__name__)

# Inverse-arc properties that together identify purpose∩scheme family membership.
_CRS_INVERSE_PROPS: tuple[str, str] = (
    "DevelopmentFinancePurpose",
    "DevelopmentFinanceScheme",
)
_RECIPIENT_SLOT = "DevelopmentFinanceRecipient"

# Public hook name — also the token written into AnswerCollection.handled_by
# so downstream hooks (CrsDacSvgExpansionHook) can short-circuit on a typed
# signal instead of pattern-matching user-facing caveats.
HOOK_NAME = "crs_dac_recipient_set"


@dataclass(frozen=True, slots=True)
class CrsDacRecipientSetHook:
    """Materialize per-recipient SVs for contained-in DevelopmentFinance queries.

    Applies when ``predicate.constraint_sets["DevelopmentFinanceRecipient"]`` is
    non-empty — i.e. a recipient set was bound (contained-in expansion).

    Mechanism: ONE combined ``<-[purpose,scheme]`` ``/v2/node`` call returns the
    purpose-set and scheme-set of SVs; their intersection is the family (~174 SVs
    for Malaria/ODAGrants).  Per-country SVs = family members whose DCID suffix
    (after the last ``-``) is a child ISO3 code.

    The pre-existing scalar-path aggregate (``-africa`` or similar) is already in
    ``result.sv_set`` before this hook runs.  ``_ordered_union`` keeps it first.

    Zero added fetches on all non-triggering paths (``applies`` returns False).
    """

    name: str = HOOK_NAME

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple,
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        return predicate.population_type == "DevelopmentFinance" and bool(
            predicate.constraint_sets.get(_RECIPIENT_SLOT)
        )

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        try:
            return self._run_inner(predicate, result, ctx)
        except Exception:
            logger.warning(
                "%s: unexpected error; failing open",
                HOOK_NAME,
                exc_info=True,
            )
            return result

    def _run_inner(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        from dc_search import hooks as _hooks_pkg
        from dc_search.config import load_config

        # Wildcard guard: an unbounded family cannot be enumerated. Log so a
        # regression upstream (extraction stops setting purpose or scheme) is
        # loud, not silent.
        purpose = predicate.constraints.get("DevelopmentFinancePurpose")
        scheme = predicate.constraints.get("DevelopmentFinanceScheme")
        if purpose is None or scheme is None:
            logger.warning(
                "%s near-miss: wildcard purpose/scheme; cannot enumerate family "
                "(purpose=%r scheme=%r)",
                HOOK_NAME,
                purpose,
                scheme,
            )
            return result

        children = sorted(predicate.constraint_sets[_RECIPIENT_SLOT])

        cap = min(load_config().child_place_cap, 500)
        children = children[:cap]

        arcs = retrieval.svs_by_inverse_arcs(
            value_dcids=(purpose, scheme),
            properties=_CRS_INVERSE_PROPS,
        )
        family = arcs.get(purpose, frozenset()) & arcs.get(scheme, frozenset())
        if not family:
            logger.warning(
                "%s near-miss: purpose∩scheme family empty (purpose=%s scheme=%s)",
                HOOK_NAME,
                purpose,
                scheme,
            )
            return result

        # ISO3 suffix filter — the DAC-region -F aggregate is intentionally
        # excluded (suffix "F" is not a country ISO3 code) so it rides the
        # scalar path.
        child_iso3 = {d.split("/", 1)[1] for d in children if d.startswith("country/")}
        per_country = sorted(
            sv
            for sv in family
            if sv.startswith("ONE/CRS_DAC/") and sv.rsplit("-", 1)[-1] in child_iso3
        )
        if not per_country:
            logger.warning(
                "%s near-miss: no family member matched child ISO3 set "
                "(family_size=%d child_iso3_size=%d)",
                HOOK_NAME,
                len(family),
                len(child_iso3),
            )
            return result

        # Aggregate-first union — result.sv_set (scalar aggregate) stays FIRST.
        new_sv_set = _ordered_union([result.sv_set, per_country])
        truncated = len(new_sv_set) > _CRS_DAC_SV_CAP
        new_sv_set = new_sv_set[:_CRS_DAC_SV_CAP]

        # set_valued_recipient: user-facing semantic ("contained-in expansion
        # ran"). set_valued_answer: size signal, only when the cap truncated.
        caveats = _caveats("set_valued_recipient", base=list(result.caveats))
        if truncated:
            caveats = _caveats("set_valued_answer", base=caveats)

        # Fetch features for per-country DCIDs absent from ctx.raw_candidates so
        # display has names.
        known_dcids = {c.dcid for c in ctx.raw_candidates}
        missing = [sv for sv in per_country if sv not in known_dcids]
        if missing:
            features = _hooks_pkg.stat_var_features_batch(sv_dcids=missing)
        else:
            features = {}

        all_features = {c.dcid: c for c in ctx.raw_candidates}
        all_features.update(features)

        variables = _project_variables(new_sv_set, all_features, ctx)

        return result.model_copy(
            update={
                "sv_set": new_sv_set,
                "caveats": caveats,
                "handled_by": result.handled_by | {HOOK_NAME},
                "confidence": _resolve_confidence(
                    current=result.confidence, upgrade_to="high"
                ),
                "variables": variables,
            }
        )
