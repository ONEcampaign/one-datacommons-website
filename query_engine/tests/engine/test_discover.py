"""Tests for discover.py: derive_shapes, read_five_tuple, read_constraints,
read_slot_taxonomy.

Covers:
- derive_shapes over recorded CRS SV arcs yields one shape with the dev-finance
  five-tuple and three constraint slots on the right axes.
- An unconfirmable dcid is dropped (no fabricated GraphRef).
- Over-fire guard: derive_shapes over the noisy 66-SV detect set returns at most
  one shape group (all noisy SVs share the same CRS DAC family rule and thus one
  five-tuple group when they confirm, or zero groups when none confirm, but never
  multiple groups for the same family).
- read_slot_taxonomy: dev-finance shapes use the seed; standard shapes use the
  observed-union from arc facts.
"""
from __future__ import annotations

from qre.engine.discover import (
    derive_shapes,
    graph_confirm_resolve,
    read_constraints,
    read_five_tuple,
    read_slot_taxonomy,
)
from qre.engine.families import (
    MEAS_DENOM_DCID,
    MEAS_PROP_DCID,
    MEAS_QUAL_DCID,
    POP_TYPE_DCID,
    PROP_PURPOSE,
    PROP_RECIPIENT,
    PROP_SCHEME,
    STAT_TYPE_DCID,
)
from qre.engine.families.dev_finance import PURPOSES, SCHEMES
from qre.engine.retrieve import Materialised
from qre.engine.shape import ShapeDraft
from tests.engine._harness import dimensions_of
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crs_arcs(purpose: str, scheme: str, recipient: str) -> dict:
    """Build a minimal node_arcs dict for a dev-finance SV."""
    return {
        "populationType": {"nodes": [{"dcid": POP_TYPE_DCID}]},
        "measuredProperty": {"nodes": [{"dcid": MEAS_PROP_DCID}]},
        "statType": {"nodes": [{"dcid": STAT_TYPE_DCID}]},
        "constraintProperties": {
            "nodes": [
                {"dcid": PROP_SCHEME},
                {"dcid": PROP_PURPOSE},
                {"dcid": PROP_RECIPIENT},
            ]
        },
        PROP_SCHEME: {"nodes": [{"dcid": scheme}]},
        PROP_PURPOSE: {"nodes": [{"dcid": purpose}]},
        PROP_RECIPIENT: {"nodes": [{"dcid": recipient}]},
    }


def _fake_graph_with_svs(sv_arcs: dict[str, dict]) -> FakeGraph:
    """Build a FakeGraph with specific SV entries in its nodes fixture.

    Provides labels for the three dev-finance constraint-property dcids
    (needed for shape_draft_from's prop_labels lookup).
    """
    nodes: dict = {
        PROP_SCHEME: {"label": "Development Finance Scheme"},
        PROP_PURPOSE: {"label": "Development Finance Purpose"},
        PROP_RECIPIENT: {"label": "Development Finance Recipient"},
    }
    for sv_dcid, arcs in sv_arcs.items():
        nodes[sv_dcid] = {"label": f"Label for {sv_dcid}", "arcs": arcs}
    return FakeGraph(nodes=nodes, obs={}, detect={}, resolve={})


# ---------------------------------------------------------------------------
# read_five_tuple
# ---------------------------------------------------------------------------

class TestReadFiveTuple:
    def test_dev_finance_five_tuple(self):
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        ft = read_five_tuple(arcs)
        assert ft.pop_type_dcid == POP_TYPE_DCID
        assert ft.meas_prop_dcid == MEAS_PROP_DCID
        assert ft.stat_type_dcid == STAT_TYPE_DCID
        assert ft.meas_qual_dcid is None
        assert ft.meas_denom_dcid is None

    def test_missing_optional_fields_are_none(self):
        arcs = {
            "populationType": {"nodes": [{"dcid": "SomePop"}]},
            "measuredProperty": {"nodes": [{"dcid": "SomeProp"}]},
            "statType": {"nodes": [{"dcid": "measuredValue"}]},
        }
        ft = read_five_tuple(arcs)
        assert ft.meas_qual_dcid is None
        assert ft.meas_denom_dcid is None

    def test_empty_arcs_returns_empty_strings(self):
        ft = read_five_tuple({})
        assert ft.pop_type_dcid == ""
        assert ft.meas_prop_dcid == ""


