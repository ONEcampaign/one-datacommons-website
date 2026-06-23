"""Regression gate: raises ``RegressionError`` when any metric breaches its threshold."""
from langfuse import RegressionError

GATE_THRESHOLDS: dict = {
    "interpretation_match_rate": {"mode": "baseline_minus", "tolerance": 0.05},
    "behaviour_match_rate_definite": {"mode": "baseline_minus", "tolerance": 0.05},
    "behaviour_match_rate_candidates": {"mode": "baseline_minus", "tolerance": 0.05},
    "behaviour_match_rate_no_data": {"mode": "baseline_minus", "tolerance": 0.05},
    "fabricated_ref_rate": {"mode": "exact", "value": 0.0},
    "materialisation_correct_rate": {"mode": "exact", "value": 1.0},
    "structural_conformance_rate": {"mode": "exact", "value": 1.0},
}


def check_gate(
    result,
    *,
    baseline: dict[str, float] | None = None,
    thresholds: dict = GATE_THRESHOLDS,
):
    """Raise RegressionError if any gated metric breaches its threshold.

    Args:
        result: Langfuse ExperimentResult (exposes .run_evaluations).
        baseline: Optional dict of metric name to prior value. When None, only
            exact-mode rules apply; baseline_minus rules are skipped.
        thresholds: Override the default gate thresholds for testing.

    Returns the result unchanged when all checks pass.
    """
    metrics = {ev.name: ev.value for ev in result.run_evaluations}

    for name, rule in thresholds.items():
        value = metrics.get(name)

        if rule["mode"] == "baseline_minus":
            # No regression claim possible without a baseline or a concrete value.
            if baseline is None or value is None:
                continue
            base = baseline.get(name)
            if base is None:
                continue
            floor = float(base) - float(rule["tolerance"])
            if float(value) < floor:
                raise RegressionError(
                    result=result,
                    metric=name,
                    value=float(value),
                    threshold=floor,
                    message=(
                        f"{name}={value} is below baseline {base} minus "
                        f"tolerance {rule['tolerance']} (floor={floor:.3f})"
                    ),
                )

        elif rule["mode"] == "exact":
            # exact metrics must always be present and match the target.
            if value is None:
                raise RegressionError(
                    result=result,
                    metric=name,
                    value=0.0,
                    threshold=0.0,
                    message=f"metric '{name}' is missing from run_evaluations",
                )
            target = float(rule["value"])
            if float(value) != target:
                raise RegressionError(
                    result=result,
                    metric=name,
                    value=float(value),
                    threshold=target,
                    message=f"{name}={value} does not equal exact target {target}",
                )

    return result
