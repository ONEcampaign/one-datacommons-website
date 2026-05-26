"""Slot-binding stage of the predicate-paradigm pipeline.

One google-genai call maps a natural-language query (via its ShapeContext) to a
fully structured ``(Shape, Predicate)`` pair, or returns an ``AskClarification``
when the model cannot commit.

Pipeline position::

    build_shape_context(query, candidates)   ← shape.py
    → bind(shape_context)                   ← this module
    → materialize(namespace, predicate, …)  ← predicate.py
"""

from __future__ import annotations

import itertools
import logging
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import BaseModel, Field

from dc_search import llm
from dc_search.place_role import offerable_places_for_slot
from dc_search.predicate import AskClarification, Predicate
from dc_search.shape import Shape, ShapeContext
from dc_search.telemetry import Usage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BindResult:
    """Successful result from ``bind``.

    Attributes:
        shape: The elected ``Shape``.
        predicates: One predicate per cross-product element (always a tuple).
        usage: LLM token-usage telemetry, or ``None`` on the topic-shape path.
        defaulted_recipient: ``True`` when a DevelopmentFinance recipient was
            assigned by the unqualified-place default (i.e. no "to X" cue
            was present in the query) — drives the
            ``interpreted_place_as_recipient`` caveat in S6.
    """

    shape: Shape
    predicates: tuple[Predicate, ...]
    usage: Usage | None
    defaulted_recipient: bool


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an indicator-search agent for a knowledge graph of statistical
indicators (StatisticalVariable nodes). Your job is to map a natural-language
query to the indicator the user is asking about — never to answer the query
itself with a data value.

You do not have access to observation values. A downstream system fetches
the data after you have selected an indicator. The user is searching a
catalog; the deliverable is "this indicator measures the phenomenon named
in the query", and that is the entire deliverable. Do NOT refuse on the
grounds that you cannot supply a number, that the location is small or
obscure, or that the data may be unavailable — those concerns are out of
scope. If a candidate shape clearly measures the phenomenon named in the
query, elect it and leave underspecified slots null — short or vague
queries are normal and not grounds to defer.

You will be given:

1. The user query.
2. A list of candidate shapes (groups of related indicators), each with:
   - header "Shape N" — N is the value to emit as chosen_shape_index
   - is_topic: whether this shape is a Topic node (curated indicator collection)
   - populationType, measuredProperty
   - constraint_keys: which slots can be bound for indicators in this shape
   - features (when present): measurementDenominator, measurementQualifier,
     statType — structural properties from the knowledge graph that mark
     per-capita / share-of-X variants, temporal aggregation, etc.
   - slot_taxonomy: observed values per slot (the value space to choose from)
   - sample member DCIDs (up to 5 examples)

Pick ONE shape (chosen_shape_index).  Then for each of its constraint_keys,
emit one entry in `bindings` with "slot" set to the constraint key and
"value" set to one value from slot_taxonomy[key] when the query names a
value for that slot, or null when it does not.  Every constraint_key MUST
appear in bindings; do NOT omit a key.  An absent key changes downstream
semantics; null is the correct way to express "no value mentioned".

Before falling back to null, scan slot_taxonomy for a value that
matches what the query says.  Aggregate or "all of X" phrasing in the
query often corresponds to a literal aggregate value in the taxonomy
(world/total/all-of-some-region rollups appear as ordinary entries
alongside the granular ones).  Only emit null when nothing in
slot_taxonomy reasonably matches what the query says.

Leaving a slot null is correct for genuinely under-specified queries
— a downstream system fans out across all values for that slot.

When the query mentions multiple values for the same slot, emit a JSON
array of value DCIDs for that slot (e.g. ["country/KEN", "country/TGO"]).
Use this only for values that appear in the slot's slot_taxonomy.  A single
value remains a string; underspecified remains null.  Treat "and" and "or"
between values identically — both mean "any of these" because each
indicator records exactly one value per slot, so AND across values is
always empty. If the user mentions a value not in the slot taxonomy, drop
it silently; if no values survive, leave the slot null.

TOPIC vs STRUCTURED shapes.  A Topic shape carries one member_dcid that
expands downstream into many SVs — it is a roll-up.  A structured shape
names a populationType + measuredProperty and lists specific
`sample_dcids`; those sample DCIDs are the indicators the shape will
return.

