"""Tests for BindingSet on dev-finance two-recipient directional queries.

Covers the _has_constraint_slots (dev-finance) code path only. The BindingSet
mechanism lives inside that branch and is not added to the standard else-branch.

Tests cover: where_binding emits kind='set' for multi-recipient, spec_id is
order-independent, partial grounding returns BindingValue (not BindingUnbound),
and probe_donor fallback fires once per SV (not once per recipient).
"""
from __future__ import annotations

import asyncio

from qre.engine.bind import SlotBindingDraft, _BindOutput
from qre.engine.families.dev_finance import PROP_PURPOSE, PROP_RECIPIENT, PROP_SCHEME
from qre.engine.regions import RegionResult, resolve_variable
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_DETECT_QUERY = "health ODA grants"
_ROLE_QUERY = "health ODA grants to Kenya and to Uganda"

# Minimal dev-finance five-tuple + slot-property + entity nodes for the tests.
# Health-ODAGrants-UGA is present here (full-grounding test) and
# absent in the partial-grounding variant (_NODES_PARTIAL below).
_NODES: dict = {
    # Five-tuple
    "DevelopmentFinance": {"label": "Development Finance"},
    "DevelopmentFinanceFlow": {"label": "Development Finance Flow"},
    "measuredValue": {"label": "Measured Value"},
    # Constraint property refs
    "DevelopmentFinanceScheme": {"label": "Development Finance Scheme"},
    "DevelopmentFinancePurpose": {"label": "Development Finance Purpose"},
    "DevelopmentFinanceRecipient": {"label": "Development Finance Recipient"},
    # Role refs (needed for _ground_answer)
    "observationAbout": {"label": "observation about"},
    # Slot value refs (needed to ground the what/how slots)
    "ODAGrants": {"label": "Official Development Assistance Grants"},
    "DAC/Health": {"label": "Health (Total)"},
    # Entity refs
    "country/KEN": {"label": "Kenya", "type": "Country"},
    "country/UGA": {"label": "Uganda", "type": "Country"},
    # Confirmed SV nodes (node_label must return non-None for _construct_resolve to confirm)
    "ONE/CRS_DAC/Health-ODAGrants-KEN": {
        "label": "ONE CRS DAC Health ODA Grants KEN",
        "type": "StatisticalVariable",
        "arcs": {
            "typeOf": {"nodes": [{"dcid": "StatisticalVariable"}]},
            "populationType": {"nodes": [{"dcid": "DevelopmentFinance"}]},
            "measuredProperty": {"nodes": [{"dcid": "DevelopmentFinanceFlow"}]},
            "statType": {"nodes": [{"dcid": "measuredValue"}]},
            "DevelopmentFinanceScheme": {"nodes": [{"dcid": "ODAGrants"}]},
            "DevelopmentFinancePurpose": {"nodes": [{"dcid": "DAC/Health"}]},
            "DevelopmentFinanceRecipient": {"nodes": [{"dcid": "country/KEN"}]},
            # constraintProperties is required for read_constraints / derive_shapes
            # to recognise the three constraint slots and build a non-empty slot_taxonomy.
            "constraintProperties": {"nodes": [
                {"dcid": "DevelopmentFinanceScheme"},
                {"dcid": "DevelopmentFinancePurpose"},
                {"dcid": "DevelopmentFinanceRecipient"},
            ]},
        },
    },
    "ONE/CRS_DAC/Health-ODAGrants-UGA": {
        "label": "ONE CRS DAC Health ODA Grants UGA",
        "type": "StatisticalVariable",
        "arcs": {
            "typeOf": {"nodes": [{"dcid": "StatisticalVariable"}]},
            "populationType": {"nodes": [{"dcid": "DevelopmentFinance"}]},
            "measuredProperty": {"nodes": [{"dcid": "DevelopmentFinanceFlow"}]},
            "statType": {"nodes": [{"dcid": "measuredValue"}]},
            "DevelopmentFinanceScheme": {"nodes": [{"dcid": "ODAGrants"}]},
            "DevelopmentFinancePurpose": {"nodes": [{"dcid": "DAC/Health"}]},
            "DevelopmentFinanceRecipient": {"nodes": [{"dcid": "country/UGA"}]},
            "constraintProperties": {"nodes": [
                {"dcid": "DevelopmentFinanceScheme"},
                {"dcid": "DevelopmentFinancePurpose"},
                {"dcid": "DevelopmentFinanceRecipient"},
            ]},
        },
    },
}

