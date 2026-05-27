"""Place-role classification helpers for the predicate-paradigm pipeline.

This is a pure leaf module: no imports from pipeline or hooks.  Its three
functions decide whether a resolved place is an observation **entity** (donor)
or a constraint **value** (recipient), and which places are offerable to a
given constraint slot based on DCID-namespace membership.

Pipeline position:
    pipeline._build_resolved_places_triples calls place_directional_role (once per query,
        from the ORIGINAL full query — not per-variable scoped shape_query; Amendment 2).
    slot_binding.bind() calls offerable_places_for_slot and reads pre-computed role from
        the 4-tuple — it no longer calls place_directional_role directly.
    pipeline._run_one_variable calls classify_place_roles (post-bind).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dc_search.predicate import Predicate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _namespace(dcid: str) -> str:
    """Return the namespace prefix of a place DCID.

    Defined as the segment before the first ``/``; the whole string when
    there is no ``/`` (e.g. bare country codes or unknown formats).

    Examples::

        _namespace("country/TGO")  → "country"
        _namespace("country/JOR")  → "country"
        _namespace("DAC/Health")   → "DAC"
        _namespace("NOGEO")        → "NOGEO"
    """
    slash = dcid.find("/")
    return dcid[:slash] if slash != -1 else dcid


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def offerable_places_for_slot(
    *,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...],
    slot_values: tuple[str, ...],
) -> tuple[str, ...]:
    """Return DCIDs from *resolved_places* whose namespace matches *slot_values*.

    A resolved place is offerable to a constraint slot when its DCID's
    namespace (segment before the first ``/``) matches the namespace of any
    value already observed in that slot's taxonomy.  This is self-calibrating:
    no hard-coded per-namespace table; the slot's own observed values define
    what is on-taxonomy.

    Args:
        resolved_places: Sequence of ``(dcid, canonical_name, input_surface, role)``
            4-tuples for every place the query resolved to.  ``role`` is one of
            ``"donor"``, ``"recipient"``, or ``"ambiguous"``, pre-computed from
            the original query by ``place_directional_role``.
        slot_values: Observed constraint values for the target slot, as
            returned by ``Shape.slot_taxonomy[slot_key]``.

    Returns:
        Tuple of DCIDs (preserving input order) whose namespace appears in the
        set of namespaces derived from *slot_values*.  Empty when
        *resolved_places* or *slot_values* is empty, or when no namespace
        matches.
    """
    if not resolved_places or not slot_values:
        return ()

    slot_namespaces = {_namespace(v) for v in slot_values}

    return tuple(
        dcid
        for dcid, _name, _surface, _role in resolved_places
        if _namespace(dcid) in slot_namespaces
    )


# Adjacency window: words within this distance of the preposition are
# considered "immediately preceded" by it.  Small to stay conservative
# (fail-open → "ambiguous").
_ADJACENCY_WORDS = 3

# Prepositions that signal a directional role.
_DONOR_PREPS = frozenset({"from"})
_RECIPIENT_PREPS = frozenset({"to"})

# Tokeniser: lower-case words (letters and apostrophes) and hyphens inside
# words, but strip punctuation at edges.
_TOKEN_RE = re.compile(r"[a-z][a-z'\-]*[a-z]|[a-z]")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def place_directional_role(
    *,
    query: str,
    input_surface: str | None,
    canonical_name: str | None,
    place_dcid: str,
) -> Literal["donor", "recipient", "ambiguous"]:
    """Classify a place's role by scanning for directional prepositions.

    Tokenises the query and searches for each candidate anchor phrase in
    order: *input_surface* first (verbatim query text, guaranteed to appear),
    then *canonical_name*, then the DCID's last path segment.  For the first
    anchor found in the query, checks whether a directional preposition appears
    within a small window immediately before it.

    Decision table::

        "from <anchor>" → "donor"
        "to <anchor>"   → "recipient"
        otherwise       → "ambiguous"

    Fail-open: any unexpected condition (no anchor locatable, no adjacent
    preposition) returns ``"ambiguous"``.

    Args:
        query: The raw user query string.
        input_surface: The verbatim text the user typed to name the place
            (e.g. ``"us"``), if available from extraction.  Tried first
            because it is guaranteed to appear literally in the query.
        canonical_name: Canonical display name from the DC API (e.g.
            ``"United States"``), or ``None``.  Tried second.
        place_dcid: DCID of the resolved place.  Its last path segment is
            used as the final fallback anchor (e.g. ``"USA"`` from
            ``"country/USA"``).

    Returns:
        ``"donor"``, ``"recipient"``, or ``"ambiguous"``.
    """
    # Build candidate anchors in priority order: input surface → canonical
    # name → DCID slug.  The input surface is tried first because it is the
    # only token guaranteed to appear literally in the query.
    last_segment = place_dcid.rsplit("/", 1)[-1]

    raw_anchors: list[str | None] = [input_surface, canonical_name, last_segment]
    anchors: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in raw_anchors:
        if not raw:
            continue
        tokens = _tokenize(raw)
        key = tuple(tokens)
        if tokens and key not in seen:
            anchors.append(tokens)
            seen.add(key)

    if not anchors:
        return "ambiguous"

    query_tokens = _tokenize(query)
    n = len(query_tokens)

    # Try each anchor; return the first definitive (non-ambiguous) result.
    for anchor_tokens in anchors:
        anchor_len = len(anchor_tokens)
        for i in range(n - anchor_len + 1):
            if query_tokens[i : i + anchor_len] != anchor_tokens:
                continue

            # Check the window before position i for a directional preposition.
            window_start = max(0, i - _ADJACENCY_WORDS)
            window = query_tokens[window_start:i]

            for prep in reversed(window):  # nearest preposition wins
                if prep in _DONOR_PREPS:
                    return "donor"
                if prep in _RECIPIENT_PREPS:
                    return "recipient"
            # Found the anchor but no qualifying preposition nearby → keep scanning.

    return "ambiguous"


def classify_place_roles(
    *,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...],
    predicates: tuple[Predicate, ...],
) -> tuple[str, ...]:
    """Return the **donor (entity) DCIDs** — resolved places not bound as a constraint.

    Implements the set difference::

        donor_dcids = resolved_dcids − {values bound in any predicate's constraints
                                        or constraint_sets}

    A place that appears as a constraint value in *any* predicate (scalar via
    ``constraints`` OR set-valued via ``constraint_sets``) is considered a recipient
    (observation-constraint axis) and is excluded from the observation entity set.
    Preserves the input order of *resolved_places*.

    Pure function; no side effects.  Empty input → empty output.

    Args:
        resolved_places: Sequence of ``(dcid, canonical_name, input_surface, role)``
            4-tuples returned by the place-resolution step.  The ``role`` field
            is ignored here — donor classification uses the set-difference of
            bound constraint values, not the pre-computed directional role.
        predicates: All bound predicates for the current variable.  Constraint
            values are collected from every predicate's ``constraints`` mapping
            and from every frozenset in ``constraint_sets``.

    Returns:
        Tuple of DCIDs that are NOT bound as a constraint value in any predicate,
        in the same order they appear in *resolved_places*.
    """
    if not resolved_places:
        return ()

    # Collect every non-None constraint value across all predicates —
    # including set-bound recipients from constraint_sets (each a frozenset).
    bound_values: set[str] = set()
    for pred in predicates:
        for v in pred.constraints.values():
            if v is not None:
                bound_values.add(v)
        for members in pred.constraint_sets.values():
            bound_values.update(members)

    return tuple(
        dcid for dcid, _name, _surface, _role in resolved_places if dcid not in bound_values
    )
