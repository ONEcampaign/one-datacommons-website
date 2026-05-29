"""LLM call #1 for the default endpoint — structured query extraction.

Parses a natural-language query into thematic variables, entities, and date
references.  Used exclusively by ``pipeline.run_default`` before multi-variable
fan-out.

Sample output for ``"GDP per capita in Kenya and Uganda since 2010"``::

    {
      "variables": ["GDP per capita"],
      "entities": ["Kenya", "Uganda"],
      "dates": [{"kind": "range", "start": "2010", "end": null}]
    }
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from dc_search import llm
from dc_search.telemetry import Usage

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ExtractedDate(BaseModel):
    kind: Literal["point", "range", "latest"] = Field(
        description=(
            'Date reference type. "point" for a single year/month, "range" for a span '
            'with a start and/or end bound, "latest" ONLY when the query explicitly asks '
            'for the most recent / current value using a word like "current", "latest", '
            '"now", or "today" (e.g. "current population"). Do not use "latest" merely '
            "because a query omits a date."
        )
    )
    # Date strings are LLM-emitted; cap at 32 chars to cover ISO 8601 with timezone
    # (e.g. "2010-01-01T00:00:00+00:00" = 25 chars) while blocking log-injection.
    start: str | None = Field(
        default=None,
        max_length=32,
        description='ISO year string (e.g. "2010"); null when the start bound is open '
        'or kind is "latest".',
    )
    end: str | None = Field(
        default=None,
        max_length=32,
        description='ISO year string (e.g. "2020"); null when the end bound is open '
        'or kind is "latest".',
    )


class QueryExtraction(BaseModel):
    variables: list[str] = Field(
        description=(
            "One entry per distinct thematic measure, as a short noun phrase in ENGLISH "
            "suitable for indicator search. If the query is in another language, "
            'translate the measure to its standard English term ("esperanza de vida" → '
            '"life expectancy"). Split conjunctive queries — "life expectancy and '
            'population" yields two entries. Fold any qualifier directly into the '
            'phrase: "GDP per capita" (not "GDP"), "population as % of total", '
            '"inflation-adjusted GDP". Never merge two distinct measures into one entry.'
        )
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Place names or named regions, rendered in ENGLISH. If the query is in "
            "another language, translate each place name to its common English name "
            '("España" → "Spain", "Allemagne" → "Germany", "Costa de Marfil" → '
            '"Ivory Coast"). When the place name is already English, keep it as '
            "written. Empty list when the query names no place."
        ),
    )
    dates: list[ExtractedDate] = Field(
        default_factory=list,
        description=(
            "Date references in the query. Leave empty when the query has no temporal "
            "reference at all — do not infer an implicit date for an otherwise "
            "timeless query."
        ),
    )
    contained_in_parents: list[str] = Field(
        default_factory=list,
        description=(
            "The subset of `entities` to expand into the places they CONTAIN. Add an "
            "entity here when the query asks about the places INSIDE it rather than the "
            'place itself ("African countries" → "Africa"; "counties in California" → '
            '"California"; "districts of India" → "India"; "across US states" → '
            '"United States"). Leave a plainly-named place out when the query is about '
            'that place itself ("poverty in Kenya", "aid to Africa", "France"), even '
            "when another entity in the SAME query is a contained-in parent — decide "
            "per entity. Zero, one, or several entities may qualify, independently of "
            "each other and of any role a place plays in the query (a donor/source "
            "place is expanded only if the query asks for the places inside IT). Each "
            "value MUST appear verbatim in `entities`, which still names the PARENT(S) "
            "ONLY — never enumerate the children here or in `entities`. Empty when no "
            'entity is being broken into its contents, including when "across"/"within" '
            'modifies a concept rather than a place ("GDP across sectors").'
        ),
    )

    @property
    def contained_in(self) -> bool:
        """True when at least one entity is flagged for contained-in expansion.

        Derived view of ``contained_in_parents`` for boolean consumers (the
        interpretation echo, logging). Expansion itself is scoped per-entity off
        the list, not this flag.
        """
        return bool(self.contained_in_parents)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# [[TODAY]] is substituted with the current date at call time by _system_instruction().
_EXTRACTION_SYSTEM_PROMPT: str = """\
You are a structured-data extraction assistant for a statistical data search engine.
Given a natural-language query, populate the provided schema and return ONLY the JSON
object — no preamble, no commentary, no markdown fences. Each field's description in
the schema defines how to fill it; follow those exactly, and leave optional fields as
empty lists rather than guessing.

Today's date is [[TODAY]]. Your own knowledge cutoff predates this, so resolve any
relative or open-ended time reference ("current", "now", "recent", "over the last
decade", "since") against today's date above — not your own sense of the current year.

The query may be written in any language. ALWAYS emit the `variables` and `entities`
fields in English — the downstream indicator search and place resolver are English-only.
Translate foreign-language measures and place names into their standard English form;
preserve proper nouns that have no distinct English equivalent. Dates are
language-independent (emit ISO year strings regardless of the query language).

Example — "GDP per capita in Kenya and Uganda since 2010":
{"variables": ["GDP per capita"], "entities": ["Kenya", "Uganda"],
 "dates": [{"kind": "range", "start": "2010", "end": null}]}

Example (non-English) — "esperanza de vida en España y Alemania desde 2010":
{"variables": ["life expectancy"], "entities": ["Spain", "Germany"],
 "dates": [{"kind": "range", "start": "2010", "end": null}]}

Example (contained-in) — "poverty rate in US states":
{"variables": ["poverty rate"], "entities": ["United States"],
 "dates": [], "contained_in_parents": ["United States"]}

Example (mixed — one place expands, one does not) —
"malaria grants from France to African countries":
{"variables": ["malaria grants"], "entities": ["France", "Africa"],
 "dates": [], "contained_in_parents": ["Africa"]}
(France is named as one place; only "African countries" asks for the places inside
Africa — so only "Africa" is expanded, regardless of which place is the funder.)

Counter-example (contained_in_parents must be empty) — "GDP across sectors":
{"variables": ["GDP"], "entities": [],
 "dates": [], "contained_in_parents": []}
(Here "across" modifies a concept, not a named parent place — no parent to expand.)
"""


def _system_instruction() -> str:
    """Return the system prompt with ``[[TODAY]]`` resolved to today's date.

    The injected date anchors the model when it resolves relative or open-ended
    time references; without it the model falls back on its training-cutoff sense
    of "now" and emits stale year ranges (e.g. "over the last decade" → 2014–2024).
    """
    return _EXTRACTION_SYSTEM_PROMPT.replace("[[TODAY]]", date.today().isoformat())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract(
    query: str,
    *,
    model: str | None = None,
) -> tuple[QueryExtraction, Usage]:
    """Extract structured fields from a natural-language query.

    Args:
        query: The user's raw query string.
        model: Override the model for this call (test use only). Production
            callers pass nothing and the value of ``llm.MODEL`` is used.

    Returns:
        A ``(QueryExtraction, Usage)`` tuple.
    """
    result, usage = await llm.generate_structured(
        prompt=query,
        system=_system_instruction(),
        schema=QueryExtraction,
        model=model or llm.MODEL,
        thinking=False,
    )
    return result, usage
