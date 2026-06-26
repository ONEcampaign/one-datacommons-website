"""Tests for check_gate."""
from pathlib import Path

import pytest
from langfuse import Evaluation, RegressionError
from langfuse.experiment import ExperimentResult

from qre.eval.gate import GATE_THRESHOLDS, check_gate, load_baseline


def _make_result(run_evaluations):
    """Build a minimal ExperimentResult with the given run_evaluations list."""
    return ExperimentResult(
        name="test-exp",
        run_name="build-1",
        description=None,
        item_results=[],
        run_evaluations=run_evaluations,
        experiment_id="exp-1",
        dataset_run_id=None,
        dataset_run_url=None,
    )


def _all_passing_evals():
    return [
        Evaluation(name="interpretation_match_rate", value=0.9),
        Evaluation(name="behaviour_match_rate_definite", value=0.9),
        Evaluation(name="behaviour_match_rate_candidates", value=0.9),
        Evaluation(name="behaviour_match_rate_no_data", value=0.9),
        Evaluation(name="fabricated_ref_rate", value=0.0),
        Evaluation(name="materialisation_correct_rate", value=1.0),
        Evaluation(name="structural_conformance_rate", value=1.0),
    ]


def test_gate_passes_all_exact_met():
    result = _make_result(_all_passing_evals())
    out = check_gate(result, baseline={"interpretation_match_rate": 0.9})
    assert out is result


def test_gate_passes_no_baseline():
    result = _make_result(_all_passing_evals())
    out = check_gate(result, baseline=None)
    assert out is result


def test_gate_raises_on_fabricated_ref_rate():
    evs = _all_passing_evals()
    for ev in evs:
        if ev.name == "fabricated_ref_rate":
            evs[evs.index(ev)] = Evaluation(name="fabricated_ref_rate", value=0.01)
            break
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result)
    assert exc_info.value.metric == "fabricated_ref_rate"


def test_gate_raises_on_materialisation_below_1():
    evs = _all_passing_evals()
    for i, ev in enumerate(evs):
        if ev.name == "materialisation_correct_rate":
            evs[i] = Evaluation(name="materialisation_correct_rate", value=0.9)
            break
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result)
    assert exc_info.value.metric == "materialisation_correct_rate"


def test_gate_raises_on_structural_below_1():
    evs = _all_passing_evals()
    for i, ev in enumerate(evs):
        if ev.name == "structural_conformance_rate":
            evs[i] = Evaluation(name="structural_conformance_rate", value=0.95)
            break
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result)
    assert exc_info.value.metric == "structural_conformance_rate"


def test_gate_raises_on_interpretation_below_baseline():
    evs = _all_passing_evals()
    for i, ev in enumerate(evs):
        if ev.name == "interpretation_match_rate":
            evs[i] = Evaluation(name="interpretation_match_rate", value=0.80)
            break
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result, baseline={"interpretation_match_rate": 0.90})
    assert exc_info.value.metric == "interpretation_match_rate"


def test_gate_within_baseline_tolerance():
    evs = _all_passing_evals()
    for i, ev in enumerate(evs):
        if ev.name == "interpretation_match_rate":
            evs[i] = Evaluation(name="interpretation_match_rate", value=0.86)
            break
    result = _make_result(evs)
    out = check_gate(result, baseline={"interpretation_match_rate": 0.90})
    assert out is result


def test_gate_raises_on_missing_metric():
    evs = [ev for ev in _all_passing_evals() if ev.name != "fabricated_ref_rate"]
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result)
    assert exc_info.value.metric == "fabricated_ref_rate"


def test_gate_thresholds_config():
    # Verify GATE_THRESHOLDS has the expected keys and modes.
    assert "fabricated_ref_rate" in GATE_THRESHOLDS
    assert GATE_THRESHOLDS["fabricated_ref_rate"]["mode"] == "exact"
    assert GATE_THRESHOLDS["fabricated_ref_rate"]["value"] == 0.0
    assert "interpretation_match_rate" in GATE_THRESHOLDS
    assert GATE_THRESHOLDS["interpretation_match_rate"]["mode"] == "baseline_minus"


def test_gate_baseline_none_with_value_none_does_not_raise():
    evs = [ev for ev in _all_passing_evals() if ev.name != "interpretation_match_rate"]
    evs.append(Evaluation(name="interpretation_match_rate", value=None))
    result = _make_result(evs)
    out = check_gate(result, baseline=None)
    assert out is result


def test_gate_baseline_provided_but_metric_value_none_does_not_raise():
    evs = [ev for ev in _all_passing_evals() if ev.name != "interpretation_match_rate"]
    evs.append(Evaluation(name="interpretation_match_rate", value=None))
    result = _make_result(evs)
    out = check_gate(result, baseline={"interpretation_match_rate": 0.9})
    assert out is result


def test_gate_exact_metric_value_none_still_raises():
    evs = [ev for ev in _all_passing_evals() if ev.name != "fabricated_ref_rate"]
    evs.append(Evaluation(name="fabricated_ref_rate", value=None))
    result = _make_result(evs)
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result)
    assert exc_info.value.metric == "fabricated_ref_rate"


def test_load_baseline_reads_metrics(tmp_path):
    p = tmp_path / "b.json"
    p.write_text('{"metrics": {"interpretation_match_rate": 0.9, "fabricated_ref_rate": 0.0}}')
    assert load_baseline(p) == {"interpretation_match_rate": 0.9, "fabricated_ref_rate": 0.0}


_BASELINE_DIR = Path(__file__).parents[2] / "baselines"
_COMMITTED_BASELINES = sorted(_BASELINE_DIR.glob("*.json"))


@pytest.mark.parametrize(
    "baseline_path", _COMMITTED_BASELINES, ids=[p.stem for p in _COMMITTED_BASELINES]
)
def test_committed_baseline_passes_its_own_gate(baseline_path):
    """A run that exactly reproduces a frozen baseline must PASS check_gate.

    Self-consistency guard over every committed baseline: catches committing a
    baseline whose values would fail the gate (e.g. an exact metric below its
    target or a malformed metrics block).
    """
    metrics = load_baseline(baseline_path)
    result = _make_result([Evaluation(name=n, value=v) for n, v in metrics.items()])
    assert check_gate(result, baseline=metrics) is result


def test_baselines_directory_is_not_empty():
    """Guards against the glob silently matching nothing (which would skip the gate)."""
    assert _COMMITTED_BASELINES, "no baselines/*.json found; self-consistency gate would be a no-op"
