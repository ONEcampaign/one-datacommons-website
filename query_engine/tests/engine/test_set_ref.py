"""Unit tests for the set_ref detector.

Covers:
- detect_set_ref: full-children match → GraphRef; partial subset → None;
  members spanning parents → None; member with no isPartOf arc → None;
  parent with no label → None.
- FakeGraph.child_dcids: returns correct children from reverse isPartOf scan.
- _RaiseOnAny.child_dcids: propagates GraphInfraError.
- build_slot set_ref kwarg: set_ref is threaded into BindingSet.
- _ground_answer (bind-time what/how set): set_ref detected on what/how;
  where-axis set stays None.
- collapse_same_shape set_ref_for: what/how merged set gets set_ref;
  set_ref_for=None keeps set_ref=None (backward compat); where-axis set not labelled.
"""
from __future__ import annotations

import pytest

from qre.engine.assemble import build_slot, build_spec
from qre.engine.bind import SlotBindingDraft
from qre.engine.conjoin import collapse_same_shape
from qre.engine.errors import GraphInfraError
from qre.engine.regions import RegionResult, detect_set_ref
from qre.engine.shape import SlotKeyDraft
from qre.models import (
    Axis,
    BindingSet,
    BindingValue,
    CoverageBare,
    GraphRef,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    StatVar,
)
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Fixture helpers — synthetic isPartOf hierarchy
# ---------------------------------------------------------------------------
#
# Synthetic hierarchy used throughout:
#   Agg (parent) ← {c1, c2, c3} (full children via ->isPartOf->Agg)
#
# Partial hierarchy:
#   AggPartial (parent) ← {p1, p2, p3, p4}  (four children; tests use only p1+p2)


def _agg_nodes() -> dict:
    """Minimal _nodes dict with a parent Agg and its three full children."""
    return {
        "Agg": {
            "label": "Aggregate Label",
            "type": "TestEnum",
            "arcs": {},
        },
        "c1": {
            "label": "Child One",
            "type": "TestEnum",
            "arcs": {
                "isPartOf": {"nodes": [{"dcid": "Agg"}]},
            },
        },
        "c2": {
            "label": "Child Two",
            "type": "TestEnum",
            "arcs": {
                "isPartOf": {"nodes": [{"dcid": "Agg"}]},
            },
        },
        "c3": {
            "label": "Child Three",
            "type": "TestEnum",
            "arcs": {
                "isPartOf": {"nodes": [{"dcid": "Agg"}]},
            },
        },
        # AggPartial has four children; tests that post only two of them exercise
        # the partial-subset guard.
        "AggPartial": {
            "label": "Partial Aggregate",
            "type": "TestEnum",
            "arcs": {},
        },
        "p1": {
            "label": "Partial Child One",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggPartial"}]}},
        },
        "p2": {
            "label": "Partial Child Two",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggPartial"}]}},
        },
        "p3": {
            "label": "Partial Child Three",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggPartial"}]}},
        },
        "p4": {
            "label": "Partial Child Four",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggPartial"}]}},
        },
        # AggNoLabel exists in the graph but has no name arc.
        "AggNoLabel": {
            "type": "TestEnum",
            "arcs": {},
        },
        "nl1": {
            "label": "No-label Child One",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggNoLabel"}]}},
        },
        "nl2": {
            "label": "No-label Child Two",
            "type": "TestEnum",
            "arcs": {"isPartOf": {"nodes": [{"dcid": "AggNoLabel"}]}},
        },
    }


def _fake_graph() -> FakeGraph:
    return FakeGraph(nodes=_agg_nodes(), obs={}, detect={}, resolve={})


# ---------------------------------------------------------------------------
# FakeGraph.child_dcids
# ---------------------------------------------------------------------------


def test_child_dcids_returns_full_children() -> None:
    g = _fake_graph()
    children = set(g.child_dcids("Agg"))
    assert children == {"c1", "c2", "c3"}


def test_child_dcids_absent_parent_returns_empty() -> None:
    g = _fake_graph()
    assert g.child_dcids("NonExistentParent") == []


def test_child_dcids_leaf_node_returns_empty() -> None:
    """A node that is itself a child (not a parent) has no <-isPartOf children."""
    g = _fake_graph()
    assert g.child_dcids("c1") == []


def test_child_dcids_raise_on_call() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.child_dcids("Agg")


# ---------------------------------------------------------------------------
# detect_set_ref
# ---------------------------------------------------------------------------


def test_detect_set_ref_full_children_returns_graphref() -> None:
    """All three children of Agg → GraphRef(dcid=Agg, label=<graph-read>)."""
    g = _fake_graph()
    result = detect_set_ref(value_dcids=["c1", "c2", "c3"], graph=g)
    assert result is not None
    assert result.dcid == "Agg"
    assert result.label == "Aggregate Label"


def test_detect_set_ref_partial_subset_returns_none() -> None:
    """Only two of four children of AggPartial — must not over-claim."""
    g = _fake_graph()
    result = detect_set_ref(value_dcids=["p1", "p2"], graph=g)
    assert result is None


