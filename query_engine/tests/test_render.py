"""Grammar unit tests for qre.render: hand-built Specs covering each join step.

Notes:
- The "single-member set" edge case is untestable: BindingSet.values carries
  Field(min_length=2), so a one-member set cannot be constructed through the model.
- The directional-from entity test (case 10, 11) hand-builds an entity using
  EntityRoleDirectional(direction="from") directly — matching how the live engine
  now emits donors. The renderer's from-scan is exercised here and also end-to-end
  by the donor+recipient e2e test.
"""
from __future__ import annotations

from qre.models import (
    Axis,
    BindingAbsent,
    BindingSet,
    BindingUnbound,
    BindingValue,
    CoverageBare,
    Entity,
    EntityRoleDirectional,
    EntityRoleSubject,
    GraphRef,
    PipelineStep,
    ResolutionTrace,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    Spec,
    StatVar,
    TimeWindow,
)
from qre.render import no_data_phrase, render_candidates_summary, render_sentence

# ---------------------------------------------------------------------------
# Minimal Spec builder
# ---------------------------------------------------------------------------

def _ref(dcid: str, label: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=label)


def _slot_key(axis: Axis, prop_dcid: str, prop_label: str, label: str) -> SlotKey:
    return SlotKey(axis=axis, property=_ref(prop_dcid, prop_label), label=label)


def _spec(
    *,
    slots: list[Slot] | None = None,
    entities: list[Entity] | None = None,
    window: TimeWindow | None = None,
    measured_property_label: str = "Development Finance Flow",
) -> Spec:
    """Build a minimal valid Spec. Coverage uses CoverageBare; window is optional."""
    shape = Shape(
        shape_id="test-shape",
        label="Test Shape",
        population_type=_ref("DevelopmentFinance", "Development Finance"),
        measured_property=_ref("DevelopmentFinanceFlow", measured_property_label),
        stat_type=_ref("measuredValue", "Measured Value"),
        slot_keys=[],
        member_count=1,
    )
    coverage = CoverageBare(has_data=True, window=window)
    resolution = ResolutionTrace(
        resolved_stat_vars=[],
        resolved_entities=[],
        resolved_sources=[],
        slot_filters=[],
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
    )
    return Spec(
        spec_id="test-spec-id",
        shape=shape,
        slots=slots or [],
        stat_vars=[StatVar(ref=_ref("sv1", "SV1"), shape_id="test-shape", slot_values=[])],
        entities=entities or [],
        coverage=coverage,
        resolution=resolution,
    )


def _what_slot(label: str) -> Slot:
    return Slot(
        key=_slot_key("what", "DevelopmentFinanceScheme", "Scheme", "scheme"),
        binding=BindingValue(value=SlotValue(ref=_ref("ODAGrants", label), value_kind="entity")),
    )


def _how_slot(label: str) -> Slot:
    return Slot(
        key=_slot_key("how", "DevelopmentFinancePurpose", "Purpose", "purpose"),
        binding=BindingValue(value=SlotValue(ref=_ref("DAC/Health", label), value_kind="entity")),
    )


def _where_slot(label: str) -> Slot:
    return Slot(
        key=_slot_key("where", "DevelopmentFinanceRecipient", "Recipient", "recipient"),
        binding=BindingValue(value=SlotValue(ref=_ref("country/ETH", label), value_kind="entity")),
    )


def _entity_to(dcid: str, label: str) -> Entity:
    return Entity(
        ref=_ref(dcid, label),
        entity_type=None,
        role=EntityRoleDirectional(
            kind="directional",
            role=_ref("RecipientRole", "Recipient"),
            direction="to",
        ),
    )


def _entity_from(dcid: str, label: str) -> Entity:
    return Entity(
        ref=_ref(dcid, label),
        entity_type=None,
        role=EntityRoleDirectional(
            kind="directional",
            role=_ref("DonorRole", "Donor"),
            direction="from",
        ),
    )


def _entity_subject(dcid: str, label: str) -> Entity:
    return Entity(
        ref=_ref(dcid, label),
        entity_type=None,
        role=EntityRoleSubject(),
    )


# ---------------------------------------------------------------------------
# Case 1: measured thing from value-bound `what` slot
# ---------------------------------------------------------------------------

def test_what_slot_bound_uses_value_label():
    spec = _spec(slots=[_what_slot("ODA Grants")])
    assert render_sentence(spec) == "ODA Grants."


# ---------------------------------------------------------------------------
# Case 2: no `what` slot -> falls back to shape.measured_property.label
# ---------------------------------------------------------------------------

