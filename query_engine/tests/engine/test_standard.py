"""E2E engine tests: standard Data Commons family goldens.

Tests std-01 (definite Count_Person), nd-01 (no_observations), and nd-03
(no_observations from unrelated SVs) via the shared offline_resolve harness
backed by recorded fixture JSON.

Standard shapes skip the LLM bind step (standard resolver does not use
bindings; it probes observations directly from the resolved entity).  The
entity is the subject (no directional preposition), so recipient_dcid is set
from the single resolved entity.
"""
from __future__ import annotations

import asyncio

from qre.engine.core import resolve_async
from qre.engine.extract import Extraction
from qre.models import (
    BindingAbsent,
    DefiniteResponse,
    NoDataResponse,
    RawTextInput,
    ResolveRequest,
    ResolveResponse,
)
from tests.engine._harness import offline_resolve
from tests.fixtures import FakeGraph


def _req(query: str) -> ResolveRequest:
    return ResolveRequest(input=RawTextInput(query=query))


# ---------------------------------------------------------------------------
# std-01: total population India — definite, Count_Person, country/IND
# ---------------------------------------------------------------------------

class TestStd01TotalPopulationIndia:
    """std-01: 'total population India' → definite, shape Person/count/measuredValue,
    sv Count_Person, where-entity country/IND."""

    def test_status_is_definite(self):
        result = offline_resolve(_req("total population India"))
        assert result.root.status == "definite"

    def test_shape_is_person_count(self):
        result = offline_resolve(_req("total population India"))
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation
        assert spec.shape.population_type.dcid == "Person"
        assert spec.shape.measured_property.dcid == "count"
        assert spec.shape.stat_type.dcid == "measuredValue"

    def test_sv_is_count_person(self):
        result = offline_resolve(_req("total population India"))
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        sv_dcids = [sv.ref.dcid for sv in inner.interpretation.stat_vars]
        assert "Count_Person" in sv_dcids

    def test_entity_is_india(self):
        result = offline_resolve(_req("total population India"))
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        entity_dcids = [e.ref.dcid for e in inner.interpretation.entities]
        assert "country/IND" in entity_dcids

    def test_entity_role_is_subject(self):
        """Standard entities have subject role (no directional preposition)."""
        result = offline_resolve(_req("total population India"))
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        for entity in inner.interpretation.entities:
            if entity.ref.dcid == "country/IND":
                assert entity.role.kind == "subject"


# ---------------------------------------------------------------------------
# nd-01: teacher count in Nauru — no_data no_observations
# ---------------------------------------------------------------------------

class TestNd01TeacherCountNauru:
    """nd-01: 'teacher count in Nauru' → no_data, reason=no_observations.

    Count_Teacher confirms but has zero observations for country/NRU.
    """

    def test_status_is_no_data(self):
        result = offline_resolve(_req("teacher count in Nauru"))
        assert result.root.status == "no_data"

    def test_reason_is_no_observations(self):
        result = offline_resolve(_req("teacher count in Nauru"))
        inner = result.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "no_observations"


# ---------------------------------------------------------------------------
# nd-03: left-handedness rate in France — no_data no_observations
# ---------------------------------------------------------------------------

class TestNd03LeftHandednessFrance:
    """nd-03: 'left-handedness rate in France' → no_data, reason=variable_not_resolved.

    Prod detect returns ~60 health SVs as nearest matches (cosine ~0.42), but
    QRE_RELEVANCE_THRESHOLD=0.5 drops all of them.  Empty recall means no SV
    survives the threshold -> variable_not_resolved.  No actual left-handedness
    StatVar exists in Data Commons.
    """

    def test_status_is_no_data(self):
        result = offline_resolve(_req("left-handedness rate in France"))
        assert result.root.status == "no_data"

    def test_reason_is_variable_not_resolved(self):
        result = offline_resolve(_req("left-handedness rate in France"))
        inner = result.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "variable_not_resolved"


# ---------------------------------------------------------------------------
# Regression: single standard shape without measuredProperty must not crash
# ---------------------------------------------------------------------------


class _ExtractOnlyLLM:
    """Minimal LLM stub: returns a fixed Extraction; raises on any other schema."""

    def __init__(self, variable: str, entities: list[str]):
        self._extraction = Extraction(variables=[variable], entities=entities)

    def generate_structured(self, *, prompt, system, schema):
        if schema.__name__ == "Extraction":
            return self._extraction, None
        raise AssertionError(f"unexpected schema {schema.__name__!r} (standard shapes skip bind)")


