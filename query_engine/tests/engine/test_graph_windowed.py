"""Tests for windowed count_observations and Facet.dates population.

Covers:
- LiveGraphClient.observation_facets parses dates from the observations array.
- LiveGraphClient.count_observations windowed vs window-free.
- FakeGraph.observation_facets populates dates from fixture observations array.
- FakeGraph.count_observations windowed vs window-free.
"""
from __future__ import annotations

from qre.engine.graph import LiveGraphClient
from qre.models import TimeWindow
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# LiveGraphClient: observation_facets parses dates
# ---------------------------------------------------------------------------


def test_live_observation_facets_parses_dates(monkeypatch) -> None:
    """_post returns orderedFacets with observations array → Facet.dates populated."""
    sv = "SomeVar"
    entity = "country/TEST"

    def fake_post(self, url: str, payload: dict) -> dict:
        return {
            "byVariable": {
                sv: {
                    "byEntity": {
                        entity: {
                            "orderedFacets": [
                                {
                                    "earliestDate": "2018",
                                    "latestDate": "2022",
                                    "obsCount": 5,
                                    "observations": [
                                        {"date": "2018", "value": 1.0},
                                        {"date": "2019", "value": 2.0},
                                        {"date": "2020", "value": 3.0},
                                        {"date": "2021", "value": 4.0},
                                        {"date": "2022", "value": 5.0},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    facets = client.observation_facets(stat_var=sv, entity=entity)

    assert len(facets) == 1
    assert facets[0].dates == ["2018", "2019", "2020", "2021", "2022"]


def test_live_observation_facets_skips_missing_dates(monkeypatch) -> None:
    """Observation entries without a 'date' field are skipped."""
    sv = "SomeVar"
    entity = "country/TEST"

    def fake_post(self, url: str, payload: dict) -> dict:
        return {
            "byVariable": {
                sv: {
                    "byEntity": {
                        entity: {
                            "orderedFacets": [
                                {
                                    "earliestDate": "2020",
                                    "latestDate": "2021",
                                    "obsCount": 2,
                                    "observations": [
                                        {"date": "2020", "value": 1.0},
                                        {"value": 9.9},  # no 'date' key — skipped
                                        {"date": "2021", "value": 2.0},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    facets = client.observation_facets(stat_var=sv, entity=entity)

    assert len(facets) == 1
    assert facets[0].dates == ["2020", "2021"]


# ---------------------------------------------------------------------------
# LiveGraphClient: windowed count_observations
# ---------------------------------------------------------------------------


def _make_facets_post(sv: str, entity: str, observations: list[dict]) -> dict:
    return {
        "byVariable": {
            sv: {
                "byEntity": {
                    entity: {
                        "orderedFacets": [
                            {
                                "earliestDate": observations[0]["date"] if observations else None,
                                "latestDate": observations[-1]["date"] if observations else None,
                                "obsCount": len(observations),
                                "observations": observations,
                            }
                        ]
                    }
                }
            }
        }
    }


def test_live_count_observations_window_free_sums_obs_count(monkeypatch) -> None:
    sv = "SomeVar"
    entity = "country/TEST"
    obs = [{"date": str(y), "value": 1.0} for y in range(2010, 2015)]  # 5 obs

    monkeypatch.setattr(
        LiveGraphClient,
        "_post",
        lambda self, url, payload: _make_facets_post(sv, entity, obs),
    )
    client = LiveGraphClient(base="http://fake")
    count = client.count_observations(stat_vars=[sv], entities=[entity])
    assert count == 5


def test_live_count_observations_windowed_counts_in_window_dates(monkeypatch) -> None:
    sv = "SomeVar"
    entity = "country/TEST"
    obs = [{"date": str(y), "value": 1.0} for y in range(2010, 2025)]  # 15 obs

    monkeypatch.setattr(
        LiveGraphClient,
        "_post",
        lambda self, url, payload: _make_facets_post(sv, entity, obs),
    )
    client = LiveGraphClient(base="http://fake")
    window = TimeWindow(start_year=2015, end_year=2020)  # 6 years
    count = client.count_observations(stat_vars=[sv], entities=[entity], window=window)
    assert count == 6


# ---------------------------------------------------------------------------
# FakeGraph: observation_facets populates dates from observations array
# ---------------------------------------------------------------------------


def test_fake_observation_facets_populates_dates_from_observations_array() -> None:
    obs_fixture = {
        "testSV|testEntity": [
            {
                "earliestDate": "2018",
                "latestDate": "2022",
                "obsCount": 3,
                "observations": [
                    {"date": "2018", "value": 1.0},
                    {"date": "2020", "value": 2.0},
                    {"date": "2022", "value": 3.0},
                ],
            }
        ]
    }
    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    facets = g.observation_facets(stat_var="testSV", entity="testEntity")
    assert len(facets) == 1
    assert facets[0].dates == ["2018", "2020", "2022"]


def test_fake_observation_facets_populates_dates_from_dates_key() -> None:
    """Fixture entry with explicit 'dates' key takes precedence over observations."""
    obs_fixture = {
        "testSV|testEntity": [
            {
                "earliestDate": "2015",
                "latestDate": "2019",
                "obsCount": 3,
                "dates": ["2015", "2017", "2019"],
            }
        ]
    }
    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    facets = g.observation_facets(stat_var="testSV", entity="testEntity")
    assert facets[0].dates == ["2015", "2017", "2019"]


def test_fake_observation_facets_empty_dates_when_no_observations_key() -> None:
    """Existing fixture entries without dates or observations → dates=[]."""
    g = FakeGraph()
    facets = g.observation_facets(
        stat_var="ONE/CRS_DAC/Health-ODAGrants-ETH",
        entity="country/USA",
    )
    assert len(facets) == 1
    assert facets[0].dates == []  # existing fixture has no dates or observations key


# ---------------------------------------------------------------------------
# FakeGraph: windowed count_observations
# ---------------------------------------------------------------------------


def test_fake_count_observations_window_free_sums_obs_count() -> None:
    obs_fixture = {
        "sv|entity": [
            {
                "earliestDate": "2010",
                "latestDate": "2024",
                "obsCount": 50,
                "dates": [str(y) for y in range(2010, 2015)],
            }
        ]
    }
    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    count = g.count_observations(stat_vars=["sv"], entities=["entity"])
    assert count == 50


def test_fake_count_observations_windowed_counts_in_window_dates() -> None:
    obs_fixture = {
        "sv|entity": [
            {
                "earliestDate": "2010",
                "latestDate": "2024",
                "obsCount": 10,
                "dates": [str(y) for y in range(2010, 2020)],  # 2010-2019 = 10 dates
            }
        ]
    }
    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    window = TimeWindow(start_year=2015, end_year=2019)  # 5 dates in range
    count = g.count_observations(stat_vars=["sv"], entities=["entity"], window=window)
    assert count == 5


def test_fake_count_observations_windowed_exclude_all_returns_none() -> None:
    obs_fixture = {
        "sv|entity": [
            {
                "earliestDate": "2000",
                "latestDate": "2005",
                "obsCount": 6,
                "dates": [str(y) for y in range(2000, 2006)],
            }
        ]
    }
    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    window = TimeWindow(start_year=2015, end_year=2020)
    count = g.count_observations(stat_vars=["sv"], entities=["entity"], window=window)
    assert count is None
