"""Composable hook pipeline for the universal materializer.

Each hook answers two questions:
  1. ``applies(predicate, candidates, ctx) -> bool`` — should this hook run?
  2. ``run(predicate, result, ctx) -> HookResult``   — what does it do?

The dispatcher in ``materialize_via_hooks`` iterates ``HOOKS`` in declaration
order, runs applicable hooks in sequence, and short-circuits on the first
``AskClarification`` result.

Hook ordering::

    _universal_materialize (NOT a hook — runs once before the hook chain)
    → TopicExpansionHook                fires for ``relevantTopic`` predicates
    → WeakRetrievalTopicDumpHook        fires after topic expansion when retrieval weak
    → SdgAskClarificationHook           fires for SDG with missing populationType
    → CrsDacSvgExpansionHook   fires for DevelopmentFinance predicates
    → DonorIsObservationFacetHook  fires for CRS_DAC wildcards
    → DenominatorImplicitHook  fires for Person/count without denominator
    → SetCapHook               universal post-hook (caveat at ≥5 SVs)
    → PlaceAvailabilityHook    universal post-hook (availability filtering)
    → RetrievalQualityHook     universal post-hook (data-driven confidence)
    → EmptyResultHook          terminal guard → AskClarification
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from dc_search.extraction import ExtractedDate

from dc_search.predicate import (
    CONFIDENCE_LEVELS,
    AnswerCollection,
    AskClarification,
    Caveat,
    Confidence,
    Predicate,
    _apply_availability_filter,
    _build_crs_svg_dcid,
    _filter_by_predicate,
)
from dc_search.retrieval import (
    StatVarFeatures,
    child_vars_of_groups,
    expand_topic,
    variable_group,
    variable_groups_batch,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence ladder
# ---------------------------------------------------------------------------


def _resolve_confidence(
    *,
    current: Confidence,
    upgrade_to: Confidence | None = None,
    downgrade_to: Confidence | None = None,
) -> Confidence:
    """Monotone update: promote toward ``upgrade_to``, then cap at ``downgrade_to``."""
    rank = CONFIDENCE_LEVELS.index
    new = current
    if upgrade_to is not None and rank(upgrade_to) > rank(new):
        new = upgrade_to
    if downgrade_to is not None and rank(downgrade_to) < rank(new):
        new = downgrade_to
    return new


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------

# SV retrieval-score threshold below which confidence is downgraded to "low".
# Adjust here without touching hook logic.
_RETRIEVAL_QUALITY_THRESHOLD: float = 0.5

# Retrieval-score threshold for WeakRetrievalTopicDumpHook. Queries whose
# retrieval signal falls below this after topic-expansion are ambiguous
# enough to warrant a clarification request rather than a topic dump.
_WEAK_RETRIEVAL_TOPIC_DUMP_THRESHOLD: float = 0.7

# Maximum SVs from CRS_DAC SVG traversal before the cap fires.
_CRS_DAC_SV_CAP = 200

# Maximum SVs from Topic expansion before the cap fires.
_TOPIC_SV_CAP = 200

# ---------------------------------------------------------------------------
# HookContext — immutable bag of per-invocation context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HookContext:
    """Immutable context threaded through every hook invocation.

    ``place_availability`` is the union of ``variables_for_entity`` across all
    resolved place DCIDs.  A single set suffices for both subject-availability
    (Census) and donor-availability (CRS_DAC) — union semantics absorb the
    role distinction.

    ``retrieval_scores`` maps SV DCID → score from ``resolve_indicator``.
    Drives ``RetrievalQualityHook``.  An empty dict means scores were not
    populated (e.g. unit tests that bypass the retrieval step); the hook
    is a no-op in that case.

    ``dates`` carries extracted date references from the default endpoint's
    extraction step.  Empty list for the simple endpoint (no extraction LLM
    call).  ``DateFilterHook`` reads this field.
    """

    place_dcids: tuple[str, ...]
    """Already-resolved place DCIDs from ``extract_place_tokens``."""
    place_availability: frozenset[str] | None
    """Union of variables_for_entity across place_dcids; None = not computed."""
    retrieval_scores: dict[str, float]
    """SV DCID → retrieval score; empty dict when not available."""
    raw_candidates: tuple[StatVarFeatures, ...]
    """Candidate features as received by the materializer."""
    dates: list[ExtractedDate] = dataclasses_field(default_factory=list)
    """Extracted date references; empty list when not provided (simple endpoint)."""
    hook_timings: dict[str, float] | None = None
    """Optional sink for per-hook wall-clock seconds written by materialize_via_hooks.

    When set to a dict, the dispatcher writes ``{"universal_filter": secs,
    "<hook.name>": secs, ...}`` for each phase that actually ran.  Hooks that
    did not apply are not recorded.  The reference is frozen; the dict contents
    are mutated in place by the dispatcher.
    """


# ---------------------------------------------------------------------------
# Hook protocol
# ---------------------------------------------------------------------------

HookResult = AnswerCollection | AskClarification


@runtime_checkable
class Hook(Protocol):
    """Protocol every hook must satisfy.

    ``name`` identifies the hook in telemetry output.
    """

    name: str

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool: ...

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult: ...


# ---------------------------------------------------------------------------
# Helper: canonical caveat type
# ---------------------------------------------------------------------------


def _caveats(*extra: Caveat, base: list[Caveat] | None = None) -> list[Caveat]:
    """Build a caveat list, de-duplicating as we go."""
    seen: set[str] = set(base or [])
    result: list[Caveat] = list(base or [])
    for c in extra:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


# ---------------------------------------------------------------------------
# TopicExpansionHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# WeakRetrievalTopicDumpHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SdgAskClarificationHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CrsDacSvgExpansionHook
# ---------------------------------------------------------------------------

# CRS_DAC structural-group DCID prefix. The DevelopmentFinance import mints
# group DCIDs under this namespace; an SV may also belong to unrelated
# (topic/rollup) groups, so memberOf is filtered to this prefix when reading the
# group identity back out of candidate data.
_CRS_SVG_PREFIX = "ONE/g/DevelopmentFinance_"


def _observed_crs_svg_dcids(
    sv_set: list[str], candidates: tuple[StatVarFeatures, ...]
) -> set[str]:
    """CRS_DAC group DCIDs the in-result candidate SVs declare via ``memberOf``.

    Reads the group identity straight from graph data (the ``memberOf`` arc on
    each SV) instead of reconstructing it, so it can cross-check — and, for a
    fully-bound predicate, stand in for — the synthesized DCID without
    re-coupling to the import's naming recipe. Empty when candidates carry no
    ``memberOf`` (e.g. unit tests that bypass feature fetching).
    """
    in_result = set(sv_set)
    return {
        group
        for c in candidates
        if c.dcid in in_result
        for group in c.member_of
        if group.startswith(_CRS_SVG_PREFIX)
    }


@dataclass(frozen=True, slots=True)
class CrsDacSvgExpansionHook:
    """Build the CRS_DAC SVG DCID and expand sv_set via SVG traversal.

    Fires when ``predicate.population_type == "DevelopmentFinance"``.

    For wildcard predicates, walks ``<-memberOf`` via ``variable_group`` to
    expand the sv_set beyond the initial-k retrieval cap.  Adds
    ``retrieval_weak`` caveat when the SVG cannot be verified.
    """

    name: str = "crs_dac_svg_expansion"

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool:
        del candidates, ctx
        # Population-type alone identifies the DevelopmentFinance namespace.
        # _build_crs_svg_dcid uses predicate.constraints.get(slot) so absent
        # keys are equivalent to None (wildcard). Gating on the recipient key
        # being present caused an asymmetry: an LLM that omitted the recipient
        # key entirely (instead of emitting null) would silently lose SVG expansion.
        return predicate.population_type == "DevelopmentFinance"

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult:
        if not result.sv_set:
            return AskClarification(
                reason="under_specified",
                message=(
                    "No matching CRS_DAC statistical variables were found for the "
                    "supplied purpose/recipient/scheme combination. Please narrow "
                    "or broaden your query."
                ),
            )

        svg_dcid = _build_crs_svg_dcid(predicate)
        has_wildcards = any(v is None for v in predicate.constraints.values())
        sv_set = list(result.sv_set)
        caveats = list(result.caveats)
        svg_verified = True
        observed = _observed_crs_svg_dcids(result.sv_set, ctx.raw_candidates)

        # Drift check: for a fully-bound predicate the synthesized DCID should
        # equal the group the candidates actually declare via memberOf. A
        # mismatch means the import's naming recipe has moved out from under
        # _build_crs_svg_dcid — log it instead of letting recall quietly rot.
        if not has_wildcards and observed and svg_dcid not in observed:
            logger.warning(
                "CRS_DAC SVG drift: synthesized %s absent from candidate memberOf %s",
                svg_dcid,
                sorted(observed),
            )

        try:
            vg = variable_group(dcid=svg_dcid)
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
        # Anything else stays at the universal materializer's default (medium).
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

    # Slots a fully-bound CRS_DAC predicate is expected to have. An absent key
    # is treated as a wildcard (equivalent to value=None) so the LLM omitting a
    # slot doesn't silently suppress this caveat.
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


# ---------------------------------------------------------------------------
# DenominatorImplicitHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SetCapHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# PlaceAvailabilityHook
# ---------------------------------------------------------------------------


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
        # Topic predicates already filtered inside TopicExpansionHook.
        if not bool(ctx.place_availability):
            return False
        if "relevantTopic" in predicate.constraints:
            return False
        # Skip when every resolved place DCID is already bound as a constraint
        # value on this sub-predicate.  In the multi-value fan-out path the
        # HookContext is narrowed so ctx.place_dcids contains only the places
        # that appear in *this* sub-predicate's constraints; filtering by
        # availability would be redundant and would incorrectly shrink sv_set.
        constraint_values: set[str] = {v for v in predicate.constraints.values() if v is not None}
        if ctx.place_dcids and all(d in constraint_values for d in ctx.place_dcids):
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


# ---------------------------------------------------------------------------
# RetrievalQualityHook
# ---------------------------------------------------------------------------


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
        # No-op when retrieval scores not provided.
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


# ---------------------------------------------------------------------------
# EmptyResultHook
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DateFilterHook
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateFilterHook:
    """Filter candidates by extracted date range. SCAFFOLD ONLY — body is a no-op.

    The hook is fully wired into the chain and applies() correctly detects when
    dates are present; run() is a pass-through that logs the dates so we can
    validate the data path. Actual filtering logic is deferred to the next pass
    once we settle on how mixer date-window queries are constructed.
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
        # TODO(date-filter): implement actual date-window filtering.
        # For now, log the received dates and pass candidates through unchanged.
        logger.info("DateFilterHook received dates: %s (no-op pass-through)", ctx.dates)
        return result


