"""QRE eval harness. Requires the 'eval' extra: uv sync --extra eval

Public surface:
    from qre.eval import sync_dataset, run_eval, check_gate, GATE_THRESHOLDS
    from qre.eval import EngineTask, GraphClient
    from qre.eval import (
        structural_conformance, interpretation_match,
        behaviour_by_tag, axis_classification,
        make_groundedness, make_materialisation,
        DEFAULT_ITEM_EVALUATORS, DEFAULT_RUN_EVALUATORS,
    )

The core qre module does not import this. The eval extra (langfuse,
datacommons-client) is not required for the contract models.
"""
try:
    from langfuse import Evaluation  # noqa: F401  (probe the extra)
except ModuleNotFoundError as exc:  # pragma: no cover - tested via subprocess
    raise ModuleNotFoundError(
        "qre.eval requires the 'eval' extra. Install with: uv sync --extra eval "
        "(or pip install 'qre[eval]')."
    ) from exc

from typing import Protocol

from qre import ResolveRequest, ResolveResponse
from qre.eval.dataset import sync_dataset  # noqa: F401
from qre.eval.evaluators import (  # noqa: F401
    DEFAULT_ITEM_EVALUATORS,
    DEFAULT_RUN_EVALUATORS,
    axis_classification,
    behaviour_by_tag,
    interpretation_match,
    make_groundedness,
    make_materialisation,
    structural_conformance,
)
from qre.eval.gate import GATE_THRESHOLDS, check_gate  # noqa: F401
from qre.eval.graph import GraphClient  # noqa: F401
from qre.eval.runner import run_eval  # noqa: F401


class EngineTask(Protocol):
    """The engine seam: a typed callable from ResolveRequest to ResolveResponse."""

    def __call__(self, request: ResolveRequest) -> ResolveResponse: ...


__all__ = [
    "sync_dataset",
    "run_eval",
    "check_gate",
    "GATE_THRESHOLDS",
    "EngineTask",
    "GraphClient",
    "structural_conformance",
    "interpretation_match",
    "behaviour_by_tag",
    "axis_classification",
    "make_groundedness",
    "make_materialisation",
    "DEFAULT_ITEM_EVALUATORS",
    "DEFAULT_RUN_EVALUATORS",
]
