"""Operational baseline-freeze script.

Requires GEMINI_API_KEY + graph access + Langfuse credentials.

Usage:
    uv run --env-file .env --extra eval --extra engine python scripts/run_experiment.py

Targets the dataset named by QRE_DATASET (default qre-goldens-v1). Exits non-zero if
the Phase-0 fabrication gate is violated (fabricated_ref_rate != 0) or the metric is
missing, so CI and automation fail loudly instead of passing on a printed [FAIL].
"""
import os
import sys

from qre.engine import resolve
from qre.engine.config import ENGINE_BUILD_ID, QRE_ENGINE_MODEL, QRE_GRAPH_BASE
from qre.engine.graph import LiveGraphClient
from qre.eval import run_eval

dataset_name = os.environ.get("QRE_DATASET", "qre-goldens-v1")

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

# Phase-0 gate: the engine must never fabricate a GraphRef. A missing metric is a
# failure too, so an absent score cannot pass silently.
fab = next((e for e in result.run_evaluations if e.name == "fabricated_ref_rate"), None)
if fab is None:
    print("\nfabricated_ref_rate metric MISSING [FAIL]")
    sys.exit(1)
if fab.value != 0:
    print(f"\nfabricated_ref_rate = {fab.value}  [FAIL]")
    sys.exit(1)
print(f"\nfabricated_ref_rate = {fab.value}  [PASS]")