# ---------------------------------------------------------------------------
# Hook registry — execution order matters
# ---------------------------------------------------------------------------

HOOKS: tuple[Hook, ...] = (
    TopicExpansionHook(),
    WeakRetrievalTopicDumpHook(),
    SdgAskClarificationHook(),
    CrsDacSvgExpansionHook(),
    DonorIsObservationFacetHook(),
    DenominatorImplicitHook(),
    DateFilterHook(),
    SetCapHook(),
    PlaceAvailabilityHook(),
    RetrievalQualityHook(),
    EmptyResultHook(),
)


# ---------------------------------------------------------------------------
# Universal materializer
# ---------------------------------------------------------------------------


def _universal_materialize(
    predicate: Predicate,
    candidates: list[StatVarFeatures],
) -> AnswerCollection:
    """Filter candidates by predicate and build an initial AnswerCollection.

    The result has ``confidence="medium"`` and empty caveats.  Domain hooks
    run after this to add namespace-specific behaviour (SVG traversal, caveats,
    confidence adjustments).

    Note: ``_filter_by_predicate`` is skipped for Topic predicates because
    Topic predicates have no SV candidates (``candidates == []``); their sv_set
    is populated by ``TopicExpansionHook`` instead.
    """
    sv_set = _filter_by_predicate(predicate, candidates)
    return AnswerCollection(
        predicate=predicate,
        sv_set=sv_set,
        svg_dcids=(),
        collection_dcid=None,
        confidence="medium",
        caveats=[],
    )


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def materialize_via_hooks(
    predicate: Predicate,
    candidates: list[StatVarFeatures],
    *,
    ctx: HookContext,
) -> AnswerCollection | AskClarification:
    """Run the universal materializer then the hook chain.

    1. ``_universal_materialize`` filters candidates and builds the base result.
    2. Each hook in ``HOOKS`` is checked; applicable hooks run in order.
    3. An ``AskClarification`` result short-circuits the remaining chain.
    """
    cand_tuple = tuple(candidates)
    sink = ctx.hook_timings

    # Universal pre-filter.
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

    return result


