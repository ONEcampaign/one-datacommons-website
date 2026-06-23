"""Operational baseline-freeze script.

Requires GEMINI_API_KEY + graph access + Langfuse credentials.

Usage:
    uv run --env-file .env --extra eval --extra engine python scripts/run_experiment.py
"""
from qre.engine import resolve
from qre.engine.config import ENGINE_BUILD_ID, QRE_ENGINE_MODEL, QRE_GRAPH_BASE
from qre.engine.graph import LiveGraphClient
from qre.eval import run_eval

graph = LiveGraphClient()
try:
    result = run_eval(
        resolve,
        engine_build=ENGINE_BUILD_ID,
        graph=graph,
        model_pin=QRE_ENGINE_MODEL,
        graph_endpoint=QRE_GRAPH_BASE,
    )
finally:
    graph.close()

print(result.format())

# Phase-0 gate: the engine must never fabricate a GraphRef.
fab = next((e for e in result.run_evaluations if e.name == "fabricated_ref_rate"), None)
if fab is not None:
    verdict = "PASS" if fab.value == 0 else "FAIL"
    print(f"\nfabricated_ref_rate = {fab.value}  [{verdict}]")
