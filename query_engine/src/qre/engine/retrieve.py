"""Materialise stage: confirm SVs exist and probe for observations.

Returns either Materialised (confirmed SV dcids with observation facets and coverage)
or NoDataDraft (a named reason why no data can be returned).

For dev-finance, the SV dcid is constructed from (scheme, purpose, recipient)
and confirmed via a node read. Unconfirmed SVs are dropped silently.

When no donor is named (donor_dcid=None), a default active donor is used
for the has_data probe.
"""
from __future__ import annotations

from dataclasses import dataclass

from qre.engine.bind import SlotBindingDraft
from qre.engine.coverage import coverage_from_facets
from qre.engine.families import SCHEMES, construct_sv_dcid
from qre.engine.graph import EngineGraphClient, Facet
from qre.engine.shape import ShapeDraft
from qre.models import CoverageBreadth

# Default donor for has_data probes when no specific donor is named.
# country/USA is consistently active in dev-finance data.
_DEFAULT_PROBE_DONOR = "country/USA"


@dataclass
class Materialised:
    """Confirmed SV dcids and observation facets for a dev-finance query."""

    sv_dcids: list[str]
    facets: list[Facet]
    has_data: bool
    coverage: CoverageBreadth


@dataclass
class NoDataDraft:
    """Named no-data outcome from the materialise stage."""

    reason: str  # "no_observations" | "denominator_not_available" | "variable_not_resolved"


def _find_binding(bindings: list[SlotBindingDraft], property_dcid: str) -> SlotBindingDraft | None:
    """Return the first binding whose property_dcid matches, or None."""
    for b in bindings:
        if b.property_dcid == property_dcid:
            return b
    return None


def _confirm_sv(sv_dcid: str, graph: EngineGraphClient) -> bool:
    """Return True if the SV dcid exists in the graph (has a label)."""
    label = graph.node_label(sv_dcid)
    return label is not None


def _probe_facets(sv_dcid: str, entity_dcid: str, graph: EngineGraphClient) -> list[Facet]:
    """Return observation facets for (sv, entity), empty list if none."""
    return graph.observation_facets(stat_var=sv_dcid, entity=entity_dcid)


def materialise(
    shape: ShapeDraft,
    bindings: list[SlotBindingDraft],
    recipient_dcid: str | None,
    donor_dcid: str | None,
    *,
    graph: EngineGraphClient,
) -> Materialised | NoDataDraft:
    """Confirm dev-finance SVs exist and probe for observations.

    For dev-finance, the SV dcid is constructed from (scheme, purpose, recipient)
    using construct_sv_dcid(), then confirmed via graph.node_label(). Only confirmed
    SVs are included in the result.

    Binding semantics for scheme:
      value  → construct and confirm one SV.
      set    → construct, confirm each, collect all confirmed.
      unbound → no SV dcids (all schemes are open); probe one member SV for has_data.
      absent → treated as unbound.

    Args:
        shape: The ShapeDraft for the query's family.
        bindings: Slot bindings from the LLM bind stage.
        recipient_dcid: The resolved recipient dcid (from the where binding or roles).
            When None, the where slot is absent/unbound; no SV can be constructed.
        donor_dcid: The resolved donor dcid (the observationAbout entity).
            When None (no named donor), the default probe donor is used.
        graph: Graph client (injected; use FakeGraph in tests).

    Returns:
        Materialised on success, NoDataDraft on any data-absence outcome.
    """
    # Find scheme, purpose, recipient bindings
    scheme_binding = _find_binding(bindings, "DevelopmentFinanceScheme")
    purpose_binding = _find_binding(bindings, "DevelopmentFinancePurpose")

    # Without a recipient dcid we cannot construct any SV
    if recipient_dcid is None:
        return NoDataDraft(reason="variable_not_resolved")

    # The donor for observation probing
    probe_donor = donor_dcid or _DEFAULT_PROBE_DONOR

    # Scheme unbound (df-09): all schemes are open. Probe one member to test for data.
    scheme_kind = scheme_binding.kind if scheme_binding else "unbound"
    if scheme_kind in ("unbound", "absent"):
        # When purpose is bound (value or set), probe across all its dcids so an
        # education query is not falsely evaluated against the Health sector.
        # Fall back to DAC/Health only when purpose is genuinely unbound or empty.
        if (
            purpose_binding
            and purpose_binding.kind in ("value", "set")
            and purpose_binding.value_dcids
        ):
            probe_purpose_dcids = list(purpose_binding.value_dcids)
        else:
            probe_purpose_dcids = ["DAC/Health"]

        # Probe the first scheme member across all relevant purpose dcids.
        probe_scheme = SCHEMES[0]
        probe_facets: list[Facet] = []
        for purpose_dcid in probe_purpose_dcids:
            probe_sv = construct_sv_dcid(probe_scheme, purpose_dcid, recipient_dcid)
            probe_facets.extend(_probe_facets(probe_sv, probe_donor, graph))

        # Return a named no-data outcome if no observations found.
        if not probe_facets or not any(f.obs_count > 0 for f in probe_facets):
            return NoDataDraft(reason="no_observations")

        coverage = coverage_from_facets(probe_facets)
        return Materialised(
            sv_dcids=[],
            facets=probe_facets,
            has_data=True,
            coverage=coverage,
        )

    # Purpose must be bound (value or set) to construct SVs
    if purpose_binding is None or purpose_binding.kind == "unbound":
        return NoDataDraft(reason="variable_not_resolved")

    # Collect (scheme, purpose) pairs based on binding kinds
    purpose_dcids: list[str]
    scheme_dcids: list[str]

    if scheme_kind == "value":
        scheme_dcids = scheme_binding.value_dcids[:1] if scheme_binding.value_dcids else []
    elif scheme_kind == "set":
        scheme_dcids = list(scheme_binding.value_dcids)
    else:
        scheme_dcids = []

    if purpose_binding.kind == "value":
        purpose_dcids = purpose_binding.value_dcids[:1] if purpose_binding.value_dcids else []
    elif purpose_binding.kind == "set":
        purpose_dcids = list(purpose_binding.value_dcids)
    else:
        purpose_dcids = []

    if not scheme_dcids or not purpose_dcids:
        return NoDataDraft(reason="variable_not_resolved")

    # Construct and confirm each (scheme, purpose) × recipient combination
    confirmed_svs: list[str] = []
    all_facets: list[Facet] = []

    for scheme in scheme_dcids:
        for purpose in purpose_dcids:
            sv_dcid = construct_sv_dcid(scheme, purpose, recipient_dcid)
            if not _confirm_sv(sv_dcid, graph):
                continue
            confirmed_svs.append(sv_dcid)
            facets = _probe_facets(sv_dcid, probe_donor, graph)
            all_facets.extend(facets)

    if not confirmed_svs:
        return NoDataDraft(reason="no_observations")

    # Determine has_data from facets
    has_data = any(f.obs_count > 0 for f in all_facets)
    if not has_data:
        return NoDataDraft(reason="no_observations")

    coverage = coverage_from_facets(all_facets)
    return Materialised(
        sv_dcids=confirmed_svs,
        facets=all_facets,
        has_data=has_data,
        coverage=coverage,
    )
