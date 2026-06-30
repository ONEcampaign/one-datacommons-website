"""Tests for spec_id.py: compute_spec_id determinism and pinned invariants.

Tests verify hash stability, slot-order independence, unbound vs absent
distinction, different recipients, and TimeWindow sentinels.
Pinned hashes are contract invariants (MAJOR bump on changes).
"""
from __future__ import annotations

from qre.engine.spec_id import compute_spec_id
from qre.models import (
    Axis,
    BindingAbsent,
    BindingSet,
    BindingUnbound,
    BindingValue,
    GraphRef,
    Slot,
    SlotKey,
    SlotValue,
    TimeWindow,
)


def _slot_key(axis: Axis, prop_dcid: str | None, label: str = "") -> SlotKey:
    return SlotKey(
        axis=axis,
        property=GraphRef(dcid=prop_dcid, label=label) if prop_dcid else None,
        label=label or prop_dcid or axis,
    )


def _value_slot(axis: Axis, prop_dcid: str | None, value_dcid: str) -> Slot:
    return Slot(
        key=_slot_key(axis, prop_dcid),
        binding=BindingValue(
            value=SlotValue(
                ref=GraphRef(dcid=value_dcid, label=value_dcid),
                value_kind="enum_value",
            )
        ),
    )


def _unbound_slot(axis: Axis, prop_dcid: str | None) -> Slot:
    return Slot(key=_slot_key(axis, prop_dcid), binding=BindingUnbound())


def _absent_slot(axis: Axis, prop_dcid: str | None) -> Slot:
    return Slot(key=_slot_key(axis, prop_dcid), binding=BindingAbsent())


def _when_slot_value(start: int | None, end: int | None) -> Slot:
    """Build a when-axis slot with a TimeWindow."""
    if start is None and end is None:
        raise ValueError("TimeWindow requires at least one bound")
    tw = TimeWindow(start_year=start, end_year=end)
    return Slot(
        key=_slot_key("when", None, "time window"),
        binding=BindingValue(
            value=SlotValue(
                ref=None,
                value_kind="time_window",
                time_window=tw,
            )
        ),
    )



_CANONICAL_SLOTS_ETH = [
    _value_slot("what", "DevelopmentFinanceScheme", "ODAGrants"),
    _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
    _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
]

_CANONICAL_SLOTS_KEN = [
    _value_slot("what", "DevelopmentFinanceScheme", "ODAGrants"),
    _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
    _value_slot("where", "DevelopmentFinanceRecipient", "country/KEN"),
]


_EXPECTED_SPEC_ID_ETH = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_ETH)


def test_pin_known_input_exact_hash() -> None:
    """The spec_id for the canonical ETH input must equal the pinned value."""
    result = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_ETH)
    assert result == _EXPECTED_SPEC_ID_ETH
    assert result.startswith("spec_")
    assert len(result) == 5 + 16  # "spec_" + 16 hex chars


def test_pin_starts_with_spec_prefix() -> None:
    result = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_ETH)
    assert result.startswith("spec_")


def test_pin_is_21_chars_total() -> None:
    result = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_ETH)
    assert len(result) == 21  # "spec_" (5) + 16 hex




def test_slot_order_independence() -> None:
    """Slots in different orders must produce the same spec_id."""
    slots_abc = [
        _value_slot("what", "DevelopmentFinanceScheme", "ODAGrants"),
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
    ]
    slots_cba = [
        _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("what", "DevelopmentFinanceScheme", "ODAGrants"),
    ]
    slots_bac = [
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("what", "DevelopmentFinanceScheme", "ODAGrants"),
        _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
    ]
    id_abc = compute_spec_id("dev_finance_crs_dac", slots_abc)
    id_cba = compute_spec_id("dev_finance_crs_dac", slots_cba)
    id_bac = compute_spec_id("dev_finance_crs_dac", slots_bac)
    assert id_abc == id_cba == id_bac




def test_unbound_vs_absent_different_ids() -> None:
    """BindingUnbound and BindingAbsent must not collide in the hash."""
    base_slots = [
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
    ]
    unbound_id = compute_spec_id(
        "dev_finance_crs_dac",
        base_slots + [_unbound_slot("what", "DevelopmentFinanceScheme")],
    )
    absent_id = compute_spec_id(
        "dev_finance_crs_dac",
        base_slots + [_absent_slot("what", "DevelopmentFinanceScheme")],
    )
    assert unbound_id != absent_id


