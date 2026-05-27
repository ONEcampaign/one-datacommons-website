"""SetValuedRecipientHook — per-country CRS_DAC SV materialization.

Materializes per-recipient SVs for contained-in DevelopmentFinance queries by
intersecting inverse constraint arcs (purpose∩scheme family) and filtering to
child ISO3 suffixes.  The geographic aggregate is NOT produced here — it already
sits in ``result.sv_set`` from the universal materializer (scalar recipient path).
Fails open on every error path so the aggregate-only scalar answer stands.
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


@dataclass(frozen=True, slots=True)
class SetValuedRecipientHook:
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

    name: str = "set_valued_recipient"

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
                "SetValuedRecipientHook: unexpected error; failing open",
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

        # Wildcard guard: an unbounded family cannot be enumerated.
        purpose = predicate.constraints.get("DevelopmentFinancePurpose")
        scheme = predicate.constraints.get("DevelopmentFinanceScheme")
        if purpose is None or scheme is None:
            return result

        # Sort children for stable cache-key and deterministic per_country order.
        children = sorted(predicate.constraint_sets[_RECIPIENT_SLOT])

        # Defensive input bound.
        cap = min(load_config().child_place_cap, 500)
        children = children[:cap]

        # ONE inverse-arc call: purpose∩scheme family.
        arcs = retrieval.svs_by_inverse_arcs(
            value_dcids=(purpose, scheme),
            properties=_CRS_INVERSE_PROPS,
        )
        family = arcs.get(purpose, frozenset()) & arcs.get(scheme, frozenset())
        if not family:
            return result

        # ISO3 suffix filter — the DAC-region -F aggregate is intentionally excluded
        # (suffix "F" is not a country ISO3 code) so it rides the scalar path.
        child_iso3 = {d.split("/", 1)[1] for d in children if d.startswith("country/")}
        per_country = sorted(
            sv
            for sv in family
            if sv.startswith("ONE/CRS_DAC/") and sv.rsplit("-", 1)[-1] in child_iso3
        )
        if not per_country:
            return result

        # Aggregate-first union — result.sv_set (scalar aggregate) stays FIRST.
        new_sv_set = _ordered_union([result.sv_set, per_country])
        truncated = len(new_sv_set) > _CRS_DAC_SV_CAP
        new_sv_set = new_sv_set[:_CRS_DAC_SV_CAP]

        # set_valued_recipient = semantic "contained-in expansion" signal (always).
        # set_valued_answer = size signal, only when the cap truncated the result.
        caveats = _caveats("set_valued_recipient", base=list(result.caveats))
        if truncated:
            caveats = _caveats("set_valued_answer", base=caveats)

        # Fetch features for per-country DCIDs absent from ctx.raw_candidates so
        # display has names (mirrors CrsDacSvgExpansionHook Piece D).
        known_dcids = {c.dcid for c in ctx.raw_candidates}
        missing = [sv for sv in per_country if sv not in known_dcids]
        if missing:
            features = _hooks_pkg.stat_var_features_batch(sv_dcids=missing)
        else:
            features = {}

        # Merge raw_candidates features with freshly-fetched features for projection.
        all_features = {c.dcid: c for c in ctx.raw_candidates}
        all_features.update(features)

        variables = _project_variables(new_sv_set, all_features, ctx)

        return result.model_copy(
            update={
                "sv_set": new_sv_set,
                "caveats": caveats,
                "confidence": _resolve_confidence(
                    current=result.confidence, upgrade_to="high"
                ),
                "variables": variables,
            }
        )
