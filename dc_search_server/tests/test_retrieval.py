"""Tests for retrieval.py — LRU caches, syntax-fix branch, and eviction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import cachetools
import pytest

import dc_search.retrieval as retrieval

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear all module-level caches before each test."""
    retrieval._resolve_cache.clear()
    retrieval._features_cache.clear()
    retrieval._entity_svs_cache.clear()
    retrieval._presence_cache.clear()
    retrieval._coverage_cache.clear()
    retrieval._variable_info_dates_cache.clear()
    retrieval._observation_dates_cache.clear()
    retrieval._observation_facet_ranges_cache.clear()
    retrieval._place_names_cache.clear()
    retrieval._vgroups_cache.clear()
    retrieval._topic_arc_cache.clear()
    retrieval._resolve_indicator_cache_lru.clear()
    retrieval._stat_var_features_cache_lru.clear()
    retrieval._variables_for_entity_cache_lru.clear()
    retrieval._child_vars_of_groups_cache_lru.clear()
    retrieval._expand_topic_cache_lru.clear()
    retrieval._topic_metadata_batch_cache_lru.clear()
    yield


@pytest.fixture
def mock_dc_client():
    """Return a MagicMock DataCommonsClient, patched into retrieval.get_client."""
    client = MagicMock()
    with patch("dc_search.retrieval.get_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# Cache type assertions — all 6 module-level dict caches must be LRUCache
# ---------------------------------------------------------------------------


def test_resolve_cache_is_lru():
    assert isinstance(retrieval._resolve_cache, cachetools.LRUCache)


def test_features_cache_is_lru():
    assert isinstance(retrieval._features_cache, cachetools.LRUCache)


def test_entity_svs_cache_is_lru():
    assert isinstance(retrieval._entity_svs_cache, cachetools.LRUCache)


def test_presence_cache_is_lru():
    assert isinstance(retrieval._presence_cache, cachetools.LRUCache)


def test_vgroups_cache_is_lru():
    assert isinstance(retrieval._vgroups_cache, cachetools.LRUCache)


def test_topic_arc_cache_is_lru():
    assert isinstance(retrieval._topic_arc_cache, cachetools.LRUCache)


# Also verify the @cache-replacement caches are LRUCache
def test_resolve_indicator_cache_lru_is_lru():
    assert isinstance(retrieval._resolve_indicator_cache_lru, cachetools.LRUCache)


def test_stat_var_features_cache_lru_is_lru():
    assert isinstance(retrieval._stat_var_features_cache_lru, cachetools.LRUCache)


def test_expand_topic_cache_lru_is_lru():
    assert isinstance(retrieval._expand_topic_cache_lru, cachetools.LRUCache)


def test_topic_metadata_batch_cache_lru_is_lru():
    assert isinstance(retrieval._topic_metadata_batch_cache_lru, cachetools.LRUCache)


# ---------------------------------------------------------------------------
# Cache maxsize checks
# ---------------------------------------------------------------------------


def test_cache_maxsizes():
    assert retrieval._resolve_cache.maxsize == 2048
    assert retrieval._features_cache.maxsize == 4096
    assert retrieval._entity_svs_cache.maxsize == 512
    assert retrieval._presence_cache.maxsize == 1024
    assert retrieval._vgroups_cache.maxsize == 1024
    assert retrieval._topic_arc_cache.maxsize == 2048


# ---------------------------------------------------------------------------
# Syntax-fix branch: TypeError and ValueError in score parsing
# ---------------------------------------------------------------------------


def test_resolve_indicator_handles_type_error_in_score(mock_dc_client):
    """The fixed ``except (TypeError, ValueError):`` catches a bad score."""
    mock_dc_client.resolve.fetch.return_value.to_dict.return_value = {
        "entities": [
            {
                "node": "life expectancy",
                "candidates": [
                    {
                        "dcid": "LifeExpectancy_Person",
                        "typeOf": ["StatisticalVariable"],
                        "metadata": {"score": "not-a-float"},  # triggers ValueError
                    }
                ],
            }
        ]
    }
    with patch("dc_search.config.load_config") as mock_cfg:
        mock_cfg.return_value.resolve_target = "base_and_custom"
        candidates = retrieval.resolve_indicator(query="life expectancy")

    assert len(candidates) == 1
    assert candidates[0].score is None  # score fell back to None
    assert candidates[0].dcid == "LifeExpectancy_Person"


def test_resolve_indicator_handles_none_score(mock_dc_client):
    """A None score_raw does not attempt float() and returns score=None."""
    mock_dc_client.resolve.fetch.return_value.to_dict.return_value = {
        "entities": [
            {
                "node": "population",
                "candidates": [
                    {
                        "dcid": "Count_Person",
                        "typeOf": ["StatisticalVariable"],
                        "metadata": {"score": None},
                    }
                ],
            }
        ]
    }
    with patch("dc_search.config.load_config") as mock_cfg:
        mock_cfg.return_value.resolve_target = "base_and_custom"
        candidates = retrieval.resolve_indicator(query="population")

    assert len(candidates) == 1
    assert candidates[0].score is None


def test_resolve_indicator_parses_valid_score(mock_dc_client):
    """A valid numeric score string is parsed to float."""
    mock_dc_client.resolve.fetch.return_value.to_dict.return_value = {
        "entities": [
            {
                "node": "gdp",
                "candidates": [
                    {
                        "dcid": "Amount_EconomicActivity_GDPByNAICS",
                        "typeOf": ["StatisticalVariable"],
                        "metadata": {"score": "0.95"},
                    }
                ],
            }
        ]
    }
    with patch("dc_search.config.load_config") as mock_cfg:
        mock_cfg.return_value.resolve_target = "base_and_custom"
        candidates = retrieval.resolve_indicator(query="gdp")

    assert candidates[0].score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Caching behaviour: second call returns cached result without extra HTTP calls
# ---------------------------------------------------------------------------


def test_resolve_indicator_caches_result(mock_dc_client):
    """Second call with same args returns cached result (no extra API call)."""
    response = {
        "entities": [
            {
                "node": "unemployment",
                "candidates": [
                    {
                        "dcid": "UnemploymentRate_Person",
                        "typeOf": ["StatisticalVariable"],
                        "metadata": {"score": "0.8"},
                    }
                ],
            }
        ]
    }
    mock_dc_client.resolve.fetch.return_value.to_dict.return_value = response
    with patch("dc_search.config.load_config") as mock_cfg:
        mock_cfg.return_value.resolve_target = "base_and_custom"
        first = retrieval.resolve_indicator(query="unemployment")
        second = retrieval.resolve_indicator(query="unemployment")

    assert first == second
    # API should only have been called once (second call hits cache)
    assert mock_dc_client.resolve.fetch.call_count == 1


def test_stat_var_features_caches_result(mock_dc_client):
    """stat_var_features returns cached result on second call."""
    mock_dc_client.node.fetch.return_value.to_dict.return_value = {
        "data": {
            "Count_Person": {
                "arcs": {
                    "name": {"nodes": [{"value": "Population"}]},
                    "populationType": {"nodes": [{"dcid": "Person"}]},
                    "measuredProperty": {"nodes": [{"dcid": "count"}]},
                    "constraintProperties": {"nodes": []},
                }
            }
        }
    }
    first = retrieval.stat_var_features(sv_dcid="Count_Person")
    second = retrieval.stat_var_features(sv_dcid="Count_Person")

    assert first is second
    assert first.name == "Population"


def test_resolve_places_batch_caches(mock_dc_client):
    """resolve_places_batch populates _resolve_cache; second call skips HTTP."""
    mock_dc_client.resolve.fetch_dcids_by_name.return_value.to_dict.return_value = {
        "entities": [
            {"node": "Kenya", "candidates": [{"dcid": "country/KEN"}]},
        ]
    }
    result1 = retrieval.resolve_places_batch(names=("Kenya",))
    result2 = retrieval.resolve_places_batch(names=("Kenya",))

    assert result1 == result2
    assert mock_dc_client.resolve.fetch_dcids_by_name.call_count == 1


# ---------------------------------------------------------------------------
# LRU eviction at maxsize
# ---------------------------------------------------------------------------


def test_lru_eviction_on_resolve_cache():
    """Writing maxsize+1 entries to _resolve_cache evicts the oldest."""
    small_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=3)
    small_cache["a"] = ("cand_a",)
    small_cache["b"] = ("cand_b",)
    small_cache["c"] = ("cand_c",)
    # Access "a" to make it recently used
    _ = small_cache["a"]
    # Add "d" — should evict "b" (least recently used)
    small_cache["d"] = ("cand_d",)
    assert "b" not in small_cache
    assert "a" in small_cache
    assert "c" in small_cache
    assert "d" in small_cache


