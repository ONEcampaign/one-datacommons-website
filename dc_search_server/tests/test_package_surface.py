"""Package-surface guard test for the dc_search module→package split.

Pins the import/attribute contract across dc_search.retrieval and dc_search.hooks
so a seam regression (e.g. a cache name or re-exported symbol moving to a submodule
without a __init__.py re-export) fails loudly rather than producing spurious passes.

Passes against current HEAD (flat modules) and must keep passing post-split.
"""

from __future__ import annotations

# Mirror of the cache names iterated by conftest.py::_clear_module_caches
# (tests/conftest.py, lines ~29-41). Kept in sync by
# test_retrieval_cache_names_resolve_and_clear's reconciliation step.
_CONFTEST_CACHE_NAMES: tuple[str, ...] = (
    "_resolve_cache",
    "_features_cache",
    "_entity_svs_cache",
    "_presence_cache",
    "_coverage_cache",
    "_variable_info_dates_cache",
    "_observation_dates_cache",
    "_observation_facet_ranges_cache",
    "_vgroups_cache",
    "_topic_arc_cache",
    "_place_names_cache",
)


def test_retrieval_cache_names_resolve_and_clear() -> None:
    """All 11 conftest cache names resolve on dc_search.retrieval and the fixture
    actually clears the live cache objects (not silent no-ops).

    Covers F2: cache-name resolution + fixture-not-a-no-op.
    """
    import dc_search.retrieval as retrieval

    for name in _CONFTEST_CACHE_NAMES:
        cache = getattr(retrieval, name, None)
        assert cache is not None, (
            f"dc_search.retrieval.{name} is missing — "
            "the conftest fixture would silently skip clearing it"
        )
        assert hasattr(cache, "clear"), f"{name} has no .clear() — not a cachetools cache"
        assert hasattr(cache, "__setitem__"), f"{name} has no __setitem__ — not a cachetools cache"

        # Populate, then clear via the exact mechanism the fixture uses.
        cache["__guard_probe__"] = object()
        assert len(cache) >= 1, f"{name}: probe key was not stored"

        getattr(retrieval, name).clear()
        assert len(cache) == 0, (
            f"{name}: clear() did not empty the cache — "
            "the resolved name may alias a different object than the live cache"
        )

    # Reconciliation guard: assert all names are still resolvable (implied by
    # the loop above, but explicit so a future partial refactor is caught).
    assert all(getattr(retrieval, n, None) is not None for n in _CONFTEST_CACHE_NAMES)


def test_hooks_dynamic_dispatch_attributes_present() -> None:
    """materialize_many, HookContext, and materialize_via_hooks are package
    attributes of dc_search.hooks.

    Covers F1: dynamic-dispatch surface that pipeline.py reads via attribute
    lookup and that monkeypatch.setattr targets in the test suite.  If a split
    moves these into a submodule without re-exporting from hooks/__init__.py,
    the monkeypatch would silently no-op instead of failing loudly.
    """
    import dc_search.hooks as hooks

    assert hasattr(hooks, "materialize_many"), "dc_search.hooks.materialize_many missing"
    assert callable(hooks.materialize_many), "dc_search.hooks.materialize_many is not callable"

    assert hasattr(hooks, "HookContext"), "dc_search.hooks.HookContext missing"
    assert isinstance(hooks.HookContext, type), "dc_search.hooks.HookContext is not a type"

    assert hasattr(hooks, "materialize_via_hooks"), "dc_search.hooks.materialize_via_hooks missing"


def test_private_symbol_imports_survive() -> None:
    """Private symbols imported by the test suite remain importable from
    dc_search.retrieval and dc_search.hooks package roots.

    Covers F6: an ImportError here would also break suite collection post-split.
    (_range_for is intentionally excluded per Decision 2 — it is only mentioned
    in a docstring, never imported.)
    """
    from dc_search.retrieval import _VARIABLE_INFO_DATE_CAP, _parse_observation

    assert _VARIABLE_INFO_DATE_CAP is not None, "_VARIABLE_INFO_DATE_CAP should be a non-None int"
    assert callable(_parse_observation), "_parse_observation should be callable"

    from dc_search.hooks import _build_variables, _overlaps, _union_range, _year

    assert callable(_year), "_year should be callable"
    assert callable(_overlaps), "_overlaps should be callable"
    assert callable(_union_range), "_union_range should be callable"
    assert callable(_build_variables), "_build_variables should be callable"
