"""Tests for run-level (aggregate) evaluators."""
from langfuse import Evaluation

from qre.eval.evaluators import (
    agg_behaviour_match_rate_candidates,
    agg_behaviour_match_rate_definite,
    agg_behaviour_match_rate_no_data,
    agg_fabricated_ref_rate,
    agg_interpretation_match_rate,
    agg_materialisation_correct_rate,
    agg_structural_conformance_rate,
)


def _item(evaluations):
    """Build a minimal fake item result carrying the given Evaluations."""

    class FakeItem:
        pass

    fi = FakeItem()
    fi.evaluations = evaluations
    return fi


def test_interpretation_match_rate_mean():
    items = [
        _item([Evaluation(name="interpretation_match", value=1.0)]),
        _item([Evaluation(name="interpretation_match", value=0.0)]),
        _item([Evaluation(name="interpretation_match", value=None)]),  # skipped
    ]
    ev = agg_interpretation_match_rate(item_results=items)
    assert ev.name == "interpretation_match_rate"
    assert abs(ev.value - 0.5) < 1e-9


def test_interpretation_match_rate_empty_bucket():
    items = [_item([Evaluation(name="interpretation_match", value=None)])]
    ev = agg_interpretation_match_rate(item_results=items)
    assert ev.value is None


def test_fabricated_ref_rate_sums_counts():
    meta_0 = {"fabricated": 0, "walked": 10}
    meta_2 = {"fabricated": 2, "walked": 8}
    items = [
        _item([Evaluation(name="groundedness", value=1.0, metadata=meta_0)]),
        _item([Evaluation(name="groundedness", value=0.0, metadata=meta_2)]),
    ]
    ev = agg_fabricated_ref_rate(item_results=items)
    assert ev.name == "fabricated_ref_rate"
    # 2 / 18 = 0.1111...
    assert abs(ev.value - 2 / 18) < 1e-9


def test_fabricated_ref_rate_not_a_boolean_mean():
    # Per-item booleans (one 0.0 and one 1.0) would mean 0.5; the correct sum gives 0.1.
    meta_ok = {"fabricated": 0, "walked": 5}
    meta_fail = {"fabricated": 1, "walked": 5}
    items = [
        _item([Evaluation(name="groundedness", value=1.0, metadata=meta_ok)]),
        _item([Evaluation(name="groundedness", value=0.0, metadata=meta_fail)]),
    ]
    ev = agg_fabricated_ref_rate(item_results=items)
    assert abs(ev.value - 0.1) < 1e-9  # 1 / 10, not 0.5


def test_fabricated_ref_rate_no_walked():
    items = [_item([])]
    ev = agg_fabricated_ref_rate(item_results=items)
    assert ev.value == 0.0


def test_behaviour_definite_rate():
    items = [
        _item([Evaluation(name="behaviour_match_definite", value=1.0)]),
        _item([Evaluation(name="behaviour_match_definite", value=None)]),
        _item([Evaluation(name="behaviour_match_definite", value=0.0)]),
    ]
    ev = agg_behaviour_match_rate_definite(item_results=items)
    assert ev.name == "behaviour_match_rate_definite"
    assert abs(ev.value - 0.5) < 1e-9


def test_behaviour_candidates_rate():
    items = [
        _item([Evaluation(name="behaviour_match_candidates", value=1.0)]),
        _item([Evaluation(name="behaviour_match_candidates", value=1.0)]),
    ]
    ev = agg_behaviour_match_rate_candidates(item_results=items)
    assert ev.name == "behaviour_match_rate_candidates"
    assert ev.value == 1.0


def test_behaviour_no_data_empty_bucket():
    items = [_item([Evaluation(name="behaviour_match_no_data", value=None)])]
    ev = agg_behaviour_match_rate_no_data(item_results=items)
    assert ev.value is None


def test_materialisation_correct_rate():
    items = [
        _item([Evaluation(name="materialisation", value=1.0)]),
        _item([Evaluation(name="materialisation", value=1.0)]),
        _item([Evaluation(name="materialisation", value=None)]),
    ]
    ev = agg_materialisation_correct_rate(item_results=items)
    assert ev.name == "materialisation_correct_rate"
    assert ev.value == 1.0


def test_structural_conformance_rate():
    items = [
        _item([Evaluation(name="structural_conformance", value=1.0)]),
        _item([Evaluation(name="structural_conformance", value=0.0)]),
    ]
    ev = agg_structural_conformance_rate(item_results=items)
    assert ev.name == "structural_conformance_rate"
    assert ev.value == 0.5
