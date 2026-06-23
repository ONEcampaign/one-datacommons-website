"""Tests for qre.engine.bind — slot-binding stage with FakeLLM.

Tests cover value, set, unbound, and absent binding states.
All tests replay from llm_responses.json (offline).
"""
from __future__ import annotations

import asyncio

from qre.engine.bind import SlotBindingDraft, bind
from tests.fixtures import FakeLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


# Dev-finance slot taxonomy for the three core constraint axes.
# Matches AXIS_OVERRIDES: scheme→what, purpose→how, recipient→where.
_DF_SLOT_TAXONOMY = {
    "what:DevelopmentFinanceScheme": [
        "ODAGrants",
        "OfficialDevelopmentAssistance",
        "ODALoans",
        "ODAPrivateSectorInstruments",
        "OtherOfficialFlows",
        "PrivateDevelopmentFinance",
        "ODAEquityInvestment",
    ],
    "how:DevelopmentFinancePurpose": [
        "DAC/Health",
        "DAC/BasicHealth",
        "DAC/Reproductivehealthcare",
        "DAC/STDcontrolincludingHIVAIDS",
        "DAC/Malariacontrol",
        "DAC/Healtheducation",
        "DAC/Medicaleducationtraining",
    ],
    "where:DevelopmentFinanceRecipient": [
        "country/ETH",
        "country/KEN",
        "country/USA",
        "country/GBR",
        "country/DEU",
        "country/FRA",
        "country/IND",
        "country/NRU",
    ],
}




def test_bind_value_health_oda_eth():
    """Value case: health ODA grants → scheme=ODAGrants, purpose=DAC/Health, where=ETH."""
    result = _run(bind(
        "health ODA grants",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    assert isinstance(result, list)
    assert len(result) == 3

    by_axis_prop = {(b.axis, b.property_dcid): b for b in result}

    scheme = by_axis_prop[("what", "DevelopmentFinanceScheme")]
    assert scheme.kind == "value"
    assert scheme.value_dcids == ["ODAGrants"]

    purpose = by_axis_prop[("how", "DevelopmentFinancePurpose")]
    assert purpose.kind == "value"
    assert purpose.value_dcids == ["DAC/Health"]

    recipient = by_axis_prop[("where", "DevelopmentFinanceRecipient")]
    assert recipient.kind == "value"
    assert recipient.value_dcids == ["country/ETH"]


def test_bind_value_hiv_aids_ken():
    """Value case: HIV/AIDS ODA grants to Kenya → purpose=DAC/STDcontrolincludingHIVAIDS."""
    result = _run(bind(
        "HIV/AIDS ODA grants to Kenya",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result}

    purpose = by_axis_prop[("how", "DevelopmentFinancePurpose")]
    assert purpose.kind == "value"
    assert "DAC/STDcontrolincludingHIVAIDS" in purpose.value_dcids

    recipient = by_axis_prop[("where", "DevelopmentFinanceRecipient")]
    assert recipient.kind == "value"
    assert "country/KEN" in recipient.value_dcids




def test_bind_set_education_oda_india():
    """Set case (df-10): no DAC/Education rollup → purpose binds to 2 education members."""
    result = _run(bind(
        "education ODA to India",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result}

    purpose = by_axis_prop[("how", "DevelopmentFinancePurpose")]
    assert purpose.kind == "set"
    assert set(purpose.value_dcids) == {"DAC/Healtheducation", "DAC/Medicaleducationtraining"}

    scheme = by_axis_prop[("what", "DevelopmentFinanceScheme")]
    assert scheme.kind == "value"
    assert "OfficialDevelopmentAssistance" in scheme.value_dcids

    recipient = by_axis_prop[("where", "DevelopmentFinanceRecipient")]
    assert recipient.kind == "value"
    assert "country/IND" in recipient.value_dcids




def test_bind_unbound_scheme_health_aid_kenya():
    """Unbound case (df-09): 'health aid' names no scheme → what slot is unbound."""
    result = _run(bind(
        "health aid to Kenya",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result}

    scheme = by_axis_prop[("what", "DevelopmentFinanceScheme")]
    assert scheme.kind == "unbound"
    assert scheme.value_dcids == []

    purpose = by_axis_prop[("how", "DevelopmentFinancePurpose")]
    assert purpose.kind == "value"
    assert "DAC/Health" in purpose.value_dcids

    recipient = by_axis_prop[("where", "DevelopmentFinanceRecipient")]
    assert recipient.kind == "value"
    assert "country/KEN" in recipient.value_dcids


_ABSENT_SLOT_TAXONOMY = {
    "how:healthFinancingSource": [
        "ExternalHealthFinancing",
        "DomesticPrivateHealthFinancing",
        "DomesticGeneralGovernmentHealthFinancing",
    ],
}


def test_bind_absent_irrelevant_property():
    """A property not applicable to the resolved series returns absent."""
    result = _run(bind(
        "health ODA grants",
        _ABSENT_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    assert len(result) == 1
    b = result[0]
    assert b.axis == "how"
    assert b.property_dcid == "healthFinancingSource"
    assert b.kind == "absent"
    assert b.value_dcids == []


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_slot_binding_draft_schema():
    """SlotBindingDraft is a valid pydantic model."""
    draft = SlotBindingDraft(
        axis="what",
        property_dcid="DevelopmentFinanceScheme",
        kind="value",
        value_dcids=["ODAGrants"],
    )
    assert draft.axis == "what"
    assert draft.kind == "value"
    assert draft.value_dcids == ["ODAGrants"]


def test_slot_binding_draft_unbound():
    """Unbound SlotBindingDraft has empty value_dcids."""
    draft = SlotBindingDraft(
        axis="what",
        property_dcid="DevelopmentFinanceScheme",
        kind="unbound",
        value_dcids=[],
    )
    assert draft.kind == "unbound"
    assert draft.value_dcids == []


def test_slot_binding_draft_absent():
    """Absent SlotBindingDraft has empty value_dcids."""
    draft = SlotBindingDraft(
        axis="how",
        property_dcid="healthFinancingSource",
        kind="absent",
        value_dcids=[],
    )
    assert draft.kind == "absent"
    assert draft.value_dcids == []
