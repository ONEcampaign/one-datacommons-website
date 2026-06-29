"""Tests for RETRIEVAL_SCORE_WEAK info warning in resolve_variable.

RETRIEVAL_SCORE_WEAK is emitted when the top standard shape's representative
cosine score is above QRE_RELEVANCE_THRESHOLD but below QRE_WEAK_SCORE_THRESHOLD.
It is suppressed on the candidates path and when the score equals 1.0 (the
dev-finance fallback / offline-fixture sentinel).

resolve_variable is called directly (async; wrapped with asyncio.run) using
an inline FakeGraph on a STANDARD-family scenario so the LLM bind is skipped.
"""
from __future__ import annotations

import asyncio

from qre.engine.regions import RETRIEVAL_SCORE_WEAK, resolve_variable
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

_RESOLVE: dict = {"Kenya": "country/KEN"}


def _run(cosine_score: float | None) -> object:
    """Run resolve_variable with a single standard SV at the given cosine score.

    cosine_score=None means the detect entry carries no cosine_scores, so FakeGraph
    defaults every SV to 1.0 — the offline-fixture sentinel.
    """
    detect_entry: dict = {"svs": [_SV]}
    if cosine_score is not None:
        detect_entry["cosine_scores"] = [cosine_score]

    # Use a fixed detect query key; supply one entity so the standard resolver
    # probes obs and returns no_data (obs={}), carrying any accumulated warnings.
    detect: dict = {"exports": detect_entry}
    graph = FakeGraph(nodes=_NODES, detect=detect, resolve=_RESOLVE, obs={})

    return asyncio.run(
        resolve_variable(
            "exports",
            entities=["Kenya"],
            date_request=None,
            detect_query="exports",
            role_query="exports Kenya",
            pac=True,
            graph=graph,
            llm=FakeLLM(),
            base_steps=[],
            base_timing={},
        )
    )


def test_weak_score_fires():
    """A shape whose cosine is in (QRE_RELEVANCE_THRESHOLD, QRE_WEAK_SCORE_THRESHOLD)
    triggers RETRIEVAL_SCORE_WEAK.

    Score 0.55 is above the relevance floor (0.5) and below the weak threshold (0.65),
    so the warning must be present and the sentinel guard must not suppress it.
    """
    result = _run(cosine_score=0.55)
    assert any(w.code == RETRIEVAL_SCORE_WEAK for w in result.warnings)


def test_clean_score_silent():
    """A shape whose cosine meets or exceeds QRE_WEAK_SCORE_THRESHOLD is silent.

    Score 0.70 is above both thresholds; the condition
    ``score < QRE_WEAK_SCORE_THRESHOLD`` is False so no warning fires.
    """
    result = _run(cosine_score=0.70)
    assert not any(w.code == RETRIEVAL_SCORE_WEAK for w in result.warnings)


def test_sentinel_1_0_silent():
    """The representative_score == 1.0 sentinel suppresses the warning.

    Omitting cosine_scores from the detect entry makes FakeGraph return 1.0 for
    every SV (the dev-finance fallback / offline-fixture default).  Even if
    QRE_WEAK_SCORE_THRESHOLD were raised above 1.0 at runtime, the explicit
    ``!= 1.0`` guard must keep the warning silent.
    """
    result = _run(cosine_score=None)
    assert not any(w.code == RETRIEVAL_SCORE_WEAK for w in result.warnings)
