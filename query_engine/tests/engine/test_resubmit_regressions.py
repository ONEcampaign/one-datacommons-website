"""Regression tests for two Path C bugs fixed in _resolve_named_family_resubmit and
_resolve_standard_promote.

Finding A: multi-recipient BindingSet where-slot left recipient_dcid=None, causing
variable_not_resolved; and the roles dict omitted non-first recipients so the spec's
entity list was incomplete.

Finding B: the seventh return element of _ground_answer (source_refs) was bound to
_source_refs in both resubmit functions and never forwarded to build_spec, so
resolved_sources was always [].

All tests are offline (FakeGraph, no LLM).
"""
from __future__ import annotations

from qre.engine.families import rule_for_shape_id
from qre.engine.regions import resolve_spec_resubmit
from qre.models import (
    Axis,
    BindingSet,
    BindingValue,
    GraphRef,
    Slot,
    SlotKey,
    SlotValue,
    SpecResubmitInput,
)
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gref(dcid: str, label: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=label)


def _value_slot(
    axis: Axis,
    prop_dcid: str,
    prop_label: str,
    val_dcid: str,
    val_label: str,
) -> Slot:
    return Slot(
        key=SlotKey(
            axis=axis,
            property=_gref(prop_dcid, prop_label),
            label=prop_label,
        ),
        binding=BindingValue(
            value=SlotValue(
                ref=_gref(val_dcid, val_label),
                value_kind="enum_value",
            )
        ),
    )


# Standard dev-finance what/how slots (Health ODA Grants)
_SCHEME_SLOT = _value_slot(
    "what", "DevelopmentFinanceScheme", "finance scheme",
    "ODAGrants", "Official Development Assistance Grants",
)
_PURPOSE_SLOT = _value_slot(
    "how", "DevelopmentFinancePurpose", "sector/purpose",
    "DAC/Health", "Health (Total)",
)

# GDP standard SV/shape constants (mirrors test_path_c.py)
_GDP_SV_DCID = "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
_GDP_SHAPE_ID = "economicactivity_amount_measuredvalue_nominal"
_GDP_ENTITY_DCID = "country/IND"


# ---------------------------------------------------------------------------
# Finding A: multi-recipient BindingSet
# ---------------------------------------------------------------------------


class TestMultiRecipientBindingSet:
    """BindingSet on the where-slot must resolve to definite with all entities in the Spec."""

    def _make_bindingset_where_slot(self, dcids: list[str]) -> Slot:
        """Build a where-slot BindingSet covering the given country dcids."""
        labels = {
            "country/ETH": "Ethiopia",
            "country/KEN": "Kenya",
        }
        return Slot(
            key=SlotKey(
                axis="where",
                property=_gref("DevelopmentFinanceRecipient", "Development Finance Recipient"),
                label="recipient",
            ),
            binding=BindingSet(
                values=[
                    SlotValue(
                        ref=_gref(dcid, labels.get(dcid, dcid)),
                        value_kind="entity",
                    )
                    for dcid in dcids
                ]
            ),
        )

    def test_status_definite_not_variable_not_resolved(self):
        """BindingSet where-slot must not leave recipient_dcid=None → no variable_not_resolved."""
        where_slot = self._make_bindingset_where_slot(["country/ETH", "country/KEN"])
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT, where_slot],
            entity_dcids=None,
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite", (
            f"expected definite but got {region.status!r} "
            f"(no_data_reason={region.no_data_reason!r})"
        )

    def test_both_countries_in_entity_list(self):
        """Both ETH and KEN must appear in the returned Spec's entity list."""
        where_slot = self._make_bindingset_where_slot(["country/ETH", "country/KEN"])
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT, where_slot],
            entity_dcids=None,
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite"
        entity_dcids = {e.ref.dcid for e in region.specs[0].entities}
        assert "country/ETH" in entity_dcids, f"ETH missing from entities: {entity_dcids}"
        assert "country/KEN" in entity_dcids, f"KEN missing from entities: {entity_dcids}"

    def test_entity_dcids_takes_precedence_over_bindingset(self):
        """entity_dcids[0] still takes precedence over the where-slot BindingSet."""
        where_slot = self._make_bindingset_where_slot(["country/ETH", "country/KEN"])
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT, where_slot],
            entity_dcids=["country/ETH"],  # explicit override
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        # Must still resolve (entity_dcids path is unaffected by the fix).
        assert region.status == "definite"


# ---------------------------------------------------------------------------
# Finding B: resolved_sources propagated on resubmit
# ---------------------------------------------------------------------------


class TestResubmitResolvedSources:
    """source_refs must be forwarded to build_spec in both resubmit functions."""

    def test_named_family_provenance_non_empty(self):
        """Named-family resubmit: facets with provenanceId → resolved_sources non-empty.

        ONE/CRS_DAC/Health-ODAGrants-ETH|country/USA in graph_obs.json carries
        provenanceId='dc/base/HumanCRS', and dc/base/HumanCRS is in graph_nodes.json.
        """
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT],
            entity_dcids=["country/ETH"],
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite"
        sources = region.specs[0].resolution.resolved_sources
        assert sources, "resolved_sources must not be empty when facets carry a provenanceId"
        assert any(s.dcid == "dc/base/HumanCRS" for s in sources), (
            f"expected dc/base/HumanCRS in resolved_sources, got {[s.dcid for s in sources]}"
        )

    def test_standard_promote_no_provenance_empty_sources(self):
        """Standard promote: observations with no provenanceId → resolved_sources == [].

        Amount_EconomicActivity_GrossDomesticProduction_Nominal|country/IND in
        graph_obs.json has no provenanceId, so resolved_sources must be [].
        """
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_GDP_ENTITY_DCID],
        )
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert region.status == "definite"
        assert region.specs[0].resolution.resolved_sources == [], (
            "resolved_sources must be [] when no provenanceId is present in facets"
        )

    def test_named_family_custom_no_provenance_empty_sources(self):
        """Named-family resubmit: custom obs with no provenanceId → resolved_sources == [].

        Builds a FakeGraph with only the observation entries needed, stripped of
        provenanceId, to confirm the no-provenance path returns [].
        """
        import json
        from pathlib import Path

        nodes = json.loads(
            (Path(__file__).parent.parent / "fixtures" / "graph_nodes.json").read_text()
        )
        # Same observation key as default fixture but no provenanceId / importName.
        custom_obs = {
            "ONE/CRS_DAC/Health-ODAGrants-ETH|country/USA": [
                {"earliestDate": "2000", "latestDate": "2022", "obsCount": 10}
            ]
        }
        graph = FakeGraph(nodes=nodes, obs=custom_obs)

        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT],
            entity_dcids=["country/ETH"],
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=graph)
        assert region.status == "definite"
        assert region.specs[0].resolution.resolved_sources == [], (
            "resolved_sources must be [] when facets carry no provenanceId"
        )