def test_detect_set_ref_members_span_two_parents_returns_none() -> None:
    """c1 → Agg, p1 → AggPartial — different parents → None."""
    g = _fake_graph()
    result = detect_set_ref(value_dcids=["c1", "p1"], graph=g)
    assert result is None


def test_detect_set_ref_member_no_ispartof_arc_returns_none() -> None:
    """A member with no ->isPartOf arc (e.g. Agg itself) → None."""
    g = _fake_graph()
    # "Agg" has no isPartOf arc; combine with c1 which does → should return None
    result = detect_set_ref(value_dcids=["Agg", "c1"], graph=g)
    assert result is None


def test_detect_set_ref_parent_no_label_returns_none() -> None:
    """Parent exists but has no name → label is None → return None."""
    g = _fake_graph()
    # nl1 and nl2 are the full children of AggNoLabel, but AggNoLabel has no label.
    result = detect_set_ref(value_dcids=["nl1", "nl2"], graph=g)
    assert result is None


def test_detect_set_ref_single_member_returns_none() -> None:
    """Single member never qualifies for a set reference."""
    g = _fake_graph()
    result = detect_set_ref(value_dcids=["c1"], graph=g)
    assert result is None


def test_detect_set_ref_empty_returns_none() -> None:
    g = _fake_graph()
    result = detect_set_ref(value_dcids=[], graph=g)
    assert result is None


def test_detect_set_ref_member_with_multiple_parents_returns_none() -> None:
    """A member with two isPartOf entries → != 1 parent → None."""
    nodes = _agg_nodes()
    nodes["multi_parent"] = {
        "label": "Multi Parent Child",
        "type": "TestEnum",
        "arcs": {
            "isPartOf": {"nodes": [{"dcid": "Agg"}, {"dcid": "AggPartial"}]},
        },
    }
    g = FakeGraph(nodes=nodes, obs={}, detect={}, resolve={})
    result = detect_set_ref(value_dcids=["multi_parent", "c1"], graph=g)
    assert result is None


# ---------------------------------------------------------------------------
# build_slot: set_ref kwarg threads into BindingSet
# ---------------------------------------------------------------------------


def _gr(dcid: str, label: str | None = None) -> GraphRef:
    return GraphRef(dcid=dcid, label=label or dcid)


def _key_draft(axis: Axis = "what") -> SlotKeyDraft:
    return SlotKeyDraft(axis=axis, property_dcid="prop/A", label="prop A")


def test_build_slot_set_ref_none_by_default() -> None:
    """Omitting set_ref leaves BindingSet.set_ref=None (backward compat)."""
    draft = SlotBindingDraft(
        axis="what", property_dcid="prop/A", kind="set", value_dcids=["c1", "c2"]
    )
    slot = build_slot(
        _key_draft("what"),
        draft,
        [_gr("c1"), _gr("c2")],
        property_ref=_gr("prop/A"),
    )
    assert isinstance(slot.binding, BindingSet)
    assert slot.binding.set_ref is None


def test_build_slot_set_ref_propagates_to_binding_set() -> None:
    """A supplied set_ref appears in the returned BindingSet."""
    draft = SlotBindingDraft(
        axis="what", property_dcid="prop/A", kind="set", value_dcids=["c1", "c2"]
    )
    ref = _gr("Agg", "Aggregate Label")
    slot = build_slot(
        _key_draft("what"),
        draft,
        [_gr("c1"), _gr("c2")],
        property_ref=_gr("prop/A"),
        set_ref=ref,
    )
    assert isinstance(slot.binding, BindingSet)
    assert slot.binding.set_ref == ref


# ---------------------------------------------------------------------------
# collapse_same_shape: set_ref_for threading
# ---------------------------------------------------------------------------


def _shape(pop: str = "Pop", mp: str = "MP", axis: Axis = "what") -> Shape:
    return Shape(
        shape_id=f"{pop}/{mp}",
        label="Test",
        population_type=_gr(pop),
        measured_property=_gr(mp),
        stat_type=_gr("measuredValue"),
        measurement_qualifier=None,
        measurement_denominator=None,
        slot_keys=[SlotKey(axis=axis, property=_gr("prop/A"), label="prop A")],
        member_count=1,
    )


def _value_slot(axis: Axis, val: str) -> Slot:
    return Slot(
        key=SlotKey(axis=axis, property=_gr("prop/A"), label="prop A"),
        binding=BindingValue(value=SlotValue(ref=_gr(val), value_kind="enum_value")),
    )


def _entity_slot(axis: Axis, val: str) -> Slot:
    return Slot(
        key=SlotKey(axis=axis, property=_gr("prop/A"), label="prop A"),
        binding=BindingValue(value=SlotValue(ref=_gr(val), value_kind="entity")),
    )


