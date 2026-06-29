"""End-to-end tests for the multi-variable conjunction pipeline.

Mix of approaches:
  - offline_resolve (shared fixtures): single-variable regression, cross-shape definite.
  - offline_resolve (per-test FakeLLM + FakeGraph): clamp, duplicate dedupe.
  - combine_regions directly: same-shape collapse, all mixed-outcome branches.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from unittest.mock import patch

import pytest

from qre.engine.assemble import build_spec, now_ms
from qre.engine.bind import SlotBindingDraft, _BindOutput
from qre.engine.conjoin import (
    CONJUNCTION_CROSS_SHAPE,
    CONJUNCTION_PART_AMBIGUOUS,
    CONJUNCTION_PART_NO_DATA,
    VARIABLES_CLAMPED,
    combine_regions,
)
from qre.engine.core import resolve_async
from qre.engine.errors import GraphInfraError
from qre.engine.extract import _EXTRACTION_SYSTEM_PROMPT, Extraction
from qre.engine.regions import RegionResult
from qre.models import (
    BindingValue,
    CoverageBare,
    DefiniteResponse,
    GraphRef,
    NoDataResponse,
    PipelineStep,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    StatVar,
)
from tests.engine._harness import PINNED_DATE, make_request, offline_resolve
from tests.fixtures import FakeGraph, FakeLLM

# ---------------------------------------------------------------------------
# Extraction key helper for per-test FakeLLM fixtures
# ---------------------------------------------------------------------------

_EXTRACT_SYS = _EXTRACTION_SYSTEM_PROMPT.replace("[[TODAY]]", PINNED_DATE.isoformat())


def _extract_key(query: str) -> str:
    digest = hashlib.sha1((_EXTRACT_SYS + "\x01" + query).encode()).hexdigest()
    return f"Extraction:{digest}"


def _resolve(request, graph, llm):
    """Run resolve_async with custom graph/llm, date pinned as in _harness."""
    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = PINNED_DATE
        mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
        return asyncio.run(resolve_async(request, graph=graph, llm=llm))


# ---------------------------------------------------------------------------
# Minimal builders for combine_regions tests
# ---------------------------------------------------------------------------


def _gr(dcid: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=dcid)


def _shape(pop: str, mp: str, mq: str | None = None) -> Shape:
    return Shape(
        shape_id=f"{pop}/{mp}/{mq or ''}",
        label="Test",
        population_type=_gr(pop),
        measured_property=_gr(mp),
        stat_type=_gr("measuredValue"),
        measurement_qualifier=_gr(mq) if mq else None,
        measurement_denominator=None,
        slot_keys=[
            SlotKey(axis="what", property=_gr("constraintProp"), label="constraintProp")
        ],
        member_count=1,
    )


def _value_slot(prop: str, val: str) -> Slot:
    return Slot(
        key=SlotKey(axis="what", property=_gr(prop), label=prop),
        binding=BindingValue(value=SlotValue(ref=_gr(val), value_kind="enum_value")),
    )


def _spec(shape: Shape, constraint_val: str, sv_dcid: str = "sv", variable_text: str | None = None):
    return build_spec(
        shape=shape,
        slots=[_value_slot("constraintProp", constraint_val)],
        stat_vars=[StatVar(ref=_gr(sv_dcid), shape_id=shape.shape_id, slot_values=[])],
        entities=[],
        coverage=CoverageBare(has_data=True),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        timing_by_step={},
        variable_text=variable_text,
    )


def _rr(
    variable_text: str,
    status: str,
    spec=None,
    *,
    earliest_index: int = 0,
    no_data_reason: str | None = None,
    extra_specs: tuple = (),
) -> RegionResult:
    if status == "definite" and spec is not None:
        specs: tuple = (spec,)
    elif status == "candidates" and spec is not None:
        specs = (spec,) + extra_specs
    else:
        specs = ()
    return RegionResult(
        variable_text=variable_text,
        status=status,  # type: ignore[arg-type]
        specs=specs,
        no_data_reason=no_data_reason or "variable_not_resolved",
        warnings=(),
        timing_by_step={},
        earliest_index=earliest_index,
    )


def _combine(regions):
    return combine_regions(
        regions,
        query="test query",
        variable_texts=[r.variable_text for r in regions],
        extra_warnings=[],
        start_ms=now_ms(),
        engine_build="test",
        include_sentence=False,
        max_candidates=10,
    )


_PERSON_SHAPE = _shape("Person", "count")
_ECON_SHAPE = _shape("EconomicActivity", "amount", mq="Nominal")


# ---------------------------------------------------------------------------
# Single-variable regression (shared fixtures, N=1 path unchanged)
# ---------------------------------------------------------------------------


def test_single_variable_regression():
    """N=1 path unchanged: no conjunction warnings, additional_interpretations stays None."""
    result = offline_resolve(make_request("total population India"))
    r = result.root
    assert r.status == "definite"
    assert isinstance(r, DefiniteResponse)
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_CROSS_SHAPE not in codes
    assert CONJUNCTION_PART_AMBIGUOUS not in codes
    assert CONJUNCTION_PART_NO_DATA not in codes
    assert r.additional_interpretations is None
    # The engine sets variable_text even on N=1 specs; what matters is
    # no conjunction back-pointer is signalled via additional_interpretations.
    assert r.interpretation.variable_text == "total population"


# ---------------------------------------------------------------------------
# Cross-shape both-definite (shared fixtures)
# ---------------------------------------------------------------------------


def test_cross_shape_both_definite():
    """Two definite cross-shape regions get a CROSS_SHAPE warning and populated extras."""
    result = offline_resolve(make_request("population and GDP in Brazil"))
    r = result.root
    assert r.status == "definite"
    assert isinstance(r, DefiniteResponse)

    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_CROSS_SHAPE in codes
    cross_w = next(w for w in r.diagnostics.warnings if w.code == CONJUNCTION_CROSS_SHAPE)
    assert cross_w.severity == "info"

    # Primary is population (earliest_index=0)
    assert r.interpretation.variable_text == "population"
    assert any(sv.ref.dcid == "Count_Person" for sv in r.interpretation.stat_vars)

    # additional_interpretations has GDP
    assert r.additional_interpretations is not None
    assert len(r.additional_interpretations) == 1
    assert r.additional_interpretations[0].variable_text == "GDP"
    assert any(
        sv.ref.dcid == "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
        for sv in r.additional_interpretations[0].stat_vars
    )


# ---------------------------------------------------------------------------
# Same-shape collapse (combine_regions direct)
# ---------------------------------------------------------------------------


def test_same_shape_collapse():
    """Two definite same-shape regions → single merged definite, no cross-shape warning."""
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "ValB", variable_text="var B")
    result = _combine([
        _rr("var A", "definite", spec_a, earliest_index=0),
        _rr("var B", "definite", spec_b, earliest_index=1),
    ])
    r = result.root
    assert r.status == "definite"
    assert isinstance(r, DefiniteResponse)
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_CROSS_SHAPE not in codes
    # Same-shape collapse → additional_interpretations is None (only one effective region)
    assert r.additional_interpretations is None
    # Merged slot is a BindingSet
    assert r.interpretation.slots[0].binding.kind == "set"


# ---------------------------------------------------------------------------
# Interim [] — primary definite but no other definite extras
# ---------------------------------------------------------------------------


def test_cross_shape_interim_empty():
    """Primary definite, only non-definite extras → additional_interpretations=[] (interim)."""
    spec_person = _spec(_PERSON_SHAPE, "any", variable_text="population")
    result = _combine([
        _rr("population", "definite", spec_person, earliest_index=0),
        _rr("malaria", "candidates", spec_person, earliest_index=1),
    ])
    r = result.root
    assert r.status == "definite"
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_CROSS_SHAPE in codes
    # No other definite → interim empty list
    assert r.additional_interpretations == []


# ---------------------------------------------------------------------------
# Primary-definite + ambiguous / no_data parts
# ---------------------------------------------------------------------------


def test_primary_definite_ambiguous_part():
    """Primary definite + candidates extra → CONJUNCTION_PART_AMBIGUOUS."""
    spec_person = _spec(_PERSON_SHAPE, "any", variable_text="population")
    spec_cand = _spec(_PERSON_SHAPE, "any", variable_text="ambiguous")
    result = _combine([
        _rr("population", "definite", spec_person, earliest_index=0),
        _rr("ambiguous measure", "candidates", spec_cand, earliest_index=1),
    ])
    r = result.root
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_PART_AMBIGUOUS in codes
    w = next(w for w in r.diagnostics.warnings if w.code == CONJUNCTION_PART_AMBIGUOUS)
    assert "ambiguous measure" in w.message


def test_primary_definite_no_data_part():
    """Primary definite + no_data extra → CONJUNCTION_PART_NO_DATA."""
    spec_person = _spec(_PERSON_SHAPE, "any", variable_text="population")
    result = _combine([
        _rr("population", "definite", spec_person, earliest_index=0),
        _rr("left-handedness", "no_data", earliest_index=1),
    ])
    r = result.root
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_PART_NO_DATA in codes
    w = next(w for w in r.diagnostics.warnings if w.code == CONJUNCTION_PART_NO_DATA)
    assert "left-handedness" in w.message


# ---------------------------------------------------------------------------
# Primary promotion — leading no_data / candidates
# ---------------------------------------------------------------------------


def test_primary_promotion():
    """First variable no_data, second variable definite → second becomes primary."""
    spec_pop = _spec(_ECON_SHAPE, "any", variable_text="GDP")
    result = _combine([
        _rr("left-handedness", "no_data", earliest_index=0),
        _rr("GDP", "definite", spec_pop, earliest_index=1),
    ])
    r = result.root
    assert r.status == "definite"
    assert isinstance(r, DefiniteResponse)
    # Promoted primary
    assert r.interpretation.variable_text == "GDP"
    # PART_NO_DATA for the leading no_data part
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_PART_NO_DATA in codes


# ---------------------------------------------------------------------------
# Primary candidates / no_data outcomes
# ---------------------------------------------------------------------------


def test_primary_candidates():
    """Primary is candidates → CandidatesResponse (contract bars additional_interpretations)."""
    spec_a = _spec(_PERSON_SHAPE, "ValA")
    spec_b = _spec(_PERSON_SHAPE, "ValB")
    # CandidateSet requires ≥2 specs; pass spec_b as extra_specs
    result = _combine([
        _rr("ambiguous A", "candidates", spec_a, earliest_index=0, extra_specs=(spec_b,)),
        _rr("left-handedness", "no_data", earliest_index=1),
    ])
    r = result.root
    assert r.status == "candidates"


def test_primary_no_data():
    """All parts no_data → NoDataResponse."""
    result = _combine([
        _rr("left-handedness", "no_data", earliest_index=0),
        _rr("flying saucers", "no_data", earliest_index=1),
    ])
    r = result.root
    assert r.status == "no_data"
    assert isinstance(r, NoDataResponse)
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_PART_NO_DATA in codes


# ---------------------------------------------------------------------------
# Clamp (per-test FakeLLM + FakeGraph via resolve_async)
# ---------------------------------------------------------------------------

_CLAMP_QUERY = "a and b and c and d and e and f and g in Brazil"


def test_clamp():
    """7 variables → clamped to 6; VARIABLES_CLAMPED emitted."""
    fake_llm = FakeLLM(responses={
        _extract_key(_CLAMP_QUERY): {
            "variables": ["a", "b", "c", "d", "e", "f", "g"],
            "entities": ["Brazil"],
            "dates": [],
        }
    })
    fake_graph = FakeGraph(
        detect={},  # empty → all variables no_data
        resolve={"Brazil": "country/BRA"},
    )
    result = _resolve(make_request(_CLAMP_QUERY), fake_graph, fake_llm)
    codes = [w.code for w in result.root.diagnostics.warnings]
    assert VARIABLES_CLAMPED in codes
    w = next(w for w in result.root.diagnostics.warnings if w.code == VARIABLES_CLAMPED)
    assert "7" in w.message and "6" in w.message


# ---------------------------------------------------------------------------
# Duplicate dedupe (per-test FakeLLM + FakeGraph)
# ---------------------------------------------------------------------------

_DEDUP_QUERY = "population and population in India"


def test_duplicate_dedupe():
    """Duplicate variable names dedupe to one region: no conjunction warnings."""
    fake_llm = FakeLLM(responses={
        _extract_key(_DEDUP_QUERY): {
            "variables": ["population", "population"],
            "entities": ["India"],
            "dates": [],
        }
    })
    # N=1 after dedup: detect_query = full original query
    fake_graph = FakeGraph(
        detect={_DEDUP_QUERY: {"svs": ["Count_Person"]}},
        resolve={"India": "country/IND"},
        # obs uses shared file (Count_Person|country/IND exists)
    )
    result = _resolve(make_request(_DEDUP_QUERY), fake_graph, fake_llm)
    r = result.root
    assert r.status == "definite"
    codes = [w.code for w in r.diagnostics.warnings]
    assert CONJUNCTION_CROSS_SHAPE not in codes
    assert r.additional_interpretations is None


# ---------------------------------------------------------------------------
# Directional roles with multi-variable queries
# ---------------------------------------------------------------------------

_MULTI_VAR_DIRECTIONAL_QUERY = "health ODA grants and health ODA loans from USA to Ethiopia"


class _DirectionalFakeLLM:
    """Minimal fake LLM for the multi-variable directional roles regression test.

    Extracts two dev-finance variables from the full query and provides a minimal
    scheme binding for each. The where/recipient slot is overwritten deterministically
    by the engine, so only the what/scheme slot needs to be covered here.
    """

    def generate_structured(self, *, prompt, system, schema):
        name = schema.__name__
        if name == "Extraction":
            return Extraction(
                variables=["health ODA grants", "health ODA loans"],
                entities=["USA", "Ethiopia"],
            )
        if name == "_BindOutput":
            scheme = "ODAGrants" if "health ODA grants" in prompt else "ODALoans"
            return _BindOutput(bindings=[
                SlotBindingDraft(
                    axis="what",
                    property_dcid="DevelopmentFinanceScheme",
                    kind="value",
                    value_dcids=[scheme],
                ),
            ])
        raise AssertionError(f"unexpected schema {name!r}")


def test_conjunction_directional_roles_use_full_query():
    """Each conjunction part detects directional roles from the full query.

    For a multi-variable dev-finance query "... from USA to Ethiopia", each part
    must receive the full query to detect directional prepositions. The bare variable
    "health ODA grants" alone carries no prepositions; without the full query, roles
    like "from USA" and "to Ethiopia" would be invisible.

    Observable signal: with pac=False the engine emits ENTITY_ROLE_DISABLED
    (SEAM_OFF_WARN_CODE) only when directional prepositions are detected. Even when
    obs is empty (each part returns no_data), region warnings are captured in
    RegionResult and propagated to the final response.
    """
    fake_graph = FakeGraph(
        detect={
            "health ODA grants": {"svs": ["ONE/CRS_DAC/Health-ODAGrants-ETH"]},
            "health ODA loans": {"svs": ["ONE/CRS_DAC/Health-ODAGrants-ETH"]},
        },
        resolve={"USA": "country/USA", "Ethiopia": "country/ETH"},
        obs={},  # no observations → parts are no_data; seam warnings still propagated
    )
    req = make_request(_MULTI_VAR_DIRECTIONAL_QUERY, pac=False)
    result = _resolve(req, fake_graph, _DirectionalFakeLLM())

    codes = [w.code for w in result.root.diagnostics.warnings]
    assert "ENTITY_ROLE_DISABLED" in codes, (
        "ENTITY_ROLE_DISABLED must be emitted when the full query's directional "
        "prepositions are detected on each conjunction leg; got codes: " + str(codes)
    )


# ---------------------------------------------------------------------------
# Infra error propagation — N≥2 path must not swallow EngineInfraError
# ---------------------------------------------------------------------------

_INFRA_QUERY = "population and GDP in Brazil"


def test_infra_error_reraises_in_multi_variable():
    """N≥2 path: GraphInfraError from one variable's leg must propagate, not collapse to no_data.

    Before the fix the gather-result loop mapped every exception to a no_data RegionResult,
    silently masking infrastructure failures. After the fix, EngineInfraError (and subclasses
    like GraphInfraError) are re-raised so the caller gets a proper 500-class failure.
    """
    fake_llm = FakeLLM(responses={
        _extract_key(_INFRA_QUERY): {
            "variables": ["population", "GDP"],
            "entities": ["Brazil"],
            "dates": [],
        }
    })
    # raise_on_call=True makes every graph method raise GraphInfraError, simulating
    # a transport/timeout failure on any leg of the concurrent resolve.
    fake_graph = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        _resolve(make_request(_INFRA_QUERY), fake_graph, fake_llm)