# Partial-grounding variant: UGA's SV is absent so node_label returns None for it,
# and country/UGA has no label so graphrefs drops it from the where slot grounding.
_NODES_PARTIAL: dict = {k: v for k, v in _NODES.items() if "UGA" not in k}

_DETECT: dict = {_DETECT_QUERY: {"svs": ["ONE/CRS_DAC/Health-ODAGrants-KEN"], "entities": []}}
_RESOLVE: dict = {"Kenya": "country/KEN", "Uganda": "country/UGA"}

# Probe donor is country/USA (dev_finance._DEFAULT_PROBE_DONOR).
_OBS: dict = {
    "ONE/CRS_DAC/Health-ODAGrants-KEN|country/USA": [
        {"earliestDate": "2000", "latestDate": "2022", "obsCount": 100}
    ],
    "ONE/CRS_DAC/Health-ODAGrants-UGA|country/USA": [
        {"earliestDate": "2000", "latestDate": "2022", "obsCount": 80}
    ],
}


class _FakeBindLLM:
    """Returns ODAGrants + DAC/Health bindings; the where override in regions.py
    handles the recipient regardless of what the LLM would return."""

    def generate_structured(self, *, prompt, system, schema):
        name = schema.__name__
        if name == "_BindOutput":
            return _BindOutput(bindings=[
                SlotBindingDraft(
                    axis="what",
                    property_dcid=PROP_SCHEME,
                    kind="value",
                    value_dcids=["ODAGrants"],
                ),
                SlotBindingDraft(
                    axis="how",
                    property_dcid=PROP_PURPOSE,
                    kind="value",
                    value_dcids=["DAC/Health"],
                ),
            ]), None
        raise AssertionError(f"unexpected schema {name!r} in _FakeBindLLM")


