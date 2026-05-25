"""Tests for dc_search.retrieval date-coverage helpers.

Covers: variable_date_coverage, variable_info_date_ranges, observation_date_ranges,
and the shared _parse_observation refactor (presence_for_entities equivalence).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import cachetools
import httpx

import dc_search.retrieval as retrieval
from dc_search.retrieval import (
    _VARIABLE_INFO_DATE_CAP,
    DateCoverage,
    _parse_observation,
    observation_date_ranges,
    presence_for_entities,
    variable_date_coverage,
    variable_info_date_ranges,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VAR_A = "ONE/SV_A"
_VAR_B = "dc/SV_B"
_ENT_KEN = "country/KEN"
_ENT_NGA = "country/NGA"

_COVERAGE_RESPONSE = {
    "variableCoverage": {
        _VAR_A: {"earliest": "2010", "latest": "2024"},
    },
    "entityCoverage": {
        _VAR_A: {
            "entity": {
                _ENT_KEN: {"earliest": "2012", "latest": "2020"},
            }
        }
    },
}


def _make_api_client(return_value: dict) -> MagicMock:
    """Return a mock `get_client()` result with api.post stubbed."""
    client = MagicMock()
    client.api.post.return_value = return_value
    return client


# ---------------------------------------------------------------------------
# variable_date_coverage — basic parse
# ---------------------------------------------------------------------------


def test_variable_date_coverage_parses_envelopes():
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client(_COVERAGE_RESPONSE)):
        cov = variable_date_coverage(variable_dcids=(_VAR_A,))
    assert _VAR_A in cov.envelopes
    assert cov.envelopes[_VAR_A] == ("2010", "2024")


def test_variable_date_coverage_parses_entity_ranges():
    """JSON key-shape assertion (api-ux A1): EntityRanges.entity wrapper is handled."""
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client(_COVERAGE_RESPONSE)):
        cov = variable_date_coverage(
            variable_dcids=(_VAR_A,),
            entity_dcids=(_ENT_KEN,),
        )
    assert (_VAR_A, _ENT_KEN) in cov.entity_ranges
    assert cov.entity_ranges[(_VAR_A, _ENT_KEN)] == ("2012", "2020")


def test_variable_date_coverage_snake_case_fallback():
    """Accepts snake_case keys when the Envoy transcoder emits them."""
    snake_response = {
        "variable_coverage": {
            _VAR_A: {"earliest": "2010", "latest": "2024"},
        },
        "entity_coverage": {
            _VAR_A: {
                "entity": {
                    _ENT_KEN: {"earliest": "2012", "latest": "2020"},
                }
            }
        },
    }
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client(snake_response)):
        cov = variable_date_coverage(variable_dcids=(_VAR_A,), entity_dcids=(_ENT_KEN,))
    assert cov.envelopes[_VAR_A] == ("2010", "2024")
    assert cov.entity_ranges[(_VAR_A, _ENT_KEN)] == ("2012", "2020")


def test_variable_date_coverage_absent_var_not_in_envelopes():
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client(_COVERAGE_RESPONSE)):
        cov = variable_date_coverage(variable_dcids=(_VAR_A, _VAR_B))
    assert _VAR_B not in cov.envelopes


def test_variable_date_coverage_empty_dcids_returns_empty_no_call():
    client = _make_api_client({})
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov = variable_date_coverage(variable_dcids=())
    client.api.post.assert_not_called()
    assert cov.envelopes == {}
    assert cov.entity_ranges == {}


# ---------------------------------------------------------------------------
# variable_date_coverage — cache hit / dedup
# ---------------------------------------------------------------------------


def test_variable_date_coverage_cache_hit_avoids_second_call():
    client = _make_api_client(_COVERAGE_RESPONSE)
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov1 = variable_date_coverage(variable_dcids=(_VAR_A,))
        cov2 = variable_date_coverage(variable_dcids=(_VAR_A,))
    # Second call must be served from cache.
    client.api.post.assert_called_once()
    assert cov1 is cov2


def test_variable_date_coverage_sorted_key_deduplicates():
    client = _make_api_client(_COVERAGE_RESPONSE)
    with patch("dc_search.retrieval.get_client", return_value=client):
        variable_date_coverage(variable_dcids=(_VAR_A, _VAR_B))
        variable_date_coverage(variable_dcids=(_VAR_B, _VAR_A))
    # Different order → same sorted key → one network call.
    client.api.post.assert_called_once()


# ---------------------------------------------------------------------------
# variable_date_coverage — fail-open
# ---------------------------------------------------------------------------


def test_variable_date_coverage_fail_open_on_http_error():
    client = MagicMock()
    client.api.post.side_effect = httpx.HTTPError("timeout")
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov = variable_date_coverage(variable_dcids=(_VAR_A,))
    assert cov.envelopes == {}
    assert cov.entity_ranges == {}


def test_variable_date_coverage_fail_open_on_sdk_error():
    """SDK raises a non-httpx exception (e.g. DCConnectionError); helper must fail open."""
    client = MagicMock()
    client.api.post.side_effect = RuntimeError("DC connection refused")
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov = variable_date_coverage(variable_dcids=(_VAR_A,))
    assert cov.envelopes == {}
    assert cov.entity_ranges == {}


def test_variable_date_coverage_fail_open_on_value_error():
    client = MagicMock()
    client.api.post.side_effect = ValueError("bad json")
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov = variable_date_coverage(variable_dcids=(_VAR_A,))
    assert isinstance(cov, DateCoverage)
    assert cov.envelopes == {}


def test_variable_date_coverage_error_result_not_cached():
    """On error the empty result must NOT be cached so a retry can succeed."""
    client = MagicMock()
    client.api.post.side_effect = [
        httpx.HTTPError("first"),
        _COVERAGE_RESPONSE,
    ]
    with patch("dc_search.retrieval.get_client", return_value=client):
        cov1 = variable_date_coverage(variable_dcids=(_VAR_A,))
        cov2 = variable_date_coverage(variable_dcids=(_VAR_A,))
    # First call errored (empty); second call succeeded (populated).
    assert cov1.envelopes == {}
    assert cov2.envelopes == {_VAR_A: ("2010", "2024")}
    assert client.api.post.call_count == 2


# ---------------------------------------------------------------------------
# variable_date_coverage — request payload shape
# ---------------------------------------------------------------------------


def test_variable_date_coverage_sends_correct_payload():
    client = _make_api_client(_COVERAGE_RESPONSE)
    with patch("dc_search.retrieval.get_client", return_value=client):
        variable_date_coverage(
            variable_dcids=(_VAR_A,),
            entity_dcids=(_ENT_KEN,),
        )
    args, kwargs = client.api.post.call_args
    payload = args[0]
    assert set(payload["variables"]) == {_VAR_A}
    assert set(payload["entities"]) == {_ENT_KEN}
    assert kwargs.get("endpoint") == "variable/coverage"


# ---------------------------------------------------------------------------
# variable_info_date_ranges — basic parse
# ---------------------------------------------------------------------------

_VAR_INFO_RESPONSE = {
    "data": [
        {
            "node": _VAR_B,
            "info": {
                "provenanceSummary": {
                    "prov1": {
                        "seriesSummary": [
                            {"earliestDate": "2000", "latestDate": "2018"},
                            {"earliestDate": "2005", "latestDate": "2022"},
                        ]
                    }
                }
            },
        }
    ]
}


def test_variable_info_date_ranges_parses_and_folds():
    client = _make_api_client(_VAR_INFO_RESPONSE)
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert _VAR_B in result
    # min earliest = "2000", max latest = "2022"
    assert result[_VAR_B] == ("2000", "2022")


def test_variable_info_date_ranges_sends_nodes_field():
    client = _make_api_client(_VAR_INFO_RESPONSE)
    with patch("dc_search.retrieval.get_client", return_value=client):
        variable_info_date_ranges(variable_dcids=(_VAR_B,))
    args, kwargs = client.api.post.call_args
    payload = args[0]
    assert "nodes" in payload
    assert kwargs.get("endpoint") == "bulk/info/variable"


def test_variable_info_date_ranges_n_cap():
    """Input exceeding cap is truncated before the network call."""
    many_vars = tuple(f"dcid/SV_{i}" for i in range(_VARIABLE_INFO_DATE_CAP + 10))
    client = _make_api_client({"data": []})
    with patch("dc_search.retrieval.get_client", return_value=client):
        variable_info_date_ranges(variable_dcids=many_vars)
    args, _ = client.api.post.call_args
    payload = args[0]
    assert len(payload["nodes"]) <= _VARIABLE_INFO_DATE_CAP


def test_variable_info_date_ranges_fail_open_on_http_error():
    client = MagicMock()
    client.api.post.side_effect = httpx.HTTPError("timeout")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert result == {}


def test_variable_info_date_ranges_fail_open_on_sdk_error():
    """SDK raises a non-httpx exception; helper must fail open."""
    client = MagicMock()
    client.api.post.side_effect = RuntimeError("DC connection refused")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert result == {}


def test_variable_info_date_ranges_absent_var_omitted():
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client({"data": []})):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert _VAR_B not in result


# ---------------------------------------------------------------------------
# observation_date_ranges — basic
# ---------------------------------------------------------------------------

_OBS_RESPONSE = {
    "byVariable": {
        _VAR_B: {
            "byEntity": {
                _ENT_KEN: {
                    "orderedFacets": [
                        {
                            "earliestDate": "2008",
                            "latestDate": "2021",
                            "observations": [],
                        }
                    ]
                }
            }
        }
    }
}


def _make_obs_client(return_value: dict) -> MagicMock:
    client = MagicMock()
    obs_result = MagicMock()
    obs_result.to_dict.return_value = return_value
    client.observation.fetch.return_value = obs_result
    return client


def test_observation_date_ranges_parses_facets():
    with patch("dc_search.retrieval.get_client", return_value=_make_obs_client(_OBS_RESPONSE)):
        result = observation_date_ranges(variable_dcids=(_VAR_B,), entity_dcids=(_ENT_KEN,))
    assert (_VAR_B, _ENT_KEN) in result
    assert result[(_VAR_B, _ENT_KEN)] == ("2008", "2021")


def test_observation_date_ranges_fail_open_on_http_error():
    client = MagicMock()
    client.observation.fetch.side_effect = httpx.HTTPError("timeout")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = observation_date_ranges(variable_dcids=(_VAR_B,), entity_dcids=(_ENT_KEN,))
    assert result == {}


def test_observation_date_ranges_fail_open_on_sdk_error():
    """SDK raises a non-httpx exception; helper must fail open."""
    client = MagicMock()
    client.observation.fetch.side_effect = RuntimeError("DC connection refused")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = observation_date_ranges(variable_dcids=(_VAR_B,), entity_dcids=(_ENT_KEN,))
    assert result == {}


def test_observation_date_ranges_empty_inputs():
    client = _make_obs_client({})
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = observation_date_ranges(variable_dcids=(), entity_dcids=(_ENT_KEN,))
    client.observation.fetch.assert_not_called()
    assert result == {}


# ---------------------------------------------------------------------------
# Shared-parse equivalence: refactored presence_for_entities still returns the
# same frozenset as before the _parse_observation refactor.
# ---------------------------------------------------------------------------


def test_presence_for_entities_unchanged_after_refactor():
    """Refactored presence_for_entities returns identical frozenset to pre-refactor logic."""
    raw = {
        "byVariable": {
            _VAR_A: {
                "byEntity": {
                    _ENT_KEN: {"orderedFacets": [{"earliestDate": "2010", "latestDate": "2020"}]}
                }
            },
            _VAR_B: {
                "byEntity": {
                    _ENT_KEN: {"orderedFacets": []}  # no facets → not present
                }
            },
        }
    }
    obs_result = MagicMock()
    obs_result.to_dict.return_value = raw
    client = MagicMock()
    client.observation.fetch.return_value = obs_result

    with patch("dc_search.retrieval.get_client", return_value=client):
        result = presence_for_entities(
            variable_dcids=(_VAR_A, _VAR_B),
            entity_dcids=(_ENT_KEN,),
        )

    assert isinstance(result, frozenset)
    assert _VAR_A in result
    assert _VAR_B not in result


def test_presence_for_entities_fail_open_on_sdk_error():
    """SDK raises a non-httpx exception; presence_for_entities must fail open."""
    client = MagicMock()
    client.observation.fetch.side_effect = RuntimeError("DC connection refused")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = presence_for_entities(
            variable_dcids=(_VAR_A,),
            entity_dcids=(_ENT_KEN,),
        )
    assert result == frozenset()


def test_parse_observation_direct():
    """_parse_observation returns both presence and ranges correctly."""
    raw = {
        "byVariable": {
            _VAR_A: {
                "byEntity": {
                    _ENT_KEN: {"orderedFacets": [{"earliestDate": "2005", "latestDate": "2019"}]},
                    _ENT_NGA: {"orderedFacets": [{"earliestDate": "2008", "latestDate": "2022"}]},
                }
            }
        }
    }
    present, ranges = _parse_observation(raw, (_VAR_A,))
    assert present == frozenset({_VAR_A})
    assert ranges[(_VAR_A, _ENT_KEN)] == ("2005", "2019")
    assert ranges[(_VAR_A, _ENT_NGA)] == ("2008", "2022")


# ---------------------------------------------------------------------------
# Cache type assertions
# ---------------------------------------------------------------------------


def test_coverage_cache_is_lru():
    assert isinstance(retrieval._coverage_cache, cachetools.LRUCache)


def test_variable_info_dates_cache_is_lru():
    assert isinstance(retrieval._variable_info_dates_cache, cachetools.LRUCache)


def test_observation_dates_cache_is_lru():
    assert isinstance(retrieval._observation_dates_cache, cachetools.LRUCache)


# ---------------------------------------------------------------------------
# Fix 1 — socket timeout: requests.exceptions.Timeout fails open
# ---------------------------------------------------------------------------


def test_variable_info_date_ranges_timeout_fails_open():
    """requests.exceptions.Timeout from the SDK is caught and fails open."""
    import requests.exceptions

    client = MagicMock()
    client.api.post.side_effect = requests.exceptions.Timeout("timed out")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert result == {}


def test_observation_date_ranges_timeout_fails_open():
    """requests.exceptions.Timeout from the SDK is caught and fails open."""
    import requests.exceptions

    client = MagicMock()
    client.observation.fetch.side_effect = requests.exceptions.Timeout("timed out")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = observation_date_ranges(variable_dcids=(_VAR_B,), entity_dcids=(_ENT_KEN,))
    assert result == {}


def test_get_client_sets_socket_default_timeout():
    """get_client() sets socket.getdefaulttimeout() to the configured value."""
    import socket
    from unittest.mock import patch

    from dc_search.client import _DC_SDK_SOCKET_TIMEOUT_S

    # Restore the original default after the test; patch DataCommonsClient so
    # get_client() does not make a real network call to a DC instance.
    original = socket.getdefaulttimeout()
    try:
        with (
            patch("dc_search.client.DataCommonsClient"),
            patch("dc_search.client.load_config") as mock_cfg,
        ):
            mock_cfg.return_value.api_key = "test-key"
            mock_cfg.return_value.api_url = ""
            # Clear the @cache so our patched path is actually executed.
            from dc_search import client as client_mod

            client_mod.get_client.cache_clear()
            client_mod.get_client()
        assert socket.getdefaulttimeout() == _DC_SDK_SOCKET_TIMEOUT_S
    finally:
        socket.setdefaulttimeout(original)
        # Re-clear so subsequent tests don't get our mocked singleton.
        from dc_search import client as client_mod

        client_mod.get_client.cache_clear()


# ---------------------------------------------------------------------------
# Fix 2 — _parse_observation unions across all facets
# ---------------------------------------------------------------------------


def test_parse_observation_unions_across_facets():
    """A (var, entity) pair with two facets [2010,2015] and [2000,2023] yields (2000, 2023)."""
    raw = {
        "byVariable": {
            _VAR_A: {
                "byEntity": {
                    _ENT_KEN: {
                        "orderedFacets": [
                            {"earliestDate": "2010", "latestDate": "2015"},
                            {"earliestDate": "2000", "latestDate": "2023"},
                        ]
                    }
                }
            }
        }
    }
    present, ranges = _parse_observation(raw, (_VAR_A,))
    assert _VAR_A in present
    assert ranges[(_VAR_A, _ENT_KEN)] == ("2000", "2023")


# ---------------------------------------------------------------------------
# Fix 4 — sort before cap yields deterministic cache key for >25-var sets
# ---------------------------------------------------------------------------


def test_variable_info_date_ranges_sort_before_cap_deterministic():
    """Calling with the same >25-var set in two different orders hits the same cache."""
    many_vars = tuple(f"dcid/SV_{i:03d}" for i in range(_VARIABLE_INFO_DATE_CAP + 10))
    shuffled = many_vars[::-1]  # reverse order
    client = _make_api_client({"data": []})
    with patch("dc_search.retrieval.get_client", return_value=client):
        variable_info_date_ranges(variable_dcids=many_vars)
        variable_info_date_ranges(variable_dcids=shuffled)
    # Same sorted-then-capped key → only one network call.
    assert client.api.post.call_count == 1


# ---------------------------------------------------------------------------
# Fix 5 — single-prov dict shape (seriesSummary at top level)
# ---------------------------------------------------------------------------


def test_variable_info_date_ranges_single_prov_shape():
    """provenanceSummary as a flat single-prov dict with seriesSummary yields correct dates."""
    single_prov_response = {
        "data": [
            {
                "node": _VAR_B,
                "info": {
                    "provenanceSummary": {
                        # Single-prov shape: seriesSummary is a direct key, not
                        # a prov-keyed map.
                        "seriesSummary": [
                            {"earliestDate": "2003", "latestDate": "2019"},
                        ]
                    }
                },
            }
        ]
    }
    client = _make_api_client(single_prov_response)
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_info_date_ranges(variable_dcids=(_VAR_B,))
    assert _VAR_B in result
    assert result[_VAR_B] == ("2003", "2019")


# ---------------------------------------------------------------------------
# Fail-open degraded flag (feeds the filtering_degraded caveat)
# ---------------------------------------------------------------------------


def test_variable_date_coverage_trips_degraded_flag_on_error():
    """A transient error fails open AND trips the per-request degraded flag, so the
    pipeline can attach a filtering_degraded caveat for the unfiltered fallback."""
    retrieval.reset_dc_call_degraded()
    client = MagicMock()
    client.api.post.side_effect = RuntimeError("boom")
    with patch("dc_search.retrieval.get_client", return_value=client):
        result = variable_date_coverage(variable_dcids=(_VAR_A,))
    assert result.envelopes == {}
    assert retrieval.dc_call_was_degraded() is True


def test_variable_date_coverage_clean_call_leaves_degraded_flag_unset():
    """A successful coverage fetch does not trip the degraded flag."""
    retrieval.reset_dc_call_degraded()
    with patch("dc_search.retrieval.get_client", return_value=_make_api_client(_COVERAGE_RESPONSE)):
        variable_date_coverage(variable_dcids=(_VAR_A,))
    assert retrieval.dc_call_was_degraded() is False
