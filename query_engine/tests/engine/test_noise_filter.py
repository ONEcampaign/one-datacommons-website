"""Unit tests for the candidate-noise filter (filter_offtopic_shapes).

Exercises the filter with synthetic ShapeDraft instances so the rule can be
verified independently of the fixture data.  The filter runs as a post-pass
over standard ShapeDrafts; dev-finance shapes are never touched.

Key invariants:
  - "total population" keeps Count_Person/Count_Household/GrowthRate_Count_Person/
    Count_Person_PerArea and drops "Percent of Internet Users".
  - An empty variable (or variable with <2 content stems) keeps every shape.
  - A missing name arc keeps the shape (never drop on missing label).
  - Dev-finance shapes pass through untouched.
"""
from __future__ import annotations

from qre.engine.discover import filter_offtopic_shapes
from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.families.registry import STANDARD_RULE
from qre.engine.shape import ShapeDraft

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _name_arcs(name: str) -> dict:
    """Build a minimal arc dict carrying a `name` value node."""
    return {"name": {"nodes": [{"value": name, "provenanceId": "test"}]}}


def _no_name_arcs() -> dict:
    """Arc dict with no `name` entry (missing label case)."""
    return {}


def _std_shape(
    sv_dcid: str,
    label: str | None,
    *,
    shape_id: str = "test_shape",
) -> ShapeDraft:
    """Build a standard ShapeDraft whose representative SV has the given label."""
    arcs = _name_arcs(label) if label is not None else _no_name_arcs()
    return ShapeDraft(
        shape_id=shape_id,
        label=shape_id,
        pop_type_dcid="Person",
        meas_prop_dcid="count",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
        slot_keys=(),
        family_rule=STANDARD_RULE,
        sv_arc_facts={sv_dcid: arcs},
    )


def _df_shape(sv_dcid: str, label: str) -> ShapeDraft:
    """Build a dev-finance ShapeDraft (must never be filtered)."""
    return ShapeDraft(
        shape_id="dev_finance_crs_dac",
        label=label,
        pop_type_dcid="CRSDACAidActivity",
        meas_prop_dcid="amount",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
        slot_keys=(),
        family_rule=DEV_FINANCE_RULE,
        sv_arc_facts={sv_dcid: _name_arcs(label)},
    )


# ---------------------------------------------------------------------------
# "total population" baseline: keep population family, drop internet-user noise
# ---------------------------------------------------------------------------

class TestTotalPopulationFilter:
    """filter_offtopic_shapes("total population") keeps population shapes, drops noise."""

    _VARIABLE = "total population"

    def _shapes(self) -> list[ShapeDraft]:
        return [
            _std_shape("Count_Person", "Total population", shape_id="count_person"),
            _std_shape(
                "Count_Household",
                "Total number of households",
                shape_id="count_household",
            ),
            _std_shape(
                "GrowthRate_Count_Person",
                "Population growth rate",
                shape_id="growthrate_count_person",
            ),
            _std_shape(
                "Count_Person_PerArea",
                "Population Density",
                shape_id="count_person_perarea",
            ),
            _std_shape(
                "Count_Person_IsInternetUser_PerCapita",
                "Percent of Internet Users",
                shape_id="internet_user",
            ),
        ]

    def test_keeps_count_person(self):
        result = filter_offtopic_shapes(self._shapes(), variable=self._VARIABLE)
        ids = {s.shape_id for s in result}
        assert "count_person" in ids

    def test_keeps_count_household(self):
        # "Total number of households" matches "total population" on "total"
        result = filter_offtopic_shapes(self._shapes(), variable=self._VARIABLE)
        ids = {s.shape_id for s in result}
        assert "count_household" in ids

    def test_keeps_growthrate_count_person(self):
        # "Population growth rate" matches "total population" on "popu"
        result = filter_offtopic_shapes(self._shapes(), variable=self._VARIABLE)
        ids = {s.shape_id for s in result}
        assert "growthrate_count_person" in ids

    def test_keeps_count_person_perarea(self):
        # "Population Density" matches "total population" on "popu"
        result = filter_offtopic_shapes(self._shapes(), variable=self._VARIABLE)
        ids = {s.shape_id for s in result}
        assert "count_person_perarea" in ids

    def test_drops_internet_users(self):
        # "Percent of Internet Users" shares no content-word token with "total population"
        result = filter_offtopic_shapes(self._shapes(), variable=self._VARIABLE)
        ids = {s.shape_id for s in result}
        assert "internet_user" not in ids


