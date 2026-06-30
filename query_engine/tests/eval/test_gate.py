"""Tests for check_gate."""
from pathlib import Path

import pytest
from langfuse import Evaluation, RegressionError
from langfuse.experiment import ExperimentItemResult, ExperimentResult

from qre.eval.gate import GATE_THRESHOLDS, check_gate, load_baseline, load_baseline_items


def _make_result(run_evaluations, item_results=None):
    """Build a minimal ExperimentResult with the given run_evaluations list."""
    return ExperimentResult(
        name="test-exp",
        run_name="build-1",
        description=None,
        item_results=item_results or [],
        run_evaluations=run_evaluations,
        experiment_id="exp-1",
        dataset_run_id=None,
        dataset_run_url=None,
    )


def _make_item_result(golden_id: str, evaluations: list[Evaluation]) -> ExperimentItemResult:
    """Build a minimal ExperimentItemResult for a given golden id."""
    return ExperimentItemResult(
        item={"input": {}, "expected_output": None, "metadata": {"id": golden_id}},
        output=None,
        evaluations=evaluations,
        trace_id=None,
        dataset_run_id=None,
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


def test_gate_thresholds_bijection_with_default_run_evaluators():
    """GATE_THRESHOLDS keys must equal the metric names produced by DEFAULT_RUN_EVALUATORS.

    Guards against a new evaluator being added to DEFAULT_RUN_EVALUATORS without a
    matching entry in GATE_THRESHOLDS (or vice versa).  gate.py:51 silently continues
    when a baseline_minus key is absent, so ungated evaluators produce no CI signal.
    """
    from qre.eval.evaluators import DEFAULT_RUN_EVALUATORS

    produced_names = {fn(item_results=[]).name for fn in DEFAULT_RUN_EVALUATORS}
    assert set(GATE_THRESHOLDS) == produced_names


# ---------------------------------------------------------------------------
# Per-item flip guard tests
# ---------------------------------------------------------------------------


def test_flip_guard_raises_when_check_goes_1_to_0():
    """A 1.0 → 0.0 flip on a single golden raises RegressionError naming that golden.

    The aggregate metrics all hold within tolerance so the aggregate loop does not
    fire — the flip guard is the only thing that should trigger here.
    """
    item_results = [
        _make_item_result(
            "df-01",
            [Evaluation(name="interpretation_match", value=0.0)],
        ),
        _make_item_result(
            "df-02",
            [Evaluation(name="interpretation_match", value=1.0)],
        ),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)
    baseline_items = {
        "df-01": {"interpretation_match": 1.0},
        "df-02": {"interpretation_match": 1.0},
    }
    with pytest.raises(RegressionError) as exc_info:
        check_gate(result, baseline_items=baseline_items)
    err = exc_info.value
    assert err.metric == "per_item_flip"
    assert "df-01" in str(err)
    assert "interpretation_match" in str(err)


def test_flip_guard_passes_when_no_flip():
    """All goldens maintaining their check values → gate passes."""
    item_results = [
        _make_item_result("df-01", [Evaluation(name="interpretation_match", value=1.0)]),
        _make_item_result("df-02", [Evaluation(name="interpretation_match", value=1.0)]),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)
    baseline_items = {
        "df-01": {"interpretation_match": 1.0},
        "df-02": {"interpretation_match": 1.0},
    }
    out = check_gate(result, baseline_items=baseline_items)
    assert out is result


def test_flip_guard_skipped_when_baseline_items_none():
    """baseline_items=None (default) means no per-item check is run."""
    item_results = [
        _make_item_result("df-01", [Evaluation(name="interpretation_match", value=0.0)]),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)
    # No baseline_items → flip guard is silent even for a 0.0 value.
    out = check_gate(result)
    assert out is result


def test_flip_guard_ignores_0_to_0_transitions():
    """A check that was already 0.0 in the baseline and stays 0.0 is not a flip."""
    item_results = [
        _make_item_result("df-01", [Evaluation(name="some_check", value=0.0)]),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)
    baseline_items = {"df-01": {"some_check": 0.0}}
    out = check_gate(result, baseline_items=baseline_items)
    assert out is result


def test_flip_guard_skips_golden_not_in_baseline():
    """A golden absent from baseline_items is treated as new — not a regression."""
    item_results = [
        _make_item_result("new-golden", [Evaluation(name="interpretation_match", value=0.0)]),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)
    baseline_items = {}  # new-golden not yet in baseline
    out = check_gate(result, baseline_items=baseline_items)
    assert out is result


def test_load_baseline_items_reads_per_item_block(tmp_path):
    """load_baseline_items returns the per_item dict when present."""
    data = {
        "metrics": {"interpretation_match_rate": 1.0},
        "per_item": {"df-01": {"interpretation_match": 1.0}},
    }
    p = tmp_path / "b.json"
    p.write_text(__import__("json").dumps(data))
    result = load_baseline_items(p)
    assert result == {"df-01": {"interpretation_match": 1.0}}


def test_run_experiment_wiring_flip_raises(tmp_path):
    """Mirrors the run_experiment.py wiring: load_baseline + load_baseline_items from one
    file, build a result with a 1.0 → 0.0 flip, assert check_gate raises naming the golden.

    This is the end-to-end guard that would catch the fix-4 regression: before the fix,
    baseline_items was never passed to check_gate so the per-item flip guard was dead.
    """
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        __import__("json").dumps(
            {
                "metrics": {
                    # Match _all_passing_evals() so baseline_minus rules don't fire
                    # (floor = 0.9 - 0.05 = 0.85; actual = 0.9 passes); only the
                    # per-item flip guard should raise.
                    "interpretation_match_rate": 0.9,
                    "behaviour_match_rate_definite": 0.9,
                    "behaviour_match_rate_candidates": 0.9,
                    "behaviour_match_rate_no_data": 0.9,
                    "fabricated_ref_rate": 0.0,
                    "materialisation_correct_rate": 1.0,
                    "structural_conformance_rate": 1.0,
                },
                "per_item": {
                    "df-01": {"interpretation_match": 1.0},
                },
            }
        )
    )

    # Load both halves the same way run_experiment.py does.
    baseline = load_baseline(baseline_file)
    baseline_items = load_baseline_items(baseline_file)

    # df-01 flips 1.0 → 0.0; aggregate metrics stay on-target so only the flip guard fires.
    item_results = [
        _make_item_result("df-01", [Evaluation(name="interpretation_match", value=0.0)]),
    ]
    result = _make_result(_all_passing_evals(), item_results=item_results)

    with pytest.raises(RegressionError) as exc_info:
        check_gate(result, baseline=baseline, baseline_items=baseline_items)

    err = exc_info.value
    assert err.metric == "per_item_flip"
    assert "df-01" in str(err)
    assert "interpretation_match" in str(err)


def test_load_baseline_items_returns_none_for_old_format(tmp_path):
    """Old baselines without per_item key → None (flip guard skipped)."""
    data = {"metrics": {"interpretation_match_rate": 1.0}}
    p = tmp_path / "b.json"
    p.write_text(__import__("json").dumps(data))
    assert load_baseline_items(p) is None
