"""Tests for interpret.recall — SV detection and entity resolution."""
from __future__ import annotations

import asyncio

import pytest

from qre.engine.interpret import recall
from tests.fixtures import FakeGraph


def run(coro):
    return asyncio.run(coro)


class TestRecall:
    def test_dev_finance_sv_in_candidates(self):
        graph = FakeGraph()
        result = run(recall("health ODA grants", ["USA", "Ethiopia"], graph=graph,
                            raw_query="health ODA grants from USA to Ethiopia"))
        assert any("CRS_DAC" in sv for sv in result.candidate_svs)

    def test_entity_resolution_ethiopia(self):
        graph = FakeGraph()
        result = run(recall("health ODA grants to Ethiopia", ["Ethiopia"], graph=graph))
        assert result.resolved_entity_names.get("Ethiopia") == "country/ETH"

    def test_entity_resolution_usa(self):
        graph = FakeGraph()
        result = run(recall("health ODA grants from USA to Kenya", ["USA", "Kenya"], graph=graph))
        assert result.resolved_entity_names.get("USA") == "country/USA"
        assert result.resolved_entity_names.get("Kenya") == "country/KEN"

    def test_unknown_entity_not_in_resolved(self):
        graph = FakeGraph()
        result = run(recall("ODA to Atlantis", ["Atlantis"], graph=graph))
        assert "Atlantis" not in result.resolved_entity_names

    def test_no_entities(self):
        graph = FakeGraph()
        result = run(recall("health ODA grants to Kenya", [], graph=graph))
        assert result.resolved_entity_names == {}

    def test_graph_error_propagates(self):
        from qre.engine.errors import GraphInfraError
        graph = FakeGraph(raise_on_call=True)
        with pytest.raises(GraphInfraError):
            run(recall("health ODA grants", ["Ethiopia"], graph=graph))
