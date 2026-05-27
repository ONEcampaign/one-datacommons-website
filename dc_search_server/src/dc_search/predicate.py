"""Materialization layer for the predicate paradigm.

A Predicate ``{slot: value | wildcard}`` is materialized into either an
``AnswerCollection`` (the SV-set / constructable SVG that answers the query)
or an ``AskClarification`` (when the predicate is under-specified or the
retrieval is too weak to commit).

Dispatch is handled by the hook pipeline in ``hooks.py``
(``materialize_via_hooks``).  This module owns the core types (``Predicate``,
``AnswerCollection``, ``AskClarification``) and the shared filter helpers
(``_filter_by_predicate``, ``_apply_availability_filter``) that hooks use.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dc_search.extraction import ExtractedDate

if TYPE_CHECKING:
    from dc_search.retrieval.indicator import StatVarFeatures

# ---------------------------------------------------------------------------
# Enriched variable models (public wire surface)
# ---------------------------------------------------------------------------


class DateRange(BaseModel):
    """Observation coverage window [earliest, latest] at resolved place(s).

    Both fields are ISO-style strings (e.g. ``"2010"``, ``"2023-06-01"``) or
    ``None`` when one bound is unknown.  ``None`` for the whole object when no
    place is resolved or coverage is unavailable (e.g. base-DC vars in v1).
    """

    model_config = ConfigDict(frozen=True)

    earliest: str | None = None
    latest: str | None = None


class ResolvedVariable(BaseModel):
    """One resolved statistical variable with display and availability metadata."""

    model_config = ConfigDict(frozen=True)

    dcid: str
    name: str | None = None
    description: str | None = None
    unit: str | None = None
    measured_property: str | None = None
    population_type: str | None = None
    stat_type: str | None = None
    measurement_denominator: str | None = None
    score: float | None = None
    matched_sentence: str | None = Field(
        default=None,
        description="The retrieval sentence this variable matched (why it surfaced).",
    )
    available_at_place: bool | None = Field(
        default=None,
        description=(
            "Tri-state: None = no place resolved (availability inapplicable), "
            "OR every resolved place was bound as a constraint value (scalar "
            "recipient OR a set-valued contained-in recipient) so no "
            "donor/observation entity remained → None (not False); "
            "True = has data at resolved place(s); False = resolved but no data. "
            "Union across multiple places."
        ),
    )
    date_range: DateRange | None = Field(
        default=None,
        description=(
            "Observation coverage [earliest, latest] at resolved place(s), union "
            "across places. None when no place resolved or unknown (e.g. base-DC vars in v1)."
        ),
    )


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

Caveat = Literal[
    "availability_filtered",
    "date_filtered",
    "donor_is_observation_facet",
    "denominator_implicit",
    "filtering_degraded",
    "interpreted_place_as_recipient",
    "partial_result",
    "set_valued_answer",
    "set_valued_recipient",  # contained-in expansion ran; see ResolvedPlace.expanded / .children
    "retrieval_weak",
    "topic_expanded",
]

Confidence = Literal["low", "medium", "high"]

CONFIDENCE_LEVELS: tuple[Confidence, ...] = ("low", "medium", "high")


class Predicate(BaseModel):
    """Slot-value specification for a DataCommons statistical-variable query.

    ``constraints`` maps slot DCID → value DCID; ``None`` values are wildcards
    (the slot is intentionally unbound, not missing).

    Co-keying invariant: for a slot key ``k``, ``constraints[k]`` holds the
    scalar aggregate-parent recipient and ``constraint_sets[k]`` holds the
    contained-in child recipients (a membership filter, NOT a cross-product).
    Both reference the same slot.
    """

    model_config = ConfigDict(frozen=True)

    population_type: str | None
    measured_property: str | None
    constraints: dict[str, str | None] = Field(default_factory=dict)
    constraint_sets: dict[str, frozenset[str]] = Field(
        default_factory=dict, exclude=True
    )
    """Slot DCID → set of value DCIDs for membership filtering (NOT a cross-product).

    Empty = no set constraint. One predicate carries the whole set.
    ``exclude=True``: internal-only, never serialized to the HTTP response (mirrors
    ``AnswerCollection.sv_set``); the user-visible result is ``AnswerCollection.variables``.
    """


class AnswerCollection(BaseModel):
    """A resolved SV-set (and optional SVG DCIDs) that answers the query."""

    model_config = ConfigDict(frozen=True)

    predicate: Predicate
    sv_set: list[str] = Field(default_factory=list, exclude=True, repr=False)
    """Internal working set of SV DCIDs the hook chain filters/caps/unions.

    Excluded from serialized JSON; the public surface is ``variables``.
    Callers may still pass ``sv_set=[...]`` at construction time — the field is
    retained for all hook reads/writes.
    """
    svg_dcids: tuple[str, ...] = ()
    collection_dcid: str | None = None
    confidence: Confidence
    caveats: list[Caveat] = Field(default_factory=list)
    variable_label: str | None = None
    """Populated by pipeline._run_one_variable for default-endpoint fan-out.

    Allows callers to correlate N answers back to N extracted variables when
    positional alignment breaks (e.g. any variable returns AskClarification).
    Simple endpoint leaves this None.
    """
    date_filter: ExtractedDate | None = None
    """Carries the resolved date window when DateFilterHook dropped ≥1 var.

    None when no date filtering occurred or the simple endpoint was used.
    Populated alongside the ``"date_filtered"`` caveat.
    """
    variables: list[ResolvedVariable] = Field(default_factory=list)
    """Enriched per-variable objects projected from ``sv_set`` after the hook chain.

    This is the public wire surface (``sv_set`` is excluded from JSON).
    Populated by ``_build_variables`` in ``hooks.py``; empty until that step runs.
    """
    topic_name: str | None = None
    """Display name for the topic on the topic short-circuit path; ``None`` otherwise."""
    topic_description: str | None = None
    """Display description for the topic on the topic short-circuit path; ``None`` otherwise."""
    answer_kind: Literal["variables", "topic"] = "variables"
    """Distinguishes topic short-circuit answers (``"topic"``) from ordinary SV answers."""


class AskClarification(BaseModel):
    """Signal to the caller that the query cannot be confidently answered."""

    reason: Literal[
        "under_specified",
        "retrieval_weak",
        "ambiguous_shape",
        "parse_error",
        "no_candidates",
        "no_shapes",
        "error",
    ]
    message: str
    proposed_clarifications: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Materializer protocol (retained for type-checking compatibility)
# ---------------------------------------------------------------------------


class Materializer(Protocol):
    """A callable that materializes a predicate given a candidate SV list."""

    def __call__(
        self,
        predicate: Predicate,
        candidates: list[StatVarFeatures],
    ) -> AnswerCollection | AskClarification: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _specificity_extras(sv: StatVarFeatures, addressed_slots: frozenset[str]) -> int:
    """Count SV constraint slots not in ``addressed_slots``."""
    return sum(1 for slot, vals in sv.constraints.items() if vals and slot not in addressed_slots)


def _filter_by_predicate(
    predicate: Predicate,
    candidates: list[StatVarFeatures],
) -> list[str]:
    """Return DCIDs of candidates matching ``predicate``, ranked by specificity."""
    addressed_slots = frozenset(predicate.constraints)
    scored: list[tuple[int, int, str]] = []
    for idx, sv in enumerate(candidates):
        if predicate.population_type is not None:
            if predicate.population_type not in sv.population_type:
                continue
        if predicate.measured_property is not None:
            if predicate.measured_property not in sv.measured_property:
                continue
        ok = True
        for slot, value in predicate.constraints.items():
            if value is None:
                continue
            candidate_vals = sv.constraints.get(slot, [])
            if value not in candidate_vals:
                ok = False
                break
        if not ok:
            continue
        scored.append((_specificity_extras(sv, addressed_slots), idx, sv.dcid))
    scored.sort()
    return [s[2] for s in scored]


def _apply_availability_filter(
    sv_set: list[str],
    availability_set: frozenset[str] | None,
) -> list[str]:
    """Filter sv_set to those present in availability_set.

    Fails open: None/empty availability_set returns sv_set unchanged.
    Empty intersection also falls back to sv_set (variables_for_entity
    can undercover; better to return possibly-wrong than to blank-out).
    """
    if not availability_set:
        return sv_set
    filtered = [sv for sv in sv_set if sv in availability_set]
    return filtered if filtered else sv_set


# ---------------------------------------------------------------------------
# CRS_DAC SVG construction helpers (used by CrsDacSvgExpansionHook in hooks.py)
# ---------------------------------------------------------------------------

_CRS_SLOT_ORDER: tuple[str, str, str] = (
    "DevelopmentFinancePurpose",
    "DevelopmentFinanceRecipient",
    "DevelopmentFinanceScheme",
)

_DCID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9]+/(.+)$")


def _value_slug(value_dcid: str) -> str:
    """Strip namespace prefix from a value DCID for SVG segment construction."""
    m = _DCID_SEGMENT_RE.match(value_dcid)
    if m is None:
        return value_dcid
    prefix, rest = value_dcid.split("/", 1)
    return prefix[0].upper() + prefix[1:] + rest


def _build_crs_svg_dcid(predicate: Predicate) -> str:
    """Construct the CRS_DAC SVG DCID from a predicate."""
    parts: list[str] = []
    for slot in _CRS_SLOT_ORDER:
        value = predicate.constraints.get(slot)
        if value is None:
            parts.append(slot)
        else:
            parts.append(f"{slot}-{_value_slug(value)}")
    return "ONE/g/DevelopmentFinance_" + "_".join(parts)