# ---------------------------------------------------------------------------
# Multi-predicate materializer — fan-out entry point
# ---------------------------------------------------------------------------

# Namespace constants used by materialize_many's CRS_DAC pre-warm step.
_CRS_DAC_POPULATION_TYPE = "DevelopmentFinance"
_CRS_DAC_RECIPIENT_SLOT = "DevelopmentFinanceRecipient"


def _ordered_union(seqs: Iterable[Iterable[str]]) -> list[str]:
    """Concatenate sequences, preserving first-appearance order, dedup-aware."""
    seen: set[str] = set()
    out: list[str] = []
    for seq in seqs:
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def materialize_many(
    predicates: tuple[Predicate, ...],
    candidates: list[StatVarFeatures],
    *,
    ctx: HookContext,
) -> AnswerCollection | AskClarification:
    """Materialise a tuple of predicates and union the results.

    Single-predicate fast path: delegates directly to ``materialize_via_hooks``
    with zero overhead, preserving byte-identical behaviour.

    Multi-predicate path:
    - For each sub-predicate a narrowed ``HookContext`` is built whose
      ``place_dcids`` contains only the places that appear as constraint values
      in that sub-predicate.  All other context fields are shared.
    - Each sub-predicate is materialised via the existing hook pipeline.
    - Results are classified:
        ``AnswerCollection``                         → accumulate.
        ``AskClarification(reason="retrieval_weak")`` → treat as empty; flag
                                                        ``partial_result`` caveat.
        Any other ``AskClarification``               → structural failure;
                                                        return immediately.
    - Accumulated results are unioned:
        ``sv_set``    — order-preserving dedup by ``ctx.retrieval_scores`` desc.
        ``svg_dcids`` — deduped concatenation, first-appearance order.
        ``caveats``   — set-union, first-appearance order.
        ``confidence`` — ``min`` over sub-predicate confidences.
    - A single SetCap pass is applied to the unioned ``sv_set``.
    - If the union is empty across all sub-predicates, ``AskClarification``
      with ``reason="retrieval_weak"`` is returned.

    The ``predicate`` field on the returned ``AnswerCollection`` is that of the
    first sub-predicate (the original multi-value predicate is not reconstructible
    from the explode).
    """
    if len(predicates) == 1:
        return materialize_via_hooks(predicates[0], candidates, ctx=ctx)

    # Pre-warm _vgroups_cache so each sub-predicate's CrsDacSvgExpansionHook
    # hits the cache instead of issuing a separate RTT — two bulk /v2/node
    # calls serve N SVG DCIDs, holding multi-value latency at parity with the
    # single-value path.
    prewarm_dcids = _ordered_union(
        [_build_crs_svg_dcid(p)]
        for p in predicates
        if p.population_type == _CRS_DAC_POPULATION_TYPE
        and _CRS_DAC_RECIPIENT_SLOT in p.constraints
    )
    if prewarm_dcids:
        variable_groups_batch(dcids=tuple(prewarm_dcids))

    accumulated: list[AnswerCollection] = []
    add_partial_result = False

    for sub_pred in predicates:
        constraint_values = {v for v in sub_pred.constraints.values() if v is not None}
        sub_ctx = replace(
            ctx,
            place_dcids=tuple(d for d in ctx.place_dcids if d in constraint_values),
        )
        sub_result = materialize_via_hooks(sub_pred, candidates, ctx=sub_ctx)

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

    return AnswerCollection(
        predicate=accumulated[0].predicate,
        sv_set=unioned_svs,
        svg_dcids=tuple(unioned_svgs),
        collection_dcid=accumulated[0].collection_dcid,
        confidence=merged_confidence,
        caveats=unioned_caveats,
    )