def _graph_with_no_meas_prop_sv(sv_dcid: str, entity_name: str, entity_dcid: str) -> FakeGraph:
    """FakeGraph with a single SV that lacks the measuredProperty arc.

    The SV has a populationType arc but no measuredProperty — the case that
    previously caused a Pydantic ValidationError in build_shape_model when the
    single-shape definite path was taken.
    """
    nodes = {
        sv_dcid: {
            "label": f"Label for {sv_dcid}",
            "arcs": {
                "populationType": {"nodes": [{"dcid": "Person"}]},
                # measuredProperty intentionally absent
                "statType": {"nodes": [{"dcid": "measuredValue"}]},
            },
        },
        entity_dcid: {"label": entity_name, "type": "Country"},
    }
    detect = {
        # detect_svs is keyed by the raw query; cosine_scores absent → no threshold filtering
        "who aggregate thing in India": {"svs": [sv_dcid], "entities": [entity_name]},
    }
    resolve = {entity_name: entity_dcid}
    return FakeGraph(nodes=nodes, obs={}, detect=detect, resolve=resolve)


class TestStandardShapeWithoutMeasPropNoData:
    """Single standard shape lacking measuredProperty → no_data variable_not_resolved.

    The ill-formed shape is dropped before the definite/candidates decision,
    yielding a clean no-data outcome.
    """

    def _run(self) -> ResolveResponse:
        req = ResolveRequest(input=RawTextInput(query="who aggregate thing in India"))
        graph = _graph_with_no_meas_prop_sv(
            sv_dcid="WHO/AggregateNoMeasProp",
            entity_name="India",
            entity_dcid="country/IND",
        )
        llm = _ExtractOnlyLLM(variable="who aggregate thing", entities=["India"])
        return asyncio.run(resolve_async(req, graph=graph, llm=llm))

    def test_status_is_no_data(self):
        result = self._run()
        assert result.root.status == "no_data"

    def test_reason_is_variable_not_resolved(self):
        result = self._run()
        inner = result.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "variable_not_resolved"


# ---------------------------------------------------------------------------
# arc-derived slot_values for standard specs
# ---------------------------------------------------------------------------

class TestStd01SlotValuesArcDerived:
    """Assert arc-derived slot_values contract for std-01 (Count_Person).

    Count_Person carries no constraint properties (no constraintProperties arc),
    so standard_bindings_from_arcs returns [] for its shape's representative SV.
    The shape does carry constraint slot keys from the population-family observed
    union (economicSector, employment, residenceType, etc.) — all bind BindingAbsent
    because Count_Person doesn't realise any of those properties.

    Assertion target: A BindingAbsent slot's key.property appears on no StatVar
    in stat_vars. For Count_Person this means: all constraint slots are absent
    and slot_values is [].
    """

    def _result(self):
        return offline_resolve(_req("total population India"))

    def test_count_person_slot_values_is_empty(self):
        """Count_Person realises no constraint axes → slot_values must be []."""
        result = self._result()
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        for sv in inner.interpretation.stat_vars:
            if sv.ref.dcid == "Count_Person":
                assert sv.slot_values == [], (
                    f"Count_Person must carry no slot_values; got {sv.slot_values!r}"
                )

    def test_absent_slots_not_in_stat_var_slot_values(self):
        """contract.md:342 — BindingAbsent slot properties must not appear in any StatVar."""
        result = self._result()
        inner = result.root
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation

        # Collect properties that bind absent on the spec
        absent_props = {
            slot.key.property.dcid
            for slot in spec.slots
            if isinstance(slot.binding, BindingAbsent) and slot.key.property is not None
        }

        # Collect properties that appear in any StatVar's slot_values
        realised_props = {
            sv_val.key.property.dcid
            for sv in spec.stat_vars
            for sv_val in sv.slot_values
            if sv_val.key.property is not None
        }

        # No overlap allowed (invariant: absent ∩ realised = ∅)
        overlap = absent_props & realised_props
        assert not overlap, (
            f"Properties bound absent on spec also appear in stat_var slot_values: {overlap}"
        )
