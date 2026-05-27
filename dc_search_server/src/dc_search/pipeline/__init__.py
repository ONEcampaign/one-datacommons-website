"""Pipeline package — re-exports public and private surface for monkeypatch access.

Patches on this namespace are intercepted by app.py. Internal patches
(``_run_one_variable``, ``_ROUTE_TIMEOUT_S``, ``_resolve_place_dcids``) also
re-export from ``_run`` so tests reading them via the package namespace work.
"""

from ._availability import (
    _availability_sort_key,
    _resolve_union_availability,
    _resolve_union_availability_checked,
    _resolve_union_availability_with_ranges,
)
from ._run import (
    _ROUTE_TIMEOUT_S,
    MAX_VARIABLES,
    PipelineResult,
    PlaceResolution,
    _build_resolved_places,
    _drain,
    _resolve_place_dcids,
    _run_one_variable,
    _VariableResult,
    run_default,
    run_simple,
    stream_default,
    stream_simple,
)

__all__ = [
    "PipelineResult",
    "PlaceResolution",
    "run_default",
    "run_simple",
    "stream_default",
    "stream_simple",
    "_build_resolved_places",
    "_drain",
    "_resolve_union_availability",
    "_resolve_union_availability_checked",
    "_resolve_union_availability_with_ranges",
    "_VariableResult",
    "MAX_VARIABLES",
    "_ROUTE_TIMEOUT_S",
    "_run_one_variable",
    "_resolve_place_dcids",
    "_availability_sort_key",
]
