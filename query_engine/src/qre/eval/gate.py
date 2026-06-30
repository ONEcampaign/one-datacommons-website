"""Regression gate: raises ``RegressionError`` when any metric breaches its threshold."""
import json
from pathlib import Path

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


def _item_golden_id(item) -> str | None:
    """Extract the golden id from an experiment item (dict or DatasetItem)."""
    if isinstance(item, dict):
        meta = item.get("metadata") or {}
        return meta.get("id")
    # DatasetItem (langfuse.api) — .metadata is the metadata dict
    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        return meta.get("id")
    return None


def load_baseline(path: str | Path) -> dict[str, float]:
    """Read the committed baseline metrics dict from a frozen-baseline JSON file.

    The file is the human-reviewed regression anchor: re-freezing it is a
    deliberate, diff-visible change (see .design/eval-gate.md section 3). Returns
    the ``metrics`` object, ready to pass as ``check_gate(baseline=...)``.
    """
    return json.loads(Path(path).read_text())["metrics"]


def load_baseline_items(path: str | Path) -> dict[str, dict[str, float]] | None:
    """Read the per-item baseline block from a frozen-baseline JSON file.

    Returns the ``per_item`` mapping ``{golden_id: {check_name: 0.0|1.0}}``, or
    None when the file was frozen before C1 (no ``per_item`` key — flip guard skipped).
    Pass the result as ``check_gate(baseline_items=...)``.
    """
    return json.loads(Path(path).read_text()).get("per_item")


def check_gate(
    result,
    *,
    baseline: dict[str, float] | None = None,
    baseline_items: dict[str, dict[str, float]] | None = None,
    thresholds: dict = GATE_THRESHOLDS,
):
    """Raise RegressionError if any gated metric breaches its threshold.

    Args:
        result: Langfuse ExperimentResult (exposes .run_evaluations and .item_results).
        baseline: Optional dict of metric name to prior value. When None, only
            exact-mode rules apply; baseline_minus rules are skipped.
        baseline_items: Optional per-item baseline from ``load_baseline_items``. When
            provided, any golden whose check transitions 1.0 → 0.0 raises RegressionError
            listing the flipped golden ids (eval-gate.md §3 flip guard). When None, the
            per-item guard is skipped.
        thresholds: Override the default gate thresholds for testing.

    Returns result unchanged when all checks pass.
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

    # Per-item flip guard (eval-gate.md §3): raise on any 1.0 → 0.0 transition.
    # This catches regressions on a specific golden that aggregates would smooth over.
    if baseline_items is not None:
        flipped = []
        for ir in result.item_results:
            golden_id = _item_golden_id(ir.item)
            if golden_id is None:
                continue
            item_base = baseline_items.get(golden_id)
            if item_base is None:
                continue
            current_checks = {ev.name: ev.value for ev in ir.evaluations}
            for check_name, base_val in item_base.items():
                if float(base_val) == 1.0:
                    curr_val = current_checks.get(check_name)
                    if curr_val is not None and float(curr_val) == 0.0:
                        flipped.append(f"{golden_id}/{check_name}")
        if flipped:
            raise RegressionError(
                result=result,
                metric="per_item_flip",
                value=float(len(flipped)),
                threshold=0.0,
                message=(
                    f"{len(flipped)} golden(s) flipped 1.0 → 0.0: "
                    + ", ".join(flipped)
                ),
            )

    return result