def _run(entities: list[str], *, nodes: dict = _NODES) -> RegionResult:
    """Run resolve_variable with the two-recipient dev-finance scenario."""
    graph = FakeGraph(nodes=nodes, detect=_DETECT, resolve=_RESOLVE, obs=_OBS)
    return asyncio.run(
        resolve_variable(
            "health ODA grants",
            entities=entities,
            date_request=None,
            detect_query=_DETECT_QUERY,
            role_query=_ROLE_QUERY,
            pac=True,
            graph=graph,
            llm=_FakeBindLLM(),
            base_steps=[],
            base_timing={},
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_two_recipient_where_binding_is_set():
    """Dev-finance two-recipient directional query produces kind='set' where-binding.

    Both recipients ground, the spec materialises, and the where slot carries
    BindingSet with both country dcids.
    """
    result = _run(["Kenya", "Uganda"])

    assert result.status == "definite", f"expected definite, got {result.status!r}"
    spec = result.specs[0]
    where = next(s for s in spec.slots if s.key.axis == "where")
    assert where.binding.kind == "set", (
        f"expected kind='set' on where slot; got {where.binding.kind!r}"
    )
    where_dcids: set[str] = set()
    for v in where.binding.values:
        assert v.ref is not None
        where_dcids.add(v.ref.dcid)
    assert "country/KEN" in where_dcids
    assert "country/UGA" in where_dcids


def test_two_recipient_spec_id_is_stable():
    """spec_id is order-independent: same hash regardless of recipient detection order.

    BindingSet dcids are sorted before hashing, so the spec_id is identical
    regardless of the order in which entities are detected.
    """
    result_ku = _run(["Kenya", "Uganda"])
    result_uk = _run(["Uganda", "Kenya"])

    assert result_ku.status == "definite"
    assert result_uk.status == "definite"
    assert result_ku.specs[0].spec_id == result_uk.specs[0].spec_id, (
        "spec_id should not depend on recipient detection order"
    )


def test_partial_grounding_is_binding_value_not_unbound():
    """When one of two recipients fails graphrefs on the where-axis, return BindingValue.

    The where-axis BindingSet guard is axis-aware. With only one grounded
    recipient value, build_slot returns BindingValue rather than BindingUnbound,
    so the spec_id stays stable and the slot is not blanked.
    """
    # _NODES_PARTIAL has no country/UGA and no ONE/CRS_DAC/Health-ODAGrants-UGA,
    # so only Kenya's SV and entity confirm; Uganda's drop silently.
    result = _run(["Kenya", "Uganda"], nodes=_NODES_PARTIAL)

    assert result.status == "definite", (
        f"expected definite even on partial grounding; got {result.status!r}"
    )
    spec = result.specs[0]
    where = next(s for s in spec.slots if s.key.axis == "where")
    assert where.binding.kind == "value", (
        f"expected kind='value' on partial grounding; got {where.binding.kind!r}"
    )
    where_value_ref = where.binding.value.ref
    assert where_value_ref is not None
    assert where_value_ref.dcid == "country/KEN"


def test_fallback_path_donor_facets_not_n_fold_inflated():
    """probe_donor fallback fires once per SV, not once per recipient.

    With N recipients and donor-keyed observations (dev-finance), the probe_donor
    fallback must fire at most once per SV. Donor-keyed observations must not be
    counted N-fold.

    This test covers graph_confirm_resolve (fallback path reached when scheme is
    bound but purpose is unbound). Asserts that obs_count in the returned
    Materialised equals the true donor count, not the N-fold inflated value.
    """
    from qre.engine.discover import graph_confirm_resolve
    from qre.engine.retrieve import Materialised
    from qre.engine.shape import ShapeDraft

    sv_dcid = "ONE/CRS_DAC/Health-ODAGrants-KEN"
    sv_arcs = {
        "typeOf": {"nodes": [{"dcid": "StatisticalVariable"}]},
        "populationType": {"nodes": [{"dcid": "DevelopmentFinance"}]},
        "measuredProperty": {"nodes": [{"dcid": "DevelopmentFinanceFlow"}]},
        "statType": {"nodes": [{"dcid": "measuredValue"}]},
        "DevelopmentFinanceScheme": {"nodes": [{"dcid": "ODAGrants"}]},
        "DevelopmentFinancePurpose": {"nodes": [{"dcid": "DAC/Health"}]},
        "DevelopmentFinanceRecipient": {"nodes": [{"dcid": "country/KEN"}]},
        "constraintProperties": {"nodes": [
            {"dcid": "DevelopmentFinanceScheme"},
            {"dcid": "DevelopmentFinancePurpose"},
            {"dcid": "DevelopmentFinanceRecipient"},
        ]},
    }

    # Only donor-keyed observations exist; probing recipients returns empty.
    # _DEFAULT_PROBE_DONOR is country/USA (the key used by the fallback).
    obs = {
        f"{sv_dcid}|country/USA": [
            {"earliestDate": "2000", "latestDate": "2022", "obsCount": 100}
        ]
    }
    graph = FakeGraph(nodes={}, detect={}, resolve={}, obs=obs)

    # Scheme bound, purpose absent from bindings; _construct_resolve returns None
    # for this combination, so graph_confirm_resolve is the active path.
    # Where-axis carries two recipients (the N=2 case that exposed the N-fold bug).
    bindings = [
        SlotBindingDraft(
            axis="what",
            property_dcid=PROP_SCHEME,
            kind="value",
            value_dcids=["ODAGrants"],
        ),
        SlotBindingDraft(
            axis="where",
            property_dcid=PROP_RECIPIENT,
            kind="set",
            value_dcids=["country/KEN", "country/UGA"],
        ),
        # No purpose binding: purpose is absent from bound_values, so the SV
        # passes the constraint filter. The per-recipient probes return empty
        # (donor-keyed observations), triggering the probe_donor fallback.
    ]

    shape = ShapeDraft(
        shape_id="test_cr1",
        label="test shape",
        pop_type_dcid="DevelopmentFinance",
        meas_prop_dcid="DevelopmentFinanceFlow",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
        slot_keys=(),
        sv_arc_facts={sv_dcid: sv_arcs},
    )

    result = graph_confirm_resolve(
        shape=shape,
        bindings=bindings,
        recipient_dcid="country/KEN",
        donor_dcid=None,  # uses _DEFAULT_PROBE_DONOR = country/USA
        graph=graph,
        date_request=None,
    )

    assert isinstance(result, Materialised), (
        f"expected Materialised; got {type(result).__name__}: "
        f"{getattr(result, 'reason', '?')!r}"
    )
    # The probe_donor fallback fires once per SV (not once per recipient), so
    # all_facets has one Facet with obs_count=100.
    total_obs = sum(f.obs_count for f in result.facets)
    assert total_obs == 100, (
        f"expected total obs_count=100 (one donor probe); got {total_obs}. "
        "N-fold inflation means probe_donor is still inside the recipient loop"
    )
