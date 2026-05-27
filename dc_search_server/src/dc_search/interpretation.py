"""Interpretation models for the dc-search response.

Carries the human-readable, structured representation of a resolved query:
which place(s) were resolved (with alternatives), which variable phrases were
extracted, and which date windows apply.

This is a leaf module — it imports only ``dc_search.extraction.ExtractedDate``.
``events.py``, ``app.py``, ``pipeline.py``, and ``retrieval.py`` may all import
from here without creating a cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dc_search.extraction import ExtractedDate


class PlaceAlternative(BaseModel):
    """A candidate place resolution that was not selected as the primary match.

    ``name`` is always ``None`` for alternatives — only the primary place gets a
    canonical-name fetch. Contrast with ``ChildPlace``, whose ``name`` is populated.
    """

    model_config = ConfigDict(frozen=True)

    dcid: str
    name: str | None = None
    type: str | None = None


class ChildPlace(BaseModel):
    """A child place produced by contained-in expansion.

    Selected result, not an ambiguity alternative. Unlike PlaceAlternative,
    ``name`` is populated. See the ``set_valued_recipient`` caveat on
    ``AnswerCollection`` for the parallel signal."""

    model_config = ConfigDict(frozen=True)

    dcid: str
    name: str | None = None
    type: str | None = None


class ResolvedPlace(BaseModel):
    """A single place from the query resolved to a DCID with canonical name.

    ``input_name`` is the raw string extracted from the query.  ``dcid`` /
    ``name`` / ``type`` are populated by the name-fetch step (fail-open: may be
    ``None`` if resolution or the name fetch failed).  ``alternatives`` lists
    other candidates from ``resolve_places_batch`` that were not selected.
    """

    model_config = ConfigDict(frozen=True)

    input_name: str
    dcid: str | None = None
    name: str | None = None
    type: str | None = None
    alternatives: list[PlaceAlternative] = Field(default_factory=list)
    expanded: bool = False
    """``True`` when child-place expansion ran for this place."""
    child_type: str | None = None
    children: list[ChildPlace] = Field(default_factory=list)
    """Child places discovered by expansion. Paired with ``set_valued_recipient``
    caveat on ``AnswerCollection``."""


class QueryInterpretation(BaseModel):
    """Buffered interpretation assembled from the ``interpretation`` and ``places`` SSE events.

    ``variables``    — raw LLM-extracted phrase strings (the skeleton labels).
    ``places``       — resolved place objects (populated when a ``Places`` SSE event arrives).
    ``dates``        — extracted date windows from the LLM extraction step.
    ``contained_in`` — LLM extraction *intent*: true when the query asked for places
                       contained in a named parent. Independent of whether expansion
                       found any children — a response can have ``contained_in=True``
                       with every ``ResolvedPlace.expanded=False`` / empty ``children``
                       when no children were found (intent set, zero children, not an
                       error).

    Note: ``variables`` here are *strings* (LLM-extracted phrases), distinct from
    ``AnswerCollection.variables`` which are enriched ``ResolvedVariable`` objects.
    """

    model_config = ConfigDict(frozen=True)

    variables: list[str] = Field(default_factory=list)
    places: list[ResolvedPlace] = Field(default_factory=list)
    dates: list[ExtractedDate] = Field(default_factory=list)
    contained_in: bool = False