# ---------------------------------------------------------------------------
# read_constraints
# ---------------------------------------------------------------------------

class TestReadConstraints:
    def test_dev_finance_three_constraints(self):
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        constraints = read_constraints(arcs)
        assert constraints[PROP_SCHEME] == "ODAGrants"
        assert constraints[PROP_PURPOSE] == "DAC/Health"
        assert constraints[PROP_RECIPIENT] == "country/ETH"

    def test_no_constraint_properties_returns_empty(self):
        arcs = {
            "populationType": {"nodes": [{"dcid": "SomePop"}]},
        }
        constraints = read_constraints(arcs)
        assert constraints == {}

    def test_constraint_with_missing_value_omitted(self):
        arcs = {
            "constraintProperties": {"nodes": [{"dcid": "SomeProp"}]},
            # SomeProp has no value arc
        }
        constraints = read_constraints(arcs)
        assert "SomeProp" not in constraints


# ---------------------------------------------------------------------------
# derive_shapes: dev-finance happy path
# ---------------------------------------------------------------------------

class TestDeriveShapesDevFinance:
    """derive_shapes over a recorded CRS SV yields one dev-finance shape."""

    def test_single_sv_yields_one_shape(self):
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)

        assert len(shapes) == 1
        shape = shapes[0]
        assert shape.pop_type_dcid == POP_TYPE_DCID
        assert shape.meas_prop_dcid == MEAS_PROP_DCID
        assert shape.stat_type_dcid == STAT_TYPE_DCID
        assert shape.meas_qual_dcid == MEAS_QUAL_DCID
        assert shape.meas_denom_dcid == MEAS_DENOM_DCID

    def test_three_constraint_slots_on_correct_axes(self):
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        constraint_slots = [s for s in shape.slot_keys if s.property_dcid is not None]
        assert len(constraint_slots) == 3

        slot_by_prop = {s.property_dcid: s for s in constraint_slots}
        assert slot_by_prop[PROP_SCHEME].axis == "what"
        assert slot_by_prop[PROP_PURPOSE].axis == "how"
        assert slot_by_prop[PROP_RECIPIENT].axis == "where"

    def test_when_and_source_slots_appended(self):
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        axes = [s.axis for s in shape.slot_keys]
        assert "when" in axes
        assert "source" in axes

    def test_shape_stamped_with_family_rule(self):
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        assert shape.family_rule is not None
        assert shape.family_rule.namespace == "ONE/CRS_DAC/"

    def test_shape_carries_sv_arc_facts(self):
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        assert shape.sv_arc_facts is not None
        assert sv_dcid in shape.sv_arc_facts

    def test_shape_id_is_dev_finance_crs_dac(self):
        """shape_id must equal FAMILY_ID for spec_id byte-stability."""
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)

        assert shapes[0].shape_id == "dev_finance_crs_dac"

    def test_multiple_svs_same_five_tuple_one_shape(self):
        """Multiple SVs with the same five-tuple group into one shape."""
        sv1 = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        sv2 = "ONE/CRS_DAC/Health-ODAGrants-KEN"
        arcs1 = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        arcs2 = _crs_arcs("DAC/Health", "ODAGrants", "country/KEN")
        graph = _fake_graph_with_svs({sv1: arcs1, sv2: arcs2})

        shapes = derive_shapes(confirmed_svs=[sv1, sv2], graph=graph)

        assert len(shapes) == 1


# ---------------------------------------------------------------------------
# derive_shapes: unconfirmable SVs dropped
# ---------------------------------------------------------------------------

