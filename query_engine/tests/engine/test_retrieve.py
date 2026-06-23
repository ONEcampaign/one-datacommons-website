"""Tests for retrieve.materialise SV materialization."""
from __future__ import annotations

from qre.engine.bind import SlotBindingDraft
from qre.engine.families import DEV_FINANCE_FAMILY
from qre.engine.retrieve import Materialised, NoDataDraft, materialise
from qre.engine.shape import build_shape
from tests.fixtures import FakeGraph


def _make_binding(axis, prop, kind, value_dcids=None):
    return SlotBindingDraft(
        axis=axis,
        property_dcid=prop,
        kind=kind,
        value_dcids=value_dcids or [],
    )


def _default_shape():
    return build_shape(DEV_FINANCE_FAMILY)


class TestMaterialiseValue:
    def test_value_binding_confirmed_sv(self):
        graph = FakeGraph()
        shape = _default_shape()
        bindings = [
            _make_binding("what", "DevelopmentFinanceScheme", "value", ["ODAGrants"]),
            _make_binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _make_binding("where", "DevelopmentFinanceRecipient", "value", ["country/ETH"]),
        ]
        result = materialise(shape, bindings, "country/ETH", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        assert "ONE/CRS_DAC/Health-ODAGrants-ETH" in result.sv_dcids
        assert result.has_data is True

    def test_value_binding_sv_node_absent(self):
        # NRU node exists but has 0 observations -> no_observations
        graph = FakeGraph()
        shape = _default_shape()
        bindings = [
            _make_binding("what", "DevelopmentFinanceScheme", "value", ["ODAGrants"]),
            _make_binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _make_binding("where", "DevelopmentFinanceRecipient", "value", ["country/NRU"]),
        ]
        result = materialise(shape, bindings, "country/NRU", "country/USA", graph=graph)
        # SV exists in graph_nodes but obs is empty -> no_observations
        assert isinstance(result, NoDataDraft)
        assert result.reason == "no_observations"


class TestMaterialiseSet:
    def test_set_binding_multiple_svs(self):
        graph = FakeGraph()
        shape = _default_shape()
        bindings = [
            _make_binding(
                "what", "DevelopmentFinanceScheme", "value", ["OfficialDevelopmentAssistance"]
            ),
            _make_binding(
                "how",
                "DevelopmentFinancePurpose",
                "set",
                ["DAC/Healtheducation", "DAC/Medicaleducationtraining"],
            ),
            _make_binding("where", "DevelopmentFinanceRecipient", "value", ["country/IND"]),
        ]
        result = materialise(shape, bindings, "country/IND", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        assert len(result.sv_dcids) == 2
        edu_sv = "ONE/CRS_DAC/Healtheducation-OfficialDevelopmentAssistance-IND"
        med_sv = "ONE/CRS_DAC/Medicaleducationtraining-OfficialDevelopmentAssistance-IND"
        assert edu_sv in result.sv_dcids
        assert med_sv in result.sv_dcids
        assert result.has_data is True


class TestMaterialiseUnbound:
    def test_unbound_scheme_returns_empty_sv_dcids(self):
        graph = FakeGraph()
        shape = _default_shape()
        bindings = [
            _make_binding("what", "DevelopmentFinanceScheme", "unbound"),
            _make_binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
            _make_binding("where", "DevelopmentFinanceRecipient", "value", ["country/KEN"]),
        ]
        result = materialise(shape, bindings, "country/KEN", "country/USA", graph=graph)
        assert isinstance(result, Materialised)
        assert result.sv_dcids == []
        assert result.has_data is True


class TestMaterialiseNoRecipient:
    def test_no_recipient_returns_no_data(self):
        graph = FakeGraph()
        shape = _default_shape()
        bindings = [
            _make_binding("what", "DevelopmentFinanceScheme", "value", ["ODAGrants"]),
            _make_binding("how", "DevelopmentFinancePurpose", "value", ["DAC/Health"]),
        ]
        result = materialise(shape, bindings, None, "country/USA", graph=graph)
        assert isinstance(result, NoDataDraft)
        assert result.reason == "variable_not_resolved"
