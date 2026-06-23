"""Operational baseline-freeze / regression-gate script.

Requires GEMINI_API_KEY + graph access + Langfuse credentials.

Usage:
    uv run --env-file .env --extra eval --extra engine python scripts/run_experiment.py

Targets the dataset named by QRE_DATASET (default qre-goldens-v1) and runs the eval
gate over the result. When QRE_BASELINE points at a frozen-baseline JSON, baseline-minus
rules (interpretation/behaviour) are enforced against it; exact rules always apply
(fabricated_ref_rate==0, materialisation==1.0, structural==1.0). Exits non-zero on
any breach so CI fails loudly.
"""
import os
import sys

from langfuse import RegressionError

from qre.engine import resolve
from qre.engine.config import ENGINE_BUILD_ID, QRE_ENGINE_MODEL, QRE_GRAPH_BASE
from qre.engine.graph import LiveGraphClient
from qre.eval import check_gate, load_baseline, run_eval

dataset_name = os.environ.get("QRE_DATASET", "qre-goldens-v1")
baseline_path = os.environ.get("QRE_BASELINE")
baseline = load_baseline(baseline_path) if baseline_path else None

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

try:
    check_gate(result, baseline=baseline)
except RegressionError as exc:
    print(f"\nGATE FAILED: {exc}")
    sys.exit(1)

scope = f"baseline {baseline_path}" if baseline else "exact-only (no baseline)"
print(f"\nGATE PASSED: all metrics within thresholds [{scope}]")
