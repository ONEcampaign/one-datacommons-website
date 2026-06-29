"""Smoke tests for FakeGraph.

Verifies correct lookups, absent-dcid handling, raise_on_call mode,
count_observations, and the label-last-value rule. Offline only.
"""
import pytest

from qre.engine.errors import GraphInfraError
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
