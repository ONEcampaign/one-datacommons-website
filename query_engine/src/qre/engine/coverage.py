"""Coverage computation from observation facets.

Converts raw Facet data returned by the graph into a coverage object:
  - CoverageBare    when no facets exist (honest could-not-count),
  - CoverageExact   when a precise observation count is genuinely computable,
  - CoverageBreadth (the approximate lens) when an exact count would be misleading.

An exact count is NOT claimed when the caller's facets are only a partial probe
(allow_exact=False) or when a date window is requested over facets that carry no
per-observation dates. Honors a query-derived DateRequest: a concrete year window,
or a 'latest' request resolved to the most-recent year across facets.

Pure module: no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from qre.engine.graph import Facet
from qre.models import (
    BreadthDim,
    CoverageBare,
    CoverageBreadth,
    CoverageExact,
    TimeWindow,
    in_window,
)

if TYPE_CHECKING:
    from qre.engine.extract import DateRequest


def _resolve_window(date_request, facets: list[Facet]) -> TimeWindow | None:
    if date_request is None:
        return None
    if date_request.window is not None:
        return date_request.window            # concrete window, used as-is
    if date_request.latest:
        years: list[int] = []
        for f in facets:
            try:
                years.append(int(str(f.latest_date)[:4]))
            except (ValueError, TypeError):
                continue
        if years:
            m = max(years)
            return TimeWindow(start_year=m, end_year=m)   # latest → most-recent year
        return None                            # latest unresolvable → full count
    return None


def coverage_from_facets(
    facets: list[Facet],
    *,
    date_request: "DateRequest | None" = None,
    facet_label: str = "sources",
    obs_label: str = "observations",
    has_data_override: bool | None = None,
    allow_exact: bool = True,
) -> CoverageExact | CoverageBreadth | CoverageBare:
    """Build a coverage object from a list of observation facets.

    Returns CoverageBare when no facets are available (honest could-not-count).

    Returns CoverageBreadth (the approximate lens, no exact count) when an exact
    count would be misleading:
      - allow_exact is False: the facets are a partial probe that does not represent
        the full spec (e.g. the dev-finance unbound-scheme sentinel probes a single
        scheme), so an exact count would understate the real footprint.
      - a date window is requested but a data-bearing facet carries no per-observation
        dates, so the in-window count cannot be computed (counting it as zero would be
        wrong, not just imprecise).

    Otherwise returns CoverageExact with:
      - observation_count: sum of obs_count (no window) or count of in-window dates.
      - dimensions: [BreadthDim(facet_label, facet count), BreadthDim(obs_label, max obs_count)].
      - window: the resolved concrete TimeWindow (None when no date request or unresolvable).

    Args:
        facets: orderedFacets returned by EngineGraphClient.observation_facets.
        date_request: optional date signal from the query; None means full-history count.
        facet_label: label for the per-facet-count dimension (e.g. "donors", "sources").
        obs_label: label for the max-obs-count dimension (e.g. "years", "observations").
        has_data_override: When not None, forces has_data to this value.
        allow_exact: When False, never claim an exact count (the facets are a partial
            probe); emit the breadth lens instead.
    """
    if not facets:
        has_data = has_data_override if has_data_override is not None else False
        return CoverageBare(has_data=has_data)

    has_data = (has_data_override if has_data_override is not None
                else any(f.obs_count > 0 for f in facets))
    window = _resolve_window(date_request, facets)
    dimensions = [
        BreadthDim(label=facet_label, count=len(facets)),
        BreadthDim(label=obs_label, count=max((f.obs_count for f in facets), default=0)),
    ]

    # A windowed count is only computable when every data-bearing facet carries
    # per-observation dates; otherwise we cannot tell how many fall inside the window.
    windowed_without_dates = window is not None and any(
        f.obs_count > 0 and not f.dates for f in facets
    )
    if not allow_exact or windowed_without_dates:
        return CoverageBreadth(
            kind="breadth", has_data=has_data, dimensions=dimensions, window=window
        )

    if window is None:
        observation_count = sum(f.obs_count for f in facets)
    else:
        observation_count = sum(1 for f in facets for d in f.dates if in_window(d, window))

    return CoverageExact(
        kind="exact",
        has_data=has_data,
        observation_count=observation_count,
        dimensions=dimensions,
        window=window,
    )
