"""Tests for eval harness wiring.

Verifies build_task correctly wraps offline_resolve and structural_conformance
passes for each golden. Uses evaluator functions directly (no Langfuse calls).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from qre.eval.evaluators import structural_conformance
from qre.eval.runner import build_task
from tests.engine._harness import PINNED_DATE, offline_resolve

_GOLDENS_PATH = Path(__file__).parent.parent.parent / "goldens.json"

# Dev-finance goldens that the offline harness can process
_DF_IDS = {
    "df-01", "df-02", "df-03", "df-04", "df-05", "df-06",
    "df-07", "df-08", "df-09", "df-10", "df-13",
    "df-11", "df-12", "nd-02",
}


def _load_df_goldens():
    goldens = json.loads(_GOLDENS_PATH.read_text())
    return [g for g in goldens if g["id"] in _DF_IDS]


def _run_task(task_fn, golden):
    """Run task_fn via the build_task bridge for one golden."""
    class _Item:
        input = {"entry_path": "raw_text", "query": golden["query"]}

    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = PINNED_DATE
        mock_date.side_effect = None
        return task_fn(item=_Item())


class TestBuildTaskWiring:
    def test_build_task_returns_dict(self):
        task = build_task(offline_resolve)
        golden = {"query": "health ODA grants from USA to Ethiopia"}

        class _Item:
            input = {"entry_path": "raw_text", "query": golden["query"]}

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            result = task(item=_Item())
        assert isinstance(result, dict)
        assert "status" in result

    @pytest.mark.parametrize(
        "golden", _load_df_goldens(), ids=[g["id"] for g in _load_df_goldens()]
    )
    def test_structural_conformance_passes(self, golden):
        task = build_task(offline_resolve)
        output = _run_task(task, golden)
        evaluation = structural_conformance(output=output)
        assert evaluation.value == 1.0, (
            f"structural_conformance failed for {golden['id']!r}: {evaluation}"
        )
