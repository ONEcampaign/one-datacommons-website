"""Tests for coverage.py: coverage_from_facets.

Verifies facet aggregation, override flags, and breadth dimension counts.
"""

from qre.engine.coverage import coverage_from_facets
from qre.engine.graph import Facet
from qre.models import CoverageBreadth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facet(obs_count: int = 10, earliest: str = "2010", latest: str = "2024") -> Facet:
    return Facet(earliest_date=earliest, latest_date=latest, obs_count=obs_count)




def test_coverage_has_data_with_facets() -> None:
    facets = [_facet(obs_count=402)]
    cov = coverage_from_facets(facets)
    assert isinstance(cov, CoverageBreadth)
    assert cov.has_data is True


def test_coverage_kind_is_breadth() -> None:
    cov = coverage_from_facets([_facet()])
    assert cov.kind == "breadth"


def test_coverage_dimensions_present() -> None:
    cov = coverage_from_facets([_facet()])
    dim_labels = {d.label for d in cov.dimensions}
    assert "donors" in dim_labels
    assert "years" in dim_labels


def test_coverage_donor_count_equals_facet_count() -> None:
    facets = [_facet(100), _facet(200), _facet(50)]
    cov = coverage_from_facets(facets)
    donors_dim = next(d for d in cov.dimensions if d.label == "donors")
    assert donors_dim.count == 3


def test_coverage_year_count_is_max_obs_count() -> None:
    """Year count is approximated as the maximum obs_count across facets."""
    facets = [_facet(34), _facet(402), _facet(18)]
    cov = coverage_from_facets(facets)
    years_dim = next(d for d in cov.dimensions if d.label == "years")
    assert years_dim.count == 402


def test_coverage_single_facet_correct_dims() -> None:
    facets = [_facet(obs_count=402, earliest="1991", latest="2024")]
    cov = coverage_from_facets(facets)
    donors_dim = next(d for d in cov.dimensions if d.label == "donors")
    years_dim = next(d for d in cov.dimensions if d.label == "years")
    assert donors_dim.count == 1
    assert years_dim.count == 402




def test_coverage_empty_facets_no_data() -> None:
    cov = coverage_from_facets([])
    assert cov.has_data is False


def test_coverage_empty_facets_zero_dims() -> None:
    cov = coverage_from_facets([])
    donors_dim = next(d for d in cov.dimensions if d.label == "donors")
    years_dim = next(d for d in cov.dimensions if d.label == "years")
    assert donors_dim.count == 0
    assert years_dim.count == 0


def test_coverage_facet_with_zero_obs_count() -> None:
    """A facet with obs_count=0 contributes to donor count but not has_data."""
    facets = [Facet(earliest_date=None, latest_date=None, obs_count=0)]
    cov = coverage_from_facets(facets)
    assert cov.has_data is False
    donors_dim = next(d for d in cov.dimensions if d.label == "donors")
    assert donors_dim.count == 1  # one facet was returned, even if empty


def test_has_data_override_true_with_empty_facets() -> None:
    """has_data_override=True forces has_data=True even when facets is empty."""
    cov = coverage_from_facets([], has_data_override=True)
    assert cov.has_data is True
    assert cov.kind == "breadth"


def test_has_data_override_false_with_non_empty_facets() -> None:
    """has_data_override=False forces has_data=False regardless of facets."""
    facets = [_facet(obs_count=100)]
    cov = coverage_from_facets(facets, has_data_override=False)
    assert cov.has_data is False


def test_has_data_override_none_uses_facets() -> None:
    """has_data_override=None (default) falls back to facet-based determination."""
    cov_with_data = coverage_from_facets([_facet(obs_count=50)], has_data_override=None)
    cov_without_data = coverage_from_facets([], has_data_override=None)
    assert cov_with_data.has_data is True
    assert cov_without_data.has_data is False




def test_coverage_is_pydantic_instance() -> None:
    cov = coverage_from_facets([_facet()])
    assert isinstance(cov, CoverageBreadth)
    # Pydantic model validates on construction — no exception means it's valid
    assert cov.model_dump()["kind"] == "breadth"


def test_coverage_window_is_none() -> None:
    """Coverage from facets does not set a time window (that is done in assembly)."""
    cov = coverage_from_facets([_facet()])
    assert cov.window is None
