"""Tests for coverage.py: coverage_from_facets.

Full rewrite for the CoverageExact | CoverageBare contract.
"""
from __future__ import annotations

from qre.engine.coverage import coverage_from_facets
from qre.engine.extract import DateRequest
from qre.engine.graph import Facet
from qre.models import CoverageBare, CoverageBreadth, CoverageExact, TimeWindow
from tests.engine._harness import dimensions_of

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facet(
    obs_count: int = 10,
    earliest: str = "2010",
    latest: str = "2024",
    dates: list[str] | None = None,
) -> Facet:
    return Facet(
        earliest_date=earliest,
        latest_date=latest,
        obs_count=obs_count,
        dates=dates if dates is not None else [],
    )


# ---------------------------------------------------------------------------
# No-request path (full history)
# ---------------------------------------------------------------------------


def test_no_request_returns_coverage_exact() -> None:
    cov = coverage_from_facets([_facet(obs_count=402), _facet(obs_count=18)])
    assert isinstance(cov, CoverageExact)
    assert cov.kind == "exact"


def test_no_request_observation_count_is_sum_of_obs_count() -> None:
    facets = [_facet(obs_count=100), _facet(obs_count=200), _facet(obs_count=50)]
    cov = coverage_from_facets(facets)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 350


def test_no_request_window_is_none() -> None:
    cov = coverage_from_facets([_facet(obs_count=402)])
    assert isinstance(cov, CoverageExact)
    assert cov.window is None


def test_no_request_has_data_true_when_obs_present() -> None:
    cov = coverage_from_facets([_facet(obs_count=402)])
    assert cov.has_data is True


def test_no_request_dimensions_two() -> None:
    cov = coverage_from_facets([_facet(obs_count=402)])
    assert isinstance(cov, CoverageExact)
    assert len(dimensions_of(cov)) == 2


# ---------------------------------------------------------------------------
# Concrete window — full cover (all dates inside)
# ---------------------------------------------------------------------------


