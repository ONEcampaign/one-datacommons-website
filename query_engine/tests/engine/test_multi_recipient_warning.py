"""Tests for MULTI_RECIPIENT_TRUNCATED warning in resolve_variable.

MULTI_RECIPIENT_TRUNCATED is emitted when multiple recipient entities are
detected: either directional (multiple "to" prepositions) or bare-entity
(multiple entities with no directional prepositions).

resolve_variable is called directly (async; wrapped with asyncio.run) using
an inline FakeGraph on a STANDARD-family scenario so the LLM bind is skipped.
"""
from __future__ import annotations

import asyncio
import logging

from qre.engine.regions import MULTI_RECIPIENT_TRUNCATED, decide_multi_recipient, resolve_variable
from qre.models import Warning
from tests.fixtures import FakeGraph, FakeLLM

# A single standard-family SV (non-CRS_DAC prefix so it routes to STANDARD_RULE).
# Arc structure satisfies derive_shapes: populationType non-empty, measuredProperty present.
_SV = "Count_Person"

_NODES: dict = {
    _SV: {
        "label": "Count Person",
        "type": "StatisticalVariable",
        "arcs": {
            "typeOf": {"nodes": [{"dcid": "StatisticalVariable"}]},
            "name": {"nodes": [{"value": "Count Person"}]},
            "populationType": {"nodes": [{"dcid": "Person"}]},
            "measuredProperty": {"nodes": [{"dcid": "count"}]},
            "statType": {"nodes": [{"dcid": "measuredValue"}]},
        },
    },
}

# detect_query key used for both test cases; maps to the single standard SV.
_DETECT_QUERY = "exports"
_DETECT: dict = {_DETECT_QUERY: {"svs": [_SV], "entities": []}}
_RESOLVE: dict = {"Kenya": "country/KEN", "Uganda": "country/UGA"}


def _run(variable: str, role_query: str, entities: list[str]):
    """Run resolve_variable with an inline FakeGraph and no obs (forces no_data)."""
    graph = FakeGraph(nodes=_NODES, detect=_DETECT, resolve=_RESOLVE, obs={})
    return asyncio.run(
        resolve_variable(
            variable,
            entities=entities,
            date_request=None,
            detect_query=_DETECT_QUERY,
            role_query=role_query,
            pac=True,
            graph=graph,
            llm=FakeLLM(),
            base_steps=[],
            base_timing={},
        )
    )


def test_standard_directional_two_recipient_warns():
    """Standard directional two 'to' recipients re-trigger MULTI_RECIPIENT_TRUNCATED.

    The BindingSet fix that carries every recipient only applies on the
    dev-finance constraint-slot branch. On the standard family path the where
    binding stays scalar and truncates to to_dcids[0], so the warning must fire
    to keep the drop loud.
    """
    result = _run(
        "exports",
        role_query="exports to Kenya and to Uganda",
        entities=["Kenya", "Uganda"],
    )
    assert any(w.code == MULTI_RECIPIENT_TRUNCATED for w in result.warnings)


def test_multi_bare_entity_warns():
    """Two bare entities with no directional prepositions trigger MULTI_RECIPIENT_TRUNCATED.

    "exports Kenya Uganda" has no "from"/"to" tokens, so both entities get
    SubjectRole and the to_dcids list stays empty.  The bare-entity condition fires
    because recipient_dcid is None and resolved_entity_names has two entries.
    """
    result = _run(
        "exports",
        role_query="exports Kenya Uganda",
        entities=["Kenya", "Uganda"],
    )
    assert any(w.code == MULTI_RECIPIENT_TRUNCATED for w in result.warnings)


# ---------------------------------------------------------------------------
# F23: decide_multi_recipient helper unit tests
# ---------------------------------------------------------------------------


def test_decide_multi_recipient_near_miss_emits_debug(caplog):
    """F23: decide_multi_recipient emits DEBUG when multi-recipient is handled by constraint slots."""
    warnings_out: list[Warning] = []
    with caplog.at_level(logging.DEBUG, logger="qre.engine.regions"):
        recipient, effective, conditions = decide_multi_recipient(
            ["country/ETH", "country/KEN"],
            True,  # has_constraint_slots → dev-finance path, no warning, emit DEBUG
            warnings=warnings_out,
        )

    assert recipient == "country/ETH"
    assert effective == ["country/ETH", "country/KEN"]
    assert not warnings_out  # no warning emitted
    assert "MULTI_RECIPIENT near-miss" in caplog.text


def test_decide_multi_recipient_standard_path_emits_warning():
    """F23: decide_multi_recipient emits MULTI_RECIPIENT_TRUNCATED for standard path."""
    warnings_out: list[Warning] = []
    recipient, effective, conditions = decide_multi_recipient(
        ["country/ETH", "country/KEN"],
        False,  # not has_constraint_slots → standard path, emit warning
        warnings=warnings_out,
    )
    assert recipient == "country/ETH"
    assert effective == ["country/ETH"]  # truncated to first recipient
    assert any(w.code == MULTI_RECIPIENT_TRUNCATED for w in warnings_out)


def test_decide_multi_recipient_conditions_trace():
    """F23: conditions list tracks which gates were evaluated."""
    warnings_out: list[Warning] = []
    _, _, conditions = decide_multi_recipient(
        ["country/ETH", "country/KEN"],
        True,
        warnings=warnings_out,
    )
    conditions_dict = dict(conditions)
    assert conditions_dict["multi_directional"] is True
    assert conditions_dict["has_constraint_slots"] is True


def test_decide_multi_recipient_single_recipient_no_warning():
    """F23: single recipient (no multi) emits no warning regardless of constraint slots."""
    warnings_out: list[Warning] = []
    recipient, effective, _ = decide_multi_recipient(
        ["country/ETH"],
        False,
        warnings=warnings_out,
    )
    assert recipient == "country/ETH"
    assert effective == ["country/ETH"]
    assert not warnings_out


def test_decide_multi_recipient_empty_no_data():
    """F23: empty to_dcids returns (None, [], conditions)."""
    warnings_out: list[Warning] = []
    recipient, effective, conditions = decide_multi_recipient(
        [],
        False,
        warnings=warnings_out,
    )
    assert recipient is None
    assert effective == []
    assert not warnings_out
