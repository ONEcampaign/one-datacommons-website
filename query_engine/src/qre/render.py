"""Read-back sentence render: the one shared library that turns a resolved Spec
into the confirmation sentence.

Pure and total: reads only GraphRef labels already on the Spec, makes no graph,
LLM, or I/O call, and never raises on a well-formed Spec. The fixed join order is
fixed by .design/sentence-ownership.md; wording, separators, and inflection are
this module's own concern and may change without a contract bump.
"""
from __future__ import annotations

from qre.models import (
    Binding,
    BindingSet,
    BindingValue,
    EntityRoleDirectional,
    NoDataReason,
    SlotValue,
    Spec,
    TimeWindow,
)

# no_data reason -> phrase. Keys are the frozen NoDataReason values; an unknown
# code falls back to _GENERIC_NO_DATA. Wording is library-owned.
_NO_DATA_PHRASES: dict[str, str] = {
    "no_observations": "No data found for this query.",
    "entity_not_resolved": "Could not resolve the place in this query.",
    "variable_not_resolved": "Could not resolve the variable in this query.",
    "denominator_not_available": "No denominator is available for this per-capita query.",
}
_GENERIC_NO_DATA = "No data is available for this query."


def render_sentence(spec: Spec, *, n_measures: int = 1) -> str:
    """Render the confirmation sentence for a resolved Spec.

    Walks the Spec in the fixed join order: measured thing, how-qualifiers, place,
    named subject, time window, source. Reads only labels already on the Spec.

    When n_measures >= 2 the sentence carries a parenthetical noting this is the
    primary conjunct of a multi-measure query.
    """
    parts: list[str] = []

    # 1. Measured thing: the `what` slot's bound value label(s), else the shape's
    #    measured-property label (covers no-`what`-slot and unbound/absent `what`).
    what_labels = _slot_value_labels(spec, axis="what")
    parts.append(_join_labels(what_labels) if what_labels
                 else spec.shape.measured_property.label)

    # 2. How qualifiers, in slot order ("for health").
    for slot in spec.slots:
        if slot.key.axis != "how":
            continue
        labels = _binding_labels(slot.binding)
        if labels:
            parts.append("for " + _join_labels(labels))

    # 3. Place: a directional "to" recipient ("to Ethiopia") takes precedence over
    #    a bound `where` slot ("in Kenya"). Direction comes from the entity role.
    place = _render_place(spec)
    if place:
        parts.append(place)

    # 4. Named subject: scan entities for role.direction == "from".
    for entity in spec.entities:
        role = entity.role
        if isinstance(role, EntityRoleDirectional) and role.direction == "from":
            parts.append("from " + entity.ref.label)
            break

    # 5. Time window from coverage.window. A single-year "in YYYY" reads better
    #    space-joined ("Flow in 2020"); ranges and open-ended forms take a leading
    #    comma ("Flow, 2015 to 2023").
    window_text = (
        _render_window(spec.coverage.window) if spec.coverage.window is not None else ""
    )

    # 6. Source, when a source slot is bound ("according to" avoids colliding with
    #    the donor "from"; source slots are unbound in v1, so this is rarely hit).
    source_labels = _slot_value_labels(spec, axis="source")
    source_text = "according to " + _join_labels(source_labels) if source_labels else ""

    body = " ".join(parts)
    if window_text:
        sep = " " if window_text.startswith("in ") else ", "
        body = f"{body}{sep}{window_text}" if body else window_text
    if source_text:
        body = f"{body} {source_text}" if body else source_text

    sentence = _as_sentence(body)
    if n_measures >= 2:
        sentence += f" (Primary conjunct of a {n_measures}-measure query.)"
    return sentence


def render_candidates_summary(n: int, *, n_measures: int = 1) -> str:
    """Render the candidates count summary, e.g. "2 possible interpretations."."""
    noun = "interpretation" if n == 1 else "interpretations"
    base = f"{n} possible {noun}."
    if n_measures >= 2:
        return f"{base} (Primary conjunct of a {n_measures}-measure query.)"
    return base


def no_data_phrase(reason: NoDataReason, *, n_measures: int = 1) -> str:
    """Map a NoData.reason code to a human phrase; unknown codes fall back."""
    base = _NO_DATA_PHRASES.get(reason, _GENERIC_NO_DATA)
    if n_measures >= 2:
        return f"{base} (Primary conjunct of a {n_measures}-measure query.)"
    return base


# --- internal helpers -------------------------------------------------------

def _binding_labels(binding: Binding) -> list[str]:
    """Bound value label(s) for a binding; [] for unbound/absent."""
    if isinstance(binding, BindingValue):
        return _value_labels([binding.value])
    if isinstance(binding, BindingSet):
        return _value_labels(binding.values)  # defensive: joins however many members
    return []


def _value_labels(values: list[SlotValue]) -> list[str]:
    labels: list[str] = []
    for v in values:
        if v.ref is not None:
            labels.append(v.ref.label)
        elif v.literal:
            labels.append(v.literal)
    return labels


def _slot_value_labels(spec: Spec, *, axis: str) -> list[str]:
    """Bound value labels for the first slot on `axis`; [] if none/unbound/absent."""
    for slot in spec.slots:
        if slot.key.axis == axis:
            return _binding_labels(slot.binding)
    return []


def _render_place(spec: Spec) -> str:
    for entity in spec.entities:
        role = entity.role
        if isinstance(role, EntityRoleDirectional) and role.direction == "to":
            return "to " + entity.ref.label
    where_labels = _slot_value_labels(spec, axis="where")
    return "in " + _join_labels(where_labels) if where_labels else ""


def _render_window(window: TimeWindow) -> str:
    s, e = window.start_year, window.end_year
    if s is not None and e is not None:
        return f"in {s}" if s == e else f"{s} to {e}"
    if s is not None:
        return f"since {s}"
    return f"until {e}"


def _join_labels(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def _as_sentence(text: str) -> str:
    """Capitalize the first character and ensure a terminal period.

    Wording polish so the definite read-back matches the no_data phrases, which are
    already full sentences. Library-owned; changes without a contract bump. Total on
    an empty string.
    """
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text