def test_module_level_lru_eviction():
    """Writing maxsize+1 entries to _resolve_cache evicts at the module level."""
    # Use a tiny cache to verify eviction without 2048 entries
    original_cache = retrieval._resolve_cache
    try:
        retrieval._resolve_cache = cachetools.LRUCache(maxsize=2)
        retrieval._resolve_cache["place1"] = (retrieval.PlaceCandidate(dcid="dcid1"),)
        retrieval._resolve_cache["place2"] = (retrieval.PlaceCandidate(dcid="dcid2"),)
        _ = retrieval._resolve_cache["place1"]  # access place1 to make it recent
        retrieval._resolve_cache["place3"] = (retrieval.PlaceCandidate(dcid="dcid3"),)
        # "place2" should be evicted (least recently used)
        assert "place2" not in retrieval._resolve_cache
        assert "place1" in retrieval._resolve_cache
        assert "place3" in retrieval._resolve_cache
    finally:
        retrieval._resolve_cache = original_cache


# ---------------------------------------------------------------------------
# topic_metadata_batch — public surface check
# ---------------------------------------------------------------------------


def test_topic_metadata_batch_returns_defaults_on_empty_dcids():
    """Empty dcids input returns empty dict without API call."""
    result = retrieval.topic_metadata_batch(dcids=())
    assert result == {}