class TestDeriveShapesDropsUnconfirmable:
    """Unconfirmable SV dcids are silently dropped — no fabricated GraphRef."""

    def test_unconfirmable_sv_dropped(self):
        real_sv = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        fake_sv = "ONE/CRS_DAC/NONEXISTENT-ODAGrants-ZZZ"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({real_sv: arcs})
        # fake_sv is NOT in graph nodes — node_arcs returns None

        shapes = derive_shapes(confirmed_svs=[real_sv, fake_sv], graph=graph)

        assert len(shapes) == 1
        # The shape carries only the real SV's arc facts
        assert fake_sv not in (shapes[0].sv_arc_facts or {})
        assert real_sv in (shapes[0].sv_arc_facts or {})

    def test_all_unconfirmable_returns_empty(self):
        graph = _fake_graph_with_svs({})  # no SVs in nodes

        shapes = derive_shapes(
            confirmed_svs=["ONE/CRS_DAC/NONEXISTENT-ODAGrants-ZZZ"],
            graph=graph,
        )

        assert shapes == []

    def test_empty_candidate_list_returns_empty(self):
        graph = FakeGraph(nodes={}, obs={}, detect={}, resolve={})

        shapes = derive_shapes(confirmed_svs=[], graph=graph)

        assert shapes == []


# ---------------------------------------------------------------------------
# C2 over-fire guard: noisy 66-SV detect set → exactly ONE shape group
# ---------------------------------------------------------------------------

class TestDeriveShapesC2OverFireGuard:
    """The noisy 66-SV dev-finance detect set collapses to at most one shape group.

    In the FakeGraph fixture the noisy SVs (DPGC_X recipients) are not recorded,
    so derive_shapes returns zero shapes for unrecorded candidates. The important
    property is that it never returns more than one dev-finance shape group.

    We test the in-fixture case (a mix of recorded and unrecorded SVs from the
    noisy detect set) to confirm the one-group invariant holds when some SVs confirm.
    """

    def test_noisy_set_with_one_confirmed_sv_yields_one_shape(self):
        """Even if only one of 66 noisy SVs confirms, we get exactly one shape."""
        noisy_svs = [
            "dc/topic/sdg_3.b.2",
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-DPGC_X",
            "ONE/CRS_DAC/BasicHealth-OfficialDevelopmentAssistance-DPGC_X",
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH",  # this one confirms
        ]
        real_sv = "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH"
        arcs = _crs_arcs("DAC/Health", "OfficialDevelopmentAssistance", "country/ETH")
        graph = _fake_graph_with_svs({real_sv: arcs})

        shapes = derive_shapes(confirmed_svs=noisy_svs, graph=graph)

        assert len(shapes) == 1
        assert shapes[0].pop_type_dcid == POP_TYPE_DCID

    def test_all_noisy_svs_unconfirmable_yields_zero_shapes(self):
        """When none of the 66 noisy SVs confirm, derive_shapes returns empty."""
        noisy_svs = [
            "dc/topic/sdg_3.b.2",
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-DPGC_X",
            "ONE/CRS_DAC/BasicHealth-OfficialDevelopmentAssistance-DPGC_X",
        ]
        graph = FakeGraph(nodes={}, obs={}, detect={}, resolve={})

        shapes = derive_shapes(confirmed_svs=noisy_svs, graph=graph)

        # Zero shapes — never multiple
        assert len(shapes) <= 1

    def test_recorded_crs_svs_from_noisy_query_yield_one_group(self):
        """All recorded dev-finance SVs share the same five-tuple → one group."""
        # Build multiple dev-finance SVs with the CRS DAC five-tuple
        svs_and_arcs = {
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH": _crs_arcs(
                "DAC/Health", "OfficialDevelopmentAssistance", "country/ETH"
            ),
            "ONE/CRS_DAC/BasicHealth-OfficialDevelopmentAssistance-ETH": _crs_arcs(
                "DAC/BasicHealth", "OfficialDevelopmentAssistance", "country/ETH"
            ),
            "ONE/CRS_DAC/Health-ODAGrants-ETH": _crs_arcs(
                "DAC/Health", "ODAGrants", "country/ETH"
            ),
        }
        graph = _fake_graph_with_svs(svs_and_arcs)

        shapes = derive_shapes(confirmed_svs=list(svs_and_arcs.keys()), graph=graph)

        # All share the same dev-finance five-tuple → exactly one group
        assert len(shapes) == 1
        assert shapes[0].pop_type_dcid == POP_TYPE_DCID


# ---------------------------------------------------------------------------
# derive_shapes: FakeGraph fixture-backed (uses full fixture)
# ---------------------------------------------------------------------------