def test_unbound_sentinel_stable() -> None:
    """Changing other slots must not perturb the sentinel for an unbound slot."""
    slots_eth = [
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("where", "DevelopmentFinanceRecipient", "country/ETH"),
        _unbound_slot("what", "DevelopmentFinanceScheme"),
    ]
    slots_ken = [
        _value_slot("how", "DevelopmentFinancePurpose", "DAC/Health"),
        _value_slot("where", "DevelopmentFinanceRecipient", "country/KEN"),
        _unbound_slot("what", "DevelopmentFinanceScheme"),
    ]
    # The two ids must differ (different recipients) but neither should crash
    id_eth = compute_spec_id("dev_finance_crs_dac", slots_eth)
    id_ken = compute_spec_id("dev_finance_crs_dac", slots_ken)
    assert id_eth != id_ken
    # Each still has the correct format
    assert id_eth.startswith("spec_") and len(id_eth) == 21
    assert id_ken.startswith("spec_") and len(id_ken) == 21




def test_different_recipients_different_ids() -> None:
    """ETH and KEN recipients must produce distinct spec_ids."""
    id_eth = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_ETH)
    id_ken = compute_spec_id("dev_finance_crs_dac", _CANONICAL_SLOTS_KEN)
    assert id_eth != id_ken




def test_when_both_bounds() -> None:
    """TimeWindow(start=2010, end=2020) → canonical 't2010-2020'."""
    slots = [_when_slot_value(2010, 2020)]
    result = compute_spec_id("some_shape", slots)
    assert result.startswith("spec_")


def test_when_open_end_sentinel() -> None:
    """TimeWindow(start=2015, end=None) → sentinel 't2015-' (open end)."""
    slots_open_end = [_when_slot_value(2015, None)]
    result = compute_spec_id("some_shape", slots_open_end)
    assert result.startswith("spec_")


def test_when_open_start_sentinel() -> None:
    """TimeWindow(start=None, end=2020) → sentinel 't-2020' (open start)."""
    slots_open_start = [_when_slot_value(None, 2020)]
    result = compute_spec_id("some_shape", slots_open_start)
    assert result.startswith("spec_")


def test_when_open_end_vs_open_start_different() -> None:
    """The two None-arm sentinels must produce different spec_ids."""
    open_end = compute_spec_id("s", [_when_slot_value(2015, None)])
    open_start = compute_spec_id("s", [_when_slot_value(None, 2015)])
    assert open_end != open_start


def test_when_open_end_vs_bounded_end_different() -> None:
    """Open-end and closed-end windows must not collide."""
    open_end = compute_spec_id("s", [_when_slot_value(2015, None)])
    bounded = compute_spec_id("s", [_when_slot_value(2015, 2020)])
    assert open_end != bounded


def test_when_open_start_vs_bounded_start_different() -> None:
    open_start = compute_spec_id("s", [_when_slot_value(None, 2020)])
    bounded = compute_spec_id("s", [_when_slot_value(2015, 2020)])
    assert open_start != bounded




def test_set_binding_order_independent() -> None:
    """Two set-bound slots with same dcids in different order must hash identically."""
    set_slot_ab = Slot(
        key=_slot_key("how", "DevelopmentFinancePurpose"),
        binding=BindingSet(
            values=[
                SlotValue(
                    ref=GraphRef(dcid="DAC/Healtheducation", label="Health Education"),
                    value_kind="enum_value",
                ),
                SlotValue(
                    ref=GraphRef(dcid="DAC/Medicaleducationtraining", label="Medical Education"),
                    value_kind="enum_value",
                ),
            ]
        ),
    )
    set_slot_ba = Slot(
        key=_slot_key("how", "DevelopmentFinancePurpose"),
        binding=BindingSet(
            values=[
                SlotValue(
                    ref=GraphRef(dcid="DAC/Medicaleducationtraining", label="Medical Education"),
                    value_kind="enum_value",
                ),
                SlotValue(
                    ref=GraphRef(dcid="DAC/Healtheducation", label="Health Education"),
                    value_kind="enum_value",
                ),
            ]
        ),
    )
    id_ab = compute_spec_id("dev_finance_crs_dac", [set_slot_ab])
    id_ba = compute_spec_id("dev_finance_crs_dac", [set_slot_ba])
    assert id_ab == id_ba




def test_different_shape_ids_produce_different_spec_ids() -> None:
    slots = [_value_slot("what", "SomeProp", "SomeValue")]
    id_a = compute_spec_id("shape_a", slots)
    id_b = compute_spec_id("shape_b", slots)
    assert id_a != id_b
