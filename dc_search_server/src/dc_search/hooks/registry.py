"""All hook dataclass implementations and the HOOKS registry tuple.

The six retrieval names patched via ``patch("dc_search.hooks.<name>")`` are
accessed through the hooks package namespace at call time via _hooks_pkg so
monkeypatching intercepts the runtime lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dc_search import hooks as _hooks_pkg
from dc_search.extraction import ExtractedDate
from dc_search.predicate import (
    AnswerCollection,
    AskClarification,
    Predicate,
    ResolvedVariable,
    _apply_availability_filter,
    _build_crs_svg_dcid,
)
from dc_search.retrieval import (
    StatVarFeatures,
    child_vars_of_groups,
    expand_topic,
)

from ._helpers import (
    _CRS_DAC_SV_CAP,
    _RETRIEVAL_QUALITY_THRESHOLD,
    _TOPIC_SV_CAP,
    _WEAK_RETRIEVAL_TOPIC_DUMP_THRESHOLD,
    _caveats,
    _project_variables,
    _resolve_confidence,
)
from .context import Hook, HookContext, HookResult
from .crs_dac_recipient_set import HOOK_NAME as _CRS_RECIPIENT_SET_HOOK_NAME
from .crs_dac_recipient_set import CrsDacRecipientSetHook
from .date_helpers import _overlaps, _range_for, _union_range
from .projection_enrichment import ProjectionEnrichmentHook

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TopicExpansionHook:
    """Expand a topic DCID into its descendant SV set.

    Fires when ``relevantTopic`` is in ``predicate.constraints``.  Because
    Topic predicates have no SV candidates, this hook populates sv_set from
    ``expand_topic`` and returns before the universal pre-filter has any effect.

    Adds ``topic_expanded`` caveat.  Applies a 200-SV cap with
    ``set_valued_answer`` caveat when exceeded.

    Availability filtering is applied here (not delegated to
    ``PlaceAvailabilityHook``) so that the Topic cap is applied AFTER
    availability filtering.
    """

    name: str = "topic_expansion"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        return "relevantTopic" in predicate.constraints

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        topic_dcid = predicate.constraints.get("relevantTopic")
        if not topic_dcid:
            return AskClarification(
                reason="parse_error",
                message=(
                    "The Topic predicate is missing a 'relevantTopic' constraint. "
                    "This indicates an internal routing error."
                ),
            )

        sv_set = list(expand_topic(dcid=topic_dcid))
        if not sv_set:
            return AskClarification(
                reason="retrieval_weak",
                message=(
                    f"Topic '{topic_dcid}' expanded to no StatisticalVariable DCIDs. "
                    "The topic may be a roll-up container with no usable members."
                ),
            )

        pre_filter_len = len(sv_set)
        sv_set = _apply_availability_filter(sv_set, ctx.place_availability)

        caveat_list: list[str] = ["topic_expanded"]
        if len(sv_set) != pre_filter_len:
            caveat_list.append("availability_filtered")

        if len(sv_set) > _TOPIC_SV_CAP:
            sv_set = sv_set[:_TOPIC_SV_CAP]
            caveat_list.append("set_valued_answer")

        return result.model_copy(
            update={
                "sv_set": sv_set,
                "collection_dcid": topic_dcid,
                "confidence": _resolve_confidence(current=result.confidence, upgrade_to="high"),
                "caveats": caveat_list,
            }
        )


@dataclass(frozen=True, slots=True)
class WeakRetrievalTopicDumpHook:
    """Catch topic-expansion dumps where the retrieval signal is too weak.

    Fires when ALL three conditions hold:
    1. ``"topic_expanded"`` is in ``result.caveats`` — i.e. ``TopicExpansionHook``
       already ran and produced a topic-expanded ``AnswerCollection``.
    2. ``ctx.place_dcids`` is non-empty — at least one place resolved, so the
       user clearly named a location; the topic ambiguity is the only gap.
    3. The maximum retrieval score across the **full retrieval pool**
       (``ctx.retrieval_scores.values()``) is below
       ``_WEAK_RETRIEVAL_TOPIC_DUMP_THRESHOLD``.  Using the full pool
       rather than ``result.sv_set`` avoids the confound where topic expansion
       populates sv_set with expansion outputs not present in retrieval_scores.

    When it fires, the topic dump is replaced with an ``AskClarification``
    (``reason="retrieval_weak"``) asking the user to narrow the indicator.
    """

    name: str = "weak_retrieval_topic_dump"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del predicate, candidates
        return bool(ctx.place_dcids)

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate
        if "topic_expanded" not in result.caveats:
            return result
        top_score = max(ctx.retrieval_scores.values(), default=0.0)
        if top_score >= _WEAK_RETRIEVAL_TOPIC_DUMP_THRESHOLD:
            return result
        return AskClarification(
            reason="retrieval_weak",
            message=(
                "The topic match was weak — the retrieval step did not surface a "
                "clear indicator for this query. Could you narrow the indicator? "
                "For example, specify whether you are looking for a rate, count, "
                "index, or spending figure."
            ),
        )


@dataclass(frozen=True, slots=True)
class SdgAskClarificationHook:
    """Return AskClarification for SDG predicates missing a populationType.

    Fires when the predicate's SV set contains any SDG-prefixed DCID and
    ``predicate.population_type`` is None.

    SDG SVs each have a unique populationType (the SDG indicator code), so
    without a populationType the universal filter produces an empty sv_set.
    Better to surface a clear message than a silent empty result.
    """

    name: str = "sdg_ask_clarification"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del ctx
        return (
            predicate.population_type is None
            and bool(candidates)
            and all(c.dcid.startswith("sdg/") for c in candidates)
        )

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate, result, ctx
        return AskClarification(
            reason="retrieval_weak",
            message=(
                "The SDG indicator catalog returned no matching SV — "
                "the predicate is missing a populationType to match against."
            ),
        )


def _build_variables_from_features(
    sv_set: list[str],
    features: dict[str, StatVarFeatures],
    ctx: HookContext,
) -> list[ResolvedVariable]:
    """Build ResolvedVariable list for recovered SVs using fetched features.

    Same projection as materialization._build_variables, but draws features
    from the freshly-fetched ``features`` dict rather than ctx.raw_candidates,
    which does not contain recovered DCIDs.  availability/date_range use ctx
    as-is; downstream hooks will recompute them against the donor set if needed.
    """
    return _project_variables(sv_set, features, ctx)


_CRS_SVG_PREFIX = "ONE/g/DevelopmentFinance_"


def _observed_crs_svg_dcids(sv_set: list[str], candidates: tuple[StatVarFeatures, ...]) -> set[str]:
    """Extract CRS_DAC group DCIDs from candidate memberOf metadata."""
    in_result = set(sv_set)
    return {
        group
        for c in candidates
        if c.dcid in in_result
        for group in c.member_of
        if group.startswith(_CRS_SVG_PREFIX)
    }


@dataclass(frozen=True, slots=True)
class CrsDacRetrievalRecoveryHook:
    """Recover CRS_DAC SVs via SVG traversal when embedding retrieval surfaced none.

    Fires when DevFinance + ``sv_set`` is empty + the recipient-set hook did
    not already materialize. The SV namespace is known (we can synthesize the
    SVG DCID from the bound slots), so we ask the graph directly instead of
    bailing under_specified on a retrieval miss.

    Returns ``AskClarification(under_specified)`` only when SVG traversal
    finds nothing either — that's a real data gap, not a retrieval gap.
    """

    name: str = "crs_dac_retrieval_recovery"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        return predicate.population_type == "DevelopmentFinance"

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        # CrsDacRecipientSetHook already materialized the per-country family;
        # short-circuit so we don't redo the scalar SVG path. Typed handshake
        # via handled_by — the caveat is user-facing and must not double as IPC.
        if _CRS_RECIPIENT_SET_HOOK_NAME in result.handled_by:
            return result

        # Only the empty-sv_set branch lives here; the wildcard expansion
        # branch is a separate hook.
        if result.sv_set:
            return result

        svg_dcid = _build_crs_svg_dcid(predicate)

        try:
            vg = _hooks_pkg.variable_group(dcid=svg_dcid)
            recovered: list[str] = []
            if vg.child_vars:
                recovered = [v["dcid"] for v in vg.child_vars]
            elif vg.child_groups:
                child_group_dcids = tuple(g["dcid"] for g in vg.child_groups)
                group_to_svs = child_vars_of_groups(svg_group_dcids=child_group_dcids)
                recovered = [sv for svs in group_to_svs.values() for sv in svs]
            if recovered:
                if len(recovered) > _CRS_DAC_SV_CAP:
                    recovered = recovered[:_CRS_DAC_SV_CAP]
                    recovered_caveats = _caveats("set_valued_answer", base=list(result.caveats))
                else:
                    recovered_caveats = list(result.caveats)
                # Fetch features for recovered DCIDs so display has names.
                # Availability is recomputed against the donor set by
                # ProjectionEnrichmentHook downstream.
                features = _hooks_pkg.stat_var_features_batch(sv_dcids=recovered)
                recovered_variables = _build_variables_from_features(recovered, features, ctx)
                return result.model_copy(
                    update={
                        "sv_set": recovered,
                        "svg_dcids": (svg_dcid,),
                        "caveats": recovered_caveats,
                        "confidence": _resolve_confidence(
                            current=result.confidence, upgrade_to="high"
                        ),
                        "variables": recovered_variables,
                    }
                )
        except Exception:
            logger.warning(
                "CRS_DAC retrieval recovery failed for %s; falling back to under_specified",
                svg_dcid,
            )
        return AskClarification(
            reason="under_specified",
            message=(
                "No matching CRS_DAC statistical variables were found for the "
                "supplied purpose/recipient/scheme combination. Please narrow "
                "or broaden your query."
            ),
        )


@dataclass(frozen=True, slots=True)
class CrsDacWildcardExpansionHook:
    """Verify the CRS_DAC SVG name and expand sv_set for wildcard predicates.

    Fires when DevFinance + ``sv_set`` is non-empty + the recipient-set hook
    did not already materialize. Two responsibilities:

    1. Drift check: synthesized SVG DCID should equal what candidates declare
       via ``memberOf``. Mismatch means the import's naming recipe has moved;
       log so recall doesn't quietly rot. Recover via observed memberOf when
       synthesis fails for a fully-bound predicate.
    2. Wildcard expansion: when a slot is wildcarded, walk ``<-memberOf`` via
       ``variable_group`` to expand sv_set beyond the initial-k retrieval cap.

    Adds ``retrieval_weak`` caveat when SVG cannot be verified.
    """

    name: str = "crs_dac_wildcard_expansion"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        return predicate.population_type == "DevelopmentFinance"

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        # CrsDacRecipientSetHook already materialized; short-circuit.
        if _CRS_RECIPIENT_SET_HOOK_NAME in result.handled_by:
            return result

        # Only the non-empty-sv_set branch lives here.
        if not result.sv_set:
            return result

        svg_dcid = _build_crs_svg_dcid(predicate)
        has_wildcards = any(v is None for v in predicate.constraints.values())
        sv_set = list(result.sv_set)
        caveats = list(result.caveats)
        svg_verified = True
        observed = _observed_crs_svg_dcids(result.sv_set, ctx.raw_candidates)

        # Drift check: for a fully-bound predicate the synthesized DCID should
        # equal the group the candidates actually declare via memberOf.
        if not has_wildcards and observed and svg_dcid not in observed:
            logger.warning(
                "CRS_DAC SVG drift: synthesized %s absent from candidate memberOf %s",
                svg_dcid,
                sorted(observed),
            )

        try:
            vg = _hooks_pkg.variable_group(dcid=svg_dcid)
            if not vg.child_vars and not vg.child_groups:
                svg_verified = False
            elif has_wildcards:
                if vg.child_vars:
                    expanded = [v["dcid"] for v in vg.child_vars]
                    if expanded:
                        sv_set = expanded
                elif vg.child_groups and len(vg.child_groups) > len(sv_set):
                    child_group_dcids = tuple(g["dcid"] for g in vg.child_groups)
                    group_to_svs = child_vars_of_groups(svg_group_dcids=child_group_dcids)
                    expanded = [sv_dcid for svs in group_to_svs.values() for sv_dcid in svs]
                    if expanded:
                        if len(expanded) > _CRS_DAC_SV_CAP:
                            sv_set = expanded[:_CRS_DAC_SV_CAP]
                            if "set_valued_answer" not in caveats:
                                caveats.append("set_valued_answer")
                        else:
                            sv_set = expanded
        except Exception:  # broad catch intentional — graceful fallback
            svg_verified = False

        if not svg_verified:
            # Synthesis failed. For a fully-bound predicate the candidates' own
            # memberOf names the real group — trust the graph over our
            # reconstructed DCID and recover the verified signal. Wildcard
            # predicates need a parent-walk to reach the right granularity, so
            # they degrade to the retrieved candidates as before.
            if not has_wildcards and observed:
                svg_dcid = sorted(observed)[0]
                svg_verified = True
                logger.warning(
                    "CRS_DAC SVG synthesis failed; recovered via candidate memberOf %s",
                    svg_dcid,
                )
            else:
                logger.warning(
                    "CRS_DAC SVG unverified for %s (wildcards=%s); "
                    "degrading to retrieved candidates",
                    svg_dcid,
                    has_wildcards,
                )
                if "retrieval_weak" not in caveats:
                    caveats.append("retrieval_weak")

        # Confidence: fully-bound (no wildcards) + SVG verifies → high.
        upgrade = "high" if svg_verified and not has_wildcards else None
        return result.model_copy(
            update={
                "sv_set": sv_set,
                "svg_dcids": (svg_dcid,),
                "caveats": caveats,
                "confidence": _resolve_confidence(current=result.confidence, upgrade_to=upgrade),
            }
        )


# ---------------------------------------------------------------------------
# DonorIsObservationFacetHook
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DonorIsObservationFacetHook:
    """Add ``donor_is_observation_facet`` caveat for wildcarded CRS_DAC predicates.

    Fires when:
    - ``predicate.population_type == "DevelopmentFinance"``
    - At least one constraint slot is wildcarded (value = None)
    """

    name: str = "donor_is_observation_facet"

    _CRS_REQUIRED_SLOTS: tuple[str, ...] = (
        "DevelopmentFinancePurpose",
        "DevelopmentFinanceRecipient",
        "DevelopmentFinanceScheme",
    )

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        if predicate.population_type != "DevelopmentFinance":
            return False
        return any(predicate.constraints.get(slot) is None for slot in self._CRS_REQUIRED_SLOTS)

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate, ctx
        if "donor_is_observation_facet" in result.caveats:
            return result
        caveats = _caveats("donor_is_observation_facet", base=list(result.caveats))
        return result.model_copy(update={"caveats": caveats})


@dataclass(frozen=True, slots=True)
class DenominatorImplicitHook:
    """Add ``denominator_implicit`` caveat for Census Person/count queries.

    Fires when:
    - ``predicate.population_type == "Person"``
    - ``predicate.measured_property == "count"``
    - ``"measurementDenominator"`` is NOT in ``predicate.constraints``

    Idempotent: skips if caveat already present.
    """

    name: str = "denominator_implicit"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        return (
            predicate.population_type == "Person"
            and predicate.measured_property == "count"
            and "measurementDenominator" not in predicate.constraints
        )

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate, ctx
        if "denominator_implicit" in result.caveats:
            return result
        caveats = _caveats("denominator_implicit", base=list(result.caveats))
        return result.model_copy(update={"caveats": caveats})


@dataclass(frozen=True, slots=True)
class SetCapHook:
    """Add ``set_valued_answer`` caveat when sv_set length reaches the threshold.

    Fires when sv_set has at least ``threshold`` members.  Idempotent: if the
    caveat was already added by an earlier hook the second add is skipped.

    Threshold defaults to 5 (Census/CRS_DAC behaviour); Topic uses a higher
    cap enforced inside ``TopicExpansionHook``.
    """

    name: str = "set_cap"
    threshold: int = 5

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
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
        del predicate, ctx
        if len(result.sv_set) < self.threshold:
            return result
        if "set_valued_answer" in result.caveats:
            return result
        caveats = _caveats("set_valued_answer", base=list(result.caveats))
        return result.model_copy(update={"caveats": caveats})


@dataclass(frozen=True, slots=True)
class PlaceAvailabilityHook:
    """Filter sv_set to SVs present in the place availability set.

    Role-agnostic: ``ctx.place_availability`` is the union across all place
    entities (donors, subjects, etc.).  Adds ``availability_filtered`` caveat
    when the filter removes at least one SV.

    ``TopicExpansionHook`` handles availability for Topic predicates internally,
    so this hook is a no-op for Topic predicates (they emit their own caveat).

    Applies only when ``ctx.place_availability`` is non-None and non-empty.
    """

    name: str = "place_availability"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates
        if not bool(ctx.place_availability):
            return False
        if "relevantTopic" in predicate.constraints:
            return False
        return True

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate
        post = _apply_availability_filter(result.sv_set, ctx.place_availability)
        if len(post) == len(result.sv_set):
            return result
        caveats = _caveats("availability_filtered", base=list(result.caveats))
        return result.model_copy(update={"sv_set": post, "caveats": caveats})


@dataclass(frozen=True, slots=True)
class RetrievalQualityHook:
    """Set confidence and add ``retrieval_weak`` based on retrieval scores.

    Threshold: ``_RETRIEVAL_QUALITY_THRESHOLD`` (= 0.5).

    Guard: when ``ctx.retrieval_scores`` is empty, this hook is a no-op so
    that unit tests calling materializers without retrieval data are unaffected.
    The confidence value from the universal materializer (or an earlier hook)
    stands.
    """

    name: str = "retrieval_quality"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del predicate, candidates
        return bool(ctx.retrieval_scores)

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate
        if not result.sv_set:
            return result
        top = max((ctx.retrieval_scores.get(d, 0.0) for d in result.sv_set), default=0.0)
        if top >= _RETRIEVAL_QUALITY_THRESHOLD:
            return result
        caveats = _caveats("retrieval_weak", base=list(result.caveats))
        return result.model_copy(
            update={
                "confidence": _resolve_confidence(current=result.confidence, downgrade_to="low"),
                "caveats": caveats,
            }
        )


@dataclass(frozen=True, slots=True)
class EmptyResultHook:
    """Return AskClarification when sv_set is empty after all other hooks.

    Terminal guard — fires when the universal pre-filter + all domain hooks
    produced an empty sv_set, which would otherwise be a silent wrong answer.
    """

    name: str = "empty_result"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del predicate, candidates, ctx
        return True  # always checked — fires conditionally inside run()

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate, ctx
        if result.sv_set:
            return result
        return AskClarification(
            reason="retrieval_weak",
            message=(
                "No matching statistical variables were found for the supplied "
                "predicate. The namespace may not be fully supported yet, or the "
                "retrieval step did not surface relevant candidates."
            ),
        )


@dataclass(frozen=True, slots=True)
class DateFilterHook:
    """Filter SV candidates by extracted date window using the coverage map.

    Custom-DC vars (present in the coverage map) are filtered via map ranges.
    Base-DC vars (map-absent) are routed to variable/info (placeless) or live
    observation fetch (placed). Vars with no positive range evidence are kept
    (fail-open). ``latest`` queries bypass filtering entirely.
    """

    name: str = "date_filter"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del predicate, candidates
        return bool(ctx.dates)

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        del predicate

        # Only filter on point/range dates with at least one usable bound.
        def _has_usable_bound(d: ExtractedDate) -> bool:
            if d.kind == "point":
                return bool(d.start)
            return bool(d.start or d.end)

        dates = [d for d in ctx.dates if d.kind in ("point", "range") and _has_usable_bound(d)]
        if not dates:
            return result

        window = dates[0]
        if window.kind == "point":
            win_start = win_end = window.start
        else:  # range
            win_start, win_end = window.start, window.end

        sv_set = list(result.sv_set)
        if not sv_set:
            return result

        place_dcids = ctx.place_dcids
        try:
            cov = _hooks_pkg.variable_date_coverage(
                variable_dcids=tuple(sv_set),
                entity_dcids=tuple(place_dcids),
            )
        except Exception:
            return result

        base = [v for v in sv_set if v not in cov.envelopes]

        base_ranges: dict[str, tuple[str | None, str | None]] = {}
        if base:
            if place_dcids:
                obs = _hooks_pkg.observation_date_ranges(
                    variable_dcids=tuple(base),
                    entity_dcids=tuple(place_dcids),
                )
                for (v, _e), rng in obs.items():
                    base_ranges[v] = _union_range(base_ranges.get(v), rng)
            else:
                base_ranges = _hooks_pkg.variable_info_date_ranges(variable_dcids=tuple(base))

        keep: list[str] = []
        for v in sv_set:
            verdict, rng = _range_for(v, cov, base_ranges, place_dcids)
            if verdict == "keep":
                keep.append(v)
            elif verdict == "drop":
                pass  # custom var, clear miss at resolved places -> drop
            else:  # "range"
                assert rng is not None
                if _overlaps(rng[0], rng[1], win_start, win_end):
                    keep.append(v)

        if len(keep) == len(sv_set):
            return result

        caveats = _caveats("date_filtered", base=list(result.caveats))
        return result.model_copy(
            update={
                "sv_set": keep,
                "caveats": caveats,
                "date_filter": window,
            }
        )


HOOKS: tuple[Hook, ...] = (
    TopicExpansionHook(),
    WeakRetrievalTopicDumpHook(),
    SdgAskClarificationHook(),
    CrsDacRecipientSetHook(),
    CrsDacRetrievalRecoveryHook(),
    CrsDacWildcardExpansionHook(),
    DonorIsObservationFacetHook(),
    DenominatorImplicitHook(),
    DateFilterHook(),
    SetCapHook(),
    PlaceAvailabilityHook(),
    RetrievalQualityHook(),
    EmptyResultHook(),
    # Terminal: enrich variables + stamp interpreted_place_as_recipient when
    # the donor set differs from the full resolved set. Always-applies; the
    # work is gated on ctx flags inside run() so non-projection queries pay
    # nothing. Must run last — depends on the final sv_set after every other
    # hook has had a chance to add/filter.
    ProjectionEnrichmentHook(),
)
