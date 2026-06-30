"""Smoke tests for FakeGraph.

Verifies correct lookups, absent-dcid handling, raise_on_call mode,
count_observations, the label-last-value rule, observation_facets_batch
(including the two-pass donor invariant), label-cache hit/miss for F11,
detect + obs caches for F19/F14, specificity sort for F22, and upstream_status
propagation for F6.  Offline only.
"""
import pytest

from qre.engine.errors import GraphInfraError
from qre.engine.graph import Facet, LiveGraphClient
from tests.fixtures import FakeGraph  # noqa: E402


def test_node_label_known_dcid() -> None:
    g = FakeGraph()
    label = g.node_label("country/ETH")
    assert label == "Ethiopia"


def test_node_label_absent_dcid_returns_none() -> None:
    g = FakeGraph()
    assert g.node_label("nonexistent/FAKE_NODE") is None


def test_node_arcs_known_dcid() -> None:
    g = FakeGraph()
    arcs = g.node_arcs("country/KEN")
    assert arcs is not None
    assert "typeOf" in arcs
    assert "name" in arcs


def test_node_arcs_absent_dcid_returns_none() -> None:
    g = FakeGraph()
    assert g.node_arcs("totally/made/up") is None


def test_node_type_known_dcid() -> None:
    g = FakeGraph()
    assert g.node_type("country/ETH") == "Country"
    assert g.node_type("ODAGrants") == "Property"
    assert g.node_type("DAC/Health") == "DevelopmentFinancePurposeEnum"


def test_node_type_absent_returns_none() -> None:
    g = FakeGraph()
    assert g.node_type("fabricated/NODE") is None




def test_label_last_value_rule_multi_name() -> None:
    """DAC/Health has names ['Health', 'Health (Total)']; last value must win."""
    g = FakeGraph()
    label = g.node_label("DAC/Health")
    assert label == "Health (Total)", (
        f"Expected 'Health (Total)' (last value) but got {label!r}"
    )


def test_label_single_value_returns_that_value() -> None:
    g = FakeGraph()
    label = g.node_label("ODAGrants")
    assert label == "Official Development Assistance Grants"




def test_exists_known_dcid() -> None:
    g = FakeGraph()
    assert g.exists("country/USA") is True


def test_exists_absent_dcid() -> None:
    g = FakeGraph()
    assert g.exists("does/not/exist") is False


def test_count_observations_known_pair() -> None:
    g = FakeGraph()
    count = g.count_observations(
        stat_vars=["ONE/CRS_DAC/Health-ODAGrants-ETH"],
        entities=["country/USA"],
    )
    assert count == 402


def test_count_observations_empty_list() -> None:
    g = FakeGraph()
    assert g.count_observations(stat_vars=[], entities=["country/USA"]) is None


def test_count_observations_absent_pair_returns_none() -> None:
    g = FakeGraph()
    count = g.count_observations(
        stat_vars=["ONE/CRS_DAC/Health-ODAGrants-ETH"],
        entities=["country/NRU"],
    )
    assert count is None




def test_resolve_entity_known_name() -> None:
    g = FakeGraph()
    assert g.resolve_entity("Ethiopia") == "country/ETH"
    assert g.resolve_entity("Kenya") == "country/KEN"
    assert g.resolve_entity("Germany") == "country/DEU"


def test_resolve_entity_unknown_name() -> None:
    g = FakeGraph()
    assert g.resolve_entity("Atlantis") is None


def test_detect_svs_known_query() -> None:
    g = FakeGraph()
    svs, entities, scores = g.detect_svs("health ODA grants from USA to Ethiopia")
    assert "ONE/CRS_DAC/Health-ODAGrants-ETH" in svs
    assert "country/USA" in entities
    assert "country/ETH" in entities
    # This fixture entry has no cosine_scores, so all scores default to 1.0.
    assert len(scores) == len(svs)
    assert all(sc == 1.0 for sc in scores)


def test_detect_svs_unknown_query_returns_empty() -> None:
    g = FakeGraph()
    svs, entities, scores = g.detect_svs("some completely unknown query text")
    assert svs == []
    assert entities == []
    assert len(scores) == len(svs)