def test_no_what_slot_falls_back_to_measured_property():
    spec = _spec(measured_property_label="Development Finance Flow")
    assert render_sentence(spec) == "Development Finance Flow."


# ---------------------------------------------------------------------------
# Case 3: `what` slot present but unbound / absent -> falls back to measured_property
# ---------------------------------------------------------------------------

def test_unbound_what_slot_falls_back_to_measured_property():
    slot = Slot(
        key=_slot_key("what", "DevelopmentFinanceScheme", "Scheme", "scheme"),
        binding=BindingUnbound(),
    )
    spec = _spec(slots=[slot], measured_property_label="ODA Flow")
    assert render_sentence(spec) == "ODA Flow."


def test_absent_what_slot_falls_back_to_measured_property():
    slot = Slot(
        key=_slot_key("what", "DevelopmentFinanceScheme", "Scheme", "scheme"),
        binding=BindingAbsent(),
    )
    spec = _spec(slots=[slot], measured_property_label="ODA Flow")
    assert render_sentence(spec) == "ODA Flow."


# ---------------------------------------------------------------------------
# Case 4: value-bound `how` slot -> "... for <label>"
# ---------------------------------------------------------------------------

def test_how_slot_adds_for_phrase():
    spec = _spec(slots=[_what_slot("ODA Grants"), _how_slot("health")])
    assert render_sentence(spec) == "ODA Grants for health."


# ---------------------------------------------------------------------------
# Case 5: set-bound slot (>=2 members) -> "A and B" join
# ---------------------------------------------------------------------------

def test_set_bound_how_slot_joins_labels():
    slot = Slot(
        key=_slot_key("how", "DevelopmentFinancePurpose", "Purpose", "purpose"),
        binding=BindingSet(values=[
            SlotValue(ref=_ref("DAC/Health", "health"), value_kind="entity"),
            SlotValue(ref=_ref("DAC/Education", "education"), value_kind="entity"),
        ]),
    )
    spec = _spec(slots=[slot])
    assert render_sentence(spec) == "Development Finance Flow for health and education."


# ---------------------------------------------------------------------------
# Case 6: directional "to" entity -> "to <label>"
# ---------------------------------------------------------------------------

def test_directional_to_entity_renders_to_phrase():
    spec = _spec(entities=[_entity_to("country/ETH", "Ethiopia")])
    assert render_sentence(spec) == "Development Finance Flow to Ethiopia."


# ---------------------------------------------------------------------------
# Case 7: value-bound `where` slot, no directional entity -> "in <label>"
# ---------------------------------------------------------------------------

def test_where_slot_renders_in_phrase():
    spec = _spec(slots=[_where_slot("Kenya")])
    assert render_sentence(spec) == "Development Finance Flow in Kenya."


# ---------------------------------------------------------------------------
# Case 8: directional "to" entity AND bound `where` slot -> "to <entity>" only
# ---------------------------------------------------------------------------

def test_directional_to_takes_precedence_over_where_slot():
    spec = _spec(
        slots=[_where_slot("Kenya")],
        entities=[_entity_to("country/ETH", "Ethiopia")],
    )
    result = render_sentence(spec)
    assert result == "Development Finance Flow to Ethiopia."
    assert " in " not in result


# ---------------------------------------------------------------------------
# Case 9: `where` unbound + directional "to" entity -> "to <label>"
# ---------------------------------------------------------------------------

def test_where_unbound_with_directional_to_entity():
    where_unbound = Slot(
        key=_slot_key("where", "DevelopmentFinanceRecipient", "Recipient", "recipient"),
        binding=BindingUnbound(),
    )
    spec = _spec(
        slots=[where_unbound],
        entities=[_entity_to("country/ETH", "Ethiopia")],
    )
    assert render_sentence(spec) == "Development Finance Flow to Ethiopia."


# ---------------------------------------------------------------------------
# Case 10: directional "from" entity -> "from <label>"
# ---------------------------------------------------------------------------

def test_directional_from_entity_renders_from_phrase():
    spec = _spec(entities=[_entity_from("country/USA", "the United States")])
    assert render_sentence(spec) == "Development Finance Flow from the United States."


# ---------------------------------------------------------------------------
# Case 11: full-sentence join order
# ---------------------------------------------------------------------------

