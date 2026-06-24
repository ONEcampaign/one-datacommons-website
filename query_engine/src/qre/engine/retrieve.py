"""Materialise stage: thin resolver dispatcher.

``materialise`` delegates to the shape's family resolver. All family-specific
logic lives in the resolver's ``resolve()`` method.

Result types:
  Materialised         — confirmed SV dcids + observation facets + coverage.
  NoDataDraft          — named reason why no data can be returned.
  MaterialisedCandidates — multiple Materialised results, one per surviving shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from qre.engine.bind import SlotBindingDraft
from qre.engine.graph import EngineGraphClient, Facet
from qre.engine.shape import ShapeDraft
from qre.models import Coverage


@dataclass
class Materialised:
    """Confirmed SV dcids and observation facets for a resolved query."""

    sv_dcids: list[str]
    facets: list[Facet]
    has_data: bool
    coverage: Coverage


@dataclass
class NoDataDraft:
    """Named no-data outcome from the materialise stage."""

    reason: str  # "no_observations" | "denominator_not_available" | "variable_not_resolved"


@dataclass
class MaterialisedCandidates:
    """Multiple Materialised results, one per surviving shape.

    Produced when recall+confirm yields several plausible five-tuple groups and
    no single dominant shape can be chosen.  Defined here alongside the resolver
    Protocol so its return type is stable.
    """

    candidates: list[Materialised]


def materialise(
    shape: ShapeDraft,
    bindings: list[SlotBindingDraft],
    recipient_dcid: str | None,
    donor_dcid: str | None,
    *,
    graph: EngineGraphClient,
) -> Materialised | NoDataDraft | MaterialisedCandidates:
    """Delegate resolution to the shape's family resolver.

    The shape carries its matched FamilyRule (stamped by discover.derive_shapes).
    When no family_rule is present (legacy build_shape path), falls back to the
    dev-finance resolver for backward compatibility.

    Args:
        shape:          The ShapeDraft for the query's family.
        bindings:       Slot bindings from the LLM bind stage.
        recipient_dcid: The resolved recipient dcid (from where binding or entity roles).
        donor_dcid:     The resolved donor dcid (observationAbout entity).
        graph:          Graph client (injected for testability).

    Returns:
        Materialised on success, NoDataDraft on any data-absence outcome.
    """
    if shape.family_rule is not None:
        return shape.family_rule.resolver.resolve(
            shape=shape,
            bindings=bindings,
            recipient_dcid=recipient_dcid,
            donor_dcid=donor_dcid,
            graph=graph,
        )

    # Legacy fallback: no family_rule stamped on the shape (build_shape path).
    # Route to the dev-finance resolver so backward-compat tests keep passing.
    from qre.engine.families.dev_finance import DEV_FINANCE_RESOLVER  # noqa: PLC0415

    return DEV_FINANCE_RESOLVER.resolve(
        shape=shape,
        bindings=bindings,
        recipient_dcid=recipient_dcid,
        donor_dcid=donor_dcid,
        graph=graph,
    )
