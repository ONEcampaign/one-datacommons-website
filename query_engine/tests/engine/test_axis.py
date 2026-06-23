"""Tests for axis.py: AXIS_OVERRIDES and classify_axis.

Verifies override precedence, place-fraction threshold (>= 0.9 → "where"),
ISO-date detection (→ "when"), and fallback default ("how").
"""

from qre.engine.axis import AXIS_OVERRIDES, classify_axis

# ---------------------------------------------------------------------------
# AXIS_OVERRIDES literal
# ---------------------------------------------------------------------------


def test_axis_overrides_has_18_keys() -> None:
    assert len(AXIS_OVERRIDES) == 18


def test_load_bearing_recipient_is_where() -> None:
    assert AXIS_OVERRIDES["DevelopmentFinanceRecipient"] == "where"


def test_load_bearing_scheme_is_what() -> None:
    assert AXIS_OVERRIDES["DevelopmentFinanceScheme"] == "what"


def test_comparison_region_is_where() -> None:
    assert AXIS_OVERRIDES["comparisonRegion"] == "where"


def test_place_named_props_are_how() -> None:
    for prop in (
        "placeOfBirth",
        "placeOfResidenceClassification",
        "placeOfWork",
        "placeCategory",
        "locationType",
        "jurisdiction",
        "computerUsageLocation",
        "internetUsageLocation",
    ):
        assert AXIS_OVERRIDES[prop] == "how", f"{prop!r} should be 'how'"


def test_time_named_props_are_how() -> None:
    for prop in (
        "dateBuilt",
        "periodOfMilitaryService",
        "instrumentTerm",
        "maturity",
        "commuteTime",
        "accumulationPeriod",
        "extremesOverTime",
    ):
        assert AXIS_OVERRIDES[prop] == "how", f"{prop!r} should be 'how'"


# ---------------------------------------------------------------------------
# classify_axis — override path
# ---------------------------------------------------------------------------


def test_classify_override_wins_over_values() -> None:
    # DevelopmentFinanceRecipient → "where" regardless of observed values
    result = classify_axis("DevelopmentFinanceRecipient", ["DAC/africa", "africa"])
    assert result == "where"


def test_classify_scheme_override() -> None:
    result = classify_axis("DevelopmentFinanceScheme", ["ODAGrants", "ODALoans"])
    assert result == "what"


def test_classify_place_of_birth_override() -> None:
    # Even if 100% place values, the override forces "how"
    result = classify_axis("placeOfBirth", ["country/USA", "country/FRA"])
    assert result == "how"


# ---------------------------------------------------------------------------
# classify_axis — date heuristic
# ---------------------------------------------------------------------------


def test_classify_date_iso_year() -> None:
    assert classify_axis("someDateProp", ["2020", "2021"]) == "when"


def test_classify_date_iso_year_month() -> None:
    assert classify_axis("someDateProp", ["2020-01", "2020-06"]) == "when"


def test_classify_date_iso_full() -> None:
    assert classify_axis("someDateProp", ["2020-01-15"]) == "when"


def test_classify_date_mixed_does_not_trigger_on_non_date() -> None:
    # Values are not date-shaped → falls through to how
    assert classify_axis("someHowProp", ["Rural", "Urban"]) == "how"


# ---------------------------------------------------------------------------
# classify_axis — place-fraction auto-rule
# ---------------------------------------------------------------------------


def test_classify_all_place_values_is_where() -> None:
    values = ["country/USA", "country/ETH", "country/KEN", "country/FRA", "country/DEU",
              "country/GBR", "country/IND", "country/CHN", "country/BRA", "country/ZAF"]
    assert classify_axis("someGeoProperty", values) == "where"


def test_classify_exactly_90pct_place_is_where() -> None:
    # 9 of 10 are place-namespaced → fraction=0.9, should be "where"
    values = ["country/USA"] * 9 + ["SomeEnum"]
    assert classify_axis("nearlyGeo", values) == "where"


def test_classify_below_90pct_place_is_how() -> None:
    # 8 of 10 are place-namespaced → fraction=0.8, below threshold
    values = ["country/USA"] * 8 + ["SomeEnum", "AnotherEnum"]
    assert classify_axis("mixedGeo", values) == "how"


def test_classify_no_place_values_is_how() -> None:
    values = ["ODAGrants", "ODALoans", "PrivateFlows"]
    assert classify_axis("myProperty", values) == "how"


def test_classify_empty_values_is_how() -> None:
    assert classify_axis("anyProperty", []) == "how"


# ---------------------------------------------------------------------------
# classify_axis — DevelopmentFinancePurpose is NOT overridden (correct default)
# ---------------------------------------------------------------------------


def test_purpose_not_in_overrides() -> None:
    """DevelopmentFinancePurpose omitted deliberately — DAC/ is not a place namespace."""
    assert "DevelopmentFinancePurpose" not in AXIS_OVERRIDES


def test_classify_purpose_values_is_how() -> None:
    # DAC/ prefix is not a place namespace; auto-rule sends it to "how" correctly
    values = ["DAC/Health", "DAC/BasicHealth", "DAC/Agriculture"]
    assert classify_axis("DevelopmentFinancePurpose", values) == "how"
