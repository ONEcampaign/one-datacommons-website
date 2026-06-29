"""Tests for provenance source resolution (resolved_sources in ResolutionTrace).

Covers:
  - _build_source_refs: unit tests for the provenance DCID collection and label fetch.
  - Integration: resolved_sources is populated end-to-end in a definite response.
  - Omit path: facets with no provenanceId and an unresolvable importName are excluded.
"""
from __future__ import annotations

from qre.engine.graph import Facet
from qre.engine.regions import _build_source_refs
from qre.models import DefiniteResponse
from tests.engine._harness import make_request, offline_resolve
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_graph(*, nodes: dict | None = None, obs: dict | None = None) -> FakeGraph:
    """FakeGraph with minimal nodes and obs dicts (no detect/resolve needed for unit tests)."""
    return FakeGraph(
        nodes=nodes or {},
        obs=obs or {},
        detect={},
        resolve={},
    )


# ---------------------------------------------------------------------------
# Unit tests: _build_source_refs
# ---------------------------------------------------------------------------


class TestBuildSourceRefs:
    def test_provenance_id_confirmed(self):
        """A facet with a provenanceId that resolves yields one GraphRef."""
        graph = _minimal_graph(
            nodes={"dc/base/HumanCRS": {"label": "Human CRS"}},
        )
        facets = [
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=10,
                provenance_id="dc/base/HumanCRS",
                import_name="HumanCRS",
            )
        ]
        refs = _build_source_refs(facets, graph)
        assert len(refs) == 1
        assert refs[0].dcid == "dc/base/HumanCRS"
        assert refs[0].label == "Human CRS"

    def test_no_provenance_id_fallback_resolves(self):
        """A facet with no provenanceId but a resolvable dc/base/{importName} yields a ref."""
        graph = _minimal_graph(
            nodes={"dc/base/SomeImport": {"label": "Some Import Dataset"}},
        )
        facets = [
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=5,
                provenance_id=None,
                import_name="SomeImport",
            )
        ]
        refs = _build_source_refs(facets, graph)
        assert len(refs) == 1
        assert refs[0].dcid == "dc/base/SomeImport"
        assert refs[0].label == "Some Import Dataset"

    def test_no_provenance_id_fallback_unresolvable_omitted(self):
        """A facet with no provenanceId and an unresolvable importName is omitted."""
        graph = _minimal_graph(nodes={})
        facets = [
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=5,
                provenance_id=None,
                import_name="UnknownImport",
            )
        ]
        refs = _build_source_refs(facets, graph)
        assert refs == []

    def test_mixed_facets_only_confirmed_returned(self):
        """Only confirmed sources appear; the unresolvable facet is omitted."""
        graph = _minimal_graph(
            nodes={"dc/base/HumanCRS": {"label": "Human CRS"}},
        )
        facets = [
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=10,
                provenance_id="dc/base/HumanCRS",
                import_name="HumanCRS",
            ),
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=5,
                provenance_id=None,
                import_name="UnknownImport",
            ),
        ]
        refs = _build_source_refs(facets, graph)
        assert len(refs) == 1
        assert refs[0].dcid == "dc/base/HumanCRS"

    def test_deduplication_multiple_facets_same_source(self):
        """Multiple facets pointing to the same provenanceId yield exactly one ref."""
        graph = _minimal_graph(
            nodes={"dc/base/HumanCRS": {"label": "Human CRS"}},
        )
        facets = [
            Facet(
                earliest_date="2010",
                latest_date="2020",
                obs_count=10,
                provenance_id="dc/base/HumanCRS",
            ),
            Facet(
                earliest_date="2018",
                latest_date="2018",
                obs_count=1,
                provenance_id="dc/base/HumanCRS",
            ),
        ]
        refs = _build_source_refs(facets, graph)
        assert len(refs) == 1
        assert refs[0].dcid == "dc/base/HumanCRS"

    def test_empty_facets_returns_empty(self):
        """No facets yields no refs."""
        graph = _minimal_graph()
        assert _build_source_refs([], graph) == []

    def test_facets_with_no_provenance_fields_returns_empty(self):
        """Facets that carry neither provenanceId nor importName produce no refs."""
        graph = _minimal_graph()
        facets = [
            Facet(earliest_date="2010", latest_date="2020", obs_count=10),
            Facet(earliest_date="2015", latest_date="2020", obs_count=5),
        ]
        assert _build_source_refs(facets, graph) == []


# ---------------------------------------------------------------------------
# Integration test: resolved_sources populated end-to-end
# ---------------------------------------------------------------------------


class TestResolvedSourcesEndToEnd:
    def test_definite_response_has_resolved_sources(self):
        """After a full pipeline run with provenance data in the fixture, resolved_sources
        contains the expected GraphRef for dc/base/HumanCRS."""
        # df-01 scenario: "health ODA grants from USA to Ethiopia"
        # The fixture entry ONE/CRS_DAC/Health-ODAGrants-ETH|country/USA now carries
        # provenanceId=dc/base/HumanCRS, and dc/base/HumanCRS is in graph_nodes.json.
        result = offline_resolve(
            make_request("health ODA grants from USA to Ethiopia", pac=True)
        )
        inner = result.root
        assert inner.status == "definite"
        assert isinstance(inner, DefiniteResponse)
        sources = inner.interpretation.resolution.resolved_sources
        assert len(sources) >= 1
        dcids = {s.dcid for s in sources}
        assert "dc/base/HumanCRS" in dcids
        labels = {s.dcid: s.label for s in sources}
        assert labels["dc/base/HumanCRS"] == "Human CRS"
