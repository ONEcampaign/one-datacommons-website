"""Tests for dates_to_request and the DateRequest invariant."""
from __future__ import annotations

import pytest

from qre.engine.extract import DateRequest, ExtractedDate, dates_to_request
from qre.models import TimeWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _point(year: str) -> ExtractedDate:
    return ExtractedDate(kind="point", start=year, end=None)


def _range(start: str | None, end: str | None) -> ExtractedDate:
    return ExtractedDate(kind="range", start=start, end=end)


def _latest() -> ExtractedDate:
    return ExtractedDate(kind="latest", start=None, end=None)


# ---------------------------------------------------------------------------
# dates_to_request
# ---------------------------------------------------------------------------


def test_none_input_returns_none() -> None:
    assert dates_to_request([]) is None


def test_point_collapses_to_symmetric_window() -> None:
    req = dates_to_request([_point("2020")])
    assert req is not None
    assert req.window == TimeWindow(start_year=2020, end_year=2020)
    assert req.latest is False


def test_open_range_start_only() -> None:
    """Range with only a start bound → open end."""
    req = dates_to_request([_range("2010", None)])
    assert req is not None
    assert req.window == TimeWindow(start_year=2010, end_year=None)
    assert req.latest is False


def test_open_range_end_only() -> None:
    """Range with only an end bound → open start."""
    req = dates_to_request([_range(None, "2020")])
    assert req is not None
    assert req.window == TimeWindow(start_year=None, end_year=2020)
    assert req.latest is False


def test_multi_date_collapse_to_min_start_max_end() -> None:
    """Multiple dates collapse to [min start, max end]."""
    dates = [_point("2015"), _range("2010", "2018"), _point("2020")]
    req = dates_to_request(dates)
    assert req is not None
    assert req.window == TimeWindow(start_year=2010, end_year=2020)
    assert req.latest is False


def test_latest_only_returns_latest_request() -> None:
    req = dates_to_request([_latest()])
    assert req is not None
    assert req.window is None
    assert req.latest is True


def test_point_plus_latest_point_wins() -> None:
    """Any concrete bound takes priority; latest=False."""
    req = dates_to_request([_point("2018"), _latest()])
    assert req is not None
    assert req.window == TimeWindow(start_year=2018, end_year=2018)
    assert req.latest is False


def test_unparseable_string_is_skipped() -> None:
    """Unparseable start/end parse to None and are ignored."""
    dates = [ExtractedDate(kind="range", start="not-a-year", end="also-bad")]
    req = dates_to_request(dates)
    # Both bounds unparseable → no window, no latest → None
    assert req is None


def test_unparseable_mixed_with_valid_uses_valid() -> None:
    """Valid bound alongside unparseable → the valid bound is used."""
    dates = [_range("not-a-year", "2022"), _point("2018")]
    req = dates_to_request(dates)
    assert req is not None
    assert req.window == TimeWindow(start_year=2018, end_year=2022)


# ---------------------------------------------------------------------------
# DateRequest invariant
# ---------------------------------------------------------------------------


def test_date_request_post_init_rejects_window_and_latest() -> None:
    """DateRequest raises ValueError when both window and latest=True are set."""
    with pytest.raises(ValueError, match="window or latest"):
        DateRequest(window=TimeWindow(start_year=2010, end_year=2020), latest=True)
