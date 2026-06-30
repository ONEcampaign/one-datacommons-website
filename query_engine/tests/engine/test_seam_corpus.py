"""Generalize the place-as-constraint (seam) proof across the seam:both corpus.

test_seam.py proves the seam on the single df-01 query. This replays every golden
tagged {"seam": "both"} through the offline harness in BOTH place_as_constraint
modes and asserts the seam-OFF role collapse, the seam warnings, and status/
entity-set invariance. Offline: FakeLLM + FakeGraph, no Langfuse, no Gemini.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qre.models import DefiniteResponse
from tests.engine._harness import make_request, offline_resolve

_GOLDENS_PATH = Path(__file__).parent.parent.parent / "goldens.json"
_SEAM_OFF_WARNINGS = {"PLACE_CONSTRAINT_SEAM_OFF", "ENTITY_ROLE_DISABLED"}


def _is_seam_both(golden: dict) -> bool:
    """True if the golden carries a {"seam": "both"} tag (mirrors _has_domain)."""
    return any(
        isinstance(t, dict) and t.get("seam") == "both"
        for t in golden.get("tags", [])
    )


def _load_seam_both_goldens() -> list[dict]:
    goldens = json.loads(_GOLDENS_PATH.read_text())
    return [g for g in goldens if _is_seam_both(g)]


_SEAM_BOTH = _load_seam_both_goldens()
assert len(_SEAM_BOTH) == 8, (
    f"Expected 8 seam:both goldens, got {len(_SEAM_BOTH)}. "
    "A seam:both tag may have been silently added or removed."
)


def _warn_codes(result) -> list[str]:
    return [w.code for w in result.root.diagnostics.warnings]


@pytest.mark.parametrize("golden", _SEAM_BOTH, ids=[g["id"] for g in _SEAM_BOTH])
def test_seam_both_modes(golden):
    query = golden["query"]
    on = offline_resolve(make_request(query, pac=True))
    off = offline_resolve(make_request(query, pac=False))

    # Status is seam-invariant.
    assert on.root.status == off.root.status == golden["expected_status"]

    # Seam warnings are mode-specific: absent ON, both present OFF.
    assert not (_SEAM_OFF_WARNINGS & set(_warn_codes(on)))
    assert _SEAM_OFF_WARNINGS.issubset(set(_warn_codes(off)))

    if golden["expected_status"] == "no_data":
        # NoDataResponse has no interpretation/entities (models.py:582-588); the
        # status + warning assertions above are the whole proof. df-12 shows the
        # seam transform and warnings still run when the query bottoms out in
        # no_data, with no entities to collapse.
        return

    # Definite: directional ON, subject OFF, identical dcid set, anchored to the
    # corpus by dcid only (not role fields, to avoid re-deriving the expectation).
    on_inner = on.root
    off_inner = off.root
    assert isinstance(on_inner, DefiniteResponse)
    assert isinstance(off_inner, DefiniteResponse)
    on_entities = on_inner.interpretation.entities
    off_entities = off_inner.interpretation.entities
    assert on_entities and all(e.role.kind == "directional" for e in on_entities)
    assert off_entities and all(e.role.kind == "subject" for e in off_entities)

    on_dcids = {e.ref.dcid for e in on_entities}
    off_dcids = {e.ref.dcid for e in off_entities}
    golden_dcids = {e["dcid"] for e in golden["expected_entities"]}
    assert on_dcids == off_dcids == golden_dcids