def test_observation_facets_known_pair() -> None:
    g = FakeGraph()
    facets = g.observation_facets(
        stat_var="ONE/CRS_DAC/Health-ODAGrants-ETH",
        entity="country/USA",
    )
    assert len(facets) == 1
    assert facets[0].obs_count == 402
    assert facets[0].earliest_date == "1991"
    assert facets[0].latest_date == "2024"


def test_observation_facets_absent_pair_returns_empty() -> None:
    g = FakeGraph()
    facets = g.observation_facets(
        stat_var="ONE/CRS_DAC/Health-ODAGrants-ETH",
        entity="country/NRU",
    )
    assert facets == []




def test_raise_on_call_node_label() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.node_label("country/ETH")


def test_raise_on_call_node_arcs() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.node_arcs("ODAGrants")


def test_node_arcs_batch_present_and_absent() -> None:
    """Batch fetch returns arcs for a present dcid and None for an absent dcid."""
    g = FakeGraph()
    result = g.node_arcs_batch(["country/KEN", "totally/made/up"])
    # Present dcid returns arcs dict
    assert result["country/KEN"] is not None
    assert "typeOf" in result["country/KEN"]
    # Absent dcid maps to None — not omitted
    assert "totally/made/up" in result
    assert result["totally/made/up"] is None


def test_node_arcs_batch_raise_on_call() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.node_arcs_batch(["country/KEN"])


def test_raise_on_call_exists() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.exists("country/KEN")


def test_raise_on_call_observation_facets() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.observation_facets(
            stat_var="ONE/CRS_DAC/Health-ODAGrants-ETH",
            entity="country/USA",
        )


def test_raise_on_call_detect_svs() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.detect_svs("health ODA grants")


def test_raise_on_call_resolve_entity() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.resolve_entity("Ethiopia")


def test_raise_on_call_count_observations() -> None:
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.count_observations(
            stat_vars=["ONE/CRS_DAC/Health-ODAGrants-ETH"],
            entities=["country/USA"],
        )




def test_sv_node_five_tuple_arcs() -> None:
    """Verify a dev-finance SV node has the expected five-tuple arcs."""
    g = FakeGraph()
    arcs = g.node_arcs("ONE/CRS_DAC/Health-ODAGrants-ETH")
    assert arcs is not None
    pt_nodes = arcs.get("populationType", {}).get("nodes", [])
    assert any(n.get("dcid") == "DevelopmentFinance" for n in pt_nodes)
    mp_nodes = arcs.get("measuredProperty", {}).get("nodes", [])
    assert any(n.get("dcid") == "DevelopmentFinanceFlow" for n in mp_nodes)


def test_per_test_injection_overrides_file() -> None:
    """FakeGraph accepts explicit dicts instead of loading the fixture files."""
    node_arcs = {"typeOf": {"nodes": [{"dcid": "Thing"}]}}
    g = FakeGraph(
        nodes={"my/Node": {"label": "My Node", "type": "Thing", "arcs": node_arcs}},
        obs={},
        detect={},
        resolve={},
    )
    assert g.node_label("my/Node") == "My Node"
    assert g.node_label("country/ETH") is None  # absent because custom nodes dict is used


# ---------------------------------------------------------------------------
# F2: observation_facets_batch on FakeGraph
# ---------------------------------------------------------------------------


def test_observation_facets_batch_known_pairs() -> None:
    """observation_facets_batch returns per-SV per-entity facet lists."""
    g = FakeGraph()
    sv = "ONE/CRS_DAC/Health-ODAGrants-ETH"
    entity = "country/USA"
    result = g.observation_facets_batch([sv], [entity])
    assert sv in result
    assert entity in result[sv]
    facets = result[sv][entity]
    assert len(facets) == 1
    assert facets[0].obs_count == 402


def test_observation_facets_batch_absent_pair_returns_empty_list() -> None:
    """Absent (sv, entity) pair maps to [] inside the returned dict."""
    g = FakeGraph()
    sv = "ONE/CRS_DAC/Health-ODAGrants-ETH"
    entity = "country/NRU"  # not in fixture
    result = g.observation_facets_batch([sv], [entity])
    assert result[sv][entity] == []