When choosing between a Topic and a structured shape, read the
structured shape's `sample_dcids`.  If they include an SV that names
the concept the query is asking to measure, elect that structured
shape — even if a Topic shape ranks higher, and even if the query's
phrasing is closer to the Topic's name than to the structured shape's
populationType label.  The sample DCIDs are what gets returned; trust
them over labels.

In particular: if Shape 0 (the highest-ranked candidate) is a
structured shape whose `sample_dcids` include an SV matching the
query, elect Shape 0.  Do not pass over Shape 0 for a lower-ranked
Topic shape.

Elect a Topic shape only when no structured shape has a matching
sample DCID.  A query that names one measure ("the X rate in Y", "X
per capita in Y", "amount of X") is not broad — prefer the structured
shape with that measure in its samples.  Topic shapes are correct for
queries like "indicators related to X" or "everything about X", which
ask for a roll-up rather than one measure.  Emit bindings: [] when
electing a Topic shape — the field is unused on this path but required
for schema validity.

Among shapes of the same type, the list is in order of relevance;
prefer the earliest unless there is a specific reason to deviate.

Set ask ONLY when no shape can plausibly be elected — i.e. the query asks
for an axis that is not encoded at indicator level, or none of the
candidate shapes relate to the phenomenon named in the query.  Do NOT use
ask to express uncertainty about data availability, to apologise for
missing values, or to defer because the query is short or broad.

Output format example (structured shape with two slots):
{
  "chosen_shape_index": 0,
  "bindings": [
    {"slot": "gender", "value": "Female"},
    {"slot": "race", "value": null}
  ],
  "ask": null
}

user_named_places — when present under a slot, this block lists places that
were resolved verbatim from the user's query and are on-taxonomy for that slot.
These are authoritative; use them as follows:
- "to X"   → bind X to that slot (X is the recipient/destination).
- "from X" → do NOT bind X to that slot (X is the observation subject / donor;
             leave the slot null so the system treats X as the entity).
- Unqualified place (no "from"/"to" cue) AND a DevelopmentFinance shape →
  default X to the offered recipient slot (the most common intent for
  development-finance queries); the pipeline will emit an
  "interpreted_place_as_recipient" caveat to signal the default was applied.
- Any other shape type: follow normal slot-taxonomy matching rules.
"""


class _SlotBinding(BaseModel):
    """One slot→value pair emitted by the LLM."""

    slot: str = Field(description="Slot key from the chosen shape's slot_taxonomy.")
    value: str | list[str] | None = Field(
        default=None,
        description="Single DCID, list of DCIDs, or null for wildcard.",
    )


class _Output(BaseModel):
    """Structured output from the slot-binding LLM call."""

    chosen_shape_index: int
    """Index into shape_context.shapes for the elected shape."""
    bindings: list[_SlotBinding] = Field(default_factory=list)
    """Slot bindings — one entry per constraint_key of the chosen shape."""
    ask: str | None = None
    """If the model cannot bind, populate this with a clarifying question."""


# ---------------------------------------------------------------------------
# Multi-value fan-out constants
# ---------------------------------------------------------------------------

# Maximum number of Predicates the cross-product may produce.  If the product
# would exceed this limit, the largest list slot is replaced with None
# (wildcard) and the product is recomputed.  Recursion terminates because each
# step removes at least one multi-valued slot.
_MAX_PREDICATES: int = 16


# ---------------------------------------------------------------------------
# ContextVars for per-request side-channel state
# ---------------------------------------------------------------------------

# ContextVars are isolated per asyncio Task (per uvicorn request context),
# preventing cross-request data races under concurrent uvicorn workers.
_last_raw_output_var: ContextVar[_Output | None] = ContextVar("_last_raw_output", default=None)
_last_user_message_var: ContextVar[str | None] = ContextVar("_last_user_message", default=None)
_last_usage_var: ContextVar[Usage | None] = ContextVar("_last_usage", default=None)


def get_last_raw_output() -> _Output | None:
    return _last_raw_output_var.get()


def get_last_user_message() -> str | None:
    return _last_user_message_var.get()


def get_last_usage() -> Usage | None:
    return _last_usage_var.get()


# ---------------------------------------------------------------------------
# User-message builder
# ---------------------------------------------------------------------------


def _build_user_message(shape_context: ShapeContext) -> str:
    """Format a ShapeContext as a readable user message for the LLM.

    Lays out:
    - The query string.
    - Keyword cues (shape discriminators, place tokens, modifiers).
    - Each candidate shape with index, namespace, axes, constraint taxonomy,
      and up to 5 sample member DCIDs.
    """
    lines: list[str] = []

    lines.append(f"QUERY: {shape_context.query}")
    lines.append("")

    lines.append(f"CANDIDATE SHAPES ({len(shape_context.shapes)} total):")
    lines.append("")

    for idx, shape in enumerate(shape_context.shapes):
        lines.append(f"Shape {idx}:")
        lines.append(f"  is_topic: {shape.is_topic}")

        if shape.is_topic:
            # Topic shapes: render name/description from fetched metadata when
            # available, falling back to the DCID slug for lexical matching.
            topic_dcid = shape.member_dcids[0] if shape.member_dcids else "(unknown)"
            lines.append(f"  topic_dcid: {topic_dcid}")
            meta = shape_context.topic_metadata.get(topic_dcid)
            if meta and meta.name:
                lines.append(f"  name: {meta.name}")
            else:
                # DCID-slug fallback (no network fetch available here).
                readable = topic_dcid.split("/")[-1].replace("-", " ").replace("_", " ")
                lines.append(f"  name: {readable}")
            if meta and meta.description:
                truncated = meta.description[:200]
                if len(meta.description) > 200:
                    truncated += "…"
                lines.append(f"  description: {truncated}")
            lines.append("  constraint_keys: (none — Topic shape, no slot binding)")
            lines.append(
                "  slot_taxonomy:    (none — elect this shape to get the full SV collection)"
            )
        else:
            lines.append(f"  populationType:  {shape.population_type or '(none)'}")
            lines.append(f"  measuredProperty: {shape.measured_property or '(none)'}")
            lines.append(f"  constraint_keys: {list(shape.constraint_keys) or '(none)'}")

            # Structural features from DC's property graph. These expose
            # modifier signal (per-capita, share-of-GDP, annual, …) directly
            # rather than forcing the model to infer it from DCID substrings.
            # Empty for SDG / worldBank / CRS_DAC namespaces; populated for
            # DC-native and WHO.
            if shape.measurement_denominators or shape.measurement_qualifiers or shape.stat_types:
                lines.append("  features:")
                if shape.measurement_denominators:
                    lines.append(
                        f"    measurementDenominator: {list(shape.measurement_denominators)}"
                    )
                if shape.measurement_qualifiers:
                    lines.append(
                        f"    measurementQualifier:   {list(shape.measurement_qualifiers)}"
                    )
                if shape.stat_types:
                    lines.append(f"    statType:               {list(shape.stat_types)}")

            if shape.slot_taxonomy:
                lines.append("  slot_taxonomy:")
                for slot, values in shape.slot_taxonomy.items():
                    value_list = list(values)
                    # Show at most 15 values to keep the prompt manageable.
                    if len(value_list) > 15:
                        shown = value_list[:15]
                        lines.append(f"    {slot}: {shown} … (+{len(value_list) - 15} more)")
                    else:
                        lines.append(f"    {slot}: {value_list}")

                    # Offer query-resolved places that are on-taxonomy for this slot.
                    offerable = offerable_places_for_slot(
                        resolved_places=shape_context.resolved_places,
                        slot_values=values,
                    )
                    if offerable:
                        lines.append("    user_named_places:")
                        for dcid in offerable:
                            # Find the canonical_name (field 1) and input_surface (field 2)
                            # for this DCID in resolved_places (4-tuple: dcid, name, surface, role).
                            display_name: str | None = None
                            input_surface_label: str | None = None
                            for (
                                rp_dcid,
                                rp_name,
                                rp_surface,
                                _rp_role,
                            ) in shape_context.resolved_places:
                                if rp_dcid == dcid:
                                    display_name = rp_name
                                    # Prefer input_surface over canonical slug (api-ux minor fix):
                                    # when canonical_name is None, the surface is a better label.
                                    input_surface_label = rp_surface
                                    break
                            label = (
                                display_name
                                if display_name is not None
                                else (input_surface_label or dcid.rsplit("/", 1)[-1])
                            )
                            lines.append(
                                f"      - {dcid} ({label})"
                                "  [resolved from query — assign a role via directional language]"
                            )
            else:
                lines.append("  slot_taxonomy: (no constraint slots)")

            sample = list(shape.member_dcids[:5])
            lines.append(f"  sample_dcids:   {sample}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _bindings_to_dict(
    bindings: list[_SlotBinding],
) -> dict[str, str | list[str] | None]:
    """Convert a list of _SlotBinding to the dict form used by _explode_constraints."""
    return {b.slot: b.value for b in bindings}


def _explode_constraints(
    constraints: dict[str, str | list[str] | None],
) -> tuple[dict[str, str | None], ...]:
    """Cross-product list-valued constraint slots into N scalar dicts.

    Rules:
    - Singleton lists are normalised to their scalar value before exploding.
    - The cross-product of all list-valued slots is computed; each element is
      a fully-scalar ``dict[str, str | None]``.
    - If the cross-product cardinality would exceed ``_MAX_PREDICATES``, the
      **largest** list is replaced with ``None`` (wildcard) and the function
      recurses until the product is within the cap.
    - An empty input dict produces ``({},)`` — one empty predicate, matching
      the single-predicate behaviour that exists today.
    """
    # Separate list-valued slots from scalar slots; normalise singleton lists.
    lists: dict[str, list[str]] = {}
    scalars: dict[str, str | None] = {}
    for slot, val in constraints.items():
        if isinstance(val, list):
            if len(val) == 1:
                scalars[slot] = val[0]
            else:
                lists[slot] = val
        else:
            scalars[slot] = val

    if not lists:
        # No multi-valued slots: single predicate, scalar constraints.
        return (dict(scalars),)

    # Check cap before building the product.
    product_size = 1
    for vals in lists.values():
        product_size *= len(vals)

    if product_size > _MAX_PREDICATES:
        # Replace the largest list with None (wildcard) and recurse.
        largest_slot = max(lists, key=lambda s: len(lists[s]))
        reduced: dict[str, str | list[str] | None] = dict(constraints)
        reduced[largest_slot] = None
        return _explode_constraints(reduced)

    # Build cross-product: each combination produces one scalar dict.
    slot_names = list(lists.keys())
    value_combos = list(itertools.product(*[lists[s] for s in slot_names]))
    result: list[dict[str, str | None]] = []
    for combo in value_combos:
        row: dict[str, str | None] = dict(scalars)
        for slot, val in zip(slot_names, combo, strict=True):
            row[slot] = val
        result.append(row)
    return tuple(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def bind(
    shape_context: ShapeContext,
    *,
    model: str | None = None,
) -> BindResult | AskClarification:
    """Bind slots to a chosen shape and return a ``BindResult``.

    Makes one google-genai call via ``llm.generate_structured``.  On any
    failure (model returns an ``ask`` field, output validation fails, or the
    chosen index is out of range) returns an ``AskClarification``.

    Multi-value constraints (list-valued slots emitted by the LLM) are
    cross-producted by ``_explode_constraints`` into N fully-scalar
    ``Predicate`` instances.  The caller receives a ``tuple[Predicate, ...]``
    in all cases (1-tuple for the single-value / wildcard / Topic paths).

    For ``DevelopmentFinance`` shapes a deterministic post-correction step
    runs before ``_explode_constraints``.  For each offered place P on a
    place-typed slot S the query's directional language determines role:
    donor → P must not appear in that slot; recipient → P is forced into that
    slot; ambiguous + slot null → P is defaulted to that slot and
    ``defaulted_recipient`` is set; ambiguous + slot already bound → kept.

    Args:
        shape_context: Prepared shape context from ``shape.build_shape_context``.
        model: Optional model string override (test override only).
            Production path passes nothing → uses ``llm.MODEL``.

    Returns:
        A ``BindResult`` when binding succeeds, or an ``AskClarification``
        when it cannot.
    """
    if not shape_context.shapes:
        _last_raw_output_var.set(None)
        _last_user_message_var.set(None)
        return AskClarification(
            reason="retrieval_weak",
            message=(
                "Shape grouping produced no candidate shapes for the query. "
                "The candidate set may have contained only topic-style nodes "
                "without a populationType or measuredProperty. Try rephrasing "
                "the query."
            ),
        )

    user_message = _build_user_message(shape_context)
    _last_user_message_var.set(user_message)

    model_label = model or llm.MODEL

    try:
        parsed, usage = await llm.generate_structured(
            prompt=user_message,
            system=_SYSTEM_PROMPT,
            schema=_Output,
            model=model_label,
            thinking=False,
        )
        # Set ContextVars INSIDE try, BEFORE any branch decision so token
        # telemetry is preserved even when the clarification path is taken.
        _last_usage_var.set(usage)
        output: _Output = parsed
        _last_raw_output_var.set(output)
    except Exception:
        # Fixed message only — do NOT interpolate the exception into the response
        # body. Exception details are logged server-side via exc_info=True.
        logger.warning(
            "slot_binding parse_error",
            exc_info=True,
            extra={"model": model_label},
        )
        return AskClarification(
            reason="parse_error",
            message="The search model could not process this query. Try rephrasing.",
        )

    # Model signalled it cannot bind. Cap at 500 chars to limit LLM-controlled
    # text in the HTTP response (prompt-injection surface).
    if output.ask:
        logger.info("slot_binding ask: %s", output.ask)
        return AskClarification(
            reason="under_specified",
            message=output.ask[:500] if output.ask else "...",
            proposed_clarifications=[],
        )

    # Validate chosen index.
    if output.chosen_shape_index < 0 or output.chosen_shape_index >= len(shape_context.shapes):
        return AskClarification(
            reason="ambiguous_shape",
            message=(
                f"The model chose shape index {output.chosen_shape_index}, which "
                f"is out of range (0-{len(shape_context.shapes) - 1}). "
                "Please try a more specific query."
            ),
            proposed_clarifications=[],
        )

    chosen_shape = shape_context.shapes[output.chosen_shape_index]

    # Topic shapes: translate the LLM's election back to a Topic Predicate
    # with the relevantTopic constraint pointing at the topic DCID.
    if chosen_shape.is_topic:
        topic_dcid = chosen_shape.member_dcids[0] if chosen_shape.member_dcids else ""
        predicate = Predicate(
            population_type=None,
            measured_property=None,
            constraints={"relevantTopic": topic_dcid},
        )
        return BindResult(
            shape=chosen_shape,
            predicates=(predicate,),
            usage=usage,
            defaulted_recipient=False,
        )

    # Non-topic: start with the raw LLM bindings dict.
    constraints: dict[str, str | list[str] | None] = _bindings_to_dict(output.bindings)

    # ------------------------------------------------------------------
    # Deterministic post-correction (DevelopmentFinance shapes only).
    # For each constraint slot that has offerable query-resolved places,
    # apply directional-role logic to override or confirm the LLM's choice.
    # This runs BEFORE _explode_constraints so the correction applies to
    # scalar constraints (multi-value explode happens after).
    # ------------------------------------------------------------------
    defaulted_recipient = False

    if chosen_shape.population_type == "DevelopmentFinance":
        for slot, slot_values in chosen_shape.slot_taxonomy.items():
            offerable = offerable_places_for_slot(
                resolved_places=shape_context.resolved_places,
                slot_values=slot_values,
            )
            if not offerable:
                continue

            for dcid in offerable:
                # Read the pre-computed role from the 4-tuple (dcid, name, surface, role).
                # The role was determined in the pipeline from the ORIGINAL full query —
                # not from the per-variable scoped shape_query — so "from X to Y" grammar
                # is correctly resolved even when shape_context.query is a scoped phrase
                # like "grants in us, Togo" (Amendment 2 reconciliation).
                role: str = "ambiguous"
                for rp_dcid, _rp_name, _rp_surface, rp_role in shape_context.resolved_places:
                    if rp_dcid == dcid:
                        role = rp_role
                        break

                current = constraints.get(slot)
                if role == "donor":
                    # Donor must NOT appear in this constraint slot.
                    # Clear if the LLM incorrectly bound it.
                    if current == dcid:
                        constraints[slot] = None
                elif role == "recipient":
                    # Recipient must appear in this slot (authoritative).
                    constraints[slot] = dcid
                else:
                    # Ambiguous: apply unqualified-place default for DevFinance.
                    if current is None:
                        constraints[slot] = dcid
                        defaulted_recipient = True
                    elif current == dcid:
                        # LLM already bound the offered DCID — the slot is
                        # still ambiguous-defaulted; mark the caveat so callers
                        # know the binding was not deterministic.
                        defaulted_recipient = True
                    # else: slot bound to a different value by LLM — keep as-is.

    # Explode list-valued slots into N scalar Predicates.
    constraint_dicts = _explode_constraints(constraints)
    predicates = tuple(
        Predicate(
            population_type=chosen_shape.population_type,
            measured_property=chosen_shape.measured_property,
            constraints=cd,
        )
        for cd in constraint_dicts
    )

    return BindResult(
        shape=chosen_shape,
        predicates=predicates,
        usage=usage,
        defaulted_recipient=defaulted_recipient,
    )
