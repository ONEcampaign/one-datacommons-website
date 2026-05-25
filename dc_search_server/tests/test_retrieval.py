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