# ---------------------------------------------------------------------------
# Empty / too-short variable: keep everything
# ---------------------------------------------------------------------------

class TestShortVariableKeepsAll:
    """Variables with fewer than two content stems bypass the filter entirely."""

    def _population_shapes(self) -> list[ShapeDraft]:
        return [
            _std_shape("Count_Person", "Total population", shape_id="cp"),
            _std_shape(
                "Count_Person_IsInternetUser_PerCapita",
                "Percent of Internet Users",
                shape_id="internet",
            ),
        ]

    def test_empty_variable_keeps_all(self):
        result = filter_offtopic_shapes(self._population_shapes(), variable="")
        assert len(result) == 2

    def test_single_stem_variable_keeps_all(self):
        # "GDP" has exactly one stem — too short, skip filtering
        result = filter_offtopic_shapes(self._population_shapes(), variable="GDP")
        assert len(result) == 2

    def test_single_stopword_variable_keeps_all(self):
        # "in" is a stopword → zero stems → skip
        result = filter_offtopic_shapes(self._population_shapes(), variable="in")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Missing name arc: keep the shape (R4)
# ---------------------------------------------------------------------------

class TestMissingLabelKeepsShape:
    """A shape whose representative SV has no name arc is always kept (R4)."""

    def test_missing_name_arc_is_kept(self):
        shapes = [
            _std_shape("SV_No_Name", None, shape_id="no_name"),
            _std_shape(
                "Count_Person_IsInternetUser_PerCapita",
                "Percent of Internet Users",
                shape_id="internet",
            ),
        ]
        result = filter_offtopic_shapes(shapes, variable="total population")
        ids = {s.shape_id for s in result}
        # Shape with no name arc must be kept regardless
        assert "no_name" in ids
        # Off-topic shape is still dropped
        assert "internet" not in ids


# ---------------------------------------------------------------------------
# Dev-finance shapes pass through untouched
# ---------------------------------------------------------------------------

class TestDevFinanceNeverFiltered:
    """Dev-finance shapes are never filtered, even when the label is off-topic."""

    def test_dev_finance_shape_kept_for_unrelated_variable(self):
        shapes = [
            _df_shape("ONE/CRS_DAC/HealthODA", "Health ODA"),
            _std_shape(
                "Count_Person_IsInternetUser_PerCapita",
                "Percent of Internet Users",
                shape_id="internet",
            ),
        ]
        result = filter_offtopic_shapes(shapes, variable="total population")
        ids = {s.shape_id for s in result}
        # Dev-finance shape must survive even though "Health ODA" doesn't match "total population"
        assert "dev_finance_crs_dac" in ids
        # Standard off-topic shape is still dropped
        assert "internet" not in ids

    def test_dev_finance_shape_kept_alongside_population_shapes(self):
        shapes = [
            _df_shape("ONE/CRS_DAC/PopODA", "Population policy and administrative management"),
            _std_shape("Count_Person", "Total population", shape_id="cp"),
        ]
        result = filter_offtopic_shapes(shapes, variable="total population")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# DCID fallback: shapes whose label is abstract but DCID is relevant
# ---------------------------------------------------------------------------

class TestDCIDFallback:
    """When the label doesn't match, the representative SV dcid is checked."""

    def test_birth_related_dcid_kept_for_birth_variable(self):
        # "Infant Mortality Rate" label doesn't match "birth rate"
        # but the DCID Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth does
        shapes = [
            _std_shape(
                "Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth",
                "Infant Mortality Rate",
                shape_id="infant_mort",
            ),
            _std_shape(
                "Count_Person_IsInternetUser_PerCapita",
                "Percent of Internet Users",
                shape_id="internet",
            ),
        ]
        result = filter_offtopic_shapes(shapes, variable="birth rate")
        ids = {s.shape_id for s in result}
        # Kept via DCID fallback ("birt" in CamelCase-split dcid)
        assert "infant_mort" in ids
        # Off-topic shape dropped
        assert "internet" not in ids
