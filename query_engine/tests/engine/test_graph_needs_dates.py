"""Tests for needs_dates select branching, count_observations kwarg propagation,
and node_label in-process cache.

All offline: uses monkeypatched _post / _node_data to capture posted payloads.
"""
from __future__ import annotations

from qre.engine.graph import LiveGraphClient

# ---------------------------------------------------------------------------
# observation_facets select branching (needs_dates parameter)
# ---------------------------------------------------------------------------


def test_observation_facets_no_dates_omits_date_key(monkeypatch) -> None:
    """needs_dates=False → select omits 'date' value and 'date' key entirely."""
    sv = "SomeVar"
    entity = "country/TEST"
    captured: list[dict] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        captured.append(payload)
        return {
            "byVariable": {sv: {"byEntity": {entity: {"orderedFacets": []}}}}
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    client.observation_facets(stat_var=sv, entity=entity, needs_dates=False)

    assert len(captured) == 1
    body = captured[0]
    assert body["select"] == ["variable", "entity", "facet"]
    assert "date" not in body


def test_observation_facets_with_dates_includes_date_key(monkeypatch) -> None:
    """needs_dates=True → full 5-field select plus 'date': '' key."""
    sv = "SomeVar"
    entity = "country/TEST"
    captured: list[dict] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        captured.append(payload)
        return {
            "byVariable": {sv: {"byEntity": {entity: {"orderedFacets": []}}}}
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    client.observation_facets(stat_var=sv, entity=entity, needs_dates=True)

    assert len(captured) == 1
    body = captured[0]
    assert body["select"] == ["variable", "entity", "date", "value", "facet"]
    assert "date" in body
    assert body["date"] == ""


def test_observation_facets_default_omits_date_key(monkeypatch) -> None:
    """Default call (no needs_dates arg) behaves like needs_dates=False."""
    sv = "SomeVar"
    entity = "country/TEST"
    captured: list[dict] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        captured.append(payload)
        return {
            "byVariable": {sv: {"byEntity": {entity: {"orderedFacets": []}}}}
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    client.observation_facets(stat_var=sv, entity=entity)

    assert len(captured) == 1
    assert "date" not in captured[0]


# ---------------------------------------------------------------------------
# count_observations passes needs_dates based on window presence
# ---------------------------------------------------------------------------


def test_count_observations_windowed_posts_with_date_key(monkeypatch) -> None:
    """Windowed count → observation_facets gets needs_dates=True → 'date' key in POST body."""
    sv = "SomeVar"
    entity = "country/TEST"
    captured: list[dict] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        if "/observation" in url:
            captured.append(payload)
            return {
                "byVariable": {
                    sv: {
                        "byEntity": {
                            entity: {
                                "orderedFacets": [
                                    {
                                        "earliestDate": "2015",
                                        "latestDate": "2020",
                                        "obsCount": 5,
                                        "observations": [
                                            {"date": str(y), "value": 1.0}
                                            for y in range(2015, 2021)
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        return {}

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    from qre.models import TimeWindow

    window = TimeWindow(start_year=2016, end_year=2019)
    client.count_observations(stat_vars=[sv], entities=[entity], window=window)

    assert len(captured) == 1
    body = captured[0]
    assert "date" in body, "Windowed count_observations must request dates from the graph"
    assert body["select"] == ["variable", "entity", "date", "value", "facet"]


def test_count_observations_no_window_posts_without_date_key(monkeypatch) -> None:
    """Window-free count → needs_dates=False → no 'date' key in the POST body."""
    sv = "SomeVar"
    entity = "country/TEST"
    captured: list[dict] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        if "/observation" in url:
            captured.append(payload)
            return {
                "byVariable": {
                    sv: {
                        "byEntity": {
                            entity: {
                                "orderedFacets": [
                                    {
                                        "earliestDate": "2015",
                                        "latestDate": "2020",
                                        "obsCount": 5,
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        return {}

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    client.count_observations(stat_vars=[sv], entities=[entity], window=None)

    assert len(captured) == 1
    body = captured[0]
    assert "date" not in body, "Window-free count_observations must not request dates"
    assert body["select"] == ["variable", "entity", "facet"]


# ---------------------------------------------------------------------------
# node_label in-process cache
# ---------------------------------------------------------------------------


def test_node_label_cache_deduplicates_http_calls(monkeypatch) -> None:
    """Two node_label calls for the same dcid issue only one _node_data call."""
    call_count = 0

    def fake_node_data(self, dcid: str, prop: str) -> dict | None:
        nonlocal call_count
        call_count += 1
        return {"name": {"nodes": [{"value": "Kenya"}]}}

    monkeypatch.setattr(LiveGraphClient, "_node_data", fake_node_data)
    client = LiveGraphClient(base="http://fake")

    label1 = client.node_label("country/KEN")
    label2 = client.node_label("country/KEN")

    assert label1 == "Kenya"
    assert label2 == "Kenya"
    assert call_count == 1, f"Expected 1 _node_data call but got {call_count}"


def test_node_label_none_result_not_cached(monkeypatch) -> None:
    """A None-returning dcid is not cached; a second call re-issues the HTTP call."""
    call_count = 0

    def fake_node_data(self, dcid: str, prop: str) -> dict | None:
        nonlocal call_count
        call_count += 1
        return None  # absent node

    monkeypatch.setattr(LiveGraphClient, "_node_data", fake_node_data)
    client = LiveGraphClient(base="http://fake")

    result1 = client.node_label("absent/NODE")
    result2 = client.node_label("absent/NODE")

    assert result1 is None
    assert result2 is None
    assert call_count == 2, f"Absent node must not be cached; expected 2 calls, got {call_count}"


def test_node_label_cache_separate_dcids_each_hit_once(monkeypatch) -> None:
    """Two different dcids each issue one HTTP call even when interleaved."""
    calls: list[str] = []

    def fake_node_data(self, dcid: str, prop: str) -> dict | None:
        calls.append(dcid)
        return {"name": {"nodes": [{"value": f"Label-{dcid}"}]}}

    monkeypatch.setattr(LiveGraphClient, "_node_data", fake_node_data)
    client = LiveGraphClient(base="http://fake")

    client.node_label("a/A")
    client.node_label("b/B")
    client.node_label("a/A")
    client.node_label("b/B")

    # Each dcid should hit once despite two calls each
    assert calls.count("a/A") == 1
    assert calls.count("b/B") == 1