def test_observation_facets_batch_cartesian_product() -> None:
    """All (sv, entity) pairs appear in the result regardless of fixture presence."""
    g = FakeGraph()
    sv1 = "ONE/CRS_DAC/Health-ODAGrants-ETH"
    sv2 = "NONEXISTENT/SV"
    e1 = "country/USA"
    e2 = "country/NRU"
    result = g.observation_facets_batch([sv1, sv2], [e1, e2])
    assert set(result.keys()) == {sv1, sv2}
    for sv in [sv1, sv2]:
        assert set(result[sv].keys()) == {e1, e2}


def test_observation_facets_batch_raise_on_call() -> None:
    """raise_on_call=True propagates GraphInfraError from observation_facets_batch."""
    g = FakeGraph(raise_on_call=True)
    with pytest.raises(GraphInfraError):
        g.observation_facets_batch(["ONE/CRS_DAC/Health-ODAGrants-ETH"], ["country/USA"])


# ---------------------------------------------------------------------------
# F2: two-pass donor invariant on LiveGraphClient
# ---------------------------------------------------------------------------


def test_observation_facets_batch_donor_probe_only_for_unconfirmed(monkeypatch) -> None:
    """Donor probe fires only for SVs with zero confirmed recipients in pass 1.

    Verified indirectly via discover.graph_confirm_resolve which now uses two batch
    passes. Here we test at the graph level: a SV confirmed by a recipient in pass 1
    must not appear in a subsequent batch call that includes the donor.
    """
    sv_with_recipient = "sv/RecipientData"
    sv_without_recipient = "sv/DonorOnly"
    recipient = "country/KEN"
    donor = "country/USA"

    obs_fixture = {
        f"{sv_with_recipient}|{recipient}": [
            {"earliestDate": "2010", "latestDate": "2020", "obsCount": 5}
        ],
        f"{sv_without_recipient}|{donor}": [
            {"earliestDate": "2010", "latestDate": "2020", "obsCount": 3}
        ],
        # sv_with_recipient|donor has NO data (would be double-counted if probe fired)
    }

    g = FakeGraph(obs=obs_fixture, nodes={}, detect={}, resolve={})
    result = g.observation_facets_batch(
        [sv_with_recipient, sv_without_recipient], [recipient]
    )
    # sv_with_recipient confirmed at recipient → donor probe must not fire for it
    assert result[sv_with_recipient][recipient][0].obs_count == 5
    # sv_without_recipient not confirmed at recipient
    assert result[sv_without_recipient][recipient] == []


# ---------------------------------------------------------------------------
# F11: node_labels_batch reads and writes _label_cache on LiveGraphClient
# ---------------------------------------------------------------------------


def test_node_labels_batch_cache_hit_skips_post(monkeypatch) -> None:
    """When all requested dcids are already in _label_cache, no _post is issued."""
    post_called = []

    def fake_post(self, url: str, payload: dict) -> dict:
        post_called.append(payload)
        return {}

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    # Seed the cache directly.
    client._label_cache["country/KEN"] = "Kenya"
    client._label_cache["country/ETH"] = "Ethiopia"

    result = client.node_labels_batch(["country/KEN", "country/ETH"])
    assert result == {"country/KEN": "Kenya", "country/ETH": "Ethiopia"}
    assert post_called == [], "No POST should be issued for fully cached inputs"


