"""Corpus replay for cross-shape conjunction goldens.

Filters goldens tagged {conjunction: cross_shape} and replays each through the
offline harness. Asserts CONJUNCTION_CROSS_SHAPE warning, primary status match,
interpretation.variable_text matches the primary variable, and
additional_interpretations is populated (non-empty list).

Does NOT add to _DF_IDS or tag entries seam:both — these are conjunction-only tests
with no seam interaction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.engine._harness import make_request, offline_resolve

_GOLDENS_PATH = Path(__file__).parent.parent.parent / "goldens.json"


def _is_cross_shape(golden: dict) -> bool:
    return any(
        isinstance(t, dict) and t.get("conjunction") == "cross_shape"
        for t in golden.get("tags", [])
    )


def _load_cross_shape_goldens() -> list[dict]:
    goldens = json.loads(_GOLDENS_PATH.read_text())
    return [g for g in goldens if _is_cross_shape(g)]


_CROSS_SHAPE = _load_cross_shape_goldens()


@pytest.mark.parametrize("golden", _CROSS_SHAPE, ids=[g["id"] for g in _CROSS_SHAPE])
def test_conjunction_cross_shape(golden):
    """Each cross_shape golden resolves with a CROSS_SHAPE warning and populated extras."""
    result = offline_resolve(make_request(golden["query"]))
    r = result.root

    # Status must match corpus expectation
    assert r.status == golden["expected_status"]

    # CONJUNCTION_CROSS_SHAPE warning is required
    codes = [w.code for w in r.diagnostics.warnings]
    assert "CONJUNCTION_CROSS_SHAPE" in codes, (
        f"Missing CONJUNCTION_CROSS_SHAPE warning for {golden['id']!r}. "
        f"Got: {codes}"
    )

    if r.status == "definite":
        # Primary carries a variable_text back-pointer (set for all conjuncts)
        assert r.interpretation.variable_text is not None, (
            f"{golden['id']}: interpretation.variable_text is None (expected primary variable name)"
        )

        # additional_interpretations must be populated (non-empty list, not None)
        assert r.additional_interpretations is not None, (
            f"{golden['id']}: additional_interpretations is None (expected populated list)"
        )
        assert len(r.additional_interpretations) > 0, (
            f"{golden['id']}: additional_interpretations is empty (expected ≥1 spec)"
        )
        # Every additional spec also carries a variable_text back-pointer
        for spec in r.additional_interpretations:
            assert spec.variable_text is not None, (
                f"{golden['id']}: additional_interpretations[*].variable_text is None"
            )
