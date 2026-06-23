"""Tests for shape.py: family_for and build_shape.

Verifies family detection, five-tuple correctness, axis override application,
and standard slots (when, source).
"""

import pytest

from qre.engine.families import (
    DEV_FINANCE_FAMILY,
    MEAS_DENOM_DCID,
    MEAS_PROP_DCID,
    MEAS_QUAL_DCID,
    POP_TYPE_DCID,
    PROP_PURPOSE,
    PROP_RECIPIENT,
    PROP_SCHEME,
    STAT_TYPE_DCID,
    Family,
)
from qre.engine.shape import build_shape, family_for


def test_family_for_dev_finance_sv() -> None:
    svs = ["ONE/CRS_DAC/Health-ODAGrants-ETH"]
    fam = family_for(svs)
    assert fam is not None
    assert fam.family_id == "dev_finance_crs_dac"


def test_family_for_multiple_devfinance_svs() -> None:
    svs = [
        "ONE/CRS_DAC/Health-ODAGrants-ETH",
        "ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-KEN",
    ]
    assert family_for(svs) is not None


def test_family_for_noisy_recall_one_match_is_enough() -> None:
    # detect may return non-family SVs alongside a real one
    svs = ["SomeTopicSV", "ONE/CRS_DAC/Health-ODAGrants-ETH", "AnotherNoisySV"]
    assert family_for(svs) is not None


def test_family_for_no_match_returns_none() -> None:
    svs = ["Count_Person_Male", "sdg/SH_STA_MORT.SEX--F"]
    assert family_for(svs) is None


def test_family_for_empty_returns_none() -> None:
    assert family_for([]) is None


def test_family_for_single_non_match_returns_none() -> None:
    assert family_for(["Count_Person"]) is None




def test_build_shape_five_tuple() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    assert shape.pop_type_dcid == POP_TYPE_DCID
    assert shape.meas_prop_dcid == MEAS_PROP_DCID
    assert shape.stat_type_dcid == STAT_TYPE_DCID
    assert shape.meas_qual_dcid == MEAS_QUAL_DCID  # None
    assert shape.meas_denom_dcid == MEAS_DENOM_DCID  # None


def test_build_shape_five_tuple_null_anchors() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    assert shape.meas_qual_dcid is None
    assert shape.meas_denom_dcid is None


def test_build_shape_label_and_id() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    assert shape.shape_id == "dev_finance_crs_dac"
    assert shape.label == "development finance flows"




def test_build_shape_has_five_slot_keys() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    assert len(shape.slot_keys) == 5


def test_build_shape_scheme_slot_is_what() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    scheme_slots = [k for k in shape.slot_keys if k.property_dcid == PROP_SCHEME]
    assert len(scheme_slots) == 1
    assert scheme_slots[0].axis == "what", (
        "DevelopmentFinanceScheme should be 'what' via AXIS_OVERRIDES"
    )


def test_build_shape_purpose_slot_is_how() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    purpose_slots = [k for k in shape.slot_keys if k.property_dcid == PROP_PURPOSE]
    assert len(purpose_slots) == 1
    assert purpose_slots[0].axis == "how"


def test_build_shape_recipient_slot_is_where() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    recipient_slots = [k for k in shape.slot_keys if k.property_dcid == PROP_RECIPIENT]
    assert len(recipient_slots) == 1
    assert recipient_slots[0].axis == "where", (
        "DevelopmentFinanceRecipient should be 'where' via AXIS_OVERRIDES"
    )


def test_build_shape_when_slot_present() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    when_slots = [k for k in shape.slot_keys if k.axis == "when"]
    assert len(when_slots) == 1
    assert when_slots[0].property_dcid is None


def test_build_shape_source_slot_present() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    source_slots = [k for k in shape.slot_keys if k.axis == "source"]
    assert len(source_slots) == 1
    assert source_slots[0].property_dcid is None


def test_build_shape_slot_axes_complete() -> None:
    shape = build_shape(DEV_FINANCE_FAMILY)
    axes = [k.axis for k in shape.slot_keys]
    assert "what" in axes
    assert "how" in axes
    assert "where" in axes
    assert "when" in axes
    assert "source" in axes




def test_build_shape_unknown_family_raises() -> None:
    unknown_family = Family(
        family_id="unknown_family",
        label="Unknown",
        pop_type_dcid="SomePop",
        meas_prop_dcid="SomeProp",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
    )
    with pytest.raises(ValueError, match="Unknown family"):
        build_shape(unknown_family)
