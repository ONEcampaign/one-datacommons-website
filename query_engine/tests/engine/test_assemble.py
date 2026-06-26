"""Tests for assemble.py — candidates path.

Tests assemble_candidates with 2+ specs and verifies valid CandidatesResponse,
pairwise-distinct spec_ids, and broadest_first ordering.

Note: the production pipeline (core.py) always routes dev-finance to
DefiniteResponse; there is no in-corpus trigger for assemble_candidates.
This test proves the assembly path is reachable and correct.
"""
from __future__ import annotations

from qre.engine.assemble import assemble_candidates, build_shape_model, build_slot, build_spec
from qre.engine.bind import SlotBindingDraft
from qre.engine.families import DEV_FINANCE_FAMILY  # noqa: E402
from qre.engine.shape import build_shape
from qre.models import (
    BreadthDim,
    CandidatesResponse,
    CoverageBare,
    CoverageExact,
    Diagnostics,
    Entity,
    EntityRoleSubject,
    GraphRef,
    PipelineStep,
    QueryEcho,
    ResolveResponse,
    StatVar,
    TimeWindow,
    Timing,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_query_echo() -> QueryEcho:
    return QueryEcho(
        entry_path="raw_text",
        raw_query="test query",
        normalized_query="test query",
        variable_text=["test variable"],
        extract_skipped=False,
    )


def _make_diagnostics() -> Diagnostics:
    return Diagnostics(
        engine_build="test",
        warnings=[],
        timing_ms=Timing(total=0),
    )


def _make_coverage(has_data: bool = True) -> CoverageBare:
    return CoverageBare(has_data=has_data)


def _make_pipeline_steps() -> list[PipelineStep]:
    return [PipelineStep(step="extract", ran=True)]


def _make_entity(dcid: str, label: str) -> Entity:
    return Entity(
        ref=GraphRef(dcid=dcid, label=label),
        entity_type=None,
        role=EntityRoleSubject(),
    )


def _make_five_tuple_refs() -> dict[str, GraphRef]:
    return {
        "DevelopmentFinance": GraphRef(
            dcid="DevelopmentFinance", label="Development Finance"
        ),
        "DevelopmentFinanceFlow": GraphRef(
            dcid="DevelopmentFinanceFlow", label="Development Finance Flow"
        ),
        "measuredValue": GraphRef(dcid="measuredValue", label="Measured Value"),
    }


def _make_spec(sv_dcid: str, purpose_dcid: str, recipient_dcid: str):
    """Build a minimal dev-finance Spec for testing assemble_candidates."""
    family = DEV_FINANCE_FAMILY
    shape_draft = build_shape(family)
    five_tuple_refs = _make_five_tuple_refs()

    # Build slots: scheme=ODAGrants (value), purpose=<purpose_dcid> (value),
    # recipient=<recipient_dcid> (value)
    scheme_draft = SlotBindingDraft(
        axis="what",
        property_dcid="DevelopmentFinanceScheme",
        kind="value",
        value_dcids=["ODAGrants"],
    )
    purpose_draft = SlotBindingDraft(
        axis="how",
        property_dcid="DevelopmentFinancePurpose",
        kind="value",
        value_dcids=[purpose_dcid],
    )
    recipient_draft = SlotBindingDraft(
        axis="where",
        property_dcid="DevelopmentFinanceRecipient",
        kind="value",
        value_dcids=[recipient_dcid],
    )

    scheme_ref = GraphRef(dcid="ODAGrants", label="ODA Grants")
    purpose_ref = GraphRef(dcid=purpose_dcid, label=purpose_dcid)
    recipient_ref = GraphRef(dcid=recipient_dcid, label=recipient_dcid)

    slot_key_models = []
    slots = []
    for slot_draft_key, binding_draft, grounded in [
        (shape_draft.slot_keys[0], scheme_draft, [scheme_ref]),
        (shape_draft.slot_keys[1], purpose_draft, [purpose_ref]),
        (shape_draft.slot_keys[2], recipient_draft, [recipient_ref]),
    ]:
        prop_ref = GraphRef(
            dcid=slot_draft_key.property_dcid, label=slot_draft_key.property_dcid
        )
        slot = build_slot(slot_draft_key, binding_draft, grounded, property_ref=prop_ref)
        slots.append(slot)
        slot_key_models.append(slot.key)

    shape_model = build_shape_model(
        shape_draft,
        slot_key_models,
        five_tuple_refs,
        member_count=1,
    )

    sv_ref = GraphRef(dcid=sv_dcid, label=sv_dcid)
    stat_vars = [StatVar(ref=sv_ref, shape_id=shape_draft.shape_id, slot_values=[])]

    return build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=[_make_entity("country/ETH", "Ethiopia")],
        coverage=_make_coverage(),
        pipeline_trace=_make_pipeline_steps(),
        timing_by_step={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_assemble_candidates_returns_candidates_response():
    """assemble_candidates returns a CandidatesResponse with status=candidates."""
    spec_health = _make_spec(
        sv_dcid="ONE/CRS_DAC/Health-ODAGrants-ETH",
        purpose_dcid="DAC/Health",
        recipient_dcid="country/ETH",
    )
    spec_malaria = _make_spec(
        sv_dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH",
        purpose_dcid="DAC/Malariacontrol",
        recipient_dcid="country/ETH",
    )

    result = assemble_candidates(
        specs=[spec_health, spec_malaria],
        query_echo=_make_query_echo(),
        diagnostics=_make_diagnostics(),
    )

    assert isinstance(result, ResolveResponse)
    inner = result.root
    assert inner.status == "candidates"
    assert isinstance(inner, CandidatesResponse)


def test_assemble_candidates_has_two_or_more_distinct_spec_ids():
    """Candidates set has >= 2 specs with pairwise-distinct spec_ids."""
    spec_health = _make_spec(
        sv_dcid="ONE/CRS_DAC/Health-ODAGrants-ETH",
        purpose_dcid="DAC/Health",
        recipient_dcid="country/ETH",
    )
    spec_malaria = _make_spec(
        sv_dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH",
        purpose_dcid="DAC/Malariacontrol",
        recipient_dcid="country/ETH",
    )

    result = assemble_candidates(
        specs=[spec_health, spec_malaria],
        query_echo=_make_query_echo(),
        diagnostics=_make_diagnostics(),
    )

    inner = result.root
    assert isinstance(inner, CandidatesResponse)
    specs = inner.candidates.specs
    assert len(specs) >= 2
    spec_ids = [s.spec_id for s in specs]
    # All spec_ids must be pairwise distinct
    assert len(spec_ids) == len(set(spec_ids)), (
        f"Duplicate spec_ids found: {spec_ids}"
    )


def test_assemble_candidates_ordering_is_broadest_first():
    """CandidateSet ordering is broadest_first (only ordering in v1)."""
    spec_a = _make_spec(
        sv_dcid="ONE/CRS_DAC/Health-ODAGrants-ETH",
        purpose_dcid="DAC/Health",
        recipient_dcid="country/ETH",
    )
    spec_b = _make_spec(
        sv_dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH",
        purpose_dcid="DAC/Malariacontrol",
        recipient_dcid="country/ETH",
    )

    result = assemble_candidates(
        specs=[spec_a, spec_b],
        query_echo=_make_query_echo(),
        diagnostics=_make_diagnostics(),
    )

    inner = result.root
    assert isinstance(inner, CandidatesResponse)
    assert inner.candidates.ordering == "broadest_first"


# ---------------------------------------------------------------------------
# applied_window / date_source in ResolutionTrace
# ---------------------------------------------------------------------------


def _make_exact_coverage(window: TimeWindow | None) -> CoverageExact:
    return CoverageExact(
        has_data=True,
        observation_count=42,
        dimensions=[
            BreadthDim(label="sources", count=1),
            BreadthDim(label="observations", count=42),
        ],
        window=window,
    )


def test_build_spec_applied_window_and_date_source_when_window_present():
    """build_spec with CoverageExact(window=...) → applied_window set, date_source='query'."""
    window = TimeWindow(start_year=2015, end_year=2020)
    # Build a spec with windowed exact coverage
    from qre.engine.assemble import build_shape_model, build_slot, build_spec
    from qre.engine.bind import SlotBindingDraft
    from qre.engine.families import DEV_FINANCE_FAMILY
    from qre.engine.shape import build_shape

    family = DEV_FINANCE_FAMILY
    shape_draft = build_shape(family)
    five_tuple_refs = _make_five_tuple_refs()

    scheme_draft = SlotBindingDraft(
        axis="what",
        property_dcid="DevelopmentFinanceScheme",
        kind="value",
        value_dcids=["ODAGrants"],
    )
    purpose_draft = SlotBindingDraft(
        axis="how",
        property_dcid="DevelopmentFinancePurpose",
        kind="value",
        value_dcids=["DAC/Health"],
    )
    recipient_draft = SlotBindingDraft(
        axis="where",
        property_dcid="DevelopmentFinanceRecipient",
        kind="value",
        value_dcids=["country/ETH"],
    )

    slots = []
    slot_key_models = []
    for slot_draft_key, binding_draft, grounded in [
        (shape_draft.slot_keys[0], scheme_draft, [GraphRef(dcid="ODAGrants", label="ODA Grants")]),
        (shape_draft.slot_keys[1], purpose_draft, [GraphRef(dcid="DAC/Health", label="Health")]),
        (
            shape_draft.slot_keys[2],
            recipient_draft,
            [GraphRef(dcid="country/ETH", label="Ethiopia")],
        ),
    ]:
        prop_ref = GraphRef(dcid=slot_draft_key.property_dcid, label=slot_draft_key.property_dcid)
        slot = build_slot(slot_draft_key, binding_draft, grounded, property_ref=prop_ref)
        slots.append(slot)
        slot_key_models.append(slot.key)

    shape_model = build_shape_model(shape_draft, slot_key_models, five_tuple_refs, member_count=1)
    sv_ref = GraphRef(dcid="ONE/CRS_DAC/Health-ODAGrants-ETH", label="Health ODA")
    stat_vars = [StatVar(ref=sv_ref, shape_id=shape_draft.shape_id, slot_values=[])]

    result_spec = build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=[_make_entity("country/ETH", "Ethiopia")],
        coverage=_make_exact_coverage(window),
        pipeline_trace=_make_pipeline_steps(),
        timing_by_step={},
    )

    assert result_spec.resolution.applied_window == window
    assert result_spec.resolution.date_source == "query"


def test_build_spec_applied_window_and_date_source_none_when_no_window():
    """build_spec with CoverageBare(window=None) → applied_window=None, date_source=None."""
    spec = _make_spec(
        sv_dcid="ONE/CRS_DAC/Health-ODAGrants-ETH",
        purpose_dcid="DAC/Health",
        recipient_dcid="country/ETH",
    )
    # _make_spec uses CoverageBare(has_data=True) which has window=None
    assert spec.resolution.applied_window is None
    assert spec.resolution.date_source is None
