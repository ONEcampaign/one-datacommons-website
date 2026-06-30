"""Family registry: the ordered tuple of known FamilyRules and the rule_for lookup.

``rule_for`` iterates REGISTRY in order and returns the first rule whose resolver
matches the candidate SV list.  The last entry is the standard catch-all
(StandardResolver), which matches any non-empty confirmed-SV set.  CRS_DAC dcids
always match the dev-finance rule first.

Pure module (data + matching logic): no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.families.protocol import FamilyRule

if TYPE_CHECKING:
    from qre.engine.bind import SlotBindingDraft
    from qre.engine.extract import DateRequest
    from qre.engine.graph import EngineGraphClient
    from qre.engine.retrieve import Materialised, MaterialisedCandidates, NoDataDraft
    from qre.engine.shape import ShapeDraft


# ---------------------------------------------------------------------------
# StandardResolver (inline — no separate standard.py)
# ---------------------------------------------------------------------------

class _StandardResolver:
    """Catch-all resolver for standard Data Commons StatVars.

    Matches any non-empty confirmed-SV set that was not claimed by a more-specific
    resolver (e.g. dev-finance).  Because it is registered last, CRS_DAC dcids
    always match the dev-finance rule first and never reach here.

    Resolution strategy (detect → confirm → group):
      1. Shapes arrive from derive_shapes already carrying confirmed member SVs and
         arc facts (no re-read of node_arcs).
      2. Probe observations for the shape's representative SV (highest member_count,
         else first) against the resolved entity (subject role; no directional seam).
      3. Single confirmed shape with data → Materialised.
         Multiple plausible shapes → MaterialisedCandidates.
         Nothing confirms or no obs → NoDataDraft with the right reason.

    Disambiguation (definite vs. candidates) is handled in core.py via
    ``QRE_DOMINANCE_MARGIN`` (``_top_dominates``), not on this resolver — resolvers
    produce Materialised/NoDataDraft and do not rank shapes.
    """

    namespace: str = ""  # no namespace prefix; catch-all

    def matches(self, *, candidate_svs: list[str]) -> bool:
        """Return True for any non-empty confirmed-SV set (catch-all)."""
        return bool(candidate_svs)

    def resolve(
        self,
        *,
        shape: "ShapeDraft",
        bindings: "list[SlotBindingDraft]",
        recipient_dcid: str | None,
        donor_dcid: str | None,
        graph: "EngineGraphClient",
        date_request: "DateRequest | None" = None,
    ) -> "Materialised | NoDataDraft | MaterialisedCandidates":
        """Resolve a standard shape by probing observations for the representative SV.

        Consumes the per-SV arc facts carried on the shape — does NOT re-issue
        node_arcs.  The representative SV is the member with the highest member_count
        (here: the first SV from the shape's arc facts, which is insertion-ordered).

        The probe entity for standard is the subject entity (India, Kenya, etc.),
        passed in as recipient_dcid (core.py sets it from the single resolved entity
        when no directional preposition is detected).

        No-entity path (std-05: "under-5 child mortality rate"):
            When recipient_dcid is None the query has no place entity — this is
            variable disambiguation, not data retrieval.  Skip the per-entity
            observation probe and return bare coverage based on SV existence.
            The candidates trigger in core.py then collects one spec per
            confirmed shape and lets the user choose the right measure.
        """
        # Import here to avoid circular imports (retrieve imports discover, etc.).
        from qre.engine.coverage import coverage_from_facets  # noqa: PLC0415
        from qre.engine.retrieve import Materialised, NoDataDraft  # noqa: PLC0415

        sv_arc_facts = shape.sv_arc_facts or {}
        if not sv_arc_facts:
            return NoDataDraft(reason="variable_not_resolved")

        # P4: representative SV is the first confirmed member (derive_shapes preserves
        # insertion order from detect, so the highest-scoring SV is first).
        representative_sv = next(iter(sv_arc_facts))

        if recipient_dcid is None:
            # No-entity case: variable disambiguation without a specific place.
            # Return bare coverage (has_data from SV existence); the candidates
            # path in core.py will assemble one spec per shape so the user can
            # pick the right measure.
            from qre.models import CoverageBare  # noqa: PLC0415

            return Materialised(
                sv_dcids=[representative_sv],
                facets=[],
                has_data=True,
                coverage=CoverageBare(has_data=True),
            )

        # Probe the representative SV against the subject entity.
        probe_entity = recipient_dcid
        facets = graph.observation_facets(
            stat_var=representative_sv, entity=probe_entity, needs_dates=(date_request is not None)
        )

        if not facets or not any(f.obs_count > 0 for f in facets):
            return NoDataDraft(reason="no_observations")

        coverage = coverage_from_facets(facets, date_request=date_request)
        # Emit the representative SV as the single confirmed SV for the spec.
        # The candidates path fans out across all member SVs when triggered.
        # Thread the observation facets so StatVar.data_date_range is derived, and
        # mark the SV recipient-confirmed: the probe entity IS the subject entity
        # (read directly), so data_confirmed_at_recipient is True for standard.
        return Materialised(
            sv_dcids=[representative_sv],
            facets=facets,
            has_data=True,
            coverage=coverage,
            facets_by_sv={representative_sv: facets},
            recipient_confirmed={representative_sv},
        )


_STANDARD_RESOLVER = _StandardResolver()

STANDARD_RULE = FamilyRule(
    label="standard Data Commons",
    namespace="",  # catch-all; no namespace prefix
    resolver=_STANDARD_RESOLVER,
    shape_id="",   # derive_shapes uses the five-tuple string; no stable override needed
    axis_pins={},  # standard uses graph-derived axis classification only
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Ordered tuple of all registered families.  Iterated front-to-back by rule_for;
# more-specific namespaces appear before catch-alls.
#
# To add a family:
#   1. Create families/<name>.py with a FamilyResolver implementation and a FamilyRule.
#   2. Import the FamilyRule here and insert it before STANDARD_RULE.
#   3. Write fixtures and golden tests for the new family (see tests/engine/).
REGISTRY: tuple[FamilyRule, ...] = (
    DEV_FINANCE_RULE,
    STANDARD_RULE,   # catch-all: must be last
)


def rule_for_shape_id(*, shape_id: str) -> FamilyRule | None:
    """Return the registered FamilyRule whose stable shape_id matches exactly, or None.

    Exact match over REGISTRY. STANDARD_RULE.shape_id is "" so a standard dynamic
    five-tuple shape_id returns None (Path C then routes it to the standard-promote path).

    Args:
        shape_id: The stable shape_id string from a SpecResubmitInput.

    Returns:
        The matching FamilyRule, or None when no named family claims this shape_id
        (caller routes to standard promote or raises EngineInputError as appropriate).
    """
    for rule in REGISTRY:
        if rule.shape_id and rule.shape_id == shape_id:
            return rule
    return None


def rule_for(*, candidate_svs: list[str]) -> FamilyRule | None:
    """Return the first registered FamilyRule whose resolver matches, or None.

    CRS_DAC dcids match the dev-finance rule first; the standard catch-all matches
    any remaining non-empty SV list.  Returns None only when candidate_svs is empty.

    Args:
        candidate_svs: Candidate SV dcids from the recall stage.

    Returns:
        The matching FamilyRule, or None when no rule matches (empty list).
    """
    for rule in REGISTRY:
        if rule.resolver.matches(candidate_svs=candidate_svs):
            return rule
    return None