def test_topic_metadata_batch_caches(mock_dc_client):
    """Second call with same dcids returns cached result."""
    mock_dc_client.node.fetch.return_value.to_dict.return_value = {
        "data": {
            "dc/topic/Health": {
                "arcs": {
                    "name": {"nodes": [{"value": "Health"}]},
                    "description": {"nodes": []},
                }
            }
        }
    }
    dcids = ("dc/topic/Health",)
    first = retrieval.topic_metadata_batch(dcids=dcids)
    second = retrieval.topic_metadata_batch(dcids=dcids)
    assert first == second
    assert mock_dc_client.node.fetch.call_count == 1


# ---------------------------------------------------------------------------
# Import smoke test — module imports cleanly
# ---------------------------------------------------------------------------


def test_module_import():
    """Importing dc_search.retrieval raises no errors."""
    import dc_search.retrieval as r

    assert r is not None


# ---------------------------------------------------------------------------
# place_names_batch — parse, cache, fail-open
# ---------------------------------------------------------------------------


def _make_place_node_response(dcid_to_name_type: dict) -> dict:
    """Build a minimal v2/node response for place_names_batch."""
    data: dict = {}
    for dcid, (name, type_of) in dcid_to_name_type.items():
        arcs: dict = {}
        if name is not None:
            arcs["name"] = {"nodes": [{"value": name}]}
        if type_of is not None:
            arcs["typeOf"] = {"nodes": [{"dcid": type_of}]}
        data[dcid] = {"arcs": arcs}
    return {"data": data}


def test_place_names_batch_empty_returns_empty():
    """Empty dcids input returns empty dict without API call."""
    result = retrieval.place_names_batch(dcids=())
    assert result == {}


def test_place_names_batch_parses_name_and_typeof(mock_dc_client):
    """place_names_batch parses name + typeOf from a mocked client.node.fetch."""
    mock_dc_client.node.fetch.return_value.to_dict.return_value = _make_place_node_response(
        {
            "country/KEN": ("Kenya", "Country"),
            "country/UGA": ("Uganda", "Country"),
        }
    )
    result = retrieval.place_names_batch(dcids=("country/KEN", "country/UGA"))

    assert result["country/KEN"] == ("Kenya", "Country")
    assert result["country/UGA"] == ("Uganda", "Country")


def test_place_names_batch_missing_node_seeds_none(mock_dc_client):
    """A DCID not present in the response is still present in the result as (None, None)."""
    mock_dc_client.node.fetch.return_value.to_dict.return_value = _make_place_node_response(
        {"country/KEN": ("Kenya", "Country")}
    )
    result = retrieval.place_names_batch(dcids=("country/KEN", "country/MISSING"))

    assert result["country/KEN"] == ("Kenya", "Country")
    assert result["country/MISSING"] == (None, None)


def test_place_names_batch_cache_hit_avoids_second_fetch(mock_dc_client):
    """Cache hit: second call with same dcids skips client.node.fetch."""
    mock_dc_client.node.fetch.return_value.to_dict.return_value = _make_place_node_response(
        {"country/KEN": ("Kenya", "Country")}
    )
    first = retrieval.place_names_batch(dcids=("country/KEN",))
    second = retrieval.place_names_batch(dcids=("country/KEN",))

    assert first == second
    assert mock_dc_client.node.fetch.call_count == 1, "Cache hit should skip the second fetch call"


def test_place_names_batch_transient_error_returns_all_none(mock_dc_client):
    """A transient client error → fail-open: all requested DCIDs map to (None, None)."""
    mock_dc_client.node.fetch.side_effect = RuntimeError("mixer unavailable")

    result = retrieval.place_names_batch(dcids=("country/KEN", "country/UGA"))

    assert result["country/KEN"] == (None, None)
    assert result["country/UGA"] == (None, None)


def test_place_names_batch_is_lru_cache():
    """_place_names_cache is a cachetools.LRUCache (mirrors other retrieval caches)."""
    import cachetools

    assert isinstance(retrieval._place_names_cache, cachetools.LRUCache)


