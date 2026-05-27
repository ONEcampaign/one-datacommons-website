"""Tests for predicate.py classes and utilities."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dc_search.extraction import ExtractedDate
from dc_search.predicate import (
    AnswerCollection,
    AskClarification,
    Predicate,
    _apply_availability_filter,
    _build_crs_svg_dcid,
)


def _make_predicate(**kwargs) -> Predicate:
    defaults = {"population_type": None, "measured_property": None, "constraints": {}}
    defaults.update(kwargs)
    return Predicate(**defaults)


def _make_answer(predicate: Predicate | None = None, **kwargs: Any) -> AnswerCollection:
    p = predicate or _make_predicate()
    defaults: dict[str, Any] = {"predicate": p, "sv_set": ["dcid/SV1"], "confidence": "medium"}
    defaults.update(kwargs)
    return AnswerCollection(**defaults)


class TestAnswerCollectionInstantiation:
    def test_minimal(self):
        a = _make_answer()
        assert a.sv_set == ["dcid/SV1"]
        assert a.confidence == "medium"
        assert a.variable_label is None
        assert a.caveats == []
        assert a.svg_dcids == ()

    def test_with_variable_label(self):
        a = _make_answer(variable_label="life expectancy")
        assert a.variable_label == "life expectancy"

    def test_date_filter_defaults_to_none(self):
        a = _make_answer()
        assert a.date_filter is None

    def test_date_filter_accepts_extracted_date(self):
        window = ExtractedDate(kind="range", start="2015", end="2020")
        a = _make_answer(date_filter=window)
        assert a.date_filter is not None
        assert a.date_filter.start == "2015"
        assert a.date_filter.end == "2020"

    def test_date_filter_point(self):
        window = ExtractedDate(kind="point", start="2020", end=None)
        a = _make_answer(date_filter=window)
        assert a.date_filter is not None
        assert a.date_filter.kind == "point"

    def test_date_filtered_caveat_is_valid(self):
        # "date_filtered" must be accepted by the Caveat type.
        a = _make_answer(caveats=["date_filtered"])
        assert "date_filtered" in a.caveats

    def test_frozen(self):
        a = _make_answer()
        with pytest.raises(Exception):
            a.sv_set = ["other"]  # type: ignore[misc]

    def test_predicate_frozen(self):
        p = _make_predicate(population_type="Person")
        with pytest.raises(Exception):
            p.population_type = "Animal"  # type: ignore[misc]

    def test_ask_clarification(self):
        ask = AskClarification(reason="parse_error", message="Could not parse.")
        assert ask.reason == "parse_error"
        assert ask.proposed_clarifications == []


class TestCaveatLiteral:
    def test_date_filtered_is_valid_caveat(self):
        """'date_filtered' must be a valid Caveat literal."""
        # Pydantic validates the literal at model construction time.
        a = _make_answer(caveats=["date_filtered"])
        assert "date_filtered" in a.caveats

    def test_all_caveats_coexist(self):
        """Multiple caveats including date_filtered can appear together."""
        a = _make_answer(caveats=["date_filtered", "availability_filtered"])
        assert len(a.caveats) == 2

    def test_interpreted_place_as_recipient_round_trips(self):
        """'interpreted_place_as_recipient' must be wire-visible via model_dump()."""
        a = _make_answer(caveats=["interpreted_place_as_recipient"])
        dumped = a.model_dump()
        assert "interpreted_place_as_recipient" in dumped["caveats"]


class TestApplyAvailabilityFilter:
    def test_none_availability_returns_unchanged(self):
        sv_set = ["A", "B", "C"]
        assert _apply_availability_filter(sv_set, None) == sv_set

    def test_empty_availability_returns_unchanged(self):
        sv_set = ["A", "B"]
        assert _apply_availability_filter(sv_set, frozenset()) == sv_set

    def test_filters_to_intersection(self):
        sv_set = ["A", "B", "C"]
        result = _apply_availability_filter(sv_set, frozenset({"B", "C"}))
        assert result == ["B", "C"]

    def test_empty_intersection_falls_back(self):
        sv_set = ["A", "B"]
        result = _apply_availability_filter(sv_set, frozenset({"X", "Y"}))
        assert result == sv_set

    def test_preserves_order(self):
        sv_set = ["C", "A", "B"]
        result = _apply_availability_filter(sv_set, frozenset({"A", "C"}))
        assert result == ["C", "A"]


class TestBuildCrsSvgDcid:
    def test_all_bound(self):
        p = _make_predicate(
            constraints={
                "DevelopmentFinancePurpose": "DAC/Malariacontrol",
                "DevelopmentFinanceRecipient": "country/ZAF",
                "DevelopmentFinanceScheme": "ODAGrants",
            }
        )
        dcid = _build_crs_svg_dcid(p)
        assert dcid == (
            "ONE/g/DevelopmentFinance_"
            "DevelopmentFinancePurpose-DACMalariacontrol_"
            "DevelopmentFinanceRecipient-CountryZAF_"
            "DevelopmentFinanceScheme-ODAGrants"
        )

    def test_wildcard_slot(self):
        p = _make_predicate(
            constraints={
                "DevelopmentFinancePurpose": None,
                "DevelopmentFinanceRecipient": "country/KEN",
                "DevelopmentFinanceScheme": None,
            }
        )
        dcid = _build_crs_svg_dcid(p)
        assert "DevelopmentFinancePurpose_" in dcid
        assert "DevelopmentFinanceScheme" in dcid
        assert "CountryKEN" in dcid


class TestPredicateConstraintSets:
    def test_construction_with_constraint_sets(self):
        """Predicate with constraint_sets constructs and is frozen."""
        p = Predicate(
            population_type="DevelopmentFinance",
            measured_property=None,
            constraints={"DevelopmentFinanceRecipient": "DAC/Africa"},
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
            },
        )
        assert p.constraint_sets == {
            "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
        }
        # Frozen: mutation must raise.
        with pytest.raises(Exception):
            p.constraint_sets = {}  # type: ignore[misc]

    def test_wire_exclude_model_dump(self):
        """constraint_sets must NOT appear in model_dump() (wire-excluded, mirrors sv_set)."""
        p = Predicate(
            population_type="DevelopmentFinance",
            measured_property=None,
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN"})
            },
        )
        dumped = p.model_dump()
        assert "constraint_sets" not in dumped

    def test_wire_exclude_model_dump_json(self):
        """constraint_sets must NOT appear in model_dump_json()."""
        p = Predicate(
            population_type="DevelopmentFinance",
            measured_property=None,
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN"})
            },
        )
        parsed = json.loads(p.model_dump_json())
        assert "constraint_sets" not in parsed

    def test_default_empty_constraint_sets(self):
        """Default constraint_sets is {} — existing Predicate(...) calls unaffected."""
        p = _make_predicate(population_type="Person")
        assert p.constraint_sets == {}

    def test_back_compat_equality(self):
        """Predicate with no constraint_sets equals one explicitly set to {} (back-compat)."""
        p1 = _make_predicate(population_type="Person")
        p2 = Predicate(
            population_type="Person",
            measured_property=None,
            constraints={},
            constraint_sets={},
        )
        assert p1 == p2