def test_concrete_window_full_cover_counts_all_dates() -> None:
    dates_in = ["2015", "2016", "2017", "2018", "2020"]
    facets = [_facet(obs_count=5, dates=dates_in)]
    req = DateRequest(window=TimeWindow(start_year=2010, end_year=2024), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 5


def test_concrete_window_full_cover_window_carried() -> None:
    window = TimeWindow(start_year=2010, end_year=2024)
    req = DateRequest(window=window, latest=False)
    cov = coverage_from_facets([_facet(obs_count=2, dates=["2015", "2016"])], date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.window == window


def test_concrete_window_full_cover_dimensions_two() -> None:
    req = DateRequest(window=TimeWindow(start_year=2010, end_year=2024), latest=False)
    cov = coverage_from_facets([_facet(obs_count=2, dates=["2015", "2016"])], date_request=req)
    assert len(dimensions_of(cov)) == 2


# ---------------------------------------------------------------------------
# Partial overlap
# ---------------------------------------------------------------------------


def test_partial_overlap_counts_only_in_window_dates() -> None:
    # 5 dates, 3 fall in 2014–2018
    dates = ["2012", "2014", "2016", "2018", "2022"]
    facets = [_facet(obs_count=5, dates=dates)]
    req = DateRequest(window=TimeWindow(start_year=2014, end_year=2018), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 3


def test_partial_overlap_dimensions_two() -> None:
    dates = ["2012", "2014", "2016"]
    facets = [_facet(obs_count=3, dates=dates)]
    req = DateRequest(window=TimeWindow(start_year=2014, end_year=2016), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert len(dimensions_of(cov)) == 2


# ---------------------------------------------------------------------------
# Exclude all
# ---------------------------------------------------------------------------


def test_exclude_all_window_observation_count_zero() -> None:
    dates = ["2000", "2001", "2002"]
    facets = [_facet(obs_count=3, dates=dates)]
    req = DateRequest(window=TimeWindow(start_year=2015, end_year=2020), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 0


def test_exclude_all_window_has_data_still_true() -> None:
    """has_data is request-independent: series exists even if none in window."""
    dates = ["2000", "2001"]
    facets = [_facet(obs_count=2, dates=dates)]
    req = DateRequest(window=TimeWindow(start_year=2015, end_year=2020), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert cov.has_data is True


def test_exclude_all_window_dimensions_two() -> None:
    facets = [_facet(obs_count=1, dates=["2000"])]
    req = DateRequest(window=TimeWindow(start_year=2015, end_year=2020), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert len(dimensions_of(cov)) == 2


# ---------------------------------------------------------------------------
# latest → TimeWindow(maxYear, maxYear) and in-that-year count
# ---------------------------------------------------------------------------


def test_latest_resolves_to_max_year_window() -> None:
    facets = [
        _facet(obs_count=5, latest="2021", dates=["2021", "2021", "2021"]),
        _facet(obs_count=5, latest="2023", dates=["2023", "2023"]),
    ]
    req = DateRequest(window=None, latest=True)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.window == TimeWindow(start_year=2023, end_year=2023)


def test_latest_counts_dates_in_max_year_only() -> None:
    facets = [
        _facet(obs_count=5, latest="2021", dates=["2021", "2021", "2021"]),
        _facet(obs_count=5, latest="2023", dates=["2023", "2023"]),
    ]
    req = DateRequest(window=None, latest=True)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 2


def test_latest_dimensions_two() -> None:
    facets = [_facet(obs_count=3, latest="2022", dates=["2022", "2020", "2021"])]
    req = DateRequest(window=None, latest=True)
    cov = coverage_from_facets(facets, date_request=req)
    assert len(dimensions_of(cov)) == 2


# ---------------------------------------------------------------------------
# No facets → CoverageBare
# ---------------------------------------------------------------------------


def test_no_facets_returns_coverage_bare() -> None:
    cov = coverage_from_facets([])
    assert isinstance(cov, CoverageBare)


def test_no_facets_has_data_false() -> None:
    cov = coverage_from_facets([])
    assert cov.has_data is False


def test_no_facets_has_data_override_true_returns_bare_with_has_data() -> None:
    """no facets + has_data_override=True → CoverageBare(has_data=True)."""
    cov = coverage_from_facets([], has_data_override=True)
    assert isinstance(cov, CoverageBare)
    assert cov.has_data is True


# ---------------------------------------------------------------------------
# Family labels: default sources/observations and dev-finance donors/years
# ---------------------------------------------------------------------------


def test_default_labels_are_sources_and_observations() -> None:
    cov = coverage_from_facets([_facet(obs_count=10)])
    assert isinstance(cov, CoverageExact)
    dim_labels = [d.label for d in dimensions_of(cov)]
    assert dim_labels[0] == "sources"
    assert dim_labels[1] == "observations"


def test_sources_dim_count_equals_facet_count() -> None:
    facets = [_facet(obs_count=100), _facet(obs_count=200)]
    cov = coverage_from_facets(facets)
    assert isinstance(cov, CoverageExact)
    sources_dim = next(d for d in dimensions_of(cov) if d.label == "sources")
    assert sources_dim.count == 2


def test_observations_dim_count_is_max_obs_count() -> None:
    facets = [_facet(obs_count=34), _facet(obs_count=402), _facet(obs_count=18)]
    cov = coverage_from_facets(facets)
    assert isinstance(cov, CoverageExact)
    obs_dim = next(d for d in dimensions_of(cov) if d.label == "observations")
    assert obs_dim.count == 402


def test_devfinance_labels_donors_and_years() -> None:
    cov = coverage_from_facets(
        [_facet(obs_count=34)],
        facet_label="donors",
        obs_label="years",
    )
    assert isinstance(cov, CoverageExact)
    dim_labels = {d.label for d in dimensions_of(cov)}
    assert "donors" in dim_labels
    assert "years" in dim_labels


def test_devfinance_donors_dim_count_equals_facet_count() -> None:
    facets = [_facet(obs_count=100), _facet(obs_count=200), _facet(obs_count=50)]
    cov = coverage_from_facets(facets, facet_label="donors", obs_label="years")
    assert isinstance(cov, CoverageExact)
    donors_dim = next(d for d in dimensions_of(cov) if d.label == "donors")
    assert donors_dim.count == 3


def test_devfinance_years_dim_count_is_max_obs_count() -> None:
    facets = [_facet(obs_count=34), _facet(obs_count=402), _facet(obs_count=18)]
    cov = coverage_from_facets(facets, facet_label="donors", obs_label="years")
    assert isinstance(cov, CoverageExact)
    years_dim = next(d for d in dimensions_of(cov) if d.label == "years")
    assert years_dim.count == 402


# ---------------------------------------------------------------------------
# Degrade to breadth: a partial probe (allow_exact=False) or a windowed request
# over facets that carry no per-observation dates.
# ---------------------------------------------------------------------------


def test_allow_exact_false_returns_breadth() -> None:
    cov = coverage_from_facets([_facet(obs_count=5, dates=["2020"])], allow_exact=False)
    assert isinstance(cov, CoverageBreadth)
    assert cov.has_data is True


def test_allow_exact_false_keeps_family_dimensions() -> None:
    cov = coverage_from_facets(
        [_facet(obs_count=5), _facet(obs_count=3)],
        facet_label="donors",
        obs_label="years",
        allow_exact=False,
    )
    assert isinstance(cov, CoverageBreadth)
    assert {d.label for d in cov.dimensions} == {"donors", "years"}


def test_windowed_without_dates_degrades_to_breadth() -> None:
    """A data-bearing facet with no per-observation dates cannot be counted in-window;
    emit breadth rather than a misleading observation_count=0."""
    facets = [_facet(obs_count=40, earliest="2010", latest="2020", dates=[])]
    req = DateRequest(window=TimeWindow(start_year=2012, end_year=2018), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageBreadth)
    assert cov.has_data is True
    assert cov.window == TimeWindow(start_year=2012, end_year=2018)


def test_latest_without_dates_degrades_to_breadth() -> None:
    facets = [_facet(obs_count=40, latest="2023", dates=[])]
    req = DateRequest(window=None, latest=True)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageBreadth)


def test_partial_dates_missing_degrades_to_breadth() -> None:
    """Mixed set: one facet has dates, another data-bearing facet does not -> not exact."""
    facets = [
        _facet(obs_count=2, dates=["2015", "2016"]),
        _facet(obs_count=9, dates=[]),
    ]
    req = DateRequest(window=TimeWindow(start_year=2014, end_year=2018), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageBreadth)


def test_windowed_with_dates_stays_exact() -> None:
    """Sanity: when every data-bearing facet has dates, a window still yields exact."""
    facets = [_facet(obs_count=3, dates=["2012", "2015", "2030"])]
    req = DateRequest(window=TimeWindow(start_year=2010, end_year=2020), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 2  # 2012, 2015 inside; 2030 outside


def test_zero_obs_facets_without_dates_stay_exact() -> None:
    """A facet with obs_count == 0 and no dates does not block exactness (no data to miss)."""
    facets = [_facet(obs_count=2, dates=["2015"]), _facet(obs_count=0, dates=[])]
    req = DateRequest(window=TimeWindow(start_year=2010, end_year=2020), latest=False)
    cov = coverage_from_facets(facets, date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.observation_count == 1


# ---------------------------------------------------------------------------
# has_data_override
# ---------------------------------------------------------------------------


def test_has_data_override_false_with_non_empty_facets() -> None:
    facets = [_facet(obs_count=100)]
    cov = coverage_from_facets(facets, has_data_override=False)
    assert cov.has_data is False


def test_has_data_override_none_uses_facets() -> None:
    cov_with = coverage_from_facets([_facet(obs_count=50)], has_data_override=None)
    cov_without = coverage_from_facets([], has_data_override=None)
    assert cov_with.has_data is True
    assert cov_without.has_data is False


# ---------------------------------------------------------------------------
# CoverageExact.window carries the resolved concrete window
# ---------------------------------------------------------------------------


def test_window_on_exact_is_concrete_time_window() -> None:
    """CoverageExact.window is a TimeWindow, not a DateRequest."""
    window = TimeWindow(start_year=2015, end_year=2020)
    req = DateRequest(window=window, latest=False)
    cov = coverage_from_facets([_facet(obs_count=5, dates=["2016", "2018"])], date_request=req)
    assert isinstance(cov, CoverageExact)
    assert cov.window == window
    assert isinstance(cov.window, TimeWindow)