# ---------------------------------------------------------------------------
# observation_facet_ranges — parse, union, cache, fail-open
# ---------------------------------------------------------------------------

_FACET_RESPONSE_MULTI = {
    "byVariable": {
        "Count_Person": {
            "byEntity": {
                "country/KEN": {
                    "orderedFacets": [
                        {
                            "facetId": "facet1",
                            "obsCount": 65,
                            "earliestDate": "1960",
                            "latestDate": "2020",
                            "observations": [],
                        },
                        {
                            "facetId": "facet2",
                            "obsCount": 10,
                            "earliestDate": "1990",
                            "latestDate": "2024",
                            "observations": [],
                        },
                    ]
                },
                "country/UGA": {
                    "orderedFacets": [
                        {
                            "facetId": "facet3",
                            "obsCount": 30,
                            "earliestDate": "1970",
                            "latestDate": "2022",
                            "observations": [],
                        }
                    ]
                },
            }
        }
    }
}


def test_observation_facet_ranges_parses_presence_and_ranges(mock_dc_client):
    """Parses presence + per-var ranges; unions across facets and entities."""
    mock_dc_client.api.post.return_value = _FACET_RESPONSE_MULTI

    present, ranges = retrieval.observation_facet_ranges(
        variable_dcids=("Count_Person",),
        entity_dcids=("country/KEN", "country/UGA"),
    )

    # Variable is present.
    assert "Count_Person" in present

    # Per-var range unions across both entities and all facets:
    #   KEN: min(1960, 1990)=1960  max(2020, 2024)=2024
    #   UGA: min(1970)=1970  max(2022)=2022
    #   union: min(1960, 1970)=1960  max(2024, 2022)=2024
    lo, hi = ranges["Count_Person"]
    assert lo == "1960", f"Expected earliest='1960', got {lo!r}"
    assert hi == "2024", f"Expected latest='2024', got {hi!r}"


def test_observation_facet_ranges_absent_var_not_present(mock_dc_client):
    """Variable with no orderedFacets is absent from the present set and ranges."""
    mock_dc_client.api.post.return_value = {
        "byVariable": {
            "Count_Person": {
                "byEntity": {"country/KEN": {"orderedFacets": []}},
            }
        }
    }

    present, ranges = retrieval.observation_facet_ranges(
        variable_dcids=("Count_Person",),
        entity_dcids=("country/KEN",),
    )

    assert "Count_Person" not in present
    assert "Count_Person" not in ranges


def test_observation_facet_ranges_fail_open_on_exception(mock_dc_client):
    """Transient error → fail-open: returns (frozenset(), {}) and sets degraded flag."""
    mock_dc_client.api.post.side_effect = RuntimeError("mixer unavailable")
    retrieval.reset_dc_call_degraded()

    present, ranges = retrieval.observation_facet_ranges(
        variable_dcids=("Count_Person",),
        entity_dcids=("country/KEN",),
    )

    assert present == frozenset()
    assert ranges == {}
    assert retrieval.dc_call_was_degraded()


def test_observation_facet_ranges_empty_inputs_return_empty():
    """Empty variable_dcids or entity_dcids → (frozenset(), {}) without API call."""
    import dc_search.retrieval as _r

    with patch("dc_search.retrieval.get_client") as mock_gc:
        present1, ranges1 = _r.observation_facet_ranges(
            variable_dcids=(),
            entity_dcids=("country/KEN",),
        )
        present2, ranges2 = _r.observation_facet_ranges(
            variable_dcids=("Count_Person",),
            entity_dcids=(),
        )
    mock_gc.assert_not_called()
    assert present1 == frozenset() and ranges1 == {}
    assert present2 == frozenset() and ranges2 == {}


def test_observation_facet_ranges_cache_hit_avoids_second_call(mock_dc_client):
    """Second call with same args returns cached result without a second API call."""
    mock_dc_client.api.post.return_value = _FACET_RESPONSE_MULTI

    first_present, first_ranges = retrieval.observation_facet_ranges(
        variable_dcids=("Count_Person",),
        entity_dcids=("country/KEN", "country/UGA"),
    )
    second_present, second_ranges = retrieval.observation_facet_ranges(
        variable_dcids=("Count_Person",),
        entity_dcids=("country/KEN", "country/UGA"),
    )

    assert first_present == second_present
    assert first_ranges == second_ranges
    assert mock_dc_client.api.post.call_count == 1, "Cache hit should skip the second API call"


def test_observation_facet_ranges_cache_is_lru():
    """_observation_facet_ranges_cache is a cachetools.LRUCache."""
    assert isinstance(retrieval._observation_facet_ranges_cache, cachetools.LRUCache)
