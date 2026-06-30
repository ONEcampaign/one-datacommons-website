"""Tests for qre.engine.bind — slot-binding stage with FakeLLM.

Tests cover value, set, unbound, and absent binding states.
All tests replay from llm_responses.json (offline).
"""
from __future__ import annotations

import asyncio

from qre.engine.bind import SlotBindingDraft, _BindOutput, bind
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
    result, _ = _run(bind(
        "health ODA grants",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    assert isinstance(result, _BindOutput)
    assert result.ask is None
    assert len(result.bindings) == 3

    by_axis_prop = {(b.axis, b.property_dcid): b for b in result.bindings}

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
    result, _ = _run(bind(
        "HIV/AIDS ODA grants to Kenya",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result.bindings}

    purpose = by_axis_prop[("how", "DevelopmentFinancePurpose")]
    assert purpose.kind == "value"
    assert "DAC/STDcontrolincludingHIVAIDS" in purpose.value_dcids

    recipient = by_axis_prop[("where", "DevelopmentFinanceRecipient")]
    assert recipient.kind == "value"
    assert "country/KEN" in recipient.value_dcids




def test_bind_set_education_oda_india():
    """Set case (df-10): no DAC/Education rollup → purpose binds to 2 education members."""
    result, _ = _run(bind(
        "education ODA to India",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result.bindings}

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
    result, _ = _run(bind(
        "health aid to Kenya",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    by_axis_prop = {(b.axis, b.property_dcid): b for b in result.bindings}

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
    result, _ = _run(bind(
        "health ODA grants",
        _ABSENT_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    assert len(result.bindings) == 1
    b = result.bindings[0]
    assert b.axis == "how"
    assert b.property_dcid == "healthFinancingSource"
    assert b.kind == "absent"
    assert b.value_dcids == []


# ---------------------------------------------------------------------------
# F1: off-topic variable → ask set → no_data(variable_not_resolved)
# ---------------------------------------------------------------------------


def test_bind_off_topic_returns_ask():
    """F1: completely off-topic variable → bind returns _BindOutput with ask set."""
    result, _ = _run(bind(
        "climate data in Germany",
        _DF_SLOT_TAXONOMY,
        llm=FakeLLM(),
    ))
    assert isinstance(result, _BindOutput)
    assert result.ask is not None
    assert len(result.ask) > 0
    # All bindings must be unbound when ask is set.
    for b in result.bindings:
        assert b.kind == "unbound", f"Expected unbound but got {b.kind!r} for slot {b.axis!r}"


def test_bind_ask_triggers_no_data_in_resolve_variable():
    """F1: when bind returns ask, resolve_variable returns no_data(variable_not_resolved)."""
    # Use a custom LLM that always returns ask to trigger the F1 path.
    import asyncio
    from qre.engine.regions import resolve_variable
    from tests.fixtures import FakeGraph

    class _AskLLM:
        """LLM that always sets ask on _BindOutput to simulate off-topic variable."""
        def generate_structured(self, *, prompt, system, schema):
            if schema.__name__ == "_BindOutput":
                return _BindOutput(
                    ask="Variable is completely off-topic.",
                    bindings=[
                        SlotBindingDraft(axis="what", property_dcid="DevelopmentFinanceScheme",
                                         kind="unbound", value_dcids=[]),
                        SlotBindingDraft(axis="how", property_dcid="DevelopmentFinancePurpose",
                                         kind="unbound", value_dcids=[]),
                        SlotBindingDraft(axis="where", property_dcid="DevelopmentFinanceRecipient",
                                         kind="unbound", value_dcids=[]),
                    ],
                ), None
            return FakeLLM().generate_structured(prompt=prompt, system=system, schema=schema)

    # Import the minimal dev-finance graph fixture from test_multi_recipient_binding.py to
    # re-use its node/detect/obs structure (health ODA grants → dev-finance shape).
    from tests.engine.test_multi_recipient_binding import _DETECT, _NODES, _OBS, _RESOLVE
    graph = FakeGraph(nodes=_NODES, detect=_DETECT, resolve=_RESOLVE, obs=_OBS)

    result = asyncio.run(
        resolve_variable(
            "health ODA grants",
            entities=["Kenya"],
            date_request=None,
            detect_query="health ODA grants",
            role_query="health ODA grants to Kenya",
            pac=True,
            graph=graph,
            llm=_AskLLM(),
            base_steps=[],
            base_timing={},
        )
    )
    assert result.status == "no_data"
    assert result.no_data_reason == "variable_not_resolved"


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
