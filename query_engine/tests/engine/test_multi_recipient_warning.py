"""Tests for MULTI_RECIPIENT_TRUNCATED warning in resolve_variable.

MULTI_RECIPIENT_TRUNCATED is emitted when multiple recipient entities are
detected: either directional (multiple "to" prepositions) or bare-entity
(multiple entities with no directional prepositions).

resolve_variable is called directly (async; wrapped with asyncio.run) using
an inline FakeGraph on a STANDARD-family scenario so the LLM bind is skipped.
"""
from __future__ import annotations

import asyncio

from qre.engine.regions import MULTI_RECIPIENT_TRUNCATED, resolve_variable
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


def test_directional_two_recipient_warns():
    """Directional two 'to' recipients trigger MULTI_RECIPIENT_TRUNCATED.

    "exports to Kenya and to Uganda" places both Kenya and Uganda after an
    explicit "to" preposition.  directional_roles assigns both direction="to",
    so the to_dcids list has length 2 after the loop and the warning fires.
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