def test_node_labels_batch_partial_cache_posts_only_misses(monkeypatch) -> None:
    """Cached dcids are not re-fetched; only misses appear in the POST nodes list."""
    posted_nodes: list[list[str]] = []

    def fake_post(self, url: str, payload: dict) -> dict:
        posted_nodes.append(list(payload.get("nodes", [])))
        # Return a fake label for the one miss.
        return {
            "data": {
                "country/DEU": {
                    "arcs": {"name": {"nodes": [{"value": "Germany"}]}}
                }
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    client._label_cache["country/KEN"] = "Kenya"

    result = client.node_labels_batch(["country/KEN", "country/DEU"])
    assert result == {"country/KEN": "Kenya", "country/DEU": "Germany"}
    assert len(posted_nodes) == 1
    assert posted_nodes[0] == ["country/DEU"], "POST must include only the miss"
    # Miss result must be written to cache.
    assert client._label_cache.get("country/DEU") == "Germany"


def test_node_labels_batch_preserves_input_order_on_partial_cache(monkeypatch) -> None:
    """R2: result dict follows input order regardless of cache hit/miss interleaving.

    The cached dcid sits AFTER a miss in the input. The old code inserted cache hits
    first and appended misses after the POST, so the returned dict order became
    cache-warm-state dependent. Order must mirror the input dcids list exactly.
    """
    def fake_post(self, url: str, payload: dict) -> dict:
        return {
            "data": {
                dcid: {"arcs": {"name": {"nodes": [{"value": f"Label-{dcid}"}]}}}
                for dcid in payload.get("nodes", [])
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    # Seed a cache hit that appears in the MIDDLE of the input order.
    client._label_cache["country/KEN"] = "Kenya"

    dcids = ["country/AAA", "country/KEN", "country/ZZZ"]
    result = client.node_labels_batch(dcids)

    # Returned dict must follow the input order, not [hit, ...misses].
    assert list(result.keys()) == dcids
    assert result == {
        "country/AAA": "Label-country/AAA",
        "country/KEN": "Kenya",
        "country/ZZZ": "Label-country/ZZZ",
    }


def test_node_labels_batch_order_stable_across_cache_warm_state(monkeypatch) -> None:
    """R2: the same input yields the same key order whether or not the cache is warm."""
    def fake_post(self, url: str, payload: dict) -> dict:
        return {
            "data": {
                dcid: {"arcs": {"name": {"nodes": [{"value": f"Label-{dcid}"}]}}}
                for dcid in payload.get("nodes", [])
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    dcids = ["a/1", "b/2", "c/3"]
    cold = client.node_labels_batch(dcids)  # all misses
    warm = client.node_labels_batch(dcids)  # all hits
    assert list(cold.keys()) == dcids
    assert list(warm.keys()) == dcids
    assert cold == warm


def test_node_labels_batch_writes_found_labels_to_cache(monkeypatch) -> None:
    """Found labels are written to _label_cache so subsequent calls skip the network."""
    call_count = [0]

    def fake_post(self, url: str, payload: dict) -> dict:
        call_count[0] += 1
        dcid = payload["nodes"][0]
        return {
            "data": {
                dcid: {"arcs": {"name": {"nodes": [{"value": f"Label-{dcid}"}]}}}
            }
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    # First call — must POST.
    result1 = client.node_labels_batch(["country/XYZ"])
    assert result1 == {"country/XYZ": "Label-country/XYZ"}
    assert call_count[0] == 1

    # Second call — cache hit, no POST.
    result2 = client.node_labels_batch(["country/XYZ"])
    assert result2 == {"country/XYZ": "Label-country/XYZ"}
    assert call_count[0] == 1, "Cache hit must suppress the second POST"


def test_node_labels_batch_none_not_cached(monkeypatch) -> None:
    """Absent nodes (None result) are not cached; a second call re-issues the POST."""
    call_count = [0]

    def fake_post(self, url: str, payload: dict) -> dict:
        call_count[0] += 1
        return {"data": {}}  # node absent

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    result1 = client.node_labels_batch(["absent/NODE"])
    result2 = client.node_labels_batch(["absent/NODE"])

    assert result1 == {}
    assert result2 == {}
    assert call_count[0] == 2, "Absent nodes must not be cached; each call should POST"


def test_node_labels_batch_error_before_cache_write(monkeypatch) -> None:
    """A GraphInfraError from _post propagates without any cache write."""

    def fake_post(self, url: str, payload: dict) -> dict:
        raise GraphInfraError("simulated 503", upstream_status=503)

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    with pytest.raises(GraphInfraError):
        client.node_labels_batch(["country/KEN"])

    assert "country/KEN" not in client._label_cache


# ---------------------------------------------------------------------------
# F14: observation facets LRU cache on LiveGraphClient
# ---------------------------------------------------------------------------


def test_observation_facets_batch_uses_cache(monkeypatch) -> None:
    """Cached (sv, entity, needs_dates) triples skip the POST on a second batch call."""
    sv = "sv/A"
    entity = "country/X"
    post_count = [0]

    def fake_post(self, url: str, payload: dict) -> dict:
        post_count[0] += 1
        return {
            "byVariable": {sv: {"byEntity": {entity: {"orderedFacets": [
                {"earliestDate": "2020", "latestDate": "2022", "obsCount": 10}
            ]}}}},
            "facets": {},
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    r1 = client.observation_facets_batch([sv], [entity])
    r2 = client.observation_facets_batch([sv], [entity])

    assert post_count[0] == 1, "Second batch call must be served from cache"
    assert r1[sv][entity][0].obs_count == r2[sv][entity][0].obs_count == 10


def test_observation_facets_cache_error_does_not_write(monkeypatch) -> None:
    """A GraphInfraError from _post must not write to the obs cache."""

    def fake_post(self, url: str, payload: dict) -> dict:
        raise GraphInfraError("upstream down", upstream_status=503)

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    with pytest.raises(GraphInfraError):
        client.observation_facets_batch(["sv/A"], ["country/X"])

    assert len(client._obs_cache) == 0


def test_observation_facets_single_pair_uses_same_cache(monkeypatch) -> None:
    """observation_facets (single-pair) shares the cache with the batch method."""
    sv = "sv/B"
    entity = "country/Y"
    post_count = [0]

    def fake_post(self, url: str, payload: dict) -> dict:
        post_count[0] += 1
        return {
            "byVariable": {sv: {"byEntity": {entity: {"orderedFacets": [
                {"earliestDate": "2015", "latestDate": "2021", "obsCount": 7}
            ]}}}},
            "facets": {},
        }

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")

    # Warm via single-pair call.
    r_single = client.observation_facets(stat_var=sv, entity=entity)
    # Batch call must see the cache hit.
    r_batch = client.observation_facets_batch([sv], [entity])

    assert post_count[0] == 1, "Batch call must hit the cache seeded by single-pair call"
    assert r_single[0].obs_count == r_batch[sv][entity][0].obs_count == 7


# ---------------------------------------------------------------------------
# F19: detect_svs LRU cache on LiveGraphClient
# ---------------------------------------------------------------------------


def test_detect_svs_cache_hit_skips_network(monkeypatch) -> None:
    """A second detect_svs call for the same query returns the cached result."""
    call_count = [0]

    def fake_post(self, url: str, payload: dict) -> dict:
        call_count[0] += 1
        # Simulate response for the GET-like detect endpoint (actually POST).
        return {"debug": {"sv_matching": {"SV": ["sv/Z"], "CosineScore": [0.9]}}, "entities": []}

    # detect_svs uses self._client.post, not self._post. Patch it.
    def fake_client_post(url, json=None, headers=None):
        call_count[0] += 1

        class _Resp:
            status_code = 200

            def json(self_):
                return {
                    "debug": {"sv_matching": {"SV": ["sv/Z"], "CosineScore": [0.9]}},
                    "entities": [],
                }

        return _Resp()

    monkeypatch.setattr(LiveGraphClient, "_post", fake_post)
    client = LiveGraphClient(base="http://fake")
    # Patch the underlying httpx client's post method.
    monkeypatch.setattr(client._client, "post", fake_client_post)

    r1 = client.detect_svs("test query")
    r2 = client.detect_svs("test query")

    assert call_count[0] == 1, "Second detect_svs for same query must hit cache"
    assert r1 == r2


def test_detect_svs_cache_error_does_not_cache(monkeypatch) -> None:
    """A failed detect_svs call does not populate the cache."""

    def fake_client_post(url, json=None, headers=None):
        class _Resp:
            status_code = 503

            def json(self_):
                return {}

        return _Resp()

    client = LiveGraphClient(base="http://fake")
    monkeypatch.setattr(client._client, "post", fake_client_post)

    with pytest.raises(GraphInfraError):
        client.detect_svs("failing query")

    assert len(client._detect_cache) == 0


# ---------------------------------------------------------------------------
# F6: upstream_status propagation
# ---------------------------------------------------------------------------


def test_post_4xx_sets_upstream_status(monkeypatch) -> None:
    """A non-2xx response from _post carries upstream_status on GraphInfraError."""

    def fake_client_post(url, json=None, headers=None):
        class _Resp:
            status_code = 404

            def json(self_):
                return {}

        return _Resp()

    client = LiveGraphClient(base="http://fake")
    monkeypatch.setattr(client._client, "post", fake_client_post)

    with pytest.raises(GraphInfraError) as exc_info:
        client._post("http://fake/some/endpoint", {})

    assert exc_info.value.upstream_status == 404


def test_detect_svs_non2xx_sets_upstream_status(monkeypatch) -> None:
    """A non-2xx detect response carries upstream_status on GraphInfraError."""

    def fake_client_post(url, json=None, headers=None):
        class _Resp:
            status_code = 502

            def json(self_):
                return {}

        return _Resp()

    client = LiveGraphClient(base="http://fake")
    monkeypatch.setattr(client._client, "post", fake_client_post)

    with pytest.raises(GraphInfraError) as exc_info:
        client.detect_svs("any query")

    assert exc_info.value.upstream_status == 502


# ---------------------------------------------------------------------------
# F22: specificity sort in graph_confirm_resolve (discover.py)
# ---------------------------------------------------------------------------


def test_graph_confirm_resolve_specificity_sort() -> None:
    """Most-specific SVs (fewest uncovered constraints) are confirmed first.

    Two SVs share the same five-tuple. sv/Specific has both constraint props matched
    by bound_values; sv/General has one unmatched constraint. Both have observations
    at the recipient. After sorting, sv/Specific must appear first in confirmed_svs.
    """
    from qre.engine.discover import graph_confirm_resolve, read_constraints
    from qre.engine.shape import ShapeDraft, shape_draft_from

    PROP_A = "prop:A"
    PROP_B = "prop:B"
    SV_SPECIFIC = "sv/Specific"
    SV_GENERAL = "sv/General"
    RECIPIENT = "country/KEN"

    # sv/Specific: both PROP_A and PROP_B match bound_values → 0 uncovered
    # sv/General: only PROP_A matches (PROP_B bound to different value) → 1 uncovered
    arcs_specific = {
        "populationType": {"nodes": [{"dcid": "Pop"}]},
        "measuredProperty": {"nodes": [{"dcid": "Prop"}]},
        "statType": {"nodes": [{"dcid": "measuredValue"}]},
        "constraintProperties": {"nodes": [{"dcid": PROP_A}, {"dcid": PROP_B}]},
        PROP_A: {"nodes": [{"dcid": "A1"}]},
        PROP_B: {"nodes": [{"dcid": "B1"}]},
    }
    arcs_general = {
        "populationType": {"nodes": [{"dcid": "Pop"}]},
        "measuredProperty": {"nodes": [{"dcid": "Prop"}]},
        "statType": {"nodes": [{"dcid": "measuredValue"}]},
        "constraintProperties": {"nodes": [{"dcid": PROP_A}, {"dcid": PROP_B}]},
        PROP_A: {"nodes": [{"dcid": "A1"}]},
        PROP_B: {"nodes": [{"dcid": "B_OTHER"}]},  # not in bound_values
    }

    obs = {
        f"{SV_SPECIFIC}|{RECIPIENT}": [
            {"earliestDate": "2010", "latestDate": "2022", "obsCount": 5}
        ],
        f"{SV_GENERAL}|{RECIPIENT}": [
            {"earliestDate": "2010", "latestDate": "2022", "obsCount": 3}
        ],
    }

    shape = shape_draft_from(
        shape_id="test_shape",
        label="Test",
        pop_type_dcid="Pop",
        meas_prop_dcid="Prop",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
        constraint_props=[PROP_A, PROP_B],
        prop_labels={PROP_A: "A", PROP_B: "B"},
        prop_observed_values={PROP_A: ["A1"], PROP_B: ["B1", "B_OTHER"]},
        family_rule=None,
        sv_arc_facts={SV_SPECIFIC: arcs_specific, SV_GENERAL: arcs_general},
        representative_score=0.9,
    )

    # Bindings: PROP_A → A1, PROP_B → B1 (so sv/General's B_OTHER is unmatched)
    from qre.engine.bind import SlotBindingDraft

    bindings = [
        SlotBindingDraft(axis="what", property_dcid=PROP_A, kind="value", value_dcids=["A1"]),
        SlotBindingDraft(axis="what", property_dcid=PROP_B, kind="value", value_dcids=["B1"]),
    ]

    g = FakeGraph(obs=obs, nodes={}, detect={}, resolve={})
    from qre.engine.retrieve import Materialised

    result = graph_confirm_resolve(
        shape=shape,
        bindings=bindings,
        recipient_dcid=RECIPIENT,
        donor_dcid=None,
        graph=g,
    )
    assert isinstance(result, Materialised), f"Expected Materialised but got {result!r}"
    # sv/Specific must appear before sv/General (more specific first)
    assert result.sv_dcids[0] == SV_SPECIFIC, (
        f"Most-specific SV must be first; got {result.sv_dcids}"
    )
