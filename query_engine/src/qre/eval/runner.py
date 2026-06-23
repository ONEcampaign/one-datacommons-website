"""Runner: thin wrapper over Langfuse dataset.run_experiment.

Owns the dict<->model bridge and wires evaluators and metadata pins.
"""
from __future__ import annotations

from typing import Callable

from qre import ResolveRequest, ResolveResponse
from qre.eval.dataset import _client
from qre.eval.evaluators import DEFAULT_ITEM_EVALUATORS, DEFAULT_RUN_EVALUATORS
from qre.eval.graph import GraphClient


def build_task(task: Callable[[ResolveRequest], ResolveResponse]):
    """Wrap an EngineTask in the Langfuse task(*, item, **kwargs) -> dict shape.

    The bridge reads item.input["entry_path"] to select the input variant. In v1
    all goldens use raw_text; the branch is here for forward compatibility.
    """

    def _lf_task(*, item, **kwargs):
        entry_path = item.input.get("entry_path", "raw_text")
        if entry_path == "raw_text":
            req = ResolveRequest.model_validate(
                {"input": {"kind": "raw_text", "query": item.input["query"]}}
            )
        else:
            raise ValueError(
                f"Unsupported entry_path {entry_path!r}. Only 'raw_text' is supported in v1."
            )
        resp = task(req)
        return resp.model_dump(mode="json")

    return _lf_task


def run_eval(
    task: Callable[[ResolveRequest], ResolveResponse],
    *,
    dataset_name: str = "qre-goldens-v1",
    engine_build: str,
    graph: GraphClient,
    model_pin: str | None = None,
    graph_endpoint: str | None = None,
    langfuse=None,
):
    """Run the QRE eval experiment against a Langfuse dataset.

    Args:
        task: An EngineTask callable; receives ResolveRequest, returns ResolveResponse.
        dataset_name: Name of the Langfuse dataset to run against.
        engine_build: Experiment name; used as the Langfuse run name for build comparison.
        graph: GraphClient implementation for groundedness and materialisation checks.
        model_pin: Optional model identifier recorded in run metadata.
        graph_endpoint: Optional graph endpoint recorded in run metadata.
        langfuse: Optional pre-built Langfuse client (for testing or DI).

    Returns the Langfuse ExperimentResult (pass to check_gate).
    """
    client = langfuse or _client()
    dataset = client.get_dataset(dataset_name)

    lf_task = build_task(task)
    item_evaluators = DEFAULT_ITEM_EVALUATORS(graph)
    run_evaluators = DEFAULT_RUN_EVALUATORS

    result = dataset.run_experiment(
        name=engine_build,
        description=f"QRE eval | model={model_pin} | graph={graph_endpoint}",
        task=lf_task,
        evaluators=item_evaluators,
        run_evaluators=run_evaluators,
        metadata={"model": model_pin, "graph_endpoint": graph_endpoint},
    )
    return result