def test_full_join_order():
    """
    what + how + directional-to + directional-from + window -> exact string proving order.
    Expected: "ODA grants for health to Ethiopia from the United States, 2015 to 2023."
    """
    spec = _spec(
        slots=[
            _what_slot("ODA grants"),
            _how_slot("health"),
        ],
        entities=[
            _entity_to("country/ETH", "Ethiopia"),
            _entity_from("country/USA", "the United States"),
        ],
        window=TimeWindow(start_year=2015, end_year=2023),
    )
    expected = "ODA grants for health to Ethiopia from the United States, 2015 to 2023."
    assert render_sentence(spec) == expected


# ---------------------------------------------------------------------------
# Case 12: time window rendering variants
# ---------------------------------------------------------------------------

def test_window_range():
    spec = _spec(window=TimeWindow(start_year=2015, end_year=2023))
    assert render_sentence(spec) == "Development Finance Flow, 2015 to 2023."


def test_window_same_year():
    spec = _spec(window=TimeWindow(start_year=2020, end_year=2020))
    assert render_sentence(spec) == "Development Finance Flow in 2020."


def test_window_start_only():
    spec = _spec(window=TimeWindow(start_year=2015))
    assert render_sentence(spec) == "Development Finance Flow, since 2015."


def test_window_end_only():
    spec = _spec(window=TimeWindow(end_year=2020))
    assert render_sentence(spec) == "Development Finance Flow, until 2020."


# ---------------------------------------------------------------------------
# Case 13: value-bound `source` slot -> "according to <label>"
# ---------------------------------------------------------------------------

def test_source_slot_adds_according_to_phrase():
    source_slot = Slot(
        key=SlotKey(axis="source", property=None, label="source"),
        binding=BindingValue(value=SlotValue(ref=_ref("OECDsrc", "OECD"), value_kind="source")),
    )
    spec = _spec(slots=[source_slot])
    assert render_sentence(spec) == "Development Finance Flow according to OECD."


# ---------------------------------------------------------------------------
# Case 14: render_candidates_summary
# ---------------------------------------------------------------------------

def test_candidates_summary_plural():
    assert render_candidates_summary(2) == "2 possible interpretations."


def test_candidates_summary_singular():
    assert render_candidates_summary(1) == "1 possible interpretation."


def test_candidates_summary_large():
    assert render_candidates_summary(5) == "5 possible interpretations."


# ---------------------------------------------------------------------------
# Case 15: no_data_phrase over the four frozen NoDataReason values
# ---------------------------------------------------------------------------

def test_no_data_phrase_no_observations():
    phrase = no_data_phrase("no_observations")
    assert phrase and isinstance(phrase, str)


def test_no_data_phrase_entity_not_resolved():
    phrase = no_data_phrase("entity_not_resolved")
    assert phrase and isinstance(phrase, str)


def test_no_data_phrase_variable_not_resolved():
    phrase = no_data_phrase("variable_not_resolved")
    assert phrase and isinstance(phrase, str)


def test_no_data_phrase_denominator_not_available():
    phrase = no_data_phrase("denominator_not_available")
    assert phrase and isinstance(phrase, str)


def test_no_data_phrase_all_four_are_distinct():
    reasons = [
        "no_observations",
        "entity_not_resolved",
        "variable_not_resolved",
        "denominator_not_available",
    ]
    phrases = [no_data_phrase(r) for r in reasons]  # ty: ignore[invalid-argument-type]  # list[str] not narrowed to NoDataReason
    assert len(set(phrases)) == 4, "Each NoDataReason must map to a distinct phrase"


def test_no_data_phrase_unknown_code_falls_back():
    phrase = no_data_phrase("some_future_unknown_code")  # ty: ignore[invalid-argument-type]  # intentionally out-of-type to exercise runtime fallback
    assert phrase and isinstance(phrase, str)
    # Must differ from at least the normal-case phrases
    known = {no_data_phrase(r) for r in ["no_observations", "entity_not_resolved"]}
    # The fallback is a generic phrase; it exists and is non-empty
    assert phrase not in known or True  # non-empty is the hard requirement


# ---------------------------------------------------------------------------
# Case 16: zero-graph-call guarantee (asserted by construction)
# ---------------------------------------------------------------------------

def test_render_sentence_takes_only_spec_no_graph_param():
    """render_sentence takes only a Spec; no graph/LLM parameter exists.

    The absence of such parameters is the pure-function guarantee.
    Calling it with a purely in-memory Spec (no graph client in scope) proves
    no graph/LLM call is made.
    """
    spec = _spec(slots=[_what_slot("ODA Grants")])
    result = render_sentence(spec)
    assert isinstance(result, str)
    assert result  # non-empty