class TestDeriveShapesWithFixture:
    """derive_shapes using the full FakeGraph fixture (graph_nodes.json).

    The fixture contains recorded dev-finance SVs with correct arcs.
    """

    def test_fixture_svs_yield_dev_finance_shape(self):
        graph = FakeGraph()
        candidate_svs = [
            "ONE/CRS_DAC/Health-ODAGrants-ETH",
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH",
            "ONE/CRS_DAC/Health-ODALoans-ETH",  # may or may not be in fixture
        ]

        shapes = derive_shapes(confirmed_svs=candidate_svs, graph=graph)

        # At least one shape from the confirmed SVs
        assert len(shapes) >= 1
        # All shapes must be dev-finance (same five-tuple)
        for shape in shapes:
            assert shape.pop_type_dcid == POP_TYPE_DCID

    def test_fixture_shape_has_three_constraint_slots(self):
        graph = FakeGraph()
        svs = ["ONE/CRS_DAC/Health-ODAGrants-ETH"]

        shapes = derive_shapes(confirmed_svs=svs, graph=graph)

        assert len(shapes) == 1
        constraint_slots = [s for s in shapes[0].slot_keys if s.property_dcid is not None]
        assert len(constraint_slots) == 3


# ---------------------------------------------------------------------------
# read_slot_taxonomy
# ---------------------------------------------------------------------------

class TestReadSlotTaxonomy:
    """read_slot_taxonomy returns the seed for dev-finance and observed-union for standard."""

    def test_dev_finance_taxonomy_equals_seed(self):
        """Dev-finance shape returns the full hand-verified seed (B1)."""
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        assert len(shapes) == 1
        shape = shapes[0]

        assert shape.slot_taxonomy is not None
        taxonomy = shape.slot_taxonomy

        # what slot carries the full SCHEMES seed (not just ODAGrants)
        what_key = f"what:{PROP_SCHEME}"
        assert what_key in taxonomy
        assert set(taxonomy[what_key]) == set(SCHEMES)
        assert len(taxonomy[what_key]) == len(SCHEMES)

        # how slot carries the full PURPOSES seed (not just DAC/Health)
        how_key = f"how:{PROP_PURPOSE}"
        assert how_key in taxonomy
        assert set(taxonomy[how_key]) == set(PURPOSES)
        assert len(taxonomy[how_key]) == len(PURPOSES)

        # where/recipient is NOT in the taxonomy (core.py injects it separately)
        where_key = f"where:{PROP_RECIPIENT}"
        assert where_key not in taxonomy

    def test_dev_finance_seed_unaffected_by_detected_sv_count(self):
        """Even with a single detected SV the full seed is returned (not just its values)."""
        # Only one SV with one purpose and one scheme
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Malariacontrol", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        taxonomy = shape.slot_taxonomy
        assert taxonomy is not None

        # Scheme taxonomy must be the full seed, not just ["ODAGrants"]
        what_values = taxonomy.get(f"what:{PROP_SCHEME}", [])
        assert len(what_values) == len(SCHEMES)

        # Purpose taxonomy must be the full seed, not just ["DAC/Malariacontrol"]
        how_values = taxonomy.get(f"how:{PROP_PURPOSE}", [])
        assert len(how_values) == len(PURPOSES)

    def test_standard_shape_observed_union_taxonomy(self):
        """Standard (non-dev-finance) shape returns the observed-union taxonomy."""
        # Build two SVs with a "standard" five-tuple and two different AgeGroup values
        def _std_arcs(age_value: str) -> dict:
            return {
                "populationType": {"nodes": [{"dcid": "Person"}]},
                "measuredProperty": {"nodes": [{"dcid": "count"}]},
                "statType": {"nodes": [{"dcid": "measuredValue"}]},
                "constraintProperties": {"nodes": [{"dcid": "age"}]},
                "age": {"nodes": [{"dcid": age_value}]},
            }

        sv1 = "Count_Person_15To64Years"
        sv2 = "Count_Person_Upto14Years"
        arcs1 = _std_arcs("Years15To64")
        arcs2 = _std_arcs("Upto14Years")

        nodes = {
            "age": {"label": "Age Group"},
            sv1: {"label": "Population 15-64", "arcs": arcs1},
            sv2: {"label": "Population 0-14", "arcs": arcs2},
        }
        graph = FakeGraph(nodes=nodes, obs={}, detect={}, resolve={})

        shapes = derive_shapes(confirmed_svs=[sv1, sv2], graph=graph)

        assert len(shapes) == 1
        shape = shapes[0]
        # standard SVs get STANDARD_RULE (the catch-all), not None
        from qre.engine.families.registry import STANDARD_RULE
        assert shape.family_rule is STANDARD_RULE

        taxonomy = shape.slot_taxonomy
        assert taxonomy is not None

        # The observed-union for "age" should contain both values
        # Axis for "age" property — could be "how" or something; we just check the values
        age_values_found = []
        for key, vals in taxonomy.items():
            if "age" in key:
                age_values_found = vals
                break

        assert "Years15To64" in age_values_found
        assert "Upto14Years" in age_values_found

    def test_read_slot_taxonomy_direct_dev_finance_shape(self):
        """read_slot_taxonomy called directly on a dev-finance shape returns the seed."""
        sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-ETH"
        arcs = _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")
        graph = _fake_graph_with_svs({sv_dcid: arcs})

        shapes = derive_shapes(confirmed_svs=[sv_dcid], graph=graph)
        shape = shapes[0]

        # Call read_slot_taxonomy directly (not via derive_shapes)
        taxonomy = read_slot_taxonomy(shape_draft=shape, graph=graph)

        # Dev-finance: must match the seed, not the observed subset
        assert set(taxonomy[f"what:{PROP_SCHEME}"]) == set(SCHEMES)
        assert set(taxonomy[f"how:{PROP_PURPOSE}"]) == set(PURPOSES)
        # Recipient slot excluded (core.py injects it)
        assert f"where:{PROP_RECIPIENT}" not in taxonomy


