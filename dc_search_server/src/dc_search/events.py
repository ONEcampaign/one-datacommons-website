"""SSE event models for the dc-search streaming interface.

Defines the discriminated-union of typed ``Event`` models emitted by the
pipeline generators (``stream_default``, ``stream_simple``) and consumed by the
SSE route layer (``app.py``).  Also provides ``serialize_sse`` — the frame
serializer that turns any event into an SSE ``event:``/``data:`` pair.

This module is the wire contract: every other slice (pipeline, routes, tests)
imports from here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

from dc_search.extraction import ExtractedDate
from dc_search.interpretation import ResolvedPlace
from dc_search.predicate import AnswerCollection, AskClarification
from dc_search.telemetry import TelemetryLLMUsage

__all__ = [
    "Start",
    "Interpretation",
    "Places",
    "Stage",
    "Result",
    "Done",
    "Error",
    "DoneTelemetry",
    "Event",
    "serialize_sse",
]

_Terminated = Literal["answer", "ask", "no_candidates", "error"]


def _answer_kind(v: Any) -> str:
    """Callable discriminator for the Result.answer union.

    AskClarification has a `reason` field; AnswerCollection does not. Works on
    both a model instance (round-trip from our own code) and a parsed dict
    (round-trip from JSON), so pydantic never silently mis-classifies — which
    would corrupt the drain's `isinstance` filter.
    """
    if isinstance(v, dict):
        return "clarification" if "reason" in v else "answer"
    return "clarification" if isinstance(v, AskClarification) else "answer"


class Start(BaseModel):
    """Emitted immediately, before any LLM work begins."""

    model_config = ConfigDict(frozen=True)
    type: Literal["start"] = "start"
    query: str
    mode: Literal["default", "simple"]


class Interpretation(BaseModel):
    """Extraction outcome (default endpoint only); drives UI skeleton count."""

    model_config = ConfigDict(frozen=True)
    type: Literal["interpretation"] = "interpretation"
    variables: list[str]
    entities: list[str]
    dates: list[ExtractedDate]
    expected_results: int  # == len(variables) post-cap (1 for the fallback)
    truncated: bool


class Places(BaseModel):
    """Resolved places (default + simple); emitted after interpretation, concurrent
    with variable fan-out.  Lightweight — does not gate the interpretation event.
    """

    model_config = ConfigDict(frozen=True)
    type: Literal["places"] = "places"
    places: list[ResolvedPlace]


class Stage(BaseModel):
    """Coarse progress, simple endpoint only."""

    model_config = ConfigDict(frozen=True)
    type: Literal["stage"] = "stage"
    stage: Literal["retrieving", "binding", "materializing"]


class Result(BaseModel):
    """One variable branch completed."""

    model_config = ConfigDict(frozen=True)
    type: Literal["result"] = "result"
    index: int
    variable_label: str | None
    # SSE-only tag so the frontend (and pydantic) can discriminate the union
    # without touching AnswerCollection/AskClarification in predicate.py (which
    # would leak a new field into the buffered SearchResponse JSON). Set
    # explicitly at construction by the generator.
    outcome_kind: Literal["answer", "clarification"]
    answer: Annotated[
        Annotated[AnswerCollection, Tag("answer")]
        | Annotated[AskClarification, Tag("clarification")],
        Discriminator(_answer_kind),
    ]


class DoneTelemetry(BaseModel):
    """Telemetry block carried in the terminal `done` event.

    Field set matches what PipelineResult/TelemetryBlock need so the buffered
    drain assembles the response without re-deriving anything.
    """

    model_config = ConfigDict(frozen=True)
    llm_usage: list[TelemetryLLMUsage]
    n_candidates: int
    n_shapes: int
    terminated_by: _Terminated
    truncated: bool = False


class Done(BaseModel):
    """Terminal event (success OR soft-timeout with partials).

    AUTHORITATIVE FIELDS: clients read top-level `terminated_by` / `truncated`.
    `telemetry.{terminated_by,truncated}` carry the same values but exist for the
    buffered drain's PipelineResult mapping; `_build_done` writes both from one
    computation (a unit test asserts equality). `timed_out=True` marks the soft-
    deadline degraded path (telemetry reflects only the partials gathered so far).
    """

    model_config = ConfigDict(frozen=True)
    type: Literal["done"] = "done"
    telemetry: DoneTelemetry
    elapsed_s: float
    terminated_by: _Terminated
    truncated: bool = False
    timed_out: bool = False
    ask: AskClarification | None = None


class Error(BaseModel):
    """Terminal failure event — generic sanitized detail only."""

    model_config = ConfigDict(frozen=True)
    type: Literal["error"] = "error"
    detail: str


Event = Annotated[
    Start | Interpretation | Places | Stage | Result | Done | Error,
    Field(discriminator="type"),
]


def serialize_sse(event: Event) -> str:
    """Serialize one event to an SSE frame: `event:` line + `data:` JSON line + blank line.

    The JSON payload also carries the `type` discriminator (NDJSON-tolerant parsers).
    """
    payload = event.model_dump_json()
    return f"event: {event.type}\ndata: {payload}\n\n"
