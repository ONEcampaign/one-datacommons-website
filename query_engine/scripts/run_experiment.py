"""Operational baseline-freeze script.

Requires GEMINI_API_KEY + graph access + Langfuse credentials.

Usage:
    uv run python scripts/run_experiment.py
"""
from qre.engine import resolve
from qre.engine.config import ENGINE_BUILD_ID
from qre.engine.graph import LiveGraphClient
from qre.eval import run_eval

graph = LiveGraphClient()
result = run_eval(
    resolve,
    engine_build=ENGINE_BUILD_ID,
    graph=graph,
)
print(result)