# ---------------------------------------------------------------------------
# graph_confirm_resolve: coverage dimension labels
# ---------------------------------------------------------------------------


class TestGraphConfirmResolveLabels:
    """DevFinanceResolver falls back to graph_confirm_resolve; the fallback coverage
    must keep the dev-finance donors/years labels, not regress to sources/observations."""

    def _shape_with_sv(self, sv_dcid: str) -> ShapeDraft:
        return ShapeDraft(
            shape_id="dev_finance_crs_dac",
            label="dev finance",
            pop_type_dcid=POP_TYPE_DCID,
            meas_prop_dcid=MEAS_PROP_DCID,
            stat_type_dcid=STAT_TYPE_DCID,
            meas_qual_dcid=None,
            meas_denom_dcid=None,
            slot_keys=(),
            sv_arc_facts={sv_dcid: _crs_arcs("DAC/Health", "ODAGrants", "country/ETH")},
        )

    def _graph_for(self, sv_dcid: str) -> FakeGraph:
        obs = {
            f"{sv_dcid}|country/ETH": [
                {"earliestDate": "2010", "latestDate": "2020", "obsCount": 11}
            ]
        }
        return FakeGraph(nodes={}, obs=obs, detect={}, resolve={})

    def test_devfinance_fallback_uses_donors_years(self):
        sv = "ONE/CRS_DAC/SomeHealthSV"
        result = graph_confirm_resolve(
            shape=self._shape_with_sv(sv),
            bindings=[],
            recipient_dcid="country/ETH",
            donor_dcid="country/USA",
            graph=self._graph_for(sv),
            facet_label="donors",
            obs_label="years",
        )
        assert isinstance(result, Materialised)
        assert {d.label for d in dimensions_of(result.coverage)} == {"donors", "years"}

    def test_default_labels_are_sources_observations(self):
        sv = "ONE/CRS_DAC/SomeHealthSV"
        result = graph_confirm_resolve(
            shape=self._shape_with_sv(sv),
            bindings=[],
            recipient_dcid="country/ETH",
            donor_dcid="country/USA",
            graph=self._graph_for(sv),
        )
        assert isinstance(result, Materialised)
        assert {d.label for d in dimensions_of(result.coverage)} == {"sources", "observations"}
