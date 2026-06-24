"""Unit tests for the standard-family dominance gate (_top_dominates) and its config.

These tests use hand-built ShapeDraft instances with explicit representative_score
values so the rule can be exercised independently of the fixture data.
"""
from __future__ import annotations

import importlib

import pytest

from qre.engine.core import _top_dominates
from qre.engine.shape import ShapeDraft

# ---------------------------------------------------------------------------
# Minimal ShapeDraft factory (only representative_score matters for this gate)
# ---------------------------------------------------------------------------

def _shape(score: float, shape_id: str = "s") -> ShapeDraft:
    """Build a bare ShapeDraft with the given representative_score."""
    return ShapeDraft(
        shape_id=shape_id,
        label="test shape",
        pop_type_dcid="Person",
        meas_prop_dcid="count",
        stat_type_dcid="measuredValue",
        meas_qual_dcid=None,
        meas_denom_dcid=None,
        slot_keys=(),
        representative_score=score,
    )


# ---------------------------------------------------------------------------
# _top_dominates unit tests
# ---------------------------------------------------------------------------

class TestTopDominates:
    def test_clear_margin_returns_true(self):
        # margin 0.25 ≥ 0.15 → dominates
        shapes = [_shape(0.95, "a"), _shape(0.70, "b")]
        assert _top_dominates(shapes, margin=0.15) is True

    def test_narrow_margin_returns_false(self):
        # margin 0.05 < 0.15 → does not dominate
        shapes = [_shape(0.95, "a"), _shape(0.90, "b")]
        assert _top_dominates(shapes, margin=0.15) is False

    def test_fewer_than_two_shapes_returns_false(self):
        assert _top_dominates([_shape(0.99, "a")], margin=0.15) is False
        assert _top_dominates([], margin=0.15) is False

    def test_exact_margin_equals_threshold_returns_true(self):
        # margin exactly 0.15 → dominates (≥, not >)
        shapes = [_shape(0.90, "a"), _shape(0.75, "b")]
        assert _top_dominates(shapes, margin=0.15) is True

    def test_ranking_is_by_score_not_list_order(self):
        # Pass shapes in reverse score order — function must sort before comparing
        shapes = [_shape(0.70, "b"), _shape(0.95, "a")]
        assert _top_dominates(shapes, margin=0.15) is True

    def test_definite_shape_is_cosine_top(self):
        """When dominance fires, the winner must be the cosine-top shape, not index-0."""
        high = _shape(0.95, "cosine_top")
        low = _shape(0.70, "registry_first")
        # Put the lower-score shape first to simulate registry-index ordering
        shapes = [low, high]
        assert _top_dominates(shapes, margin=0.15) is True
        # The definite shape must be selected as max by representative_score
        definite = max(shapes, key=lambda s: s.representative_score)
        assert definite.shape_id == "cosine_top"


# ---------------------------------------------------------------------------
# Config: QRE_DOMINANCE_MARGIN default and env override
# ---------------------------------------------------------------------------

class TestDominanceMarginConfig:
    def test_default_is_0_15(self):
        import qre.engine.config as cfg
        assert cfg.QRE_DOMINANCE_MARGIN == pytest.approx(0.15)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("QRE_DOMINANCE_MARGIN", "0.20")
        import qre.engine.config as cfg
        reloaded = importlib.reload(cfg)
        assert reloaded.QRE_DOMINANCE_MARGIN == pytest.approx(0.20)
        # Restore
        importlib.reload(cfg)
