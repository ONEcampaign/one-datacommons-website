"""Regression tests for the unbound-scheme path in retrieve.materialise.

Covers three bugs in the unbound-scheme block:
- Purpose SET probes across all purpose dcids, not just DAC/Health
- Empty probe returns NoDataDraft(reason="no_observations")
- Successful probe returns Materialised with real facets and coverage breadth
"""
from __future__ import annotations

from qre.engine.bind import SlotBindingDraft
from qre.engine.families import DEV_FINANCE_FAMILY
from qre.engine.retrieve import Materialised, NoDataDraft, materialise
from qre.engine.shape import build_shape
from tests.fixtures import FakeGraph


def _binding(
    axis: str, prop: str, kind: str, value_dcids: list[str] | None = None
) -> SlotBindingDraft:
    return SlotBindingDraft(
        axis=axis,
        property_dcid=prop,
        kind=kind,
        value_dcids=value_dcids or [],
    )


def _shape():
    return build_shape(DEV_FINANCE_FAMILY)


# Purpose SET: probes across all purpose dcids


class TestUnboundSchemePurposeSetProbe:
    """When scheme is unbound and purpose is a SET, all purpose dcids are probed."""

    def test_purpose_set_probes_across_set_not_dac_health(self):
        nodes = {}
        obs = {
            "ONE/CRS_DAC/Healtheducation-ODAGrants-ETH|country/USA": [
                {"earliestDate": "2005", "latestDate": "2024", "obsCount": 120}
            ],
            "ONE/CRS_DAC/Medicaleducationtraining-ODAGrants-ETH|country/USA": [
                {"earliestDate": "2010", "latestDate": "2024", "obsCount": 90}
            ],
        }
        graph = FakeGraph(nodes=nodes, obs=obs, detect={}, resolve={})
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding(
                "how",
                "DevelopmentFinancePurpose",
                "set",
                ["DAC/Healtheducation", "DAC/Medicaleducationtraining"],
            ),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/ETH"]),
        ]
        result = materialise(_shape(), bindings, "country/ETH", "country/USA", graph=graph)
        assert isinstance(result, Materialised), f"expected Materialised, got {result}"
        assert result.has_data is True
        assert result.sv_dcids == []

    def test_purpose_set_old_code_would_fail(self):
        nodes = {}
        obs = {
            "ONE/CRS_DAC/Healtheducation-ODAGrants-ETH|country/USA": [
                {"earliestDate": "2005", "latestDate": "2024", "obsCount": 80}
            ],
        }
        graph = FakeGraph(nodes=nodes, obs=obs, detect={}, resolve={})
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding(
                "how",
                "DevelopmentFinancePurpose",
                "set",
                ["DAC/Healtheducation"],
            ),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/ETH"]),
        ]
        result = materialise(_shape(), bindings, "country/ETH", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        assert result.has_data is True


# Empty probe: returns NoDataDraft


class TestUnboundSchemeEmptyProbeNoData:
    """When probe finds no observations, return NoDataDraft(reason="no_observations")."""

    def test_empty_obs_returns_no_data_draft(self):
        graph = FakeGraph()
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/NRU"]),
        ]
        result = materialise(_shape(), bindings, "country/NRU", "country/USA", graph=graph)
        assert isinstance(result, NoDataDraft), f"expected NoDataDraft, got {result}"
        assert result.reason == "no_observations"

    def test_all_zero_obs_count_returns_no_data_draft(self):
        nodes = {}
        obs = {
            "ONE/CRS_DAC/Health-ODAGrants-NRU|country/USA": [
                {"earliestDate": None, "latestDate": None, "obsCount": 0}
            ],
        }
        graph = FakeGraph(nodes=nodes, obs=obs, detect={}, resolve={})
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/NRU"]),
        ]
        result = materialise(_shape(), bindings, "country/NRU", "country/USA", graph=graph)
        assert isinstance(result, NoDataDraft)
        assert result.reason == "no_observations"


# Successful probe: returns Materialised with real facets and coverage


class TestUnboundSchemeSuccessHasRealFacets:
    """On the success path, facets and coverage breadth reflect actual probe results."""

    def test_success_returns_non_empty_facets(self):
        graph = FakeGraph()
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/KEN"]),
        ]
        result = materialise(_shape(), bindings, "country/KEN", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        assert result.has_data is True
        assert result.sv_dcids == []
        assert len(result.facets) > 0
        assert any(f.obs_count > 0 for f in result.facets)

    def test_success_coverage_breadth_is_non_zero(self):
        graph = FakeGraph()
        bindings = [
            _binding("what", "DevelopmentFinanceScheme", "unbound"),
            _binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _binding("where", "DevelopmentFinanceRecipient", "value", ["country/KEN"]),
        ]
        result = materialise(_shape(), bindings, "country/KEN", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        coverage = result.coverage
        assert coverage.has_data is True
        non_zero_dims = [d for d in coverage.dimensions if d.count > 0]
        assert len(non_zero_dims) > 0, f"expected non-zero coverage dims, got {coverage.dimensions}"