def _region(shape: Shape, slot: Slot, sv: str = "sv/X", index: int = 0) -> RegionResult:
    spec = build_spec(
        shape=shape,
        slots=[slot],
        stat_vars=[StatVar(ref=_gr(sv), shape_id=shape.shape_id, slot_values=[])],
        entities=[],
        coverage=CoverageBare(has_data=True),
        pipeline_trace=[],
        variable_text="test",
    )
    return RegionResult(
        variable_text="test",
        status="definite",
        specs=(spec,),
        no_data_reason=None,
        warnings=(),
        timing_by_step={},
        earliest_index=index,
    )


def test_collapse_same_shape_what_axis_set_ref_for_called() -> None:
    """What-axis merge with set_ref_for → merged BindingSet.set_ref is populated."""
    sh = _shape(axis="what")
    r1 = _region(sh, _value_slot("what", "c1"), sv="sv/1", index=0)
    r2 = _region(sh, _value_slot("what", "c2"), sv="sv/2", index=1)

    parent_ref = GraphRef(dcid="Agg", label="Aggregate Label")
    captured: list[list[str]] = []

    def fake_set_ref_for(dcids: list[str]) -> GraphRef | None:
        captured.append(list(dcids))
        return parent_ref

    effective, residual = collapse_same_shape([r1, r2], set_ref_for=fake_set_ref_for)
    assert residual == []
    assert len(effective) == 1
    merged = effective[0].spec
    diff_slot = next(s for s in merged.slots if s.binding.kind == "set")
    assert isinstance(diff_slot.binding, BindingSet)
    assert diff_slot.binding.set_ref == parent_ref
    # Verify the correct dcids were passed to set_ref_for.
    assert len(captured) == 1
    assert set(captured[0]) == {"c1", "c2"}


def test_collapse_same_shape_set_ref_for_none_leaves_set_ref_none() -> None:
    """With set_ref_for=None (default), merged BindingSet.set_ref stays None."""
    sh = _shape(axis="what")
    r1 = _region(sh, _value_slot("what", "c1"), sv="sv/1", index=0)
    r2 = _region(sh, _value_slot("what", "c2"), sv="sv/2", index=1)

    effective, residual = collapse_same_shape([r1, r2])
    assert residual == []
    diff_slot = next(s for s in effective[0].spec.slots if s.binding.kind == "set")
    assert isinstance(diff_slot.binding, BindingSet)
    assert diff_slot.binding.set_ref is None


def test_collapse_same_shape_where_axis_not_labelled() -> None:
    """Where-axis merged set does not invoke set_ref_for (geographic containment guard)."""
    sh = _shape(axis="where")
    r1 = _region(sh, _entity_slot("where", "c1"), sv="sv/1", index=0)
    r2 = _region(sh, _entity_slot("where", "c2"), sv="sv/2", index=1)

    called: list[bool] = []

    def should_not_call(_dcids: list[str]) -> GraphRef | None:
        called.append(True)
        return GraphRef(dcid="WRONG", label="WRONG")

    effective, _ = collapse_same_shape([r1, r2], set_ref_for=should_not_call)
    assert called == [], "set_ref_for must not be called for where-axis slots"
    diff_slot = next(s for s in effective[0].spec.slots if s.binding.kind == "set")
    assert isinstance(diff_slot.binding, BindingSet)
    assert diff_slot.binding.set_ref is None


def test_collapse_same_shape_how_axis_set_ref_for_called() -> None:
    """How-axis is also a taxonomy axis — set_ref_for must be invoked."""
    sh = _shape(axis="how")
    slot1 = Slot(
        key=SlotKey(axis="how", property=_gr("prop/A"), label="prop A"),
        binding=BindingValue(value=SlotValue(ref=_gr("h1"), value_kind="enum_value")),
    )
    slot2 = Slot(
        key=SlotKey(axis="how", property=_gr("prop/A"), label="prop A"),
        binding=BindingValue(value=SlotValue(ref=_gr("h2"), value_kind="enum_value")),
    )
    r1 = _region(sh, slot1, sv="sv/1", index=0)
    r2 = _region(sh, slot2, sv="sv/2", index=1)

    how_ref = GraphRef(dcid="HowParent", label="How Parent")
    effective, _ = collapse_same_shape([r1, r2], set_ref_for=lambda _: how_ref)
    diff_slot = next(s for s in effective[0].spec.slots if s.binding.kind == "set")
    assert isinstance(diff_slot.binding, BindingSet)
    assert diff_slot.binding.set_ref == how_ref


def test_collapse_same_shape_set_ref_for_returns_none_propagates() -> None:
    """When set_ref_for returns None (no full-children match), set_ref stays None."""
    sh = _shape(axis="what")
    r1 = _region(sh, _value_slot("what", "c1"), sv="sv/1", index=0)
    r2 = _region(sh, _value_slot("what", "c2"), sv="sv/2", index=1)

    effective, _ = collapse_same_shape([r1, r2], set_ref_for=lambda _: None)
    diff_slot = next(s for s in effective[0].spec.slots if s.binding.kind == "set")
    assert isinstance(diff_slot.binding, BindingSet)
    assert diff_slot.binding.set_ref is None
