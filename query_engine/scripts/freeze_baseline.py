"""Freeze a QRE eval baseline: run the live eval and write baselines/<dataset>.json.

Produces the human-reviewed regression anchor straight from a run so the metric
values are never hand-transcribed. Re-freezing is a deliberate, diff-visible change:
post the before/after metrics in the PR (see .design/eval-gate.md section 3).

Usage:
    QRE_DATASET=qre-standard-main \
      uv run --env-file .env --extra eval --extra engine python scripts/freeze_baseline.py

Reads QRE_DATASET (the Langfuse dataset to run) and writes
baselines/<QRE_DATASET>.json. The dataset must already be synced (see
.env.example). Pin the model via QRE_ENGINE_MODEL; the resolved value is recorded.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from qre.engine import resolve
from qre.engine.config import ENGINE_BUILD_ID, QRE_ENGINE_MODEL, QRE_GRAPH_BASE
from qre.engine.graph import LiveGraphClient
from qre.eval import run_eval

# The seven gated metrics, in baseline order (matches gate.GATE_THRESHOLDS keys).
_METRIC_KEYS = (
    "interpretation_match_rate",
    "behaviour_match_rate_definite",
    "behaviour_match_rate_candidates",
    "behaviour_match_rate_no_data",
    "materialisation_correct_rate",
    "structural_conformance_rate",
    "fabricated_ref_rate",
)

dataset_name = os.environ.get("QRE_DATASET")
if not dataset_name:
    sys.exit("set QRE_DATASET to the Langfuse dataset to freeze (e.g. qre-standard-main)")

out_path = Path(__file__).resolve().parents[1] / "baselines" / f"{dataset_name}.json"

graph = LiveGraphClient()
try:
    result = run_eval(
        resolve,
        dataset_name=dataset_name,
        engine_build=ENGINE_BUILD_ID,
        graph=graph,
        model_pin=QRE_ENGINE_MODEL,
        graph_endpoint=QRE_GRAPH_BASE,
    )
finally:
    graph.close()

print(result.format())

metrics = {ev.name: ev.value for ev in result.run_evaluations}
frozen = {k: metrics.get(k) for k in _METRIC_KEYS}

baseline = {
    "dataset": dataset_name,
    "engine_build": ENGINE_BUILD_ID,
    "model": QRE_ENGINE_MODEL,
    "graph_endpoint": QRE_GRAPH_BASE,
    "frozen": date.today().isoformat(),
    "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "items": len(result.item_results),
    "note": (
        "Re-freezing is a human-reviewed diff: post before/after metrics in the PR. "
        "See .design/eval-gate.md section 3."
    ),
    "metrics": frozen,
}

out_path.write_text(json.dumps(baseline, indent=2) + "\n")
rel = out_path.relative_to(Path.cwd()) if out_path.is_relative_to(Path.cwd()) else out_path
print(f"\nwrote {rel}")
print(json.dumps(frozen, indent=2))
