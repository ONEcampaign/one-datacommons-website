"""Unit tests for interpretation.py models and round-trip serialization.

Covers:
- PlaceAlternative / ResolvedPlace / QueryInterpretation construction + freeze
- DateRange serializes as ``{"earliest":...,"latest":...}`` (not a positional array)
- ResolvedVariable round-trip (DateRange field)
- AnswerCollection.model_dump() has no ``sv_set`` key but has ``variables``
- AnswerCollection.model_dump_json() likewise
- answer_kind defaults "variables" and serializes correctly
"""

from __future__ import annotations

import json

from dc_search.extraction import ExtractedDate
from dc_search.interpretation import (
    PlaceAlternative,
    QueryInterpretation,
    ResolvedPlace,
)
from dc_search.predicate import (
    AnswerCollection,
    DateRange,
    Predicate,
    ResolvedVariable,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _predicate() -> Predicate:
    return Predicate(population_type="Person", measured_property="lifeExpectancy", constraints={})


def _answer(**kwargs) -> AnswerCollection:
    defaults = {
        "predicate": _predicate(),
        "sv_set": ["LifeExpectancy_Person"],
        "confidence": "medium",
    }
    defaults.update(kwargs)
    return AnswerCollection(**defaults)


# ---------------------------------------------------------------------------
# DateRange model
# ---------------------------------------------------------------------------


class TestDateRange:
    def test_round_trip(self):
        dr = DateRange(earliest="2010", latest="2023-06")
        assert dr.earliest == "2010"
        assert dr.latest == "2023-06"

    def test_both_none(self):
        dr = DateRange()
        assert dr.earliest is None
        assert dr.latest is None

    def test_frozen(self):
        import pytest

        dr = DateRange(earliest="2010", latest="2024")
        with pytest.raises(Exception):
            dr.earliest = "2000"  # type: ignore[misc]

    def test_serializes_as_object_not_array(self):
        """DateRange must serialize as {"earliest":..., "latest":...}, NOT a positional array."""
        dr = DateRange(earliest="2010", latest="2023")
        dumped = json.loads(dr.model_dump_json())
        assert isinstance(dumped, dict), "DateRange must serialize as a JSON object"
        assert "earliest" in dumped
        assert "latest" in dumped
        assert dumped["earliest"] == "2010"
        assert dumped["latest"] == "2023"

    def test_serializes_none_bounds(self):
        """None bounds stay null in JSON (not dropped)."""
        dr = DateRange(earliest="2010", latest=None)
        dumped = json.loads(dr.model_dump_json())
        assert dumped["earliest"] == "2010"
        assert dumped["latest"] is None


# ---------------------------------------------------------------------------
# ResolvedVariable model
# ---------------------------------------------------------------------------


class TestResolvedVariable:
    def test_minimal(self):
        rv = ResolvedVariable(dcid="LifeExpectancy_Person")
        assert rv.dcid == "LifeExpectancy_Person"
        assert rv.name is None
        assert rv.date_range is None
        assert rv.available_at_place is None

    def test_full_round_trip(self):
        dr = DateRange(earliest="2010", latest="2024")
        rv = ResolvedVariable(
            dcid="LifeExpectancy_Person",
            name="Life Expectancy",
            description="Life expectancy at birth.",
            unit="years",
            measured_property="lifeExpectancy",
            population_type="Person",
            stat_type="measuredValue",
            measurement_denominator=None,
            score=0.85,
            matched_sentence="life expectancy in Kenya",
            available_at_place=True,
            date_range=dr,
        )
        assert rv.name == "Life Expectancy"
        assert rv.score == 0.85
        assert rv.available_at_place is True
        assert rv.date_range is not None
        assert rv.date_range.earliest == "2010"

    def test_date_range_serializes_as_object(self):
        """date_range inside ResolvedVariable serializes as an object, not an array."""
        rv = ResolvedVariable(
            dcid="SV_A",
            date_range=DateRange(earliest="2012", latest="2022"),
        )
        dumped = json.loads(rv.model_dump_json())
        assert isinstance(dumped["date_range"], dict), (
            "date_range must be a JSON object, not an array"
        )
        assert dumped["date_range"]["earliest"] == "2012"
        assert dumped["date_range"]["latest"] == "2022"

    def test_frozen(self):
        import pytest

        rv = ResolvedVariable(dcid="SV_A")
        with pytest.raises(Exception):
            rv.dcid = "SV_B"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AnswerCollection: sv_set excluded, variables present in JSON
# ---------------------------------------------------------------------------


class TestAnswerCollectionSerialization:
    def test_model_dump_has_no_sv_set(self):
        """model_dump() must NOT include the ``sv_set`` key (it is exclude=True)."""
        a = _answer(sv_set=["SV_A", "SV_B"])
        dumped = a.model_dump()
        assert "sv_set" not in dumped, "sv_set must be excluded from model_dump()"

    def test_model_dump_has_variables_key(self):
        """model_dump() must include ``variables`` (even when empty)."""
        a = _answer()
        dumped = a.model_dump()
        assert "variables" in dumped

    def test_model_dump_json_has_no_sv_set(self):
        a = _answer(sv_set=["SV_A"])
        raw = a.model_dump_json()
        parsed = json.loads(raw)
        assert "sv_set" not in parsed

    def test_model_dump_json_has_variables(self):
        a = _answer()
        raw = a.model_dump_json()
        parsed = json.loads(raw)
        assert "variables" in parsed

    def test_variables_carry_resolved_variable_objects(self):
        """When variables are populated they serialize as ResolvedVariable objects."""
        rv = ResolvedVariable(dcid="SV_A", name="Var A")
        a = _answer(variables=[rv])
        dumped = a.model_dump()
        assert len(dumped["variables"]) == 1
        assert dumped["variables"][0]["dcid"] == "SV_A"
        assert dumped["variables"][0]["name"] == "Var A"

    def test_sv_set_still_readable_on_model(self):
        """sv_set is excluded from JSON but still accessible on the model itself."""
        a = _answer(sv_set=["SV_X", "SV_Y"])
        assert a.sv_set == ["SV_X", "SV_Y"]

    def test_answer_kind_default_is_variables(self):
        a = _answer()
        assert a.answer_kind == "variables"

    def test_answer_kind_serializes(self):
        a = _answer()
        parsed = json.loads(a.model_dump_json())
        assert parsed["answer_kind"] == "variables"

    def test_answer_kind_topic_serializes(self):
        a = _answer(answer_kind="topic")
        parsed = json.loads(a.model_dump_json())
        assert parsed["answer_kind"] == "topic"

    def test_topic_name_and_description_roundtrip(self):
        a = _answer(
            answer_kind="topic",
            topic_name="Health",
            topic_description="Health-related indicators.",
        )
        parsed = json.loads(a.model_dump_json())
        assert parsed["topic_name"] == "Health"
        assert parsed["topic_description"] == "Health-related indicators."


# ---------------------------------------------------------------------------
# PlaceAlternative model
# ---------------------------------------------------------------------------


class TestPlaceAlternative:
    def test_minimal(self):
        pa = PlaceAlternative(dcid="country/KEN")
        assert pa.dcid == "country/KEN"
        assert pa.name is None
        assert pa.type is None

    def test_full(self):
        pa = PlaceAlternative(dcid="country/KEN", name="Kenya", type="Country")
        assert pa.name == "Kenya"
        assert pa.type == "Country"

    def test_frozen(self):
        import pytest

        pa = PlaceAlternative(dcid="country/KEN")
        with pytest.raises(Exception):
            pa.dcid = "country/UGA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ResolvedPlace model
# ---------------------------------------------------------------------------


class TestResolvedPlace:
    def test_minimal(self):
        rp = ResolvedPlace(input_name="Kenya")
        assert rp.input_name == "Kenya"
        assert rp.dcid is None
        assert rp.name is None
        assert rp.type is None
        assert rp.alternatives == []

    def test_with_alternatives(self):
        alt = PlaceAlternative(dcid="nuts/FI", name="Finland", type="EurostatNUTS1")
        rp = ResolvedPlace(
            input_name="Finland",
            dcid="country/FIN",
            name="Finland",
            type="Country",
            alternatives=[alt],
        )
        assert len(rp.alternatives) == 1
        assert rp.alternatives[0].dcid == "nuts/FI"

    def test_frozen(self):
        import pytest

        rp = ResolvedPlace(input_name="Kenya")
        with pytest.raises(Exception):
            rp.input_name = "Uganda"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QueryInterpretation model
# ---------------------------------------------------------------------------


class TestQueryInterpretation:
    def test_defaults_empty(self):
        qi = QueryInterpretation()
        assert qi.variables == []
        assert qi.places == []
        assert qi.dates == []

    def test_full_construction(self):
        rp = ResolvedPlace(input_name="Kenya", dcid="country/KEN")
        d = ExtractedDate(kind="range", start="2010", end="2020")
        qi = QueryInterpretation(
            variables=["life expectancy"],
            places=[rp],
            dates=[d],
        )
        assert qi.variables == ["life expectancy"]
        assert qi.places[0].dcid == "country/KEN"
        assert qi.dates[0].start == "2010"

    def test_frozen(self):
        import pytest

        qi = QueryInterpretation()
        with pytest.raises(Exception):
            qi.variables = ["oops"]  # type: ignore[misc]

    def test_serializes_places_inline(self):
        rp = ResolvedPlace(input_name="Kenya", dcid="country/KEN", name="Kenya", type="Country")
        qi = QueryInterpretation(variables=["v"], places=[rp], dates=[])
        parsed = json.loads(qi.model_dump_json())
        assert parsed["places"][0]["dcid"] == "country/KEN"
        assert parsed["places"][0]["name"] == "Kenya"
