"""Coverage computation from observation facets.

Converts raw Facet data returned by the graph into a CoverageBreadth object.

Pure module: no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from qre.engine.graph import Facet
from qre.models import BreadthDim, CoverageBreadth


def coverage_from_facets(
    facets: list[Facet],
    *,
    has_data_override: bool | None = None,
) -> CoverageBreadth:
    """Build CoverageBreadth from a list of observation facets.

    For dev-finance the breadth dimensions are:
      - "donors" — count of facets
      - "years" — estimated from max obs_count across facets

    Args:
        facets: orderedFacets returned by EngineGraphClient.observation_facets.
        has_data_override: When not None, forces has_data to this value.

    Returns:
        CoverageBreadth with at least one dimension.
    """
    has_data: bool
    if has_data_override is not None:
        has_data = has_data_override
    else:
        has_data = any(f.obs_count > 0 for f in facets)

    donor_count = len(facets)
    year_count = max((f.obs_count for f in facets), default=0)

    dimensions = [
        BreadthDim(label="donors", count=donor_count),
        BreadthDim(label="years", count=year_count),
    ]

    return CoverageBreadth(
        kind="breadth",
        has_data=has_data,
        dimensions=dimensions,
    )
